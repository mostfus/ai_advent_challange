"""MCP servers: connect one, ask what it offers, switch it off, forget it.

Days 16 and 17. The panel's operations over `rt.stores.mcp_servers` - a list
of other people's servers and a cache of what each last said when asked what
it can do. Only `fetch_catalogue` touches the network; drawing the panel never
does. `provider()` turns the stored catalogues into the tool provider a turn
offers the model (`tools.mcp_provider`).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from core.errors import Invalid, NotFound
from storage import MAX_MCP_SERVERS, McpServer, StoreError
from tools import mcp_client
from tools.mcp_provider import McpProvider

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


class McpService:
    """The connected servers, over `rt.stores.mcp_servers`."""

    def __init__(self, rt: "AgentRuntime") -> None:
        self.rt = rt

    @property
    def store(self):
        return self.rt.stores.mcp_servers

    def seed(self) -> None:
        """DeepWiki, connected and switched on - written once, on the first start.

        Seeded **on**, unlike the personality and invariant seeds: a server
        that is on offers its tools with every request, but a panel that opened
        onto nothing would hide the one thing it is for. The switch is one
        click away in the panel that shows exactly what it offers.
        """
        self.store.seed(
            [McpServer(id=known["id"], label=known["label"], url=known["url"])
             for known in mcp_client.KNOWN_SERVERS[:1]]
        )

    def provider(self) -> McpProvider:
        """Every tool on every server that is switched on and last answered.

        Built fresh per turn, off the stored catalogues, so switching a server
        off in the panel takes effect on the next message and not on the next
        restart. Nothing is fetched here.
        """
        return McpProvider.from_servers(self.store.load())

    @staticmethod
    def slug(label: str, url: str, taken: list) -> str:
        """A short id for a server, unique among the ones already connected.

        It is not cosmetic. This string is the namespace half of every tool name
        the catalogue reports - `deepwiki__ask_question` - so it is built out of
        the same character class `mcp_client.qualify` will allow and kept short,
        because whatever it costs comes out of the 64 characters a function name
        gets.
        """
        base = re.sub(r"[^a-z0-9]+", "-", (label or url).lower()).strip("-")[:24]
        base = base or "server"
        if base not in taken:
            return base
        for suffix in range(2, 100):
            candidate = f"{base}-{suffix}"
            if candidate not in taken:
                return candidate
        raise Invalid("Could not name that server")

    @staticmethod
    def clean_url(url: str) -> str:
        """The one check on the way in, and it is about this app rather than MCP.

        Pressing refresh makes *the server* fetch a URL a browser typed, which is
        a request originating inside whatever network this is running on. The
        scheme check below is not a defence against that - a private address over
        https passes it - and it is not pretending to be one. It is here so the
        failure for a typo is a sentence in the panel instead of an httpx
        traceback, and the real answer is that this is a local single-user app and
        the URL box is as trusted as the terminal it was launched from. An app
        with more than one user would need an allow-list here.
        """
        url = (url or "").strip()
        if not url:
            raise Invalid("A server needs a URL")
        if not url.startswith(("http://", "https://")):
            raise Invalid("The URL must start with http:// or https://")
        return url

    def report(self, servers: list | None = None) -> dict:
        """Every connected server, what it last said, and the totals.

        The whole set on every reply, as the invariants endpoints do and for a
        version of the same reason: connecting a server renames nothing, but the
        counts in the header and the chip on the button are sums over all of them,
        and a page that patched one row would have to recompute those itself.
        """
        servers = self.store.load() if servers is None else servers
        rows = [s.to_dict() for s in servers]
        live = [s for s in servers if s.enabled and s.catalogue.get("ok")]
        return {
            "servers": rows,
            # What an agent would actually be offered: tools from servers that
            # are switched on and answered. A tool on a server switched off is
            # still drawn in the panel and is deliberately not counted here.
            "tools": sum(len(s.tools()) for s in live),
            "connected": len(live),
            "limit": MAX_MCP_SERVERS,
            "room": max(0, MAX_MCP_SERVERS - len(servers)),
        }

    def fetch_catalogue(self, server: McpServer) -> dict:
        """Go and ask one server what it can do, and write down the answer.

        The only place in this file that opens an MCP session, and it does not
        keep it - `MCPClient.list_tools` connects, reads and hangs up. A failure
        comes back as a stored listing rather than an exception, so a server that
        is down is a red line on a row and not an error dialog over four rows that
        are fine.
        """
        listing = mcp_client.MCPClient(server.url).list_tools()
        self.store.record_listing(server.id, listing.to_dict(server.id))
        return listing.to_dict(server.id)

    def add(self, label: str = "", url: str = "") -> dict:
        """Connect a server - and go and ask it, once, immediately.

        The fetch is here rather than behind a second click because a row that
        appears saying "never checked" answers none of the questions somebody had
        when they pasted a URL in. The one that matters is whether it works at
        all, and this is the moment to find out.
        """
        url = self.clean_url(url)
        existing = self.store.load()
        if any(s.url == url for s in existing):
            raise Invalid("That server is already connected")
        name = (label or "").strip() or url
        server = McpServer(
            id=self.slug(label, url, [s.id for s in existing]),
            label=name[:60],
            url=url,
        )
        try:
            self.store.upsert(server)
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        self.fetch_catalogue(server)
        return self.report()

    def refresh_all(self) -> dict:
        """Day 20: ask every server switched on what it can do, all at once.

        A request across several servers needs every catalogue current, and one
        press per server is where somebody forgets one. The visits run side
        by side - five servers at a second each would otherwise be five seconds -
        and each is written down on its own, so one that is down is a red row and
        not a failed refresh.
        """
        servers = [s for s in self.store.load() if s.enabled]
        if servers:
            with ThreadPoolExecutor(max_workers=min(8, len(servers))) as pool:
                list(pool.map(self.fetch_catalogue, servers))
        return self.report()

    def refresh(self, server_id: str) -> dict:
        """Ask one server again. The only button here that touches the network."""
        server = self.store.find(server_id)
        if server is None:
            raise NotFound("No such server")
        self.fetch_catalogue(server)
        return self.report()

    def toggle(self, server_id: str, enabled: bool) -> dict:
        """Switch a server off without forgetting it.

        Distinct from deleting it in the one way that matters here: the catalogue
        survives. A server switched off keeps its tools on screen, greyed, and
        stops contributing them to the count - which is the difference between
        "not offering these" and "never heard of these".
        """
        return self.report(self.store.set_enabled(server_id, enabled))

    def delete(self, server_id: str) -> dict:
        """Disconnect. Nothing is held open, so nothing has to be closed."""
        return self.report(self.store.remove(server_id))
