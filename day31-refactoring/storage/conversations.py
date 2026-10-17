"""Conversations: what was said, and what was made of it.

One JSON file per chat in `data/conversations/` (or `CHAT_STORE_DIR`),
written whole and replaced atomically.

A conversation is one line of messages. Beside them: the summary (day 9), the
key-value facts (day 10) and the RAG chat's task state (day 25) - derived
artefacts with a cost of their own, kept so they are paid for once rather
than on every turn.

Files written while a conversation could branch (`branches`, `active_branch`,
`checkpoints`) are read as the branch that was active (`flatten`) and written
back flat on the next save.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from storage.common import DATA_DIR, now_iso, validate_id

#: The id the first chat always uses. Fixed, so that a bare
#: `{"message": "..."}` lands in a conversation the page can show.
SINGLE_CONVERSATION_ID = "single"

STORE_DIR_ENV = "CHAT_STORE_DIR"
DEFAULT_STORE_DIR = DATA_DIR / "conversations"

#: How many of the agent's unanswered suggestions a conversation keeps. A
#: panel with thirty of them is one nobody reads, and being read is the
#: entire point of proposing rather than filing.
MAX_PENDING_PROPOSALS = 10


@dataclass
class Message:
    """One bubble, as it was shown on screen.

    `error` marks a turn the API refused - a 401, a timeout. It is kept
    because it is part of what the user saw and should still be there after a
    reload, but it is deliberately left out of `Conversation.history()`: an
    error is not something the model said, and replaying it as if it were
    would teach the agent to apologise for HTTP failures.

    `usage` is day 8: what the provider said this turn cost, written down on
    the assistant message that it paid for. Everything else in the token
    readout can be recomputed from the text at any time - this cannot. It was
    reported once, in one response, and if it is not saved with the message
    then "what has this conversation cost so far" resets to zero on every
    reload, which would make the only exact number in the feature the one
    least worth trusting.

    `tools` is day 17: the tools this answer ran before it was written, with
    what they returned. Stored on the message for `usage`'s reason - it was
    true once, of this turn, and cannot be recomputed from the text. It is
    also what lets a reloaded page draw the call receipts under the answer
    they belong to without anything being called again.
    """

    role: str
    content: str
    ts: str = field(default_factory=now_iso)
    error: bool = False
    usage: dict | None = None
    tools: list | None = None
    # Day 22: what the document index returned for this question and which of
    # it the answer cites. Stored for `tools`' reason: the index is rebuilt,
    # and a reload should show the sources this answer was actually given.
    rag: dict | None = None
    # Day 25, on a user message: what it changed in the task state - goal
    # set, a clarification added, a term removed. Stored for `tools`' reason:
    # the state moves on, and the record of which message moved it would not
    # survive a reload otherwise.
    task_state: list | None = None

    def to_dict(self) -> dict:
        data = {"role": self.role, "content": self.content, "ts": self.ts}
        if self.error:
            data["error"] = True
        if self.usage:
            data["usage"] = self.usage
        if self.tools:
            data["tools"] = self.tools
        if self.rag:
            data["rag"] = self.rag
        if self.task_state:
            data["task_state"] = self.task_state
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        usage = data.get("usage")
        tools = data.get("tools")
        rag = data.get("rag")
        task_state = data.get("task_state")
        return cls(
            role=str(data.get("role", "assistant")),
            content=str(data.get("content", "")),
            ts=str(data.get("ts") or now_iso()),
            error=bool(data.get("error")),
            usage=usage if isinstance(usage, dict) else None,
            tools=tools if isinstance(tools, list) and tools else None,
            rag=rag if isinstance(rag, dict) and rag else None,
            task_state=task_state if isinstance(task_state, list) and task_state else None,
        )


@dataclass
class Summary:
    """What the earlier part of a conversation has been compressed into.

    `covers` is the whole of the bookkeeping: the number of messages, counted
    from the start of `Conversation.history()`, that this paragraph speaks
    for. It is what makes compression incremental - the next overflow only has
    to summarise what has happened since - and what guarantees that no message
    is ever both summarised and replayed verbatim, because the window starts
    at exactly this index.

    `usage` is what the last compression cost and `usage_total` what every
    compression of this chat has cost put together. Kept apart from the turns'
    own usage on purpose: this is spending the user did not ask for directly,
    and folding it into the same total would hide the one number that says
    whether compression is paying for itself.
    """

    text: str
    covers: int = 0
    updated_at: str = field(default_factory=now_iso)
    model: str = ""
    compressions: int = 0
    usage: dict | None = None
    usage_total: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "text": self.text,
            "covers": self.covers,
            "updated_at": self.updated_at,
            "compressions": self.compressions,
        }
        if self.model:
            data["model"] = self.model
        if self.usage:
            data["usage"] = self.usage
        if self.usage_total:
            data["usage_total"] = self.usage_total
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Summary | None":
        if not isinstance(data, dict):
            return None
        text = str(data.get("text") or "").strip()
        if not text:
            # A summary with no text is not a summary; treating it as one would
            # put an empty system message into every request from here on.
            return None
        usage = data.get("usage")
        usage_total = data.get("usage_total")
        return cls(
            text=text,
            covers=max(0, int(data.get("covers") or 0)),
            updated_at=str(data.get("updated_at") or now_iso()),
            model=str(data.get("model") or ""),
            compressions=max(0, int(data.get("compressions") or 0)),
            usage=usage if isinstance(usage, dict) else None,
            usage_total=usage_total if isinstance(usage_total, dict) else {},
        )


@dataclass
class Facts:
    """A conversation's key-value memory - day 10's second strategy.

    `items` is the block itself, in the order the keys were last touched:
    re-setting one moves it to the end, so the front is what has gone longest
    without being mentioned and is therefore what gets dropped when the block
    is full (`facts.apply_patch`).

    The rest is the same bookkeeping `Summary` carries, for the same reason.
    `updates` counts the extraction requests this chat has made and
    `usage_total` what they came to, and both are kept apart from the turns'
    own usage: this strategy spends a request on every single message, and
    that number is the whole argument for or against it.
    """

    items: dict = field(default_factory=dict)
    updated_at: str = field(default_factory=now_iso)
    model: str = ""
    updates: int = 0
    usage: dict | None = None
    usage_total: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "items": dict(self.items),
            "updated_at": self.updated_at,
            "updates": self.updates,
        }
        if self.model:
            data["model"] = self.model
        if self.usage:
            data["usage"] = self.usage
        if self.usage_total:
            data["usage_total"] = self.usage_total
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Facts | None":
        if not isinstance(data, dict):
            return None
        raw = data.get("items")
        if not isinstance(raw, dict):
            return None
        if not raw and not data.get("usage_total"):
            # An empty block is not a memory; treating it as one would put an
            # empty system message into every request from here on. It is kept
            # anyway once it has cost something - the early messages of a chat
            # often establish nothing, and those requests were still paid for.
            return None
        usage = data.get("usage")
        usage_total = data.get("usage_total")
        return cls(
            items={str(k): str(v) for k, v in raw.items()},
            updated_at=str(data.get("updated_at") or now_iso()),
            model=str(data.get("model") or ""),
            updates=max(0, int(data.get("updates") or 0)),
            usage=usage if isinstance(usage, dict) else None,
            usage_total=usage_total if isinstance(usage_total, dict) else {},
        )


@dataclass
class TaskState:
    """A conversation's task state - day 25, the RAG chat's memory of what it is for.

    `content` is the state itself, in `task_state`'s shape: the goal, what
    the user has clarified, the constraints and the terms. The rest is
    `Facts`' bookkeeping, for `Facts`' reason - this too is a request on every
    message, and what it has cost is the argument for or against it.
    """

    content: dict = field(default_factory=dict)
    updated_at: str = field(default_factory=now_iso)
    model: str = ""
    updates: int = 0
    usage: dict | None = None
    usage_total: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {"content": dict(self.content), "updated_at": self.updated_at, "updates": self.updates}
        if self.model:
            data["model"] = self.model
        if self.usage:
            data["usage"] = self.usage
        if self.usage_total:
            data["usage_total"] = self.usage_total
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState | None":
        if not isinstance(data, dict) or not isinstance(data.get("content"), dict):
            return None
        usage = data.get("usage")
        usage_total = data.get("usage_total")
        return cls(
            content=json.loads(json.dumps(data["content"])),
            updated_at=str(data.get("updated_at") or now_iso()),
            model=str(data.get("model") or ""),
            updates=max(0, int(data.get("updates") or 0)),
            usage=usage if isinstance(usage, dict) else None,
            usage_total=usage_total if isinstance(usage_total, dict) else {},
        )


@dataclass
class Conversation:
    """One chat: what was said in it, the settings it was said under, and what was made of it.

    `summary`, `facts` and `task_state` are descriptions of `messages`, kept
    beside them so each is paid for once: day 9's compression, day 10's
    key-value block and day 25's RAG task state.
    """

    id: str
    settings: dict = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    summary: Summary | None = None
    facts: Facts | None = None
    task_state: TaskState | None = None
    # Day 11. Which piece of work this chat is part of, or None for a chat
    # that is not part of one - the common case, and the one where the
    # working layer simply does not appear. It is a pointer and not a copy:
    # the task's memory lives in the task, so a second chat joining it sees
    # everything the first one filed, which is the entire point.
    task_id: str | None = None
    # What the agent has suggested filing and nobody has ruled on yet, as
    # plain dicts (`memory.Proposal.to_dict`). Kept here rather than returned
    # once with the answer so that a reload does not silently throw away
    # suggestions the user had not got round to reading - and kept as dicts
    # so that this module still imports nothing from the rest of the app.
    proposals: list = field(default_factory=list)
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

    def usages(self) -> list[dict]:
        """Every turn's reported cost, in order - day 8.

        The store does no arithmetic on these (that is `tokens.sum_usage`);
        it only knows which messages carry one. A failed turn has none,
        because nothing was billed for it.
        """
        return [m.usage for m in self.messages if m.usage]

    def text(self) -> str:
        """Everything ever said in this chat, concatenated.

        The one thing a token count of "the whole conversation" needs, and
        the reason it is a method here rather than a loop in the core: it
        is every message, including the old ones the agent no longer replays
        and the failed ones it never did.
        """
        return "".join(m.content for m in self.messages)

    # -- storage -------------------------------------------------------------

    def to_dict(self) -> dict:
        """The record as it is written to disk."""
        data = {
            "id": self.id,
            "settings": self.settings,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.summary:
            data["summary"] = self.summary.to_dict()
        if self.facts:
            data["facts"] = self.facts.to_dict()
        if self.task_state:
            data["task_state"] = self.task_state.to_dict()
        if self.task_id:
            data["task_id"] = self.task_id
        if self.proposals:
            data["proposals"] = self.proposals
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        data = flatten(data)
        return cls(
            id=str(data["id"]),
            settings=data.get("settings") or {},
            messages=[Message.from_dict(m) for m in data.get("messages") or []],
            summary=Summary.from_dict(data.get("summary") or {}),
            facts=Facts.from_dict(data.get("facts") or {}),
            task_state=TaskState.from_dict(data.get("task_state") or {}),
            # A task id is not checked against the tasks on disk here. The
            # store that owns tasks is a different one, and a chat pointing
            # at a task somebody deleted should read back as a chat with no
            # working layer, not as a record that refuses to load.
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            proposals=read_proposals(data.get("proposals")),
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
        )


def flatten(data: dict) -> dict:
    """A record written while conversations could branch, as the linear one it becomes.

    In that format the top-level `messages`, `summary`, `facts` and
    `task_state` were the `main` branch, and every fork sat in `branches`
    with only its own messages, its `parent` and `forked_at` - how much of
    the parent's transcript it inherited. The branch that was active is what
    survives: its transcript, rebuilt down that chain, and its own summary,
    facts and task state. The per-turn usage rides on the messages. The other
    branches and the checkpoints are dropped, and a record that never forked
    reads exactly as it was written.
    """
    forks = {str(b["id"]): b for b in data.get("branches") or []
             if isinstance(b, dict) and b.get("id") and b["id"] != "main"}
    node = forks.get(str(data.get("active_branch") or ""))
    if node is None:
        return data
    active = node
    chain: list[dict] = []
    seen: set[str] = set()
    # Loop-guarded: these ids come off disk.
    while node is not None and str(node["id"]) not in seen:
        seen.add(str(node["id"]))
        chain.append(node)
        node = forks.get(str(node.get("parent") or ""))
    chain.reverse()
    # The oldest fork's past is `main` - or nothing, for a fork of a branch
    # that is no longer in the file, which is how it always read.
    messages = list(data.get("messages") or []) if chain[0].get("parent") == "main" else []
    for fork in chain:
        messages = messages[: max(0, int(fork.get("forked_at") or 0))] + list(fork.get("messages") or [])
    return {**data, "messages": messages, "summary": active.get("summary"), "facts": active.get("facts"),
            "task_state": active.get("task_state")}


def read_proposals(raw) -> list:
    """Pending suggestions off disk, normalised to the four fields they have.

    Read back through a known shape rather than passed on whole, for the
    reason `core.reports.usage_block` gives: these come off a file an older
    version of the app may have written, and a key that no longer means
    anything should not travel any further than this function.
    """
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw[:MAX_PENDING_PROPOSALS]:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        value = str(entry.get("value") or "").strip()
        if not key or not value:
            continue
        out.append(
            {
                "key": key,
                "value": value,
                "layer": str(entry.get("layer") or ""),
                "why": str(entry.get("why") or ""),
            }
        )
    return out


def store_dir_from_env() -> Path:
    """Where conversations are kept: `CHAT_STORE_DIR`, or `data/conversations`."""
    configured = os.getenv(STORE_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_STORE_DIR


class ConversationStore:
    """Conversations as JSON files in one directory - one file per chat.

    Every method takes the lock. Requests run in FastAPI's threadpool, so two
    writes really can land at the same moment - a turn and a settings change,
    or two chats answered together; without the lock they could interleave a
    read-modify-write and lose a turn.

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
        """Every stored conversation, oldest first - the order the chat menu lists them in."""
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
        """Forget a conversation entirely - a chat deleted from the menu."""
        path = self.path_for(conversation_id)
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    # -- the three things the app actually does ------------------------------

    def upsert(self, conversation_id: str, settings: dict) -> Conversation:
        """Register a chat, or update its settings, without touching messages.

        This is what makes an empty chat survive a restart: it exists, and the
        settings it was given are worth keeping, before anyone has sent it a
        word.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(id=validate_id(conversation_id))
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
        usage: dict | None = None,
        tools: list | None = None,
        rag: dict | None = None,
        task_state: list | None = None,
    ) -> Conversation:
        """Add one exchange - the question and what came back for it.

        `usage` rides on the assistant message rather than on the turn,
        because that is the message it paid for: the prompt tokens are what
        the whole replayed conversation cost to send, and the completion
        tokens are what this one answer cost to get back.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                conversation = Conversation(id=validate_id(conversation_id))
            if settings is not None:
                conversation.settings = settings
            conversation.messages.append(Message(role="user", content=user_message,
                                                 task_state=list(task_state) if task_state else None))
            conversation.messages.append(
                Message(role="assistant", content=answer, error=error, usage=usage,
                        tools=list(tools) if tools else None, rag=rag or None)
            )
            return self.save(conversation)

    def clear(self, conversation_id: str) -> Conversation | None:
        """Drop the messages, keep the chat.

        What the "Clear context" button does. The record survives with its
        settings, so a cleared chat is still there on the next reload with its
        parameters intact - it just has nothing to remember.

        The summary goes with them (day 9). It is a description of those exact
        messages, and a description that outlived the thing it described would
        be the one way this app could still remember something the user has
        explicitly told it to forget. The facts and the task state go for the
        same reason.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.messages = []
            conversation.summary = None
            conversation.facts = None
            conversation.task_state = None
            # Day 11: the pending suggestions go too. They are the agent's
            # reading of messages that are about to stop existing, and one
            # surviving into a cleared chat would be the app offering to file
            # a fact out of a conversation nobody can read any more.
            #
            # What does *not* go is anything already filed. That was moved
            # into another layer on purpose, by a person, and "clear this
            # chat" is not a statement about the task or about the user.
            conversation.proposals = []
            return self.save(conversation)

    # -- what a chat has made of its own past (days 9, 10 and 25) ------------

    def save_summary(self, conversation_id: str, summary: Summary) -> Conversation | None:
        """Write down what the older part of a conversation has been compressed into.

        Its own call, made before the turn it is for: the summary has to be on
        disk whether or not the answer that follows it arrives, or a failed
        turn would throw away a compression that was already paid for and the
        next message would buy the same paragraph again.

        Which is also why it creates the record when there is none rather than
        reporting that there is nothing to write to. Being called before the
        first turn of a conversation has been stored is not an error here; it
        is the ordinary case, and dropping the write would silently lose
        something that has already been paid for.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(
                id=validate_id(conversation_id)
            )
            conversation.summary = summary
            return self.save(conversation)

    def clear_summary(self, conversation_id: str) -> Conversation | None:
        """Forget the compression, keep every message.

        The opposite of `clear`, and the reason both exist: this throws away
        the derived thing and leaves the source, so the next overflow rebuilds
        the summary from the transcript - which is what you want after editing
        the prompt behind it, or after a summary that came out wrong.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.summary = None
            return self.save(conversation)

    def save_facts(self, conversation_id: str, facts: Facts | None) -> Conversation | None:
        """The chat's key-value memory, as it stands after one message.

        Written before the turn, exactly as the summary is and for exactly the
        same reason: the extraction request has been paid for by the time this
        is called, and a turn that then fails must not make the next message
        buy the same patch again. This one is called before the *first* turn
        of every chat that uses the strategy, so the record it writes into is
        usually one it has just created.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(
                id=validate_id(conversation_id)
            )
            conversation.facts = facts
            return self.save(conversation)

    def clear_facts(self, conversation_id: str) -> Conversation | None:
        """Forget what the chat had decided was true, keep what was said.

        Day 9's "forget the summary" in the other strategy's shape, and worth
        having for a sharper reason. A fact is a short sentence that is sent
        with every request from now on and is never re-derived from the
        transcript while it stands - so a wrong one does not fade, it
        compounds. This is the way out of that.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.facts = None
            return self.save(conversation)

    def save_task_state(self, conversation_id: str, state: TaskState | None) -> Conversation | None:
        """Day 25: the chat's task state, as it stands after one message.

        Written *after* the turn rather than before it, unlike the facts: the
        state is also what the search was asked with, and a turn the search
        refused (no index, a silent Ollama) leaves no message behind - a
        state moved by a message nobody can see would be one the transcript
        cannot explain.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(
                id=validate_id(conversation_id)
            )
            conversation.task_state = state
            return self.save(conversation)

    def clear_task_state(self, conversation_id: str) -> Conversation | None:
        """Forget the task state, keep every message - it starts again from the next one."""
        return self.save_task_state(conversation_id, None) if self.load(conversation_id) else None

    # -- which piece of work this chat belongs to (day 11) -------------------

    def set_task(self, conversation_id: str, task_id: str | None) -> Conversation | None:
        """Point a chat at a task, or at none.

        Creates the record if there is none, for the same reason
        `save_summary` does: joining a task is something you do *before*
        saying anything, and refusing to remember it until the first message
        would mean the working layer was missing from the one turn where it
        was most obviously wanted.
        """
        with self._lock:
            conversation = self.load(conversation_id) or Conversation(
                id=validate_id(conversation_id)
            )
            conversation.task_id = task_id or None
            return self.save(conversation)

    def set_proposals(self, conversation_id: str, proposals: list) -> Conversation | None:
        """Replace this chat's unanswered suggestions.

        Replace rather than append: the proposer is sent the keys already on
        file and asked not to repeat them, so what comes back is the current
        answer to "what is still worth filing here", and keeping the previous
        one beside it would show the user the same suggestion twice.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.proposals = read_proposals(proposals)
            return self.save(conversation)
