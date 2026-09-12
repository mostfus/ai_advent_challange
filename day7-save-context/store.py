"""Where a conversation lives when the process is not running.

Day 7's whole point. Day 6 left a seam: `Agent` already replayed
`self.history` into every request, but the server built a fresh agent per
call, so the list was always empty and every question was answered from
scratch. Nothing remembered anything - not across a page reload, and
certainly not across a restart.

This module is the other side of that seam: a place to put a transcript so
it is still there next time. It knows nothing about agents, HTTP or LLMs.
It stores dictionaries and hands them back.

    server.py  ->  ConversationStore.load(id)   ->  Agent(history=...)
               ->  ConversationStore.append_turn(id, ...)

**Why on the server and not in the browser.** The transcript has two jobs:
it is drawn on screen, and it is replayed to the model - and the second one
decides where it has to live. Chat-completions APIs are stateless: every
request carries the whole conversation, and in this app the agent is what
assembles that request (day 6). Keeping the history in `localStorage` would
mean the browser shipping the full transcript up with every message and the
agent taking dictation from it - the client would own the context, the
server would forget it the moment the tab closed, and a second browser (or
a phone) would see an empty chat. The store below owns it instead; the
browser only draws what it is given.

**Why JSON files and not a database.** One file per conversation, written
whole, replaced atomically. It is the smallest thing that answers the
requirement, and it stays legible - `cat data/conversations/single.json`
is the entire debugging story. The cost is that every write rewrites the
file and that concurrent writers need the lock below; both are fine for a
handful of chats on one machine. SQLite is the next step, and the only
thing that would have to change is this file: `load`, `save`, `delete` and
`all` are the whole interface the rest of the app uses.

Storage location: `data/conversations/` next to this file, or wherever
`CHAT_STORE_DIR` points (the UI tests aim it at a temp directory so they
never touch real chats).
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Conversation ids end up as file names, so they are restricted rather than
#: escaped: no dots, no slashes, nothing that can climb out of the directory.
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The id the single-chat view always uses. Fixed, so that one chat is the
#: same conversation on every reload and in every browser.
SINGLE_CONVERSATION_ID = "single"

#: Which view a record belongs to - the only thing the store knows about the
#: UI, and it knows it only so the frontend can put restored chats back where
#: they came from.
VIEWS = ("single", "compare")
DEFAULT_VIEW = "single"

STORE_DIR_ENV = "CHAT_STORE_DIR"
DEFAULT_STORE_DIR = Path(__file__).parent / "data" / "conversations"


class StoreError(RuntimeError):
    """A conversation id that cannot be used, or a record that cannot be read."""


def now_iso() -> str:
    """UTC, to the millisecond.

    Milliseconds rather than seconds because `all()` sorts by this field and
    the compare view is rebuilt in that order: two columns added one after the
    other land in the same second, and restoring them backwards would be a
    visible bug.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def validate_id(conversation_id: str) -> str:
    if not CONVERSATION_ID_PATTERN.match(conversation_id or ""):
        raise StoreError(
            "A conversation id may only contain letters, digits, '-' and '_' "
            "(1-64 characters)."
        )
    return conversation_id


@dataclass
class Message:
    """One bubble, as it was shown on screen.

    `error` marks a turn the API refused - a 401, a timeout. It is kept
    because it is part of what the user saw and should still be there after a
    reload, but it is deliberately left out of `Conversation.history()`: an
    error is not something the model said, and replaying it as if it were
    would teach the agent to apologise for HTTP failures.
    """

    role: str
    content: str
    ts: str = field(default_factory=now_iso)
    error: bool = False

    def to_dict(self) -> dict:
        data = {"role": self.role, "content": self.content, "ts": self.ts}
        if self.error:
            data["error"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=str(data.get("role", "assistant")),
            content=str(data.get("content", "")),
            ts=str(data.get("ts") or now_iso()),
            error=bool(data.get("error")),
        )


@dataclass
class Conversation:
    """One chat: what was said in it, and the settings it was said under."""

    id: str
    view: str = DEFAULT_VIEW
    settings: dict = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def history(self) -> list[dict]:
        """The transcript as the model should see it: `[{role, content}, ...]`.

        Complete turns only. A user message whose answer never arrived is
        dropped along with the error it got, so the replayed conversation
        never contains a question left hanging.
        """
        replay: list[dict] = []
        pending_user: Message | None = None

        for message in self.messages:
            if message.role == "user":
                pending_user = None if message.error else message
            elif message.role == "assistant":
                if pending_user and not message.error:
                    replay.append({"role": "user", "content": pending_user.content})
                    replay.append({"role": "assistant", "content": message.content})
                pending_user = None

        return replay

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "view": self.view,
            "settings": self.settings,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        view = data.get("view")
        return cls(
            id=str(data["id"]),
            view=view if view in VIEWS else DEFAULT_VIEW,
            settings=data.get("settings") or {},
            messages=[Message.from_dict(m) for m in data.get("messages") or []],
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
        )


def store_dir_from_env() -> Path:
    """Where conversations are kept: `CHAT_STORE_DIR`, or `data/conversations`."""
    configured = os.getenv(STORE_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STORE_DIR


class ConversationStore:
    """Conversations as JSON files in one directory - one file per chat.

    Every method takes the lock. Requests run in FastAPI's threadpool and the
    compare view fires up to five of them at once, so two writes really can
    land at the same moment; without the lock, two panes finishing together
    could interleave a read-modify-write and lose a turn.

    Writes go to a temporary file that is then `os.replace`d over the real
    one. `os.replace` is atomic on POSIX and Windows alike, so a crash
    mid-write leaves the previous version intact rather than a half-written
    file - which matters here because the file is rewritten whole on every
    single message.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else store_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- paths ---------------------------------------------------------------

    def path_for(self, conversation_id: str) -> Path:
        return self.directory / f"{validate_id(conversation_id)}.json"

    # -- reading -------------------------------------------------------------

    def load(self, conversation_id: str) -> Conversation | None:
        """One conversation, or None if it was never stored.

        A file that cannot be parsed is treated as "not stored" rather than
        raised: a corrupt record should cost you one transcript, not the
        ability to start the app.
        """
        path = self.path_for(conversation_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                return Conversation.from_dict(json.loads(path.read_text("utf-8")))
            except (ValueError, KeyError, OSError):
                return None

    def all(self) -> list[Conversation]:
        """Every stored conversation, oldest first.

        Oldest first because the compare view rebuilds its columns from this
        list, and a restored row should stand in the order it was created.
        """
        with self._lock:
            conversations = [
                conversation
                for path in self.directory.glob("*.json")
                if (conversation := self._read(path)) is not None
            ]
        conversations.sort(key=lambda c: (c.created_at, c.id))
        return conversations

    def _read(self, path: Path) -> Conversation | None:
        try:
            return Conversation.from_dict(json.loads(path.read_text("utf-8")))
        except (ValueError, KeyError, OSError):
            return None

    # -- writing -------------------------------------------------------------

    def save(self, conversation: Conversation) -> Conversation:
        conversation.updated_at = now_iso()
        path = self.path_for(conversation.id)
        payload = json.dumps(conversation.to_dict(), ensure_ascii=False, indent=2)

        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        return conversation

    def delete(self, conversation_id: str) -> bool:
        """Forget a conversation entirely. Used when a compare column closes."""
        path = self.path_for(conversation_id)
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    # -- the three things the app actually does ------------------------------

    def upsert(self, conversation_id: str, view: str, settings: dict) -> Conversation:
        """Register a chat, or update its settings, without touching messages.

        This is what makes an empty compare column survive a restart: the
        column exists, and its model and parameters are worth keeping, before
        anyone has sent it a word.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(id=validate_id(conversation_id))
            conversation.view = view if view in VIEWS else DEFAULT_VIEW
            conversation.settings = settings
            return self.save(conversation)

    def append_turn(
        self,
        conversation_id: str,
        user_message: str,
        answer: str,
        *,
        settings: dict | None = None,
        error: bool = False,
        view: str = DEFAULT_VIEW,
    ) -> Conversation:
        """Add one exchange - the question and what came back for it."""
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                conversation = Conversation(id=validate_id(conversation_id), view=view)
            if settings is not None:
                conversation.settings = settings
            conversation.messages.append(Message(role="user", content=user_message))
            conversation.messages.append(
                Message(role="assistant", content=answer, error=error)
            )
            return self.save(conversation)

    def clear(self, conversation_id: str) -> Conversation | None:
        """Drop the messages, keep the chat.

        What the "Clear context" button does. The record survives with its
        settings, so a cleared compare column is still there on the next
        reload with its parameters intact - it just has nothing to remember.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.messages = []
            return self.save(conversation)
