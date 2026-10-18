"""What a tool is, as far as the agent is concerned - wherever it comes from.

    Tool          a name, a description, a JSON Schema for its input, and a
                  callable that runs it (sync or async) and returns a result
    ToolResult    what a call came back with - including a call that never ran
    ToolProvider  something that offers tools: the MCP servers today, a local
                  provider (files, search, git) next

The shape is chat-completions' own: `Tool.to_function()` is one entry of a
request's `tools[]`, and `input_schema` is JSON Schema stored whole and unread
- which is also what an MCP server's `tools/list` hands back, so an MCP tool
becomes a `Tool` with a rename and no translation.

A provider is anything with `tools()` - and, optionally, `guide()` (a block
of text the agent puts after its system prompt, saying what these tools are
for) and `unrouted(name)` (an answer for a name it recognises as meant for it
but cannot run). `tools.registry.ToolRegistry` gathers providers into the one
object the agent lists and calls tools through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, Union

#: What a tool's callable may return: a `ToolResult`, or plain text / a JSON
#: object, which the registry wraps - or an awaitable of any of those.
Outcome = Union["ToolResult", str, dict]
ToolCall = Callable[[dict], Union[Outcome, Awaitable[Outcome]]]


@dataclass
class ToolResult:
    """What one tool call came back with - including a call that never ran.

    `text` is the text the tool returned, `structured` its JSON object if it
    sent one. `for_model` is what goes into the `tool` message of the next
    chat-completions request: the structured object when there is one,
    because it is exact and the text is usually a rendering of it, and the
    text otherwise.

    Two kinds of failure, and they must not be confused:

      * the *call* failed - unreachable, HTTP error, a name that routes
        nowhere. The tool never ran; `transport_error` (or `routing_error`)
        says why.
      * the *tool* failed - it ran and answered with an error ("no such
        file"). That is an answer, and a model should read it exactly as it
        reads a success.

    `server_id` and `tool` say where the call went - for an MCP tool, which
    server and that server's own name for it; `cached` that the answer was
    reused from an identical call earlier in the same turn.
    """

    ok: bool = False
    is_error: bool = False
    text: str = ""
    structured: dict | None = None
    transport_error: str = ""
    elapsed_ms: int = 0
    steps: list = field(default_factory=list)
    server_id: str = ""
    tool: str = ""
    cached: bool = False
    routing_error: str = ""

    def for_model(self) -> str:
        if self.structured is not None and not self.is_error:
            return json.dumps(self.structured, ensure_ascii=False)
        return self.text or ("(the tool returned nothing)" if self.ok else "Error: tool failed")

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "is_error": self.is_error,
            "text": self.text,
            "structured": self.structured,
            "transport_error": self.transport_error,
            "elapsed_ms": self.elapsed_ms,
            "steps": list(self.steps),
            "server_id": self.server_id,
            "tool": self.tool,
            "cached": self.cached,
            "routing_error": self.routing_error,
        }

    @classmethod
    def of(cls, outcome: Any) -> "ToolResult":
        """Whatever a tool's callable returned, as a `ToolResult`."""
        if isinstance(outcome, ToolResult):
            return outcome
        if isinstance(outcome, dict):
            return cls(ok=True, text=json.dumps(outcome, ensure_ascii=False), structured=outcome)
        return cls(ok=True, text="" if outcome is None else str(outcome))


@dataclass
class Tool:
    """One tool the agent can be offered and can call.

    `name` is the name the model sees and calls it by - unique across every
    provider in a registry, and inside chat-completions' `^[a-zA-Z0-9_-]{1,64}$`.
    `source` says where it comes from (an MCP server id, `local`, ...), for
    receipts and for the panel; it plays no part in routing.
    """

    name: str
    description: str
    input_schema: dict
    call: ToolCall
    source: str = ""
    title: str = ""

    def to_function(self) -> dict:
        """The same tool in the shape a chat-completions request wants."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class ToolProvider(Protocol):
    """Something that offers tools. Only `tools()` is required.

    Optional, and looked up with `getattr`:

        guide() -> str            a system block on what these tools are for
        unrouted(name) -> ToolResult | None
                                  an answer for a name meant for this provider
                                  that matches none of its tools
    """

    def tools(self) -> list[Tool]: ...
