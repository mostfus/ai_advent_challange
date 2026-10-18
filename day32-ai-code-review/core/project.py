"""Day 31: the project the assistant works on - a folder, its git repository, its MCP servers.

    Project              the folder opened in the Project tab, and the git
                         repository it is in, if any
    ProjectToolsConfig   `project_mcp.json`: which MCP servers to start for a
                         project, and which of their tools the model may call
    ProjectServers       those servers, running - started when a project is
                         opened, stopped when another one is - and the tool
                         provider a /help turn is offered (`provider()`)

The servers are the official ones, run over stdio (`tools.mcp_stdio`):
`mcp-server-git` through `uvx` and `@modelcontextprotocol/server-filesystem`
through `npx`. Both can *write* - commit, check out, edit files - and an
assistant that answers questions must not, so the config lists the tools on
offer and nothing else is shown to the model or called. Three more things are
done to a call on its way through, all in the config:

    fixed      arguments the model never sees and always gets: git's
               `repo_path` is the project's repository, so a model can point
               it nowhere else - and a small model cannot get it wrong
    defaults   arguments filled in when the model leaves them out: the
               folders `directory_tree` does not descend into
    deny       file names the file tools refuse: `.env` holds the API key, and
               whatever the model reads is sent to the model's provider

and every result is cut to `max_result_chars` - a whole file tree in one
`tool` message would crowd out the question, and a local model's context is
a few thousand tokens.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Callable

from tools.base import ToolResult
from tools.mcp_client import MCPError
from tools.mcp_provider import McpProvider, Route
from tools.mcp_stdio import StdioServer

#: The app's own folder: the project when none was opened, and where
#: `project_mcp.json` lives.
APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "project_mcp.json"

DEFAULT_MAX_RESULT_CHARS = 8000
#: How long a /help turn waits for servers still starting before going ahead
#: without them.
READY_WAIT_SECONDS = 30.0
#: Names a denied pattern does not catch: templates of secret files are not secrets.
SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")
#: The arguments of the file tools that name a path.
PATH_ARGUMENTS = ("path", "paths", "source", "destination")


# -------------------------------------------------------------------- the project

@dataclass(frozen=True)
class Project:
    """A folder, and the git repository it belongs to (None when it is in none)."""

    path: Path
    git_root: Path | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @classmethod
    def at(cls, raw: str | Path) -> "Project":
        """The project in a folder. Raises `ValueError` for a path that is not one."""
        text = str(raw or "").strip()
        if not text:
            raise ValueError("Type the path of a project folder")
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise ValueError("Use an absolute path, like /Users/me/src/MyApp or ~/src/MyApp")
        path = path.resolve()
        if not path.is_dir():
            raise ValueError(f"There is no folder at {path}")
        return cls(path, git_toplevel(path))

    def describe(self) -> dict:
        return {"name": self.name, "path": str(self.path),
                "git_root": str(self.git_root) if self.git_root else None,
                "is_git": self.git_root is not None,
                "is_app": self.path == APP_DIR}


def git_toplevel(path: Path) -> Path | None:
    """The repository a folder is in, by asking git. None when it is in none, or git is missing."""
    try:
        done = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = done.stdout.strip()
    return Path(top).resolve() if done.returncode == 0 and top else None


# --------------------------------------------------------------------- the config

@dataclass
class ServerSpec:
    """One server of `project_mcp.json`."""

    id: str
    label: str
    command: list[str]
    tools: list[str]
    purpose: str = ""
    needs_git: bool = False
    fixed: dict = field(default_factory=dict)
    defaults: dict = field(default_factory=dict)
    deny: list[str] = field(default_factory=list)

    @staticmethod
    def fill(value, project: Project):
        """`{project}` and `{git_root}` in a string (or in a list or dict of them) replaced."""
        if isinstance(value, str):
            return (value.replace("{project}", str(project.path))
                         .replace("{git_root}", str(project.git_root or project.path)))
        if isinstance(value, list):
            return [ServerSpec.fill(v, project) for v in value]
        if isinstance(value, dict):
            return {k: ServerSpec.fill(v, project) for k, v in value.items()}
        return value

    def denied(self, arguments: dict) -> str | None:
        """The first path argument whose file name is on the deny list, or None."""
        for key in PATH_ARGUMENTS:
            value = arguments.get(key)
            for path in value if isinstance(value, list) else [value]:
                if not isinstance(path, str) or not path:
                    continue
                name = PurePosixPath(path.replace("\\", "/")).name
                if name.endswith(SAFE_SUFFIXES):
                    continue
                if any(fnmatch.fnmatch(name, pattern) for pattern in self.deny):
                    return path
        return None


@dataclass
class ProjectToolsConfig:
    servers: list[ServerSpec] = field(default_factory=list)
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    path: Path | None = None

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "ProjectToolsConfig":
        """Read `project_mcp.json`; with no file, no servers. Raises `ValueError` for a bad one."""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("servers", []), list):
            raise ValueError(f"{path.name}: expected an object with a list of servers")
        servers = []
        for entry in raw.get("servers", []):
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("command"):
                raise ValueError(f"{path.name}: every server needs an id and a command")
            if not isinstance(entry["command"], list) or not isinstance(entry.get("tools", []), list):
                raise ValueError(f"{path.name}: {entry['id']}: command and tools must be lists")
            servers.append(ServerSpec(
                id=str(entry["id"]),
                label=str(entry.get("label") or entry["id"]),
                command=[str(part) for part in entry["command"]],
                tools=[str(name) for name in entry.get("tools", [])],
                purpose=str(entry.get("purpose") or ""),
                needs_git=bool(entry.get("needs_git")),
                fixed=dict(entry.get("fixed") or {}),
                defaults=dict(entry.get("defaults") or {}),
                deny=[str(p) for p in entry.get("deny") or []],
            ))
        return cls(servers, int(raw.get("max_result_chars") or DEFAULT_MAX_RESULT_CHARS), path)


# -------------------------------------------------------------------- the servers

@dataclass
class Running:
    """One configured server for the open project, and where it has got to."""

    spec: ServerSpec
    state: str = "starting"          # starting | ready | failed | skipped
    error: str = ""
    server: StdioServer | None = None
    tools: list = field(default_factory=list)       # McpTool, the allowed ones only
    server_name: str = ""
    elapsed_ms: int = 0
    ready: threading.Event = field(default_factory=threading.Event)

    def describe(self) -> dict:
        return {"id": self.spec.id, "label": self.spec.label, "purpose": self.spec.purpose,
                "state": self.state, "error": self.error, "server_name": self.server_name,
                "command": " ".join(self.spec.command), "elapsed_ms": self.elapsed_ms,
                "tools": [t.name for t in self.tools], "allowed": list(self.spec.tools)}


ServerFactory = Callable[..., StdioServer]


def compact(text: str) -> str:
    """JSON re-written without indentation - a file tree is half whitespace. Other text as it is."""
    stripped = text.strip()
    if stripped[:1] in "[{":
        try:
            return json.dumps(json.loads(stripped), ensure_ascii=False, separators=(",", ":"))
        except ValueError:
            pass
    return text


def trim(result: ToolResult, limit: int) -> ToolResult:
    """A result cut to what a `tool` message may carry, saying that it was cut."""
    result.text = compact(result.text or "")
    if result.structured is not None and len(json.dumps(result.structured, ensure_ascii=False)) > limit:
        result.structured = None
    if len(result.text) > limit:
        result.text = (result.text[:limit] + f"\n... (cut: {len(result.text)} characters, the first "
                       f"{limit} shown - ask for something narrower)")
    return result


class ProjectServers:
    """The open project's MCP servers - each started in a thread of its own."""

    def __init__(self, config: ProjectToolsConfig, factory: ServerFactory = StdioServer,
                 log: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.factory = factory
        self.log = log or (lambda line: None)
        self.project: Project | None = None
        self.running: dict[str, Running] = {}
        self._lock = threading.Lock()
        self._generation = 0

    def open(self, project: Project) -> None:
        """Stop the old project's servers and start this one's, without waiting for them."""
        self.stop()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.project = project
            self.running = {}
            for spec in self.config.servers:
                run = Running(spec)
                self.running[spec.id] = run
                if spec.needs_git and project.git_root is None:
                    run.state, run.error = "skipped", "the folder is not in a git repository"
                    run.ready.set()
                    continue
                threading.Thread(target=self._start, args=(run, project, generation),
                                 name=f"mcp-{spec.id}", daemon=True).start()

    def _start(self, run: Running, project: Project, generation: int) -> None:
        started = time.perf_counter()
        server = self.factory(ServerSpec.fill(run.spec.command, project), cwd=str(project.path))
        try:
            result = server.start()
            listing = server.list_tools()
            if not listing.ok:
                raise MCPError(listing.error)
        except MCPError as err:
            server.stop()
            run.state, run.error = "failed", str(err)
            self.log(f"[project] {run.spec.id}: failed - {err}")
        else:
            with self._lock:
                current = generation == self._generation
            if not current:
                # Another project was opened while this one was starting.
                server.stop()
                return
            allowed = set(run.spec.tools)
            run.server = server
            run.tools = [t for t in listing.tools if t.name in allowed]
            run.server_name = str((result.get("serverInfo") or {}).get("name") or "")
            run.state = "ready"
            self.log(f"[project] {run.spec.id}: ready, {len(run.tools)} of {len(listing.tools)} tools offered")
        finally:
            run.elapsed_ms = int((time.perf_counter() - started) * 1000)
            run.ready.set()

    def stop(self) -> None:
        """Stop every server - and any still starting, once it has (the generation moves on)."""
        with self._lock:
            self._generation += 1
            running, self.running = list(self.running.values()), {}
        for run in running:
            if run.server is not None:
                run.server.stop()

    def wait(self, timeout: float = READY_WAIT_SECONDS) -> None:
        """Wait, up to `timeout` in all, for servers still starting."""
        deadline = time.monotonic() + timeout
        for run in list(self.running.values()):
            run.ready.wait(max(0.0, deadline - time.monotonic()))

    def status(self) -> list[dict]:
        return [run.describe() for run in self.running.values()]

    # -- what a /help turn is offered ------------------------------------------

    def provider(self) -> McpProvider:
        """Every allowed tool of every ready server, as one provider - its calls routed here."""
        servers = []
        for run in self.running.values():
            if run.state != "ready":
                continue
            fixed = set(run.spec.fixed)
            servers.append(SimpleNamespace(
                id=run.spec.id, label=run.spec.label, url=f"stdio:{run.spec.id}", enabled=True,
                catalogue={"ok": True, "instructions": run.spec.purpose, "server_name": run.server_name},
                tools=(lambda run=run, fixed=fixed: [
                    {"name": t.name, "title": t.title, "description": t.description,
                     "input_schema": hide(t.input_schema, fixed)} for t in run.tools]),
            ))
        return McpProvider.from_servers(servers, call=self.call, notes={})

    def call(self, route: Route, arguments: dict) -> ToolResult:
        """One routed call: allowed, filled in, checked against the deny list, run, trimmed."""
        run = self.running.get(route.server_id)
        if run is None or run.state != "ready" or run.server is None:
            reason = run.error if run is not None and run.error else "it is not running"
            return ToolResult(ok=False, is_error=True, text=f"Error: {route.server_label} is unavailable ({reason}).",
                              transport_error=reason)
        spec = run.spec
        if route.tool not in spec.tools:
            return ToolResult(ok=False, is_error=True, text=f"Error: {route.tool} is not offered.",
                              routing_error="not allowed")
        project = self.project
        args = {**ServerSpec.fill(spec.defaults.get(route.tool, {}), project),
                **(arguments or {}),
                **ServerSpec.fill(spec.fixed, project)}
        blocked = spec.denied(args)
        if blocked is not None:
            return ToolResult(ok=False, is_error=True,
                              text=(f"Error: {blocked} is not readable by this assistant - files like it "
                                    "hold secrets (the deny list in project_mcp.json)."))
        result = run.server.call_tool(route.tool, args)
        self.log(f"[project] {spec.id}/{route.tool} -> {'ok' if result.ok else 'error'} in {result.elapsed_ms} ms")
        return trim(result, self.config.max_result_chars)


def hide(schema: dict, fixed: set) -> dict:
    """A tool's input schema without the arguments the config fills in itself."""
    if not fixed or not isinstance(schema, dict):
        return schema
    out = dict(schema)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        out["properties"] = {k: v for k, v in properties.items() if k not in fixed}
    required = schema.get("required")
    if isinstance(required, list):
        out["required"] = [k for k in required if k not in fixed]
    return out
