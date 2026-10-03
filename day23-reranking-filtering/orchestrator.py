"""Day 20: several MCP servers, one agent - choosing, routing, and the flow.

Day 17 gave the agent a loop and one server to call. Day 20 gives it five -
four of our own (maps, events, weather, planner) and DeepWiki - and asks for
three things that one server never made anyone think about:

  * **choosing**: with twenty tools on offer, the model has to know which
    server a request belongs to before it can pick a tool on it;
  * **routing**: every call has to reach the server that owns it and nothing
    else, and a call that cannot be routed has to come back as something the
    model can act on;
  * **a long flow**: a request like "a jazz concert this weekend, on an
    evening without rain, dinner before it somewhere fair to all three of us,
    and save the plan" is five or six calls over four servers, where each
    call's arguments come out of the answers before it.

This module is the part of the app that sits between the agent and the
servers and does those three things. It knows about servers and tools; it
does not know about DeepSeek (the agent does) or about HTTP (`mcp_client`
does). It is built fresh for every turn, off the stored catalogues, and
thrown away at the end of it - so everything it remembers (a server that is
down, a call already made) is remembered for exactly one turn.

    server.py ─ Orchestrator.from_servers(catalogues) ─┬─ guide()      -> a system message: which server is for what
                                                       ├─ functions    -> tools[] of the request
                                                       └─ run(name, a) -> the agent's tool_runner: route, call, remember
    after the turn ─ build_flow(tool_calls, question)  -> which server, which round, what fed what

**Choosing is the model's job, and the guide is how it is taught.** A tool's
description says what *it* does; nothing says what a *server* is for, or
that a request touching three of them has an order to it. So the request
carries one more system message: every connected server, what it is for (the
`instructions` it sent at `initialize` - MCP's own place for exactly this,
which the app had been storing and not using since day 16), its tools, and
six lines on how to work through a request that needs several of them. And
today's date, without which "this weekend" is a guess.

**Routing is code.** The qualified name is the route - `weather__get_forecast`
goes to `weather`, and nowhere else. Nothing is guessed: a name that does not
route comes back as an error that says which names would have (the same tool
on another server, the server's real tools, the nearest spelling), because a
model told "no such tool" invents another one, and a model told "did you mean
`weather__get_forecast`" calls it.

**What an orchestrator can do that a loop cannot**, because it sees the whole
turn rather than one call:

  * **a server that failed is not called again this turn.** A dead server
    costs its timeout once, not once per round; every later call to it comes
    back at once saying it was down, and the model moves on or says so.
  * **an identical call is answered from the first one.** A long flow is
    where a model asks for the same forecast twice - once to choose a day,
    once to write the note in the plan. Same server, same tool, same
    arguments: the same answer, marked `cached`, at no cost.
  * **the flow is written down.** Which call went to which server, in which
    round, which calls ran side by side, and - the part that makes "the order
    was right" checkable rather than asserted - where each argument came
    from: the user's message, an earlier call's result, or the model itself.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable

import mcp_client
from mcp_client import ToolResult

#: How much of a server's `instructions` goes into the guide. DeepWiki's and
#: Context7's run to paragraphs; the guide is sent with every request.
PURPOSE_CHARS = 320

#: Provenance only looks for values long enough to mean something: "cafe"
#: is in half the results on the shelf, "Rooftop Coffee" in one of them.
MIN_MATCH_CHARS = 5
#: How many argument values per call are traced. A plan with twenty steps has
#: a hundred leaves; the first thirty say where it came from.
MAX_INPUTS = 30
VALUE_CHARS = 60

WORD = re.compile(r"[a-z0-9Ѐ-ӿ]")
ISO_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2})")
#: A value this short found somewhere in a paragraph is a coincidence, not a
#: handoff: "dinner" is in half the event descriptions on the listing.
PROSE_CHARS, SHORT_IN_PROSE = 120, 12

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


#: `(route, arguments) -> ToolResult`. `server.py` passes one that opens an
#: MCP session; tests pass one that answers from dicts.
Caller = Callable[[Route, dict], ToolResult]


def squash(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def canonical(arguments: dict) -> str:
    return json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)


class Orchestrator:
    """The tools of every connected server, and the one way a call reaches one.

    The agent sees `functions` (what to offer), `guide()` (what to say about
    them) and `run` (its `tool_runner`). It never learns that there is more
    than one server - which is the point: choosing between them is done by the
    model from the guide, and getting a call to the right one is done here.
    """

    def __init__(self, cards: list, functions: list, routes: dict, call: Caller,
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

    # -- building it ---------------------------------------------------------

    @classmethod
    def from_servers(cls, servers: list, call: Caller, notes: dict | None = None,
                     today: datetime | None = None) -> "Orchestrator":
        """Every tool on every server that is switched on and last answered.

        `servers` are `store.McpServer`s - anything with `id`, `label`, `url`,
        `enabled`, `catalogue` and `tools()`. `notes` is a fallback purpose per
        server id, for a server whose `initialize` said nothing about itself.
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
                tool = mcp_client.Tool.from_dict({
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

    # -- routing: the agent's tool_runner ------------------------------------

    def run(self, function_name: str, arguments: dict) -> ToolResult:
        route = self.routes.get(function_name)
        if route is None:
            return self.unrouted(function_name)

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

    def unrouted(self, function_name: str) -> ToolResult:
        """A name that goes nowhere, answered with the names that would have."""
        server_part, sep, tool_part = function_name.partition(mcp_client.NAMESPACE_SEPARATOR)
        if not sep:
            server_part, tool_part = "", function_name
        same_tool = sorted(r.function for r in self.routes.values() if r.tool == tool_part)
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

    def labels(self) -> dict:
        return {card.id: card.label for card in self.cards}


# --------------------------------------------------------------------------
# The flow: which server, which round, what fed what
# --------------------------------------------------------------------------


def norm(text) -> str:
    return " ".join(str(text).lower().split())


def contains(haystack: str, needle: str) -> bool:
    """`needle` inside `haystack` on word edges: "19:30" is in "at 19:30," but
    "cafe" is not in "cafeteria"."""
    if len(needle) < MIN_MATCH_CHARS:
        return False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before = haystack[start - 1] if start else " "
        after = haystack[end] if end < len(haystack) else " "
        if not (WORD.match(before) and WORD.match(needle[0])) and \
           not (WORD.match(after) and WORD.match(needle[-1])):
            return True
        start = haystack.find(needle, start + 1)
    return False


def leaves(value, path: str = "") -> list:
    """Every string and number inside a JSON value, with where it was."""
    if isinstance(value, dict):
        out = []
        for key, inner in value.items():
            out += leaves(inner, f"{path}.{key}" if path else str(key))
        return out
    if isinstance(value, list):
        out = []
        for index, inner in enumerate(value):
            out += leaves(inner, f"{path}[{index}]")
        return out
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float, str)):
        return [(path, value)]
    return []


def traceable(value) -> str | None:
    """The form of an argument value worth looking for, or None.

    Small whole numbers are left out - `days: 3` is in every result that has
    a count in it, and it came from the model's reading of "this weekend"
    whatever else it matches.
    """
    if isinstance(value, float) and not value.is_integer():
        return repr(value)
    if isinstance(value, (int, float)):
        return str(int(value)) if abs(value) >= 1000 else None
    text = norm(value)
    return text if len(text) >= MIN_MATCH_CHARS else None


def result_strings(call: dict) -> list:
    """What an earlier call returned, as strings to search in.

    ISO timestamps are split into their day and their time as well, because
    that is how they travel: the listing says `2026-10-03T19:30:00+02:00` and
    the plan says `day: 2026-10-03`, `time: 19:30`.
    """
    source = call.get("structured")
    values = [v for _, v in leaves(source)] if isinstance(source, (dict, list)) else [call.get("text") or ""]
    out = []
    for value in values:
        if isinstance(value, float):
            out.append(repr(value))
            continue
        text = norm(value)
        if not text:
            continue
        out.append(text)
        for day, clock in ISO_STAMP.findall(text):
            out += [day, clock]
    return out


def matches(value: str, strings: list, prose_guard: bool = True) -> bool:
    for s in strings:
        if value == s:
            return True
        if prose_guard and len(s) > PROSE_CHARS and len(value) < SHORT_IN_PROSE:
            continue
        if contains(s, value) or contains(value, s):
            return True
    return False


def said_by_user(value: str, user: str) -> bool:
    """Whether the user typed this - or the part of it that is not a label.

    The model writes `Anna: Kloof St 10` where the user wrote "Anna is on
    Kloof St 10", so a value is also looked for in pieces, split where a
    label or a list would be. The user's message is prose, and meant to be:
    the short-words-in-prose guard is for other tools' descriptions, not this.
    """
    parts = [value] + [p.strip() for p in re.split(r"[:;,|]", value) if p.strip() != value]
    return any(matches(p, [user], prose_guard=False) for p in parts if len(p) >= MIN_MATCH_CHARS)


def build_flow(calls: list, question: str = "", labels: dict | None = None) -> dict:
    """The turn's tool calls as a flow: steps, rounds, servers, and handoffs.

    `calls` are the agent's `tool_calls` records, in order, each carrying the
    `round` it was asked for in. An argument is traced to a step only if that
    step ran in an *earlier* round - calls asked for together cannot have fed
    each other, whatever their results happen to share.
    """
    labels = labels or {}
    user = norm(question)
    produced = []
    steps = []
    for n, call in enumerate(calls, start=1):
        server = call.get("server_id") or (call.get("name") or "").partition("__")[0]
        round_no = int(call.get("round") or 1)
        inputs, fed_by = [], set()
        for path, value in leaves(call.get("arguments") or {})[:MAX_INPUTS]:
            wanted = traceable(value)
            sources = []
            if wanted is not None:
                sources = [m for m, strings, r in produced if r < round_no and matches(wanted, strings)]
            from_user = wanted is not None and isinstance(value, str) and said_by_user(wanted, user)
            fed_by.update(sources)
            inputs.append({
                "arg": path,
                "value": squash(value, VALUE_CHARS),
                "from": sources,
                "origin": "step" if sources else ("user" if from_user else "model"),
                "user": from_user,
            })
        steps.append({
            "n": n,
            "round": round_no,
            "server": server,
            "label": labels.get(server, server),
            "tool": call.get("tool") or (call.get("name") or "").partition("__")[2],
            "name": call.get("name") or "",
            "ok": bool(call.get("ok")),
            "cached": bool(call.get("cached")),
            "elapsed_ms": int(call.get("elapsed_ms") or 0),
            "error": ("" if call.get("ok") else squash(
                call.get("routing_error") or call.get("error") or call.get("text") or "failed", 160)),
            "routing_error": call.get("routing_error") or "",
            "inputs": inputs,
            "fed_by": sorted(fed_by),
        })
        if call.get("ok"):
            produced.append((n, result_strings(call), round_no))

    by_n = {s["n"]: s for s in steps}
    handoffs = []
    for step in steps:
        for item in step["inputs"]:
            for source in item["from"]:
                handoffs.append({"from": source, "to": step["n"], "arg": item["arg"],
                                 "across": by_n[source]["server"] != step["server"]})

    order = []
    for step in steps:
        if step["server"] and step["server"] not in order:
            order.append(step["server"])
    rounds = sorted({s["round"] for s in steps})
    parallel = [r for r in rounds if sum(1 for s in steps if s["round"] == r) > 1]
    across = sum(1 for h in handoffs if h["across"])
    return {
        "steps": steps,
        "calls": len(steps),
        "rounds": len(rounds),
        "parallel_rounds": parallel,
        "servers": order,
        "handoffs": handoffs,
        "cross_server": across,
        "failed": sum(1 for s in steps if not s["ok"]),
        "cached": sum(1 for s in steps if s["cached"]),
        "summary": (
            f"{len(steps)} call{'s' if len(steps) != 1 else ''} in {len(rounds)} "
            f"round{'s' if len(rounds) != 1 else ''} across {len(order)} "
            f"server{'s' if len(order) != 1 else ''}: " + " → ".join(order)
        ) if steps else "no tool was called",
    }
