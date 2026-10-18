"""The one place the agent lists tools and calls them - whatever provides them.

    ToolRegistry([provider, ...])
        .tools()          every tool of every provider, first name wins
        .functions()      the same as a request's `tools[]`
        .guide()          the providers' guides, joined - a system block
        .call(name, args) route by name, run, wrap the result  (sync)
        .acall(name, args)                                      (async)

The agent is handed a registry and never learns what is behind a name: an MCP
server over HTTP, or a function in this process. Routing is by name, and
nothing is guessed - a name no tool has comes back as an error the model can
act on (with the nearest names), because a model told "no such tool" invents
another one, and a model told "did you mean `x__y`" calls it.

Built fresh for every turn, off the stored MCP catalogues (and, later, the
local provider) - so switching a server off takes effect on the next message,
and anything a provider remembers within a turn (a server that is down, an
identical call already made) is remembered for exactly one turn.
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import threading

from tools.base import Tool, ToolProvider, ToolResult


def run_sync(awaitable):
    """Wait for a coroutine from synchronous code.

    The agent loop is synchronous and runs a round's calls in worker threads,
    where there is no event loop - so `asyncio.run` is the whole of it. From a
    thread that already runs a loop, the coroutine gets a thread of its own.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(awaitable)
        except BaseException as err:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = err

    thread = threading.Thread(target=target, name="tool-call")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


class ToolRegistry:
    """Every provider's tools behind one name -> call."""

    def __init__(self, providers: list | tuple = ()) -> None:
        self.providers: list[ToolProvider] = list(providers)

    def add(self, provider: ToolProvider) -> "ToolRegistry":
        self.providers.append(provider)
        return self

    # -- what is on offer ----------------------------------------------------

    def tools(self) -> list[Tool]:
        """Every tool, in provider order. A name already taken is dropped
        rather than silently shadowing the first tool of that name."""
        seen: dict[str, Tool] = {}
        for provider in self.providers:
            for tool in provider.tools():
                seen.setdefault(tool.name, tool)
        return list(seen.values())

    def find(self, name: str) -> Tool | None:
        for tool in self.tools():
            if tool.name == name:
                return tool
        return None

    def functions(self) -> list[dict]:
        return [tool.to_function() for tool in self.tools()]

    def guide(self) -> str:
        parts = []
        for provider in self.providers:
            guide = getattr(provider, "guide", None)
            text = guide() if callable(guide) else ""
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def __bool__(self) -> bool:
        return bool(self.tools())

    def __len__(self) -> int:
        return len(self.tools())

    # -- calling one -----------------------------------------------------------

    def call(self, name: str, arguments: dict) -> ToolResult:
        """Run the tool called `name`. Never raises for a name that routes nowhere."""
        tool = self.find(name)
        if tool is None:
            return self.unrouted(name)
        outcome = tool.call(dict(arguments or {}))
        if inspect.isawaitable(outcome):
            outcome = run_sync(outcome)
        return self.stamp(ToolResult.of(outcome), tool)

    async def acall(self, name: str, arguments: dict) -> ToolResult:
        """`call`, for a caller that is already inside an event loop."""
        tool = self.find(name)
        if tool is None:
            return self.unrouted(name)
        outcome = tool.call(dict(arguments or {}))
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return self.stamp(ToolResult.of(outcome), tool)

    @staticmethod
    def stamp(result: ToolResult, tool: Tool) -> ToolResult:
        result.server_id = result.server_id or tool.source
        result.tool = result.tool or tool.name
        return result

    def unrouted(self, name: str) -> ToolResult:
        """A name that goes nowhere, answered with the names that would have.

        A provider that recognises the name as meant for it (an MCP-style
        `server__tool`, say) explains it in its own terms; otherwise the
        answer is the nearest names on offer.
        """
        for provider in self.providers:
            explain = getattr(provider, "unrouted", None)
            result = explain(name) if callable(explain) else None
            if result is not None:
                return result
        names = [tool.name for tool in self.tools()]
        text = f"Error: there is no tool {name!r}."
        nearest = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
        if nearest:
            text += " Did you mean " + " or ".join(nearest) + "?"
        text += " Call a tool by its name exactly as offered."
        return ToolResult(ok=False, is_error=True, text=text, transport_error="unknown tool",
                          tool=name, routing_error="unknown tool")
