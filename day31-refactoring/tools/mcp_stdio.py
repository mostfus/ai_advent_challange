"""Day 31: an MCP server as a child process - JSON-RPC over its stdin and stdout.

The other transport. `tools.mcp_client` speaks Streamable HTTP to servers
somebody else runs; a server you run yourself - the official git and
filesystem servers among them - speaks stdio: the client starts the process,
writes one JSON-RPC message per line to its stdin and reads the replies, one
per line, from its stdout. Stderr is the server's log, kept for error messages.

Unlike the HTTP client, the session is **held open** for as long as the
process lives. Starting `uvx mcp-server-git` costs a second and its first run
downloads it, so a server is started once per project and every call reuses
the session - which also means requests may overlap, so replies are matched
to requests by id rather than read in order.

    StdioServer(command, cwd).start()    spawn, `initialize`, `notifications/initialized`
        .list_tools()                    -> mcp_client.Listing
        .call_tool(name, arguments)      -> tools.base.ToolResult
        .stop()                          close stdin, then terminate the process group

A reader thread owns stdout: a reply wakes the request waiting for its id, a
request *from* the server (`ping`) is answered so the server never waits on
us, and notifications are dropped. From the outside everything is
synchronous, like the agent loop that calls it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from collections import deque

from tools.base import ToolResult
from tools.mcp_client import (
    CLIENT_NAME,
    CLIENT_VERSION,
    MAX_PAGES,
    PROTOCOL_VERSION,
    Listing,
    MCPError,
    McpTool,
)

#: A first `uvx` / `npx` run downloads the server before it can answer.
START_TIMEOUT_SECONDS = 180.0
CALL_TIMEOUT_SECONDS = 60.0
#: How much of the server's stderr is kept for error messages.
STDERR_LINES = 40
#: JSON-RPC's "method not found" - the answer to any request from the server
#: other than `ping`: this client declares no capabilities it could be asked for.
METHOD_NOT_FOUND = -32601


class StdioServer:
    """One MCP server process and the session held open over its pipes."""

    def __init__(self, command: list[str], cwd: str | None = None, env: dict | None = None,
                 start_timeout: float = START_TIMEOUT_SECONDS,
                 call_timeout: float = CALL_TIMEOUT_SECONDS) -> None:
        self.command = [str(part) for part in command]
        self.cwd = cwd
        self.env = dict(env or {})
        self.start_timeout = start_timeout
        self.call_timeout = call_timeout
        self.process: subprocess.Popen | None = None
        self.result: dict = {}
        self.protocol = PROTOCOL_VERSION
        self.stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, dict] = {}
        self._next_id = 0

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # -- the process -----------------------------------------------------------

    def start(self) -> dict:
        """Spawn the process and open the session; the `initialize` result. Raises `MCPError`.

        The process gets a session of its own, so `stop` can end it together
        with whatever it started - `npx` runs the server as a child of its own.
        """
        try:
            self.process = subprocess.Popen(
                self.command, cwd=self.cwd, env={**os.environ, **self.env},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as err:
            raise MCPError(f"could not start {self.command[0]!r}: {err}") from err
        threading.Thread(target=self._read_stdout, name="mcp-stdio-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="mcp-stdio-err", daemon=True).start()
        try:
            result = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            }, timeout=self.start_timeout)
            self.protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
            self.notify("notifications/initialized")
        except MCPError:
            self.stop()
            raise
        self.result = result
        return result

    def stop(self) -> None:
        """End the session: close stdin (a stdio server exits on EOF), then make sure."""
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                    process.wait(timeout=2)
                    break
                except (OSError, subprocess.TimeoutExpired):
                    continue
        with self._lock:
            for slot in self._pending.values():
                slot["event"].set()

    def last_words(self) -> str:
        """The end of the server's stderr - usually the reason it stopped."""
        tail = [line for line in self.stderr if line.strip()][-3:]
        return " | ".join(tail) if tail else "no output on stderr"

    # -- the wire ----------------------------------------------------------------

    def _send(self, message: dict) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            raise MCPError(f"the server is not running ({self.last_words()})")
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as err:
                raise MCPError(f"the server closed its input ({err})") from err

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """One request, its result object once the reply with its id arrives. Raises `MCPError`."""
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            slot = {"event": threading.Event(), "message": None}
            self._pending[request_id] = slot
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
            wait = self.call_timeout if timeout is None else timeout
            if not slot["event"].wait(wait):
                raise MCPError(f"{method}: no answer in {wait:.0f} s")
            message = slot["message"]
            if message is None:
                raise MCPError(f"{method}: the server exited ({self.last_words()})")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if "error" in message:
            err = message.get("error") or {}
            raise MCPError(f"{method}: the server refused it "
                           f"({err.get('code', '?')}: {err.get('message', 'no message')})")
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPError(f"{method}: reply carried no result object")
        return result

    def _read_stdout(self) -> None:
        process = self.process
        stream = process.stdout if process else None
        if stream is None:
            return
        with stream:
            for raw in stream:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    # Not JSON-RPC: a server that logs to stdout. Kept with its log.
                    self.stderr.append(line[:300])
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        self._answer(message)
                    continue
                with self._lock:
                    slot = self._pending.get(message.get("id"))
                if slot is not None:
                    slot["message"] = message
                    slot["event"].set()
        # End of output: the process is gone, and nobody should wait out a timeout for it.
        with self._lock:
            for slot in self._pending.values():
                slot["event"].set()

    def _read_stderr(self) -> None:
        process = self.process
        stream = process.stderr if process else None
        if stream is None:
            return
        with stream:
            for raw in stream:
                self.stderr.append(raw.decode("utf-8", "replace").rstrip()[:300])

    def _answer(self, message: dict) -> None:
        """A request from the server. `ping` is answered; anything else is refused at once."""
        reply: dict = {"jsonrpc": "2.0", "id": message.get("id")}
        if message.get("method") == "ping":
            reply["result"] = {}
        else:
            reply["error"] = {"code": METHOD_NOT_FOUND,
                              "message": f"{message.get('method')} is not supported by this client"}
        try:
            self._send(reply)
        except MCPError:
            pass

    # -- what the session is for ---------------------------------------------

    def list_tools(self) -> Listing:
        """The server's catalogue over the open session - a failure is a `Listing`, not an exception."""
        started = time.perf_counter()
        info = self.result.get("serverInfo") or {}
        capabilities = self.result.get("capabilities") or {}
        try:
            if "tools" not in capabilities:
                raise MCPError("this server does not offer tools")
            tools: list = []
            cursor = None
            pages = 0
            while pages < MAX_PAGES:
                page = self.request("tools/list", {"cursor": cursor} if cursor else {})
                pages += 1
                for entry in page.get("tools") or ():
                    tool = McpTool.from_dict(entry)
                    if tool is not None:
                        tools.append(tool)
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        except MCPError as err:
            return Listing(ok=False, error=str(err), protocol=self.protocol,
                           elapsed_ms=int((time.perf_counter() - started) * 1000))
        return Listing(
            ok=True,
            tools=tools,
            server_name=str(info.get("name") or ""),
            server_version=str(info.get("version") or ""),
            instructions=str(self.result.get("instructions") or "").strip(),
            protocol=self.protocol,
            capabilities=sorted(str(k) for k in capabilities),
            session=True,
            pages=pages,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    def call_tool(self, name: str, arguments: dict | None = None) -> ToolResult:
        """One `tools/call`. The call failing and the tool failing stay apart, as over HTTP."""
        started = time.perf_counter()
        try:
            result = self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        except MCPError as err:
            return ToolResult(ok=False, is_error=True, text=f"Error: {err}", transport_error=str(err),
                              elapsed_ms=int((time.perf_counter() - started) * 1000))
        content = result.get("content") if isinstance(result.get("content"), list) else []
        structured = result.get("structuredContent")
        is_error = bool(result.get("isError"))
        return ToolResult(
            ok=not is_error,
            is_error=is_error,
            text="\n".join(str(block.get("text") or "") for block in content
                           if isinstance(block, dict) and block.get("type") == "text").strip(),
            structured=structured if isinstance(structured, dict) else None,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
