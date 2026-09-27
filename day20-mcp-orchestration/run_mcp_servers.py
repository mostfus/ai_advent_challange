"""Day 20: this folder's MCP servers, started together - by the app, or by hand.

Four servers are four terminals, and a flow over four servers is exactly
where one of them is forgotten - the agent then routes a call to a port
nobody is listening on, and the flow stops at that step. So `uv run
server.py` starts them itself (`LocalServers`, from the app's lifespan) and
stops them when it stops. Or, without the app:

    uv run run_mcp_servers.py                 # all four
    uv run run_mcp_servers.py weather planner # just these

Each server is still its own process, as it would be anywhere else, and the
agent still reaches it over HTTP exactly as it reaches DeepWiki - starting
them from the app changes who presses the button, not what an MCP server is.

Three rules, in both cases:

  * **a port that already answers is left alone.** Somebody started that
    server already - in another terminal, or the events server kept running
    24/7 - and a second copy would only fail to bind. It is used, and it is
    not stopped on the way out, because it was not ours to stop;
  * **the events server is skipped when `EVENTS_MCP_URL` points elsewhere**
    (a VPS, day 18): there is one already, and it is not this machine's;
  * `MCP_AUTOSTART=0` in `.env` switches the app's half of this off.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
HOST = "127.0.0.1"

#: name -> (script, port, the server's id in the MCP panel)
SERVERS = {
    "maps": ("maps_mcp_server.py", 8787, "google-maps"),
    "events": ("events_mcp_server.py", 8788, "cape-town-events"),
    "weather": ("weather_mcp_server.py", 8789, "weather"),
    "planner": ("planner_mcp_server.py", 8790, "planner"),
}

AUTOSTART_ENV = "MCP_AUTOSTART"
READY_SECONDS = 20.0


def port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def autostart_enabled() -> bool:
    return (os.getenv(AUTOSTART_ENV) or "1").strip().lower() not in ("0", "false", "no", "off")


def events_elsewhere() -> str:
    """The remote events server's URL, if `.env` points at one."""
    remote = (os.getenv("EVENTS_MCP_URL") or "").strip()
    local = f"{HOST}:{SERVERS['events'][1]}", f"localhost:{SERVERS['events'][1]}"
    return remote if remote and not any(l in remote for l in local) else ""


class LocalServers:
    """Start some of this folder's servers, wait for them, stop the ones started."""

    def __init__(self, names: list | None = None, log=print) -> None:
        self.names = list(names or SERVERS)
        unknown = [n for n in self.names if n not in SERVERS]
        if unknown:
            raise ValueError("unknown: " + ", ".join(unknown) + " - pick from " + ", ".join(SERVERS))
        self.log = log
        self.started: dict = {}      # name -> Popen, the ones this object owns
        self.found: list = []        # names that were already running
        self.skipped: dict = {}      # name -> why

    def start(self) -> "LocalServers":
        remote = events_elsewhere()
        for name in self.names:
            script, port, _ = SERVERS[name]
            if name == "events" and remote:
                self.skipped[name] = f"EVENTS_MCP_URL points at {remote}"
                self.log(f"[{name:<7}] not started: {self.skipped[name]}")
                continue
            if port_open(port):
                self.found.append(name)
                self.log(f"[{name:<7}] already running on :{port} - using it, and leaving it running")
                continue
            process = subprocess.Popen(
                [sys.executable, str(HERE / script)], cwd=HERE, text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            self.started[name] = process
            threading.Thread(target=self.pump, args=(name, process.stdout), daemon=True).start()
            self.log(f"[{name:<7}] starting http://{HOST}:{port}/mcp  (pid {process.pid})")
        return self

    def pump(self, name: str, stream) -> None:
        for line in iter(stream.readline, ""):
            self.log(f"[{name:<7}] {line.rstrip()}")

    def wait_ready(self, timeout: float = READY_SECONDS) -> list:
        """Server ids (as in the MCP panel) whose port answers - started or found."""
        deadline = time.monotonic() + timeout
        waiting = set(self.started) | set(self.found)
        ready = []
        while waiting and time.monotonic() < deadline:
            for name in sorted(waiting):
                process = self.started.get(name)
                if process is not None and process.poll() is not None:
                    self.log(f"[{name:<7}] exited with {process.returncode} before it was ready")
                    waiting.discard(name)
                elif port_open(SERVERS[name][1]):
                    ready.append(SERVERS[name][2])
                    waiting.discard(name)
            if waiting:
                time.sleep(0.2)
        for name in waiting:
            self.log(f"[{name:<7}] not answering after {timeout:.0f} s")
        return ready

    def alive(self) -> list:
        return [name for name, p in self.started.items() if p.poll() is None]

    def stop(self, timeout: float = 10.0) -> None:
        """Stop what this object started. What it found running it leaves alone."""
        for process in self.started.values():
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + timeout
        for name, process in self.started.items():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                self.log(f"[{name:<7}] did not stop in {timeout:.0f} s - killed")


def main(argv: list) -> int:
    from dotenv import load_dotenv

    load_dotenv(HERE / ".env")
    try:
        servers = LocalServers(argv[1:] or None)
    except ValueError as err:
        print(err)
        return 2
    servers.start()
    signal.signal(signal.SIGTERM, lambda *_: servers.stop())
    try:
        while servers.alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    servers.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
