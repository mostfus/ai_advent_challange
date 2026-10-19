"""The MCP servers as one tool provider: choosing, routing, and one turn's memory.

Days 17 and 20. Every connected server's catalogue becomes `tools.base.Tool`s
the registry can offer, each under its qualified name - `deepwiki__ask_question`
- and each one's call goes over the wire to the server that owns it and
nowhere else. Three jobs, and one server never made anyone think about them:

  * **choosing**: with twenty tools on offer, the model has to know which
    server a request belongs to before it can pick a tool on it;
  * **routing**: every call has to reach the server that owns it and nothing
    else, and a call that cannot be routed has to come back as something the
    model can act on;
  * **remembering, for one turn**: a server that is down is not called again,
    and an identical call is answered once.

It knows about servers and tools; it does not know about DeepSeek (the agent
does) or about HTTP (`tools.mcp_client` does). It is built fresh for every
turn, off the stored catalogues, and thrown away at the end of it.

    McpProvider.from_servers(catalogues) ─┬─ tools()       -> Tools, under qualified names
                                          ├─ guide()       -> a system message: which server is for what
                                          └─ run(name, a)  -> route, call, remember

**Choosing is the model's job, and the guide is how it is taught.** A tool's
description says what *it* does; nothing says what a *server* is for. So the
request carries one more system message: every connected server, what it is
for (the `instructions` it sent at `initialize` - MCP's own place for exactly
this), its tools, and six lines on how to work through a request that needs
several of them. And today's date.

**Routing is code.** The qualified name is the route - `deepwiki__ask_question`
goes to `deepwiki`, and nowhere else. Nothing is guessed: a name that does not
route comes back as an error that says which names would have (the same tool
on another server, the server's real tools, the nearest spelling), because a
model told "no such tool" invents another one, and a model told "did you mean
`deepwiki__ask_question`" calls it.

**What a provider can do that a loop cannot**, because it sees the whole turn:

  * **a server that failed is not called again this turn.** A dead server
    costs its timeout once, not once per round; every later call to it comes
    back at once saying it was down, and the model moves on or says so.
  * **an identical call is answered from the first one.** Same server, same
    tool, same arguments: the same answer, marked `cached`, at no cost.
"""

from __future__ import annotations

import difflib
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from typing import Callable

from tools import mcp_client
from tools.base import Tool, ToolResult

#: How much of a server's `instructions` goes into the guide. DeepWiki's and
#: Context7's run to paragraphs; the guide is sent with every request.
PURPOSE_CHARS = 320

GUIDE_HEAD = "--- Tools: which server does what ---\n"

GUIDE_RULES = """\
How to work through a request that needs several of these:
1. Decide which servers the request needs, and in what order: a step that needs another step's result comes after it.
2. Calls that do not depend on each other go in the same round - ask for them together.
3. Pass values from one tool's result to the next exactly as they came back (names, addresses, dates, times, links). Never retype or invent them.
4. If a call fails, read the error: fix the arguments, or use another tool. An identical call returns the identical answer.
5. If the request asks for something to be saved or delivered, that call comes last, built from the results before it.
6. Then answer in the user's language: what you found, what you chose and why, and what was saved."""


@dataclass(frozen=True)
class Route:
    """Where one qualified name goes: a server, its URL, and its own name for the tool."""

    function: str
    server_id: str
    server_label: str
    url: str
    tool: str


@dataclass
class ServerCard:
    """One connected server as the guide describes it."""

    id: str
    label: str
    purpose: str
    functions: list = field(default_factory=list)

    def render(self) -> str:
        head = f"* {self.id} - {self.label}"
        if self.purpose:
            head += f": {self.purpose}"
        return head + "\n  tools: " + ", ".join(self.functions)


#: `(route, arguments) -> ToolResult`. The default opens an MCP session over
#: HTTP; tests pass one that answers from dicts.
Caller = Callable[[Route, dict], ToolResult]


def call_over_http(route: Route, arguments: dict) -> ToolResult:
    """One routed call, over the wire."""
    result = mcp_client.MCPClient(route.url).call_tool(route.tool, arguments)
    print(f"[mcp] {route.server_id}/{route.tool} -> "
          f"{'ok' if result.ok else 'error'} in {result.elapsed_ms} ms", flush=True)
    return result


def squash(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def canonical(arguments: dict) -> str:
    return json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)


class McpProvider:
    """The tools of every connected server, and the one way a call reaches one.

    The registry sees `tools()` (what to offer), `guide()` (what to say about
    them) and `unrouted` (what to say about a name meant for a server that
    does not have it). The agent never learns that there is more than one
    server - choosing between them is done by the model from the guide, and
    getting a call to the right one is done here.
    """

    def __init__(self, cards: list, functions: list, routes: dict, call: Caller = call_over_http,
                 today: datetime | None = None) -> None:
        self.cards = list(cards)
        self.functions = list(functions)
        self.routes = dict(routes)
        self.call = call
        self.today = today
        # Per turn, and shared by the calls of one round, which may run in
        # parallel threads.
        self._lock = threading.Lock()
        self.down: dict[str, str] = {}
        self.answered: dict[tuple, ToolResult] = {}
        self._tools = [
            Tool(
                name=f["function"]["name"],
                description=f["function"]["description"],
                input_schema=f["function"]["parameters"],
                call=partial(self.run, f["function"]["name"]),
                source=self.routes[f["function"]["name"]].server_id,
            )
            for f in self.functions
        ]

    # -- building it ---------------------------------------------------------

    @classmethod
    def from_servers(cls, servers: list, call: Caller = call_over_http, notes: dict | None = None,
                     today: datetime | None = None) -> "McpProvider":
        """Every tool on every server that is switched on and last answered.

        `servers` are `storage.McpServer`s - anything with `id`, `label`,
        `url`, `enabled`, `catalogue` and `tools()`. `notes` is a fallback
        purpose per server id, for a server whose `initialize` said nothing
        about itself.

        Nothing is fetched here - a request that had to visit five servers
        before it could ask the model anything would be a page that hangs for
        seconds because somebody's documentation server is slow. The same set
        the MCP panel counts, which is deliberate: the number on the button is
        the number of tools the next message will carry.
        """
        notes = notes if notes is not None else {k["id"]: k["note"] for k in mcp_client.KNOWN_SERVERS}
        cards, functions, routes = [], [], {}
        for server in servers:
            if not server.enabled or not server.catalogue.get("ok"):
                continue
            card = ServerCard(
                id=server.id,
                label=server.label or server.catalogue.get("server_name") or server.id,
                purpose=squash(server.catalogue.get("instructions") or notes.get(server.id, ""),
                               PURPOSE_CHARS),
            )
            for raw in server.tools():
                tool = mcp_client.McpTool.from_dict({
                    "name": raw.get("name"),
                    "title": raw.get("title"),
                    "description": raw.get("description"),
                    "inputSchema": raw.get("input_schema"),
                })
                if tool is None:
                    continue
                function = tool.to_function(server.id)
                name = function["function"]["name"]
                if name in routes:
                    # Two tools that qualify to the same 64 characters. The
                    # first keeps the name; the second is dropped rather than
                    # silently shadowing it (day 17's rule, unchanged).
                    continue
                routes[name] = Route(name, server.id, card.label, server.url, tool.name)
                functions.append(function)
                card.functions.append(name)
            if card.functions:
                cards.append(card)
        return cls(cards, functions, routes, call, today=today)

    def tools(self) -> list[Tool]:
        return list(self._tools)

    # -- choosing: what the model is told ------------------------------------

    def guide(self) -> str:
        """The system message that says which server is for what.

        Empty with fewer than two servers: with one, there is nothing to
        choose between, and the tool descriptions already say it all.
        """
        if len(self.cards) < 2:
            return ""
        now = self.today or datetime.now().astimezone()
        lines = [
            GUIDE_HEAD
            + f"Today is {now.strftime('%A')}, {now.strftime('%Y-%m-%d')}. You can call tools on "
            f"{len(self.cards)} MCP servers. A tool's name is `<server>__<tool>`: the part before "
            "the two underscores is the server it runs on, and the call goes to that server only.",
            "",
        ]
        lines += [card.render() for card in self.cards]
        lines += ["", GUIDE_RULES]
        return "\n".join(lines)

    # -- routing: what a call does -------------------------------------------

    def run(self, function_name: str, arguments: dict) -> ToolResult:
        route = self.routes.get(function_name)
        if route is None:
            return self.unrouted(function_name) or ToolResult(
                ok=False, is_error=True, text=f"Error: no tool {function_name!r}.",
                transport_error="unknown tool", routing_error="unknown tool")

        with self._lock:
            reason = self.down.get(route.server_id)
            earlier = self.answered.get((function_name, canonical(arguments)))
        if reason is not None:
            return ToolResult(
                ok=False, is_error=True,
                text=(f"Error: {route.server_label} did not answer earlier in this turn "
                      f"({reason}), so it was not called again. Carry on without it, or tell "
                      "the user it is unavailable."),
                server_id=route.server_id, tool=route.tool, routing_error="server down",
            )
        if earlier is not None:
            return replace(earlier, cached=True, elapsed_ms=0, steps=[])

        result = self.call(route, arguments)
        result.server_id, result.tool = route.server_id, route.tool
        with self._lock:
            if result.transport_error:
                # The call never reached the tool: the server is down, or the
                # URL is wrong. Either way, not worth another timeout.
                self.down.setdefault(route.server_id, squash(result.transport_error, 120))
            else:
                # A tool that ran - succeeded or answered with an error - will
                # answer the same arguments the same way within a turn.
                self.answered[(function_name, canonical(arguments))] = result
        return result

    def unrouted(self, function_name: str) -> ToolResult | None:
        """A name meant for an MCP server that goes nowhere, answered with the names that would have.

        None for a name that is not addressed to a server (no `__` in it) and
        is not the bare name of any connected server's tool - that is some
        other provider's business, or the registry's.
        """
        server_part, sep, tool_part = function_name.partition(mcp_client.NAMESPACE_SEPARATOR)
        if not sep:
            server_part, tool_part = "", function_name
        same_tool = sorted(r.function for r in self.routes.values() if r.tool == tool_part)
        if not server_part and not same_tool:
            return None
        servers = {c.id: c for c in self.cards}

        if server_part and server_part not in servers:
            why = f"there is no server {server_part!r} connected"
        elif server_part:
            why = (f"{servers[server_part].label} has no tool {tool_part!r} - its tools are "
                   + ", ".join(servers[server_part].functions))
        else:
            why = f"{function_name!r} has no server in front of it"
        suggestions = same_tool or difflib.get_close_matches(function_name, list(self.routes), n=3,
                                                             cutoff=0.5)
        text = f"Error: {why}."
        if suggestions:
            text += " Did you mean " + " or ".join(suggestions) + "?"
        text += " Tool names are `<server>__<tool>`, exactly as offered."
        return ToolResult(ok=False, is_error=True, text=text, transport_error="unknown tool",
                          server_id=server_part if server_part in servers else "",
                          tool=tool_part, routing_error="unknown tool")
