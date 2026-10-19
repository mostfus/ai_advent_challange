"""Day 34: agentic code - a chat that does the task in the open project itself, like a small Claude Code.

A chat with `agentic_code` on sends every message here (`core.turn.run_turn`).
The message is a goal, not instructions - "find every place X is used",
"update the docs after these changes" - and the turn differs from a plain one
in four things:

  * the system prompt says the assistant works in the project with tools,
    asks one question when the goal is ambiguous, and names the project; the
    project's own instructions file (`AGENTS.md`, `CLAUDE.md`) follows it;
  * the tools are the project's files - list, read, search, write, edit, and
    its index by meaning (`tools.files`) - and its git, read-only
    (`core.project`); the model decides which to call and when;
  * the tool loop has a bigger budget (`code_agent.json`): a task that reads
    ten files and edits four is twenty rounds, not day 20's ten;
  * what the writes did is kept (`tools.files.ChangeLog`) and stored with the
    answer, one diff per file - the "Changes" window under it.

Nothing asks the model again after an agentic answer - no stage-advisor or
invariant retry: a second answer would run the writes a second time. The
invariants still go up with the request; they are stated, not re-checked.

`CodeAgentConfig` is `code_agent.json`: the budget, the instructions files,
the `files` limits and lists, and the scenarios - the goals the page offers as
one-click starts (find usages, update the docs, generate a changelog or an
ADR, check the rules).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.project import APP_DIR, Project
from tools.files import FilesConfig

CONFIG_PATH = APP_DIR / "code_agent.json"

#: A tool argument or result kept with the answer is cut to this - the model
#: had all of it; the receipt under the answer needs a line.
STORED_ARGUMENT_CHARS = 400
STORED_RESULT_CHARS = 1500


@dataclass
class Scenario:
    """A goal the page offers as a one-click start; `{name}` marks a blank to fill in."""

    id: str
    label: str
    prompt: str

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "prompt": self.prompt}


@dataclass
class CodeAgentConfig:
    max_tool_rounds: int = 30
    max_tool_calls: int = 80
    instructions: list[str] = field(default_factory=lambda: ["AGENTS.md", "CLAUDE.md"])
    max_instructions_chars: int = 8000
    files: FilesConfig = field(default_factory=FilesConfig)
    scenarios: list[Scenario] = field(default_factory=list)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "CodeAgentConfig":
        """Read `code_agent.json`; with no file, the defaults. Raises `ValueError` for a bad one."""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected an object")
        try:
            config = cls(files=FilesConfig.from_dict(raw.get("files")), path=path)
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        for key in ("max_tool_rounds", "max_tool_calls", "max_instructions_chars"):
            if key in raw:
                try:
                    value = int(raw[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{path.name}: {key} must be a positive number") from exc
                if value < 1:
                    raise ValueError(f"{path.name}: {key} must be a positive number")
                setattr(config, key, value)
        if "instructions" in raw:
            if not isinstance(raw["instructions"], list):
                raise ValueError(f"{path.name}: instructions must be a list of file names")
            config.instructions = [str(name) for name in raw["instructions"]]
        for entry in raw.get("scenarios") or []:
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("prompt"):
                raise ValueError(f"{path.name}: every scenario needs an id and a prompt")
            config.scenarios.append(Scenario(str(entry["id"]), str(entry.get("label") or entry["id"]),
                                             str(entry["prompt"])))
        return config


SYSTEM = """You are a coding agent working inside one software project, like a small Claude Code. You do the task yourself: you read, search, analyse and change the project's files with your tools, and then report.

Project: {name}
Folder: {path} - every path you pass or get is relative to it
Git repository: {git}

Your tools:
- list_files, read_file, search_text: what is in the project. search_text is grep over every file.{docs}
- write_file, edit_file: change the project. They write to disk at once; the user sees every change as a diff under your answer.{git_tools}

How to work:
- The user gives you a goal, not steps. Decide yourself which files matter: search and read before you conclude or change anything. Never state what a file contains unless a tool showed it to you in this conversation.
- When the goal is ambiguous, or a decision is the user's (which of two approaches, whether to overwrite a hand-written file, a name you would have to invent), ask one short question and stop - change nothing until they answer.
- Change files with edit_file (read the file first and copy the fragment exactly) or write_file (a new file, or a full rewrite). Change only what the goal needs, in the project's own style and language. Never touch secrets.
- Finding or checking something is not permission to change it: when the user asks to find, list or check, report and do not edit.
- When you are done, answer briefly: what you found, with `path:line` references, and which files you changed and why. Do not paste whole files back - the diff is shown separately.
- Answer in the language of the user's message."""

DOCS_LINE = "\n- search_docs: the project's README, docs and code indexed by meaning - for when you do not know the names."
GIT_LINE = ("\n- git (git_status, git_diff_unstaged, git_diff_staged, git_diff, git_log, git_show): the current "
            "branch, uncommitted changes and history, read-only. For \"update the docs after the code changes\" "
            "start here.")

INSTRUCTIONS = ("--- The project's instructions, from {name} ---\n"
                "Written by the project's maintainers for anyone - person or agent - working in it. Follow "
                "them; when the user asks to check the project against its rules, these are the rules.\n\n{text}")


def describe_git(project: Project) -> str:
    if project.git_root is None:
        return "none - there is no git history"
    if project.git_root == project.path:
        return str(project.git_root)
    inside = project.path.relative_to(project.git_root).as_posix()
    return (f"{project.git_root} - the project folder is `{inside}/` inside it, so paths in git's output "
            f"start with `{inside}/`; the file tools take paths without it")


def system_prompt(project: Project, *, docs: bool, git_tools: bool) -> str:
    return SYSTEM.format(name=project.name, path=project.path, git=describe_git(project),
                         docs=DOCS_LINE if docs else "", git_tools=GIT_LINE if git_tools else "")


def project_instructions(project: Project, config: CodeAgentConfig) -> str | None:
    """The first instructions file the project has, as a system block - or None."""
    for name in config.instructions:
        path = project.path / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        limit = config.max_instructions_chars
        if len(text) > limit:
            text = text[:limit] + f"\n... (cut: {name} is longer - read the rest with read_file)"
        return INSTRUCTIONS.format(name=name, text=text)
    return None


def _short(value, limit: int):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f" ... ({len(value)} characters)"
    return value


def compact_calls(calls: list) -> list:
    """The turn's tool calls as they are stored and shown: long arguments (a whole
    file passed to write_file) and long results cut - the model had all of it."""
    out = []
    for call in calls or []:
        call = dict(call)
        if isinstance(call.get("arguments"), dict):
            call["arguments"] = {k: _short(v, STORED_ARGUMENT_CHARS) for k, v in call["arguments"].items()}
        call["text"] = _short(call.get("text") or "", STORED_RESULT_CHARS)
        if call.get("structured") is not None and len(json.dumps(call["structured"], ensure_ascii=False)) > STORED_RESULT_CHARS:
            call["structured"] = None
        out.append(call)
    return out


def totals(changes: list) -> dict:
    """Files changed, lines added and removed - the line on the Changes button."""
    return {"files": len(changes), "added": sum(c.get("added", 0) for c in changes),
            "removed": sum(c.get("removed", 0) for c in changes)}
