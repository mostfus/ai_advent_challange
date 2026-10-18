"""A stdio MCP server for the tests: JSON-RPC, one message per line, no SDK.

Tools: `git_status` (needs `repo_path`), `list_directory`, `read_text_file`,
`big` (twenty thousand characters), and `write_file`, which the tests check is
never offered nor called - if it ever runs, it leaves a file saying so.
Before answering `initialize` it asks the client for a `ping`, the way a real
server may, and logs a line to stderr.
"""

import json
import sys
from pathlib import Path

TOOLS = [
    {"name": "git_status", "description": "Status of a repository.",
     "inputSchema": {"type": "object", "properties": {"repo_path": {"type": "string"}},
                     "required": ["repo_path"]}},
    {"name": "list_directory", "description": "List a folder.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "read_text_file", "description": "Read a file.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "big", "description": "A long answer.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "write_file", "description": "Write a file.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def answer(name: str, args: dict) -> dict:
    if name == "git_status":
        return {"content": [{"type": "text", "text": f"On branch main\nrepo={args.get('repo_path')}"}]}
    if name == "list_directory":
        return {"content": [{"type": "text", "text": "[FILE] README.md\n[DIR] Sources"}]}
    if name == "read_text_file":
        return {"content": [{"type": "text", "text": f"contents of {args.get('path')}"}]}
    if name == "big":
        return {"content": [{"type": "text", "text": "x" * 20000}]}
    if name == "write_file":
        Path(sys.argv[1] if len(sys.argv) > 1 else ".", "WRITTEN").write_text("write_file ran")
        return {"content": [{"type": "text", "text": "written"}]}
    return {"content": [{"type": "text", "text": f"no tool {name}"}], "isError": True}


def main() -> None:
    print("fake server starting", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method, mid = message.get("method"), message.get("id")
        if method is None:
            continue                      # a reply to our ping
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-server", "version": "1.0"}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = message.get("params") or {}
            send({"jsonrpc": "2.0", "id": mid, "result": answer(params.get("name"), params.get("arguments") or {})})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}})


if __name__ == "__main__":
    main()
