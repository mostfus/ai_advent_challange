"""Day 20: start every MCP server of ours with one command.

Four servers are four terminals, and a flow over four servers is exactly
where one of them is forgotten - the agent then routes a call to a port
nobody is listening on, and the flow stops at that step (which is what the
orchestrator is for, but not what a demo is for). So:

    uv run run_mcp_servers.py                 # all four
    uv run run_mcp_servers.py weather planner # just these

Each server is its own process, as it would be anywhere else - this starts
them and prefixes their output, nothing more. Ctrl-C stops them all. The
events server is skipped when `EVENTS_MCP_URL` in `.env` points somewhere
else (a VPS, day 18): there is one already, and it is not this machine's.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent

SERVERS = {
    "maps": ("maps_mcp_server.py", 8787),
    "events": ("events_mcp_server.py", 8788),
    "weather": ("weather_mcp_server.py", 8789),
    "planner": ("planner_mcp_server.py", 8790),
}


def pump(name: str, stream) -> None:
    for line in iter(stream.readline, ""):
        print(f"[{name:<7}] {line}", end="", flush=True)


def main(argv: list) -> int:
    load_dotenv(HERE / ".env")
    wanted = argv[1:] or list(SERVERS)
    unknown = [name for name in wanted if name not in SERVERS]
    if unknown:
        print("unknown: " + ", ".join(unknown) + " - pick from " + ", ".join(SERVERS))
        return 2
    remote = (os.getenv("EVENTS_MCP_URL") or "").strip()
    if "events" in wanted and remote and "127.0.0.1:8788" not in remote and "localhost:8788" not in remote:
        print(f"[events ] not started: EVENTS_MCP_URL points at {remote}")
        wanted.remove("events")

    processes = []
    for name in wanted:
        script, port = SERVERS[name]
        process = subprocess.Popen(
            [sys.executable, str(HERE / script)], cwd=HERE, text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        processes.append((name, process))
        threading.Thread(target=pump, args=(name, process.stdout), daemon=True).start()
        print(f"[{name:<7}] http://127.0.0.1:{port}/mcp  (pid {process.pid})")

    def stop(*_args) -> None:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    try:
        while processes:
            for name, process in list(processes):
                if process.poll() is not None:
                    print(f"[{name:<7}] exited with {process.returncode}")
                    processes.remove((name, process))
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop()
        for _, process in processes:
            process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
