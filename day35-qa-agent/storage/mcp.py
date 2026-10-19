"""Day 16: the MCP servers somebody connected, and the last thing each of them said.

`data/mcp/servers.json`, or wherever `CHAT_MCP_DIR` points. Every other store
holds something somebody in this app wrote down; this one holds a **cache of
what somebody else's server said**. Delete `data/mcp/` and nothing is lost
that a refresh does not fetch again.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from storage.common import DATA_DIR, StoreError, now_iso

MCP_DIR_ENV = "CHAT_MCP_DIR"
DEFAULT_MCP_DIR = DATA_DIR / "mcp"

#: Day 16. Low for the reason every other cap here is low - the panel is a
#: popover, not a page - and lower than it could be because refreshing them
#: all is a serial walk over other people's servers, and eight slow ones is
#: already a long press of one button.
MAX_MCP_SERVERS = 8


@dataclass
class McpServer:
    """A server somebody connected, plus the catalogue it last handed back.

    Two halves with different owners, and keeping them in one record is the
    point rather than a shortcut. `label`, `url` and `enabled` are **ours** -
    typed in, and true until changed. `catalogue` is **theirs** - whatever
    `tools.mcp_client.Listing.to_dict` produced on the last visit, written down
    unread and handed back unread.

    That is the same contract `Rule.kind` has with `core.invariants`: this file
    does not know what a tool is, what a JSON Schema means or which protocol
    version was negotiated, and it should not learn, because the moment it
    does there are two places that have an opinion about the wire format and
    one of them is a file on disk.

    **Why the catalogue is stored at all**, when a refresh would fetch it
    again: so that the panel has something to draw before the network does,
    and - the part that matters more - so that "when was this last true" is a
    question with an answer. A catalogue that only ever existed in memory
    would make every restart look like every server had just gone down.
    """

    id: str
    label: str = ""
    url: str = ""
    enabled: bool = True
    catalogue: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def tools(self) -> list:
        raw = self.catalogue.get("tools")
        return raw if isinstance(raw, list) else []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "url": self.url,
            "enabled": bool(self.enabled),
            "catalogue": dict(self.catalogue or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "McpServer | None":
        """One server off disk, or None if it is not one.

        Dropped for no id or no url, on `Rule.from_dict`'s reasoning: the id is
        how every route addresses one, and a server with no address is a row
        whose refresh button cannot do anything.
        """
        if not isinstance(data, dict):
            return None
        server_id = str(data.get("id") or "").strip()
        url = str(data.get("url") or "").strip()
        if not server_id or not url:
            return None
        catalogue = data.get("catalogue")
        created = str(data.get("created_at") or "") or now_iso()
        return cls(
            id=server_id,
            label=str(data.get("label") or "").strip() or server_id,
            url=url,
            enabled=bool(data.get("enabled", True)),
            catalogue=catalogue if isinstance(catalogue, dict) else {},
            created_at=created,
            updated_at=str(data.get("updated_at") or "") or created,
        )


def mcp_dir_from_env() -> Path:
    """Where the connected servers are kept: `CHAT_MCP_DIR`, or `data/mcp`."""
    configured = os.getenv(MCP_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_MCP_DIR


class McpStore:
    """The connected servers, on disk: one file, a lock, atomic replace.

    Structurally `InvariantStore` once more, and read far less often - nothing
    here contributes to a request. It is read when the panel is drawn and
    written when a listing comes back, which is the shape of a cache rather
    than of a setting, and is why `record_listing` exists as its own method:
    the one write that is not somebody's decision.
    """

    FILENAME = "servers.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else mcp_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / self.FILENAME
        self._lock = threading.RLock()

    def load(self) -> list:
        """Every server on file. An unreadable file reads as none connected."""
        with self._lock:
            if not self.path.exists():
                return []
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                return []
        raw = data.get("servers") if isinstance(data, dict) else None
        servers = []
        for entry in raw if isinstance(raw, list) else ():
            server = McpServer.from_dict(entry)
            if server is not None:
                servers.append(server)
        return servers[:MAX_MCP_SERVERS]

    def save(self, servers: list) -> list:
        payload = json.dumps(
            {"servers": [s.to_dict() for s in servers], "updated_at": now_iso()},
            ensure_ascii=False,
            indent=2,
        )
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        return servers

    def find(self, server_id: str) -> "McpServer | None":
        for server in self.load():
            if server.id == server_id:
                return server
        return None

    def seed(self, servers: list) -> list:
        """Write these, but only if nothing has ever been written.

        `InvariantStore.seed`'s condition and `InvariantStore.seed`'s reason:
        disconnecting the server that shipped with the app is a thing somebody
        does on purpose, and putting it back on the next restart would be the
        app reconnecting to a third party they had just disconnected from.

        Unlike the seeded rules, this one is seeded **on**. A rule that is on
        changes every answer; a connected server that nobody has called does
        nothing at all until the panel is opened, and arriving to an empty
        panel would hide the one thing the day has to show.
        """
        with self._lock:
            if self.path.exists():
                return self.load()
            return self.save(list(servers))

    def upsert(self, server: McpServer) -> list:
        """File a server, replacing any with the same id and keeping its place."""
        with self._lock:
            servers = self.load()
            for index, existing in enumerate(servers):
                if existing.id == server.id:
                    server.created_at = existing.created_at
                    # A re-added server keeps what it last said, so the panel
                    # does not blank out between the write and the refresh.
                    if not server.catalogue:
                        server.catalogue = existing.catalogue
                    servers[index] = server
                    return self.save(servers)
            if len(servers) >= MAX_MCP_SERVERS:
                raise StoreError(f"No room for another MCP server (limit {MAX_MCP_SERVERS})")
            servers.append(server)
            return self.save(servers)

    def remove(self, server_id: str) -> list:
        """Disconnect one. There is nothing to clean up elsewhere: no session
        is held, so there is no connection to close - which is the one part of
        this day that a pool of live clients would have made complicated."""
        with self._lock:
            servers = [s for s in self.load() if s.id != server_id]
            return self.save(servers)

    def set_enabled(self, server_id: str, enabled: bool) -> list:
        """Switch one off without forgetting it, or its catalogue."""
        with self._lock:
            servers = self.load()
            for server in servers:
                if server.id == server_id:
                    server.enabled = bool(enabled)
                    server.updated_at = now_iso()
                    return self.save(servers)
            return servers

    def record_listing(self, server_id: str, catalogue: dict) -> list:
        """Write down what a server just said - success or failure alike.

        A failed listing is stored rather than discarded, which is the whole
        reason this is not simply `upsert`. Dropping it would leave the last
        good catalogue on screen with a stale timestamp under it, and the
        panel would quietly claim a server is up for as long as it stays down.
        """
        with self._lock:
            servers = self.load()
            for server in servers:
                if server.id == server_id:
                    server.catalogue = dict(catalogue or {})
                    server.updated_at = now_iso()
                    return self.save(servers)
            return servers
