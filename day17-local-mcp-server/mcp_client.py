"""Days 16 and 17: what is on the other end of an MCP server, and calling it.

Day 6 threw away the `openai` SDK and wrote the HTTP by hand, because talking
to an LLM turns out to be a POST with a JSON body and the SDK was the only
thing making that hard to see. This file makes the same bet a second time,
against the official `mcp` package, and for the same reason plus one more.

The reason: **MCP is JSON-RPC 2.0 over an HTTP POST.** Not a new transport,
not a socket protocol, not something that needs a daemon. Three requests get
you from nothing to a list of tools, and they are all in `list_tools` below,
spelled out. An SDK would hide all three behind `async with` and the day
would be about installing a package.

The one more: the SDK's client is **async-only**, and `agent.py` is not. A
`ClientSession` in this app would mean either turning the agent async for a
reason that has nothing to do with agents, or running an event loop in a
thread and bridging to it. The sixty lines below cost less than either.

    server.py  ->  MCPClient(url).list_tools()  ->  POST initialize
                                                ->  POST notifications/initialized
                                                ->  POST tools/list   (paged)
                                                ->  DELETE session    (if any)

    agent.py   ->  MCPClient(url).call_tool(name, args)
                                                ->  the same handshake
                                                ->  POST tools/call
                                                ->  DELETE session    (if any)

Day 17 adds the second of those, and it is the claim day 16 made coming due:
`call_tool` is `list_tools` with a different third request. The handshake
above it did not change at all, which is why it now lives in one method both
of them say.

**What this file deliberately does not do:**

  * A held-open connection. Both halves open a session, do one thing and hang
    up - enough for a catalogue and for a tool that answers in a second, and
    not enough the moment the server wants to talk back.
  * Progress notifications. A long `tools/call` may report progress over a
    held-open stream; `parse_body` takes the first JSON-RPC message and would
    read the result of a slow tool the same way it reads a fast one.
  * OAuth. Every remote server in `KNOWN_SERVERS` is open, and that is not an
    accident: OAuth 2.1 with dynamic registration and PKCE is where writing
    your own client stops being cheaper than importing one.
  * `resources/list` and `prompts/list`. Three of the five servers below
    advertise both. Same protocol, same three lines, different method name.

**What it does do that a toy would skip**, because each one is a real server
behaving differently from the others and was found by pointing this file at
them rather than by reading the specification:

  * A response to a POST is `application/json` *or* `text/event-stream`, at
    the server's discretion - AWS answers with the first and the other four
    with the second. A client that only calls `.json()` works against 20% of
    this list and fails with a decoding error rather than an HTTP one.
  * `Mcp-Session-Id` comes back from `initialize` on some servers (AWS,
    Microsoft Learn, CoinGecko) and not others (DeepWiki, Context7). When it
    comes back it is required on everything after, and a `404` later means
    the session expired rather than the URL being wrong.
  * The protocol version is *negotiated*, not declared. We ask for
    2025-06-18; AWS and GitMCP answer 2025-03-26. What goes in the header from
    then on is the server's answer, not ours.
  * `tools/list` is paged. Nobody in the list below returns a `nextCursor`
    today, which is exactly why the loop has to be written now rather than
    the first time somebody connects a server with forty tools.

This file knows about JSON-RPC and about one transport. It does not know what
a tool is for, which server a person added, or that an LLM exists - `store.py`
keeps the catalogue and `server.py` decides when to go and get it.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

# The version of the protocol this client speaks. Sent in `initialize`; what
# comes back may be older, and that is what gets used from then on.
PROTOCOL_VERSION = "2025-06-18"

CLIENT_NAME = "day17-agent"
CLIENT_VERSION = "0.1.0"

#: Both, always. The server picks which one it replies with and is allowed to
#: pick differently per request, so a client that offers only one is a client
#: that works until it meets the other kind.
ACCEPT = "application/json, text/event-stream"

DEFAULT_TIMEOUT_SECONDS = 30.0

#: A catalogue should arrive in one page or a handful. The cap is here so that
#: a server returning a cursor that never advances is a failed listing rather
#: than a spinning request.
MAX_PAGES = 20

#: What the chat-completions API will accept as a function name:
#: `^[a-zA-Z0-9_-]{1,64}$`. MCP itself places no such limit on a tool name, so
#: the qualified name below is built to satisfy the *stricter* of the two.
MAX_FUNCTION_NAME = 64
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")

#: The separator between the server and the tool in a qualified name. Two
#: underscores rather than a dot or a slash because those are not in the
#: character class above, and rather than one because single underscores are
#: common *inside* tool names and a name you cannot split back apart is a name
#: you cannot route a call with.
NAMESPACE_SEPARATOR = "__"

#: Servers that answered this client, with no credentials, on the day this was
#: written - offered in the add form so that trying the day out is not a
#: search. They are suggestions, not a whitelist: any Streamable HTTP URL goes
#: in the same box.
#:
#: `note` is why each one is worth pointing at, and for two of them the answer
#: is about the protocol rather than the tools.
KNOWN_SERVERS = (
    {
        # Day 17, and the only one on this list that is ours: `maps_mcp_server.py`
        # in this folder, started separately. Listed first because it is the one
        # whose tools this app is actually built to call.
        "id": "google-maps",
        "label": "Google Maps (local)",
        "url": "http://127.0.0.1:8787/mcp",
        "note": "This folder's own server: distance from A to B, and restaurants "
                "around a point. Start it with `uv run maps_mcp_server.py`.",
    },
    {
        "id": "deepwiki",
        "label": "DeepWiki",
        "url": "https://mcp.deepwiki.com/mcp",
        "note": "Questions about any public GitHub repository's wiki. "
                "No session, replies over SSE.",
    },
    {
        "id": "aws-knowledge",
        "label": "AWS Knowledge",
        "url": "https://knowledge-mcp.global.api.aws/mcp",
        "note": "AWS documentation. The one server here that replies with "
                "plain application/json and negotiates down to protocol "
                "2025-03-26 - the other branch of both forks in this file.",
    },
    {
        "id": "microsoft-learn",
        "label": "Microsoft Learn",
        "url": "https://learn.microsoft.com/api/mcp",
        "note": "Search over learn.microsoft.com. Issues a session id, so "
                "every request after initialize carries one.",
    },
    {
        "id": "context7",
        "label": "Context7",
        "url": "https://mcp.context7.com/mcp",
        "note": "Up-to-date documentation for libraries and frameworks.",
    },
    {
        "id": "gitmcp",
        "label": "GitMCP",
        "url": "https://gitmcp.io/docs",
        "note": "Documentation and code search across GitHub repositories.",
    },
)


class MCPError(RuntimeError):
    """Anything that stopped a listing: transport, HTTP, JSON-RPC or shape.

    One class rather than four, because every caller does the same thing with
    all of them - writes the message next to the server in the panel. What
    kind of failure it was is in the trace, which is the thing you actually
    read when one happens.
    """


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def qualify(server_id: str, tool_name: str) -> str:
    """`deepwiki` + `ask_question` -> `deepwiki__ask_question`.

    Two servers exporting `search` is not a corner case, it is the first thing
    that happens when you connect a second one - three of the five in
    `KNOWN_SERVERS` export something called some flavour of search. So a tool's
    name is not its identity; the pair is. This is the one place that decides
    how the pair is spelled, and `server.py` hands the result to the panel so
    that what the page shows is the name a model would be given rather than a
    prettier one.

    Truncation at 64 can in principle collide. It is left alone deliberately:
    silently renaming a tool to `..._a3f` would make the panel show a name the
    model was never sent, which is the one property this string has to have.
    """
    raw = f"{server_id}{NAMESPACE_SEPARATOR}{tool_name}"
    return UNSAFE_NAME.sub("_", raw)[:MAX_FUNCTION_NAME]


@dataclass
class Tool:
    """One entry out of `tools/list`, kept as the server sent it.

    `input_schema` is JSON Schema and is stored whole and unread. That is the
    part of MCP that turns out to need no adapter at all: it is already the
    shape `tools[].function.parameters` wants, which is why `to_function`
    below is four lines and not a translation layer.
    """

    name: str
    title: str = ""
    description: str = ""
    input_schema: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Tool | None":
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or "").strip()
        if not name:
            return None
        schema = data.get("inputSchema")
        return cls(
            name=name,
            # `title` is the 2025-06-18 addition for "what to show a person",
            # as against `name`, which is what to send. Older servers have
            # neither and the panel falls back to the name.
            title=str(data.get("title") or "").strip(),
            description=str(data.get("description") or "").strip(),
            input_schema=schema if isinstance(schema, dict) else {},
        )

    def arguments(self) -> list:
        """The top-level parameter names, required ones first and marked.

        Pulled out of the schema here rather than in the page because the page
        should not have to know what JSON Schema is to draw a tool row - and
        because "what would I have to pass this" is the question a catalogue
        is read to answer.
        """
        props = self.input_schema.get("properties")
        if not isinstance(props, dict):
            return []
        required = self.input_schema.get("required")
        required = set(required) if isinstance(required, list) else set()
        rows = [
            {
                "name": str(key),
                "type": str((spec or {}).get("type") or "") if isinstance(spec, dict) else "",
                "required": str(key) in required,
            }
            for key, spec in props.items()
        ]
        rows.sort(key=lambda row: (not row["required"], row["name"]))
        return rows

    def to_function(self, server_id: str) -> dict:
        """The same tool in the shape a chat-completions request wants.

        Day 16 computed this and sent nothing, because it was the whole
        argument in one object: a catalogue fetched over a protocol nobody at
        DeepSeek has heard of drops into `tools[]` with a rename and no
        translation. Day 17 sends it - `server.py` collects one of these per
        tool per connected server and hands the list to the agent.
        """
        return {
            "type": "function",
            "function": {
                "name": qualify(server_id, self.name),
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }

    def to_dict(self, server_id: str = "") -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "input_schema": self.input_schema,
            "arguments": self.arguments(),
            "function_name": qualify(server_id, self.name) if server_id else self.name,
        }


@dataclass
class Listing:
    """What one visit to a server produced - including a failed one.

    A failure is a `Listing` with `ok` false rather than an exception that
    escapes, because a server that is down is a row in the panel and not an
    error page: the other three are still listed, and this one still has to
    say when it was last reached and what it said when it was not.

    `steps` is the point of the class. Three requests went out; this is what
    each was and what came back, in order, and it is put on screen. It is the
    same instinct as the debug panel's request/response JSON - the protocol is
    the thing being taught, so the protocol is visible rather than described.
    """

    ok: bool = False
    tools: list = field(default_factory=list)
    server_name: str = ""
    server_version: str = ""
    instructions: str = ""
    protocol: str = ""
    capabilities: list = field(default_factory=list)
    session: bool = False
    pages: int = 0
    elapsed_ms: int = 0
    checked_at: str = field(default_factory=now_iso)
    error: str = ""
    steps: list = field(default_factory=list)

    def to_dict(self, server_id: str = "") -> dict:
        return {
            "ok": self.ok,
            "tools": [t.to_dict(server_id) for t in self.tools],
            "server_name": self.server_name,
            "server_version": self.server_version,
            "instructions": self.instructions,
            "protocol": self.protocol,
            "capabilities": list(self.capabilities),
            "session": self.session,
            "pages": self.pages,
            "elapsed_ms": self.elapsed_ms,
            "checked_at": self.checked_at,
            "error": self.error,
            "steps": list(self.steps),
        }


@dataclass
class ToolResult:
    """What one `tools/call` came back with - including a call that never ran.

    `text` is the text blocks joined, `structured` the `structuredContent`
    object if the tool sent one. `for_model` is what goes into the `tool`
    message of the next chat-completions request: the structured object when
    there is one, because it is exact and the text is usually a rendering of
    it, and the text otherwise.
    """

    ok: bool = False
    is_error: bool = False
    text: str = ""
    structured: dict | None = None
    transport_error: str = ""
    elapsed_ms: int = 0
    steps: list = field(default_factory=list)

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
        }


def parse_body(response: httpx.Response) -> dict:
    """One JSON-RPC message out of a reply that is JSON or an SSE stream.

    The fork this file exists to make visible. A POST to a Streamable HTTP
    endpoint may be answered either way and the specification lets the server
    choose per request, so `response.json()` is not a client - it is a client
    that happens to have met the right server first.

    The SSE branch is deliberately the small one: read frames, take the first
    `data:` payload that parses. That is correct for a request/response pair
    and *not* correct for a stream a server holds open to report progress on a
    long call - which is `tools/call`'s problem, and is where this would grow
    into `httpx.stream` and a real frame reader.
    """
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
    text = response.text
    if content_type == "text/event-stream":
        for block in re.split(r"\r?\n\r?\n", text):
            data = "\n".join(
                line[5:].lstrip()
                for line in block.splitlines()
                if line.startswith("data:")
            )
            if not data:
                continue
            try:
                message = json.loads(data)
            except ValueError:
                continue
            if isinstance(message, dict):
                return message
        raise MCPError(f"event stream carried no JSON-RPC message ({excerpt(text)})")
    try:
        message = json.loads(text)
    except ValueError as err:
        raise MCPError(f"reply was not JSON ({err}): {excerpt(text)}") from err
    if not isinstance(message, dict):
        raise MCPError("reply was JSON but not a JSON-RPC object")
    return message


def excerpt(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MCPClient:
    """One server, over Streamable HTTP.

    Instantiated per listing rather than kept around, which is the decision
    worth arguing with and so worth stating: **there is no connection held
    open here.** A session is opened, the catalogue is read and the session is
    dropped, all inside `list_tools`.

    That is right for exactly this day and wrong the moment anything else
    happens. An MCP session is stateful by design, and holding one is what
    buys you the things a catalogue does not need: `notifications/tools/
    list_changed` when the server's tools change under you, progress on a long
    call, and the server asking the *client* for something - sampling, roots,
    elicitation. None of those exist in a read of `tools/list`, and a pool of
    live sessions that exists to serve a button nobody has pressed is a
    reconnect policy, a keepalive and a lifetime bug in exchange for nothing.

    So: cheap now, and the place to change when the day after this one calls
    a tool.
    """

    def __init__(
        self,
        url: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        headers: dict | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.extra_headers = dict(headers or {})
        # Set by `initialize`, sent on everything after it. Both are part of
        # the same rule: after the handshake, a request that does not carry
        # what the handshake established is a request to a server that has
        # forgotten who is asking.
        self.session_id: str | None = None
        self.protocol = PROTOCOL_VERSION
        self.steps: list = []

    # -- the wire ---------------------------------------------------------

    def headers(self) -> dict:
        head = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol,
        }
        if self.session_id:
            head["Mcp-Session-Id"] = self.session_id
        head.update(self.extra_headers)
        return head

    def post(self, client: httpx.Client, label: str, payload: dict) -> httpx.Response:
        """One POST, with the round trip written into the trace either way."""
        started = time.perf_counter()
        try:
            response = client.post(self.url, headers=self.headers(), json=payload)
        except httpx.HTTPError as err:
            self.steps.append({
                "label": label,
                "status": 0,
                "content_type": "",
                "ms": int((time.perf_counter() - started) * 1000),
                "note": f"{type(err).__name__}: {err}",
            })
            raise MCPError(f"{label}: could not reach the server ({err})") from err
        # Where the session id arrives, and the only place it ever does: a
        # response header on whichever request the server decided to open a
        # session on - in practice `initialize`. Picked up here rather than in
        # `list_tools` so that it is taken from the reply that carried it
        # whatever that reply was, and never overwritten by a later blank.
        issued = response.headers.get("mcp-session-id")
        if issued and not self.session_id:
            self.session_id = issued
        self.steps.append({
            "label": label,
            "status": response.status_code,
            "content_type": (response.headers.get("content-type") or "").split(";")[0].strip(),
            "ms": int((time.perf_counter() - started) * 1000),
            "note": f"session {issued[:8]}\u2026" if issued else "",
        })
        return response

    def call(self, client: httpx.Client, label: str, payload: dict) -> dict:
        """A POST that expects a result: HTTP checked, JSON-RPC error raised."""
        response = self.post(client, label, payload)
        if response.status_code == 404 and self.session_id:
            # The one status worth naming. A session that expired and a URL
            # that is wrong both come back 404, and only the first one is
            # fixed by pressing the button again.
            raise MCPError(f"{label}: the session expired (HTTP 404) - reconnect and try again")
        if response.status_code >= 400:
            raise MCPError(f"{label}: HTTP {response.status_code} {excerpt(response.text)}")
        message = parse_body(response)
        if "error" in message:
            err = message.get("error") or {}
            raise MCPError(
                f"{label}: the server refused it "
                f"({err.get('code', '?')}: {err.get('message', 'no message')})"
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise MCPError(f"{label}: reply carried no result object")
        return result

    # -- the handshake, shared by both halves ------------------------------

    def handshake(self, client: httpx.Client) -> dict:
        """`initialize` + `notifications/initialized`; the `initialize` result.

        Said once per session by both `list_tools` and `call_tool`: say who you
        are and what you speak, say you are ready. The parts that are not
        obvious from that sentence are commented, and were found by running
        this against real servers rather than by reading about them.
        """
        result = self.call(client, "initialize", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                # Empty, and honestly so. This declares what the *client* can
                # be asked to do - sampling, roots, elicitation - and this
                # client can be asked to do none of them. Claiming otherwise
                # would invite a request nothing here answers.
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        })

        # The negotiated version, not the requested one: two of the five
        # known servers answer with an older one, and what goes in the header
        # from here on is their answer.
        self.protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)

        if "tools" not in (result.get("capabilities") or {}):
            # Worth its own message rather than an empty list: a server with
            # prompts and resources and no tools is working correctly and has
            # nothing this app wants.
            raise MCPError("this server does not offer tools")

        self.post(client, "notifications/initialized", {
            # No `id`. That is what makes it a notification rather than a
            # request, and why there is no reply to read: a well-behaved
            # server answers 202 with an empty body.
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        return result

    # -- the three requests -----------------------------------------------

    def list_tools(self) -> Listing:
        """Connect, ask what there is, disconnect. The whole day.

        Read top to bottom it is the handshake the specification describes and
        nothing else: say who you are and what you speak, say you are ready,
        ask. The parts that are not obvious from that sentence are the parts
        commented, and all three were found by running this against real
        servers rather than by reading about them.
        """
        started = time.perf_counter()
        self.steps = []
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                result = self.handshake(client)
                info = result.get("serverInfo") or {}
                capabilities = result.get("capabilities") or {}

                tools: list = []
                cursor = None
                pages = 0
                while pages < MAX_PAGES:
                    params = {"cursor": cursor} if cursor else {}
                    page = self.call(client, "tools/list", {
                        "jsonrpc": "2.0",
                        "id": 2 + pages,
                        "method": "tools/list",
                        "params": params,
                    })
                    pages += 1
                    for entry in page.get("tools") or ():
                        tool = Tool.from_dict(entry)
                        if tool is not None:
                            tools.append(tool)
                    cursor = page.get("nextCursor")
                    if not cursor:
                        break

                self.close(client)

                return Listing(
                    ok=True,
                    tools=tools,
                    server_name=str(info.get("name") or ""),
                    server_version=str(info.get("version") or ""),
                    # Some servers ship a paragraph on how their tools are
                    # meant to be used. It belongs in the catalogue for the
                    # same reason a tool description does.
                    instructions=str(result.get("instructions") or "").strip(),
                    protocol=self.protocol,
                    capabilities=sorted(str(k) for k in capabilities),
                    session=bool(self.session_id),
                    pages=pages,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    steps=list(self.steps),
                )
        except MCPError as err:
            return Listing(
                ok=False,
                error=str(err),
                protocol=self.protocol,
                session=bool(self.session_id),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                steps=list(self.steps),
            )

    # -- day 17: actually calling one ---------------------------------------

    def call_tool(self, name: str, arguments: dict | None = None) -> "ToolResult":
        """Connect, call one tool, disconnect.

        The same shape as `list_tools` with a different third request - which
        is the claim day 16 made and this method cashes. A session per call is
        still the right trade for this app: a turn calls a tool or two, a few
        milliseconds of handshake against a local server is nothing, and a
        pool of held-open sessions would be a lifetime bug in exchange for it.

        Like `list_tools`, a failure is a value rather than an exception, and
        there are two kinds of it that must not be confused:

          * the *call* failed - unreachable, HTTP error, JSON-RPC error. The
            tool never ran; `transport_error` says why.
          * the *tool* failed - it ran and answered `isError: true` ("no route
            between these two points"). That is an answer, and a model should
            read it exactly as it reads a success.
        """
        started = time.perf_counter()
        self.steps = []
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                self.handshake(client)
                result = self.call(client, "tools/call", {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": dict(arguments or {})},
                })
                self.close(client)
        except MCPError as err:
            return ToolResult(
                ok=False,
                is_error=True,
                text=f"Error: {err}",
                transport_error=str(err),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                steps=list(self.steps),
            )

        content = result.get("content") if isinstance(result.get("content"), list) else []
        structured = result.get("structuredContent")
        is_error = bool(result.get("isError"))
        return ToolResult(
            ok=not is_error,
            is_error=is_error,
            text="\n".join(
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip(),
            structured=structured if isinstance(structured, dict) else None,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            steps=list(self.steps),
        )

    def close(self, client: httpx.Client) -> None:
        """Hang up, if there was anything to hang up.

        A DELETE on the same URL ends the session. It is optional in both
        directions - the client may skip it, the server may refuse it - and it
        is sent anyway, because the alternative is leaving a session open on
        somebody else's server every time this panel is refreshed.
        """
        if not self.session_id:
            return
        try:
            response = client.request("DELETE", self.url, headers=self.headers())
            note = "session ended" if response.status_code < 400 else "server kept the session"
            self.steps.append({
                "label": "DELETE session",
                "status": response.status_code,
                "content_type": "",
                "ms": 0,
                "note": note,
            })
        except httpx.HTTPError:
            # Nothing was listed on the strength of this, so nothing is lost
            # by it failing.
            pass
