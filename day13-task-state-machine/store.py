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

Day 8 adds one field, `Message.usage`: what the provider said a turn cost,
written down on the assistant message that it paid for. It is the only number
in the token readout that cannot be recomputed from the text later - it was
reported once, in one response - so if it is not stored, "what has this
conversation cost so far" resets to zero on every reload. The store does no
arithmetic on it; it keeps a dict and hands it back, as it does with
everything else here.

Day 9 adds a second one, `Conversation.summary`: the compressed stand-in for
the part of the transcript that no longer fits in the window. It is kept
**beside** the messages rather than among them - the messages it describes are
still there, untouched and complete - because it is not something anyone said.
It is a derived artefact with a cost of its own and a note of how far through
the transcript it reaches, and it is stored for exactly one reason: so that it
is written once per overflow instead of once per turn.

Day 10 adds the one thing here that is not a field: **a conversation is a
tree.** Until now a record was a list of messages, and the only question was
how much of that list went into a request. A branch makes the list itself a
choice - fork at message 6, say two different things, and you have two
conversations that share a past and disagree about the present.

It is stored the cheap way, because the expensive way is not worth it. A
branch keeps only the messages said *in it*, plus the id of its parent and
`forked_at`: how much of the parent it inherits. The shared prefix exists
once, `transcript()` walks the chain to rebuild it, and forking is free -
creating a branch copies no messages at all.

`main` is a branch like any other and always exists. On disk it is written
where it always was, under `messages`, with the forks in `branches`
alongside - so every file day 7, 8 and 9 wrote reads back as a conversation
with one branch and nothing lost.

Day 11 adds the two records that are **not** part of a conversation, and
that is the whole of what makes them interesting. Everything above is scoped
to one chat and dies with it; a `Task` is scoped to a piece of work that may
take several chats, and the long-term layer is scoped to nothing at all - it
is simply always there. They are stored the same way for the same reasons
(one JSON file, written whole, replaced atomically), in `data/tasks/` and
`data/memory/long_term.json`, and they hold the same `MemoryItem` because
the difference between the two layers is where the file is, not what is in
it. `memory.py` decides what gets read back out of them; this module only
keeps them.

Storage location: `data/conversations/` next to this file, or wherever
`CHAT_STORE_DIR` points (the UI tests aim it at a temp directory so they
never touch real chats). `CHAT_TASKS_DIR` and `CHAT_MEMORY_DIR` do the same
for the two day-11 layers.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The one module this file imports out of the app, and day 13 is why. A task
# can now be in a stage, and which stage may follow which is a rule rather
# than a field - so the rule has to be enforced on the way *into* the file,
# not by whoever remembered to ask first. `TaskStore.move` calls
# `phases.check`, which means there is no way to write a stage to disk that
# the machine did not allow. A dumb setter here and a check in `server.py`
# would read the same on the day it was written and would drift the first
# time somebody added a second route.
import phases


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

#: The branch every conversation starts on and can never be without. Day 10.
#: A record with no branches at all would have nowhere to put a message, so
#: this one is created on read if the file does not mention it - which is what
#: every file written before day 10 looks like.
MAIN_BRANCH = "main"

STORE_DIR_ENV = "CHAT_STORE_DIR"
DEFAULT_STORE_DIR = Path(__file__).parent / "data" / "conversations"

#: Day 11's two layers, and where they live. Separate environment variables
#: rather than one, so the UI tests can redirect all three independently and
#: a run that is only exercising tasks does not have to relocate the chats.
TASKS_DIR_ENV = "CHAT_TASKS_DIR"
DEFAULT_TASKS_DIR = Path(__file__).parent / "data" / "tasks"
MEMORY_DIR_ENV = "CHAT_MEMORY_DIR"
DEFAULT_MEMORY_DIR = Path(__file__).parent / "data" / "memory"

#: Day 12, and a fourth directory rather than a corner of the third one. The
#: long-term layer and the personality profiles have the same lifetime - both
#: last until somebody deletes them - but they are not the same kind of thing,
#: and `data/memory/` is where a reader goes to find out what the agent
#: *knows*. A file of standing instructions filed under that name would make
#: the one distinction this day exists to draw invisible on disk.
PERSONALITY_DIR_ENV = "CHAT_PERSONALITY_DIR"
DEFAULT_PERSONALITY_DIR = Path(__file__).parent / "data" / "personality"

#: What day 11 had here was `status`, with `open` and `closed` in it, and day
#: 13 replaced it with `phases.PHASES` - four stages and a table of which may
#: follow which. `done` inherited the whole of what `closed` used to do: the
#: record stays readable and what it remembers stops being sent, which is what
#: gives the working layer a *lifetime* rather than merely a scope.
#:
#: The one thing kept from the old field is how to read it. A file written
#: before today says `"status": "closed"` and nothing else, and that sentence
#: still has an obvious meaning, so `Task.from_dict` reads it as `done` rather
#: than dropping it. An open one becomes planning, which is where a task whose
#: stage nobody recorded honestly is.
LEGACY_STATUS = "status"
LEGACY_CLOSED = "closed"

#: How many ticked steps one stage of one task can hold. Not a limit anybody
#: reaches - the checklists are three items long - but the file is read back
#: from disk and a list that came from somewhere else should not be able to
#: grow without bound.
MAX_STEP_KEYS = 32

#: The ceiling on one layer. Manual filing makes this a backstop rather than
#: a working limit - a person files dozens of things, not thousands - but a
#: layer allowed to grow without bound is the transcript again, with worse
#: recall, which is the same argument `facts.MAX_FACTS` makes.
MAX_ITEMS = 120

#: How many of the agent's unanswered suggestions a conversation keeps. A
#: panel with thirty of them is one nobody reads, and being read is the
#: entire point of proposing rather than filing.
MAX_PENDING_PROPOSALS = 10

#: How many personality profiles are kept. Low on purpose, and for a different
#: reason than `MAX_ITEMS`: that one is a backstop against a layer growing
#: into a second transcript, while this is a claim about what profiles are for.
#: One is switched to deliberately, by name, from a list you can read at a
#: glance - thirty of them would be a settings file with a worse interface.
MAX_PROFILES = 12


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

    `usage` is day 8: what the provider said this turn cost, written down on
    the assistant message that it paid for. Everything else in the token
    readout can be recomputed from the text at any time - this cannot. It was
    reported once, in one response, and if it is not saved with the message
    then "what has this conversation cost so far" resets to zero on every
    reload, which would make the only exact number in the feature the one
    least worth trusting.
    """

    role: str
    content: str
    ts: str = field(default_factory=now_iso)
    error: bool = False
    usage: dict | None = None

    def to_dict(self) -> dict:
        data = {"role": self.role, "content": self.content, "ts": self.ts}
        if self.error:
            data["error"] = True
        if self.usage:
            data["usage"] = self.usage
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        usage = data.get("usage")
        return cls(
            role=str(data.get("role", "assistant")),
            content=str(data.get("content", "")),
            ts=str(data.get("ts") or now_iso()),
            error=bool(data.get("error")),
            usage=usage if isinstance(usage, dict) else None,
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
    """A branch's key-value memory - day 10's second strategy.

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
class MemoryItem:
    """One thing a person has filed, in the working or the long-term layer.

    Day 11, and deliberately more than the bare `key: value` pair day 10's
    `Facts` keeps. The extra fields all answer one question: where did this
    come from?

    A fact block the agent wrote can be taken on trust, because it is about
    the conversation it is sitting in and you can scroll up. A memory that
    outlives its conversation cannot: it turns up weeks later, in a chat
    about something else, next to facts filed by a different task on a
    different day. If it cannot say who put it there and what was on screen
    at the time, nobody will ever dare delete anything, and a memory layer
    nobody prunes stops being a memory and becomes a liability.

    `pinned` is the manual half of `memory.ALWAYS_PREFIXES`: a way to say
    "this one is relevant to everything" about a key whose prefix does not
    already say so.
    """

    key: str
    value: str
    source: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    pinned: bool = False

    def to_dict(self) -> dict:
        data = {"key": self.key, "value": self.value, "created_at": self.created_at}
        if self.source:
            data["source"] = self.source
        if self.pinned:
            data["pinned"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryItem | None":
        """One item off disk, or None if it is not one.

        An item with no key or no value is dropped rather than repaired. It
        would go into every future prompt as a blank line or a dangling
        name, and a memory layer is the last place in this app where a
        half-record should be given the benefit of the doubt.
        """
        if not isinstance(data, dict):
            return None
        key = str(data.get("key") or "").strip()
        value = str(data.get("value") or "").strip()
        if not key or not value:
            return None
        source = data.get("source")
        return cls(
            key=key,
            value=value,
            source=source if isinstance(source, dict) else {},
            created_at=str(data.get("created_at") or now_iso()),
            pinned=bool(data.get("pinned")),
        )


@dataclass
class Task:
    """A piece of work: where it has got to, and what it has established.

    The unit the working layer is scoped to, and the reason that layer is
    not just a second fact block in a different file. A conversation ends
    when you close the tab; a task ends when the work is done, and those are
    hardly ever the same moment - which is the observation the whole of day
    11 is built on. Several chats can point at one task, and everything they
    file lands in the same place, so the second chat about a thing starts
    where the first one left off.

    Day 13 replaced the two-valued `status` with the four fields below, and
    they are the day entire:

        phase   which of `phases.PHASES` this work is in
        steps   which of that stage's checklist items are ticked, per stage
        paused  whether the machine is frozen where it stands
        log     every move and every refusal, with a time and an author

    `phase` is what turns a scope into a lifetime, exactly as `status` did:
    a task in `done` keeps every item it ever filed, keeps them readable in
    the panel, and stops sending them. Nothing is deleted by finishing, which
    is what makes finishing something a person will actually do.

    `steps` is a dict of stage to ticked keys rather than a flat list,
    because the same task passes through a stage more than once - execution,
    back to planning, execution again - and a flat list would have no way to
    say which pass it meant.

    `log` is the field worth defending. It holds refused transitions as well
    as applied ones, and those are the most informative records this app
    produces: the moment the model wanted to call something finished and the
    machine would not let it. A state machine that kept only its successes
    would be a field with a nice name.

    This record knows the rules exist and does not enforce them; `TaskStore`
    does, through `phases.check`, so that a stage cannot reach the disk
    without having been allowed.
    """

    id: str
    title: str = ""
    phase: str = phases.DEFAULT_PHASE
    steps: dict = field(default_factory=dict)
    paused: bool = False
    #: What the work is waiting on, in a line. The one part of "expected
    #: action" that is not derived from the checklist - a pause has a reason
    #: and the checklist cannot know it ("waiting for the design review").
    note: str = ""
    #: What the agent has asked to do about the stage, waiting for a person.
    #: `{"to": "execution", "why": "the plan is agreed", "at": "..."}` or None.
    #: On the task rather than on the conversation, because the stage is the
    #: task's - the suggestion has to survive a reload, a new chat about the
    #: same work, and being looked at from the panel rather than the
    #: transcript it was raised in.
    suggested: dict | None = None
    log: list = field(default_factory=list)
    items: list = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    @property
    def is_active(self) -> bool:
        """Whether this task's memory is still sent with requests.

        Every stage but `done`, paused included. Pausing stops the machine,
        not the memory: a task you come back to in a fortnight that had
        forgotten everything it knew would be a task you start again.
        """
        return self.phase != phases.DONE

    def ticked(self, phase: str | None = None) -> list:
        return phases.done_steps(self, phase)

    def tick(self, key: str, done: bool = True) -> None:
        """Tick or untick one step of the current stage. Unknown keys ignored."""
        phase = phases.phase_of(self)
        known = {step.key for step in phases.STEPS.get(phase, ())}
        if key not in known:
            return
        current = [k for k in self.ticked(phase) if k != key]
        if done:
            current.append(key)
        self.steps = dict(self.steps or {})
        self.steps[phase] = current[:MAX_STEP_KEYS]

    def record(self, kind: str, by: str, **fields) -> None:
        """Write one thing that happened to this task into the log."""
        entry = {"at": now_iso(), "kind": kind, "by": by}
        entry.update({k: v for k, v in fields.items() if v not in (None, "")})
        self.log = (list(self.log or []) + [entry])[-phases.MAX_LOG:]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "phase": self.phase,
            "steps": {k: list(v) for k, v in (self.steps or {}).items()},
            "paused": self.paused,
            "note": self.note,
            "suggested": self.suggested,
            "log": list(self.log or []),
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        task = cls(
            id=str(data["id"]),
            title=str(data.get("title") or ""),
            phase=read_phase(data),
            steps=read_steps(data.get("steps")),
            paused=bool(data.get("paused")),
            note=str(data.get("note") or "")[:200],
            suggested=read_suggestion(data.get("suggested")),
            log=read_log(data.get("log")),
            items=read_items(data.get("items")),
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
        )
        return task


def read_phase(data: dict) -> str:
    """The stage a file on disk is in, including files that predate stages.

    Day 11's `closed` becomes `done`, because that is what it meant. Anything
    else - `open`, absent, misspelt - becomes planning, which is the only
    stage that claims nothing has happened yet and is therefore the safe
    reading of "no record".
    """
    phase = data.get("phase")
    if phase in phases.PHASE_INFO:
        return phase
    if data.get(LEGACY_STATUS) == LEGACY_CLOSED:
        return phases.DONE
    return phases.DEFAULT_PHASE


def read_steps(raw) -> dict:
    """`{stage: [keys]}` out of whatever was on disk, dropping the rest.

    Both halves are checked against `phases.STEPS` rather than trusted: a
    stage or a step key that no longer exists is a checklist item nobody can
    untick, and it would hold a transition open or shut for a reason that is
    not on screen anywhere.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for phase, keys in raw.items():
        if phase not in phases.STEPS or not isinstance(keys, list):
            continue
        known = {step.key for step in phases.STEPS[phase]}
        kept = [k for k in keys if isinstance(k, str) and k in known]
        if kept:
            out[phase] = list(dict.fromkeys(kept))[:MAX_STEP_KEYS]
    return out


def read_suggestion(raw) -> dict | None:
    """A pending suggestion off disk, or None if it is not one any more.

    Checked against `phases.PHASE_INFO` rather than trusted: a suggestion
    naming a stage that no longer exists is a button that cannot be pressed,
    and the honest reading of it is that there is no suggestion.
    """
    if not isinstance(raw, dict) or raw.get("to") not in phases.PHASE_INFO:
        return None
    return {
        "to": raw["to"],
        "why": str(raw.get("why") or "")[:120],
        "at": str(raw.get("at") or now_iso()),
    }


def read_log(raw) -> list:
    """The transition log, kept as written and capped.

    Read loosely on purpose - an entry is a record of something that already
    happened, and dropping one because a field it carried has since been
    renamed loses evidence to gain nothing.
    """
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)][-phases.MAX_LOG:]


def read_items(raw) -> list:
    """A list of `MemoryItem` out of whatever was on disk, dropping the rest."""
    if not isinstance(raw, list):
        return []
    return [item for entry in raw if (item := MemoryItem.from_dict(entry)) is not None]


def upsert_item(items: list, item: MemoryItem, cap: int = MAX_ITEMS) -> list:
    """The list with `item` filed in it - replacing anything with the same key.

    Same key means same fact, so filing `constraint.language` a second time
    corrects the first rather than storing two lines that contradict each
    other in the same prompt. That is the one rule a key-value memory cannot
    do without, and it is why these are keyed at all rather than being a list
    of sentences.

    The replacement goes to the *end*, which gives the list the only ordering
    it has that means anything: the front is what has gone longest without
    being touched, so that is what the cap drops. Exactly the rule
    `facts.apply_patch` uses, and deliberately the same one - a fact promoted
    out of a day-10 block into a layer should not change how it ages.
    """
    result = [existing for existing in items if existing.key != item.key]
    result.append(item)
    while len(result) > cap:
        result.pop(0)
    return result


@dataclass
class Checkpoint:
    """A named place in a branch that you can fork from later.

    Nothing but a position: which branch, and how many of its messages come
    before it. It stores no messages and copies nothing - the transcript is
    already saved, and a checkpoint is a bookmark in it.

    Kept as its own record rather than inferred from where branches happen to
    start, because the two are different things. A checkpoint that nobody has
    forked from yet is the normal case - it is the "save here" you make
    *before* trying something - and two branches from one checkpoint, which is
    the whole point of the exercise, is one position with two forks rather
    than two positions that happen to coincide.
    """

    id: str
    branch: str = MAIN_BRANCH
    index: int = 0
    label: str = ""
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "branch": self.branch,
            "index": self.index,
            "label": self.label,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        return cls(
            id=str(data["id"]),
            branch=str(data.get("branch") or MAIN_BRANCH),
            index=max(0, int(data.get("index") or 0)),
            label=str(data.get("label") or ""),
            created_at=str(data.get("created_at") or now_iso()),
        )


@dataclass
class Branch:
    """One line of a conversation - what was said *in it*, and where it began.

    `messages` holds only this branch's own messages. Everything before the
    fork belongs to the parent and is never copied: `parent` says whose past
    this is and `forked_at` says how much of it this branch inherits, and
    `Conversation.transcript` puts the two together on demand. A fork is
    therefore free to make and costs nothing to keep, which is what makes it
    reasonable to make one on a whim - which is the only time anyone ever
    wants to.

    `facts` and `summary` are per branch rather than per conversation, and
    that is not an implementation detail. Both are descriptions of a
    conversation's past, and two branches stop having the same past the moment
    they diverge; one shared block would end up describing whichever branch
    spoke last. A fork inherits a copy of what its parent knew at the fork,
    and the two drift apart from there, exactly as the transcripts do.
    """

    id: str
    name: str = ""
    parent: str | None = None
    forked_at: int = 0
    messages: list[Message] = field(default_factory=list)
    facts: Facts | None = None
    summary: Summary | None = None
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "parent": self.parent,
            "forked_at": self.forked_at,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
        }
        if self.facts:
            data["facts"] = self.facts.to_dict()
        if self.summary:
            data["summary"] = self.summary.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Branch | None":
        if not isinstance(data, dict) or not data.get("id"):
            return None
        parent = data.get("parent")
        return cls(
            id=str(data["id"]),
            name=str(data.get("name") or ""),
            parent=str(parent) if parent else None,
            forked_at=max(0, int(data.get("forked_at") or 0)),
            messages=[Message.from_dict(m) for m in data.get("messages") or []],
            facts=Facts.from_dict(data.get("facts") or {}),
            summary=Summary.from_dict(data.get("summary") or {}),
            created_at=str(data.get("created_at") or now_iso()),
        )


@dataclass
class Conversation:
    """One chat: what was said in it, and the settings it was said under.

    Since day 10 "what was said in it" is a tree rather than a list. The
    branches are kept flat, each pointing at its parent; `main` is always the
    first of them and always exists. Everything that used to read
    `conversation.messages` now asks for a *branch's* transcript instead, and
    with no forks made that is the same list it always was.
    """

    id: str
    view: str = DEFAULT_VIEW
    settings: dict = field(default_factory=dict)
    branches: list[Branch] = field(default_factory=lambda: [Branch(id=MAIN_BRANCH, name="main")])
    active_branch: str = MAIN_BRANCH
    checkpoints: list[Checkpoint] = field(default_factory=list)
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

    # -- the tree ------------------------------------------------------------

    @property
    def main(self) -> Branch:
        """The branch a conversation cannot be without."""
        for branch in self.branches:
            if branch.id == MAIN_BRANCH:
                return branch
        # A file that somehow lost it is repaired rather than rejected: the
        # alternative is a record that exists but can never be spoken to.
        branch = Branch(id=MAIN_BRANCH, name="main")
        self.branches.insert(0, branch)
        return branch

    def branch(self, branch_id: str | None) -> Branch | None:
        if not branch_id:
            return None
        for branch in self.branches:
            if branch.id == branch_id:
                return branch
        return None

    def active(self) -> Branch:
        """The branch new messages land in - and the one drawn on screen."""
        return self.branch(self.active_branch) or self.main

    def lineage(self, branch_id: str | None = None) -> list[Branch]:
        """A branch and its ancestors, oldest first.

        Loop-guarded: these ids come off disk, and a record whose parents
        point at each other should cost one transcript, not the process.
        """
        branch = self.branch(branch_id) or self.active()
        chain: list[Branch] = []
        seen: set[str] = set()
        node: Branch | None = branch
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = self.branch(node.parent)
        chain.reverse()
        return chain

    def transcript(self, branch_id: str | None = None) -> list[Message]:
        """Everything a branch has to show: the inherited past, then its own.

        This is the list that used to be `self.messages`, rebuilt. Each step
        down the chain cuts the accumulated prefix at the point that branch
        forked and appends what was said after it, so a fork from message 6
        shows those six and then diverges.
        """
        messages: list[Message] = []
        for node in self.lineage(branch_id):
            if node.parent:
                messages = messages[: node.forked_at]
            messages = messages + node.messages
        return messages

    @property
    def messages(self) -> list[Message]:
        """The active branch's transcript.

        Kept as a property so that everything written before branching existed
        - the usage report, the message count, the restore - goes on meaning
        what it meant: the conversation you are looking at.
        """
        return self.transcript()

    def history(self, branch_id: str | None = None) -> list[dict]:
        """The transcript as the model should see it: `[{role, content}, ...]`.

        Complete turns only. A user message whose answer never arrived is
        dropped along with the error it got, so the replayed conversation
        never contains a question left hanging.
        """
        replay: list[dict] = []
        pending_user: Message | None = None

        for message in self.transcript(branch_id):
            if message.role == "user":
                pending_user = None if message.error else message
            elif message.role == "assistant":
                if pending_user and not message.error:
                    replay.append({"role": "user", "content": pending_user.content})
                    replay.append({"role": "assistant", "content": message.content})
                pending_user = None

        return replay

    def usages(self, branch_id: str | None = None) -> list[dict]:
        """Every turn's reported cost along one branch, in order - day 8.

        The store does no arithmetic on these (that is `tokens.sum_usage`);
        it only knows which messages carry one. A failed turn has none,
        because nothing was billed for it.
        """
        return [m.usage for m in self.transcript(branch_id) if m.usage]

    def all_usages(self) -> list[dict]:
        """Every turn of every branch, each counted once - day 10.

        Not the same number as the one above, and the difference is the point
        of it. A branch's own total says what the conversation you are reading
        cost; this says what the *tree* cost, and the shared prefix appears in
        it once however many forks were made from it. Two branches of ten
        messages each on a shared past of twenty is not sixty messages' worth
        of spending, and a readout that implied it was would be arguing
        against branching on the strength of an arithmetic error.
        """
        return [m.usage for branch in self.branches for m in branch.messages if m.usage]

    def text(self) -> str:
        """Everything ever said in this chat, concatenated.

        The one thing a token count of "the whole conversation" needs, and
        the reason it is a method here rather than a loop in the server: it
        is every message, including the old ones the agent no longer replays
        and the failed ones it never did.
        """
        return "".join(m.content for m in self.messages)

    def checkpoint(self, checkpoint_id: str | None) -> Checkpoint | None:
        for checkpoint in self.checkpoints:
            if checkpoint.id == checkpoint_id:
                return checkpoint
        return None

    def children(self, branch_id: str) -> list[Branch]:
        return [b for b in self.branches if b.parent == branch_id]

    def next_id(self, prefix: str, taken) -> str:
        """`branch-2`, `cp-3` - the first number of its kind that is free."""
        used = set(taken)
        number = len(used) + 1
        while f"{prefix}-{number}" in used:
            number += 1
        return f"{prefix}-{number}"

    # -- storage -------------------------------------------------------------

    def to_dict(self) -> dict:
        """The record as it is written to disk.

        `main` is written where messages have always been written, and the
        forks go beside it. That is not nostalgia: it means every file this
        app has ever produced is still a valid file, and a conversation that
        has never been forked - which is most of them - looks on disk exactly
        as it did on day 7.
        """
        main = self.main
        data = {
            "id": self.id,
            "view": self.view,
            "settings": self.settings,
            "messages": [m.to_dict() for m in main.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if main.summary:
            data["summary"] = main.summary.to_dict()
        if main.facts:
            data["facts"] = main.facts.to_dict()
        forks = [b.to_dict() for b in self.branches if b.id != MAIN_BRANCH]
        if forks:
            data["branches"] = forks
        if self.checkpoints:
            data["checkpoints"] = [c.to_dict() for c in self.checkpoints]
        if self.active_branch != MAIN_BRANCH:
            data["active_branch"] = self.active_branch
        if self.task_id:
            data["task_id"] = self.task_id
        if self.proposals:
            data["proposals"] = self.proposals
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        view = data.get("view")
        main = Branch(
            id=MAIN_BRANCH,
            name="main",
            messages=[Message.from_dict(m) for m in data.get("messages") or []],
            summary=Summary.from_dict(data.get("summary") or {}),
            facts=Facts.from_dict(data.get("facts") or {}),
            created_at=str(data.get("created_at") or now_iso()),
        )
        branches = [main]
        for raw in data.get("branches") or []:
            branch = Branch.from_dict(raw)
            # Never a second main, and never a fork of nothing: both would
            # make `lineage` describe a tree the rest of the app cannot draw.
            if branch and branch.id != MAIN_BRANCH:
                branches.append(branch)

        known = {b.id for b in branches}
        active = str(data.get("active_branch") or MAIN_BRANCH)
        checkpoints = [
            checkpoint
            for raw in data.get("checkpoints") or []
            if (checkpoint := Checkpoint.from_dict(raw)) is not None
            and checkpoint.branch in known
        ]
        return cls(
            id=str(data["id"]),
            view=view if view in VIEWS else DEFAULT_VIEW,
            settings=data.get("settings") or {},
            branches=branches,
            active_branch=active if active in known else MAIN_BRANCH,
            checkpoints=checkpoints,
            # A task id is not checked against the tasks on disk here. The
            # store that owns tasks is a different one, and a chat pointing
            # at a task somebody deleted should read back as a chat with no
            # working layer, not as a record that refuses to load.
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            proposals=read_proposals(data.get("proposals")),
            created_at=str(data.get("created_at") or now_iso()),
            updated_at=str(data.get("updated_at") or now_iso()),
        )


def read_proposals(raw) -> list:
    """Pending suggestions off disk, normalised to the four fields they have.

    Read back through a known shape rather than passed on whole, for the
    reason `usage_block` gives in the server: these come off a file an older
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
        usage: dict | None = None,
        branch: str | None = None,
    ) -> Conversation:
        """Add one exchange - the question and what came back for it.

        `usage` rides on the assistant message rather than on the turn,
        because that is the message it paid for: the prompt tokens are what
        the whole replayed conversation cost to send, and the completion
        tokens are what this one answer cost to get back.

        Day 10: into one *branch* - the active one unless the caller names
        another. This is the only write in the app that appends to a
        transcript, so it is also the only place that has to know which of
        several transcripts is being spoken to.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                conversation = Conversation(id=validate_id(conversation_id), view=view)
            if settings is not None:
                conversation.settings = settings
            target = conversation.branch(branch) or conversation.active()
            target.messages.append(Message(role="user", content=user_message))
            target.messages.append(
                Message(role="assistant", content=answer, error=error, usage=usage)
            )
            return self.save(conversation)

    def clear(self, conversation_id: str) -> Conversation | None:
        """Drop the messages, keep the chat.

        What the "Clear context" button does. The record survives with its
        settings, so a cleared compare column is still there on the next
        reload with its parameters intact - it just has nothing to remember.

        The summary goes with them (day 9). It is a description of those exact
        messages, and a description that outlived the thing it described would
        be the one way this app could still remember something the user has
        explicitly told it to forget. The facts go for the same reason, and so
        - day 10 - do the branches and the checkpoints: a fork of a
        conversation that no longer exists is not a conversation anyone can
        read, and leaving one behind would mean "clear" had cleared the screen
        and not the store.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.branches = [Branch(id=MAIN_BRANCH, name="main")]
            conversation.active_branch = MAIN_BRANCH
            conversation.checkpoints = []
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

    # -- what a branch has made of its own past (days 9 and 10) --------------

    def save_summary(
        self, conversation_id: str, summary: Summary, branch: str | None = None
    ) -> Conversation | None:
        """Write down what the older part of a branch has been compressed into.

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
            (conversation.branch(branch) or conversation.active()).summary = summary
            return self.save(conversation)

    def clear_summary(self, conversation_id: str, branch: str | None = None) -> Conversation | None:
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
            (conversation.branch(branch) or conversation.active()).summary = None
            return self.save(conversation)

    def save_facts(
        self, conversation_id: str, facts: Facts | None, branch: str | None = None
    ) -> Conversation | None:
        """The branch's key-value memory, as it stands after one message.

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
            (conversation.branch(branch) or conversation.active()).facts = facts
            return self.save(conversation)

    def clear_facts(self, conversation_id: str, branch: str | None = None) -> Conversation | None:
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
            (conversation.branch(branch) or conversation.active()).facts = None
            return self.save(conversation)

    # -- branching (day 10) --------------------------------------------------

    def add_checkpoint(
        self,
        conversation_id: str,
        *,
        label: str = "",
        at: int | None = None,
        branch: str | None = None,
    ) -> tuple[Conversation, Checkpoint] | None:
        """Mark a place in a branch worth being able to come back to.

        `at` is a position in that branch's transcript - how many messages
        come before the mark - and defaults to the end of it, which is what
        "save here" means when you press it. Nothing is copied: the messages
        are already stored, and a checkpoint that duplicated them would be a
        second copy of the truth that could go stale.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            target = conversation.branch(branch) or conversation.active()
            length = len(conversation.transcript(target.id))
            index = length if at is None else max(0, min(int(at), length))
            checkpoint = Checkpoint(
                id=conversation.next_id("cp", [c.id for c in conversation.checkpoints]),
                branch=target.id,
                index=index,
                label=label.strip(),
            )
            conversation.checkpoints.append(checkpoint)
            return self.save(conversation), checkpoint

    def delete_checkpoint(self, conversation_id: str, checkpoint_id: str) -> Conversation | None:
        """Forget a bookmark. The branches made from it are untouched - they
        have their own record of where they started, and a branch that
        vanished because a marker was tidied away would be the worst surprise
        in the app."""
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            conversation.checkpoints = [
                c for c in conversation.checkpoints if c.id != checkpoint_id
            ]
            return self.save(conversation)

    def create_branch(
        self,
        conversation_id: str,
        *,
        checkpoint: str | None = None,
        at: int | None = None,
        branch: str | None = None,
        name: str = "",
        activate: bool = True,
    ) -> tuple[Conversation, Branch] | None:
        """Fork a conversation, from a checkpoint or from a bare position.

        The new branch stores no messages - it records whose past it shares
        and how much of it - so this is a write of a few dozen bytes whatever
        the size of the conversation being forked. Two calls with the same
        checkpoint give you the two branches the exercise asks for, sharing
        one prefix rather than two copies of it.

        What it *does* copy is what the parent had made of that past: the fact
        block, and the summary when it still fits. Both describe the messages
        the fork inherits, so starting a branch with an empty memory of a
        conversation it can see the whole of would be a needless second
        beginning. The summary is the conditional one - it speaks for a number
        of messages, and a fork from before the end of what it covers is a
        fork it is no longer true of, so there it is dropped and rebuilt.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None

            if checkpoint is not None:
                mark = conversation.checkpoint(checkpoint)
                if mark is None:
                    raise StoreError(f"No such checkpoint: {checkpoint}")
                parent = conversation.branch(mark.branch) or conversation.main
                index = mark.index
            else:
                parent = conversation.branch(branch) or conversation.active()
                length = len(conversation.transcript(parent.id))
                index = length if at is None else max(0, min(int(at), length))

            new_id = conversation.next_id("branch", [b.id for b in conversation.branches])
            fork = Branch(
                id=new_id,
                name=name.strip() or new_id.replace("-", " "),
                parent=parent.id,
                forked_at=index,
            )
            conversation.branches.append(fork)

            # Inherited memory. Read after the fork is in place, so the
            # transcript it is being measured against is the fork's own.
            inherited = parent.facts
            if inherited:
                fork.facts = Facts(
                    items=dict(inherited.items),
                    model=inherited.model,
                    # The spending stays with the branch that did it: a fork
                    # has made no requests of its own yet, and inheriting a
                    # total would double it the moment anyone added the two up.
                    updates=0,
                )
            summary = parent.summary
            if summary and summary.covers <= len(conversation.history(fork.id)):
                fork.summary = Summary(
                    text=summary.text,
                    covers=summary.covers,
                    model=summary.model,
                    compressions=0,
                )

            if activate:
                conversation.active_branch = fork.id
            return self.save(conversation), fork

    def activate_branch(self, conversation_id: str, branch_id: str) -> Conversation | None:
        """Switch which branch is being spoken to - and drawn.

        One field. That is the whole of "switching between branches", and it
        is worth saying why: because a branch keeps its own messages and its
        own memory, there is nothing to unload, merge or recompute. The next
        request simply assembles itself from a different transcript.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            if conversation.branch(branch_id) is None:
                raise StoreError(f"No such branch: {branch_id}")
            conversation.active_branch = branch_id
            return self.save(conversation)

    def delete_branch(self, conversation_id: str, branch_id: str) -> Conversation | None:
        """Throw a fork away - and only a fork with nothing hanging off it.

        `main` cannot go: it is the trunk every other branch's past is made
        of. Neither can a branch that has been forked from, because deleting
        it would leave its children inheriting a past that is no longer
        stored, and silently shortening two other conversations is not what
        anyone means by closing one.
        """
        with self._lock:
            conversation = self.load(conversation_id)
            if conversation is None:
                return None
            if branch_id == MAIN_BRANCH:
                raise StoreError("The main branch cannot be deleted.")
            target = conversation.branch(branch_id)
            if target is None:
                raise StoreError(f"No such branch: {branch_id}")
            if conversation.children(branch_id):
                raise StoreError(
                    "This branch has been forked from; delete the branches made "
                    "from it first."
                )
            conversation.branches = [b for b in conversation.branches if b.id != branch_id]
            conversation.checkpoints = [
                c for c in conversation.checkpoints if c.branch != branch_id
            ]
            if conversation.active_branch == branch_id:
                conversation.active_branch = target.parent or MAIN_BRANCH
            return self.save(conversation)

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


def tasks_dir_from_env() -> Path:
    """Where tasks are kept: `CHAT_TASKS_DIR`, or `data/tasks`."""
    configured = os.getenv(TASKS_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_TASKS_DIR


def memory_dir_from_env() -> Path:
    """Where the long-term layer is kept: `CHAT_MEMORY_DIR`, or `data/memory`."""
    configured = os.getenv(MEMORY_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_MEMORY_DIR


class TaskStore:
    """Tasks as JSON files in one directory - one file per piece of work.

    Structurally a copy of `ConversationStore`, down to the lock and the
    write-to-temp-then-`os.replace`, and that repetition is on purpose. The
    two stores hold different things with different lifetimes and there is
    no shared base class waiting to be extracted here - what they have in
    common is a *technique*, not a type, and the day the working layer needs
    an index and conversations do not, this file is where that happens
    without touching chats at all.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else tasks_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, task_id: str) -> Path:
        return self.directory / f"{validate_id(task_id)}.json"

    # -- reading -------------------------------------------------------------

    def load(self, task_id: str) -> Task | None:
        if not task_id:
            return None
        try:
            path = self.path_for(task_id)
        except StoreError:
            # An id that cannot name a file cannot name a task either, and a
            # chat pointing at one should lose its working layer rather than
            # its ability to answer.
            return None
        with self._lock:
            if not path.exists():
                return None
            return self._read(path)

    def all(self) -> list[Task]:
        """Every task, newest first - the order a selector wants them in."""
        with self._lock:
            tasks = [
                task
                for path in self.directory.glob("*.json")
                if (task := self._read(path)) is not None
            ]
        tasks.sort(key=lambda t: (t.created_at, t.id), reverse=True)
        return tasks

    def _read(self, path: Path) -> Task | None:
        try:
            return Task.from_dict(json.loads(path.read_text("utf-8")))
        except (ValueError, KeyError, OSError):
            return None

    # -- writing -------------------------------------------------------------

    def save(self, task: Task) -> Task:
        task.updated_at = now_iso()
        path = self.path_for(task.id)
        payload = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        return task

    def create(self, title: str) -> Task:
        """A new piece of work, under an id derived from the clock.

        The same id shape conversations use, so that one glance at
        `data/` tells you when a thing was started without opening it.
        """
        with self._lock:
            taken = {path.stem for path in self.directory.glob("*.json")}
            base = f"task-{int(datetime.now(timezone.utc).timestamp() * 1000):x}"
            task_id = base
            suffix = 1
            while task_id in taken:
                suffix += 1
                task_id = f"{base}-{suffix}"
            return self.save(Task(id=task_id, title=" ".join(str(title or "").split())[:120]))

    def delete(self, task_id: str) -> bool:
        path = self.path_for(task_id)
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    # -- the layer itself ----------------------------------------------------

    def add_item(self, task_id: str, item: MemoryItem) -> Task | None:
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            task.items = upsert_item(task.items, item)
            return self.save(task)

    def remove_item(self, task_id: str, key: str) -> Task | None:
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            task.items = [item for item in task.items if item.key != key]
            return self.save(task)

    # -- the machine ---------------------------------------------------------
    #
    # Four methods, and every change a stage can undergo goes through one of
    # them. That is the point of putting them here rather than letting routes
    # assign to `task.phase`: `move` is the only way a stage reaches the disk,
    # and `move` cannot be called without `phases.check` having said yes.

    def suggest(self, task_id: str, target: str, why: str = "") -> "Task | None":
        """Record that the agent thinks the work has moved on.

        Not a move. The agent no longer makes one: it says where it thinks the
        work is and a person presses the button, which is the same division of
        labour day 11 drew for memories and for the same reason - the thing
        that changes state should be a decision somebody made, not a sentence
        a model produced. What is different here is that the machine has
        already ruled on it: a suggestion is only ever raised for a stage that
        is actually reachable, so accepting one can never produce a jump.
        """
        with self._lock:
            task = self.load(task_id)
            if task is None or target not in phases.PHASE_INFO or target == task.phase:
                return task
            task.suggested = {"to": target, "why": " ".join(str(why or "").split())[:120],
                              "at": now_iso()}
            return self.save(task)

    def dismiss_suggestion(self, task_id: str) -> "Task | None":
        """“Not yet.” Nothing is recorded: nothing happened."""
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            task.suggested = None
            return self.save(task)

    def move(
        self,
        task_id: str,
        target: str,
        by: str = phases.BY_USER,
        why: str = "",
        confirm: bool = False,
    ) -> tuple["Task | None", phases.Decision]:
        """Move a task to another stage, if the machine allows it.

        Returns the task and the decision, always both. A refusal is not an
        exception and not a `None`: it is an outcome with a reason attached,
        it gets written into the log like any other, and the caller wants the
        record back either way - the panel redraws from it, and what it is
        drawing after a refusal is *why*.

        Going backwards unticks the required steps of the stage being
        returned to, and that is the one rule here that is not in
        `phases.check`. It belongs to writing rather than to permission: you
        go back to planning because the plan was wrong, so a `plan` still
        ticked from the first pass would let the task walk straight forward
        again through a gate that has stopped meaning anything. Optional
        steps are left alone - the constraints you wrote down are still
        written down.
        """
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None, phases.Decision(False, phases.UNKNOWN, "No such task.")

            decision = phases.check(task, target)
            # `confirm` is a person saying "yes, this stage is done" - and
            # ticking the boxes is exactly what that sentence means, so it
            # ticks them and asks again. It can only ever answer one of the
            # five rules in `check`, the checklist one, which is the only rule
            # that was waiting for a human judgement in the first place. An
            # edge that does not exist still does not exist, a paused task is
            # still paused, and there is no flag anywhere that gets past
            # those.
            if not decision.ok and confirm and decision.code == phases.INCOMPLETE:
                for step in phases.missing_required(task, task.phase):
                    task.tick(step.key, True)
                self.save(task)
                decision = phases.check(task, target)
            if not decision.ok:
                # Nothing is written, and nothing is written down. A refused
                # move is not an event in the life of this task: it is a thing
                # that did not happen, and the log is a record of what did.
                #
                # Keeping them was the first design here, on the argument that
                # a refusal is the most informative moment the machine
                # produces. It is - to whoever is reading the code. To whoever
                # is reading the task, it is a history full of moves that were
                # never made, which is a worse record than one that is simply
                # true. The enforcement is the point; the paperwork is not.
                return task, decision

            source = task.phase
            # A suggestion is about the stage the task was in. Moving out of
            # that stage answers it, whichever way it was answered.
            task.suggested = None
            if not phases.is_forward(source, target):
                required = {s.key for s in phases.STEPS.get(target, ()) if s.required}
                kept = [k for k in task.ticked(target) if k not in required]
                task.steps = dict(task.steps or {})
                if kept:
                    task.steps[target] = kept
                else:
                    task.steps.pop(target, None)
            task.phase = target
            task.record(phases.MOVE, by, **{"from": source, "to": target, "why": why})
            return self.save(task), decision

    def set_step(self, task_id: str, key: str, done: bool = True) -> Task | None:
        """Tick or untick one step of the stage the task is in.

        Not logged. A checklist is worked through, not decided - thirty tick
        events would bury the four transitions that are worth reading, and
        the ticks themselves are already on screen.
        """
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            task.tick(key, done)
            return self.save(task)

    def set_paused(self, task_id: str, paused: bool, note: str = "") -> Task | None:
        """Freeze the machine where it stands, or let it go again.

        Pausing is not a stage and deliberately not one. A stage says what
        kind of work this is; a pause says nobody is doing it right now, and
        those are independent - you can pause in any of the four, which is
        exactly what the day asks to be able to check. Making it a fifth
        stage would mean inventing edges back out of it to all four and
        losing the answer to "paused *from where*".

        What it stops is the machine, not the memory. `phases.check` refuses
        every transition while it is set, the agent is told to hold, and the
        task's items keep going up with every request - so resuming needs no
        catching up, which is the other half of what the day asks to check.
        """
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            if bool(paused) != bool(task.paused):
                task.record(phases.PAUSE if paused else phases.RESUME, phases.BY_USER,
                            **{"from": task.phase, "to": task.phase, "why": note})
            task.paused = bool(paused)
            # A note outlives the pause it was written for only if somebody
            # wrote a new one: resuming clears it, because "waiting for the
            # design review" is false the moment the work starts again.
            task.note = " ".join(str(note or "").split())[:200] if paused else ""
            return self.save(task)

    def set_note(self, task_id: str, note: str) -> Task | None:
        """What this work is waiting on, in a line. Free text, no rules."""
        with self._lock:
            task = self.load(task_id)
            if task is None:
                return None
            task.note = " ".join(str(note or "").split())[:200]
            return self.save(task)


class LongTermStore:
    """The layer with no scope: one file, everything, forever.

    A store rather than a module-level dict because it has to survive a
    restart, and one file rather than one per item because the whole layer
    is read on every single request - `memory.recall` needs all of it in
    hand to decide which few lines to send, so splitting it up would buy
    nothing and cost a directory listing per message.
    """

    FILENAME = "long_term.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else memory_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / self.FILENAME
        self._lock = threading.RLock()

    def load(self) -> list:
        """Everything on file. An unreadable file reads as an empty layer.

        Empty rather than an exception, for the reason `ConversationStore`
        gives: a corrupt record should cost you the thing it held, not the
        ability to use the app. The file is still on disk to be looked at.
        """
        with self._lock:
            if not self.path.exists():
                return []
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                return []
        return read_items((data or {}).get("items") if isinstance(data, dict) else data)

    def save(self, items: list) -> list:
        payload = json.dumps(
            {"items": [item.to_dict() for item in items], "updated_at": now_iso()},
            ensure_ascii=False,
            indent=2,
        )
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        return items

    def add_item(self, item: MemoryItem) -> list:
        with self._lock:
            return self.save(upsert_item(self.load(), item))

    def remove_item(self, key: str) -> list:
        with self._lock:
            return self.save([item for item in self.load() if item.key != key])


# --------------------------------------------------------------------------
# Day 12: the personality profiles
# --------------------------------------------------------------------------


@dataclass
class Profile:
    """One named set of standing instructions about how to answer.

    A record with no behaviour in it. What the fields *mean*, which of them
    exist and how they are rendered into a prompt all live in
    `personality.py`, which this file does not import - `store.py` imports
    nothing from the app, and keeping it that way is what lets the UI tests
    build records without an API key. So `fields` is a plain `dict[str, str]`
    here: whatever the server cleaned and handed down, written back out
    unchanged.

    `updated_at` is separate from `created_at` because a profile is edited -
    that is the whole difference between this and a `MemoryItem`, which is
    filed once and afterwards only replaced.
    """

    id: str
    name: str
    fields: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "fields": dict(self.fields or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile | None":
        """One profile off disk, or None if it is not one.

        A profile with no id is dropped for `MemoryItem.from_dict`'s reason:
        it could never be activated, edited or deleted, because every one of
        those is addressed by id. An empty *name* is survivable and an empty
        set of fields is a legitimate state - a profile you have started and
        not finished filling in - so neither is grounds for discarding it.
        """
        if not isinstance(data, dict):
            return None
        profile_id = str(data.get("id") or "").strip()
        if not profile_id:
            return None
        raw = data.get("fields")
        fields = {
            str(k): str(v)
            for k, v in (raw.items() if isinstance(raw, dict) else ())
            if str(v or "").strip()
        }
        created = str(data.get("created_at") or "") or now_iso()
        return cls(
            id=profile_id,
            name=str(data.get("name") or "").strip(),
            fields=fields,
            created_at=created,
            updated_at=str(data.get("updated_at") or "") or created,
        )


@dataclass
class Personality:
    """Every profile, and which one is switched on.

    One record rather than two, because `active` is meaningless without the
    list it points into: an id naming a profile that has been deleted is not
    a state anything should have to handle, and keeping the pair together in
    one file makes it one that cannot arise. `resolve` is where that is
    enforced on the way in.
    """

    profiles: list = field(default_factory=list)
    active: str | None = None

    def resolve(self) -> "Personality":
        """Self, with `active` guaranteed to name a profile that exists."""
        ids = {p.id for p in self.profiles}
        if self.active not in ids:
            self.active = None
        return self

    def find(self, profile_id: str | None):
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def current(self):
        """The profile in force, or None - which is a supported answer.

        Off is a first-class state, not a missing value. It is how the day is
        demonstrated: ask a question with no profile, switch one on, ask it
        again.
        """
        return self.find(self.active)

    def to_dict(self) -> dict:
        return {
            "profiles": [p.to_dict() for p in self.profiles],
            "active": self.active,
            "updated_at": now_iso(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Personality":
        if not isinstance(data, dict):
            return cls()
        raw = data.get("profiles")
        profiles = []
        for entry in raw if isinstance(raw, list) else ():
            profile = Profile.from_dict(entry)
            if profile is not None:
                profiles.append(profile)
        active = data.get("active")
        return cls(
            profiles=profiles[:MAX_PROFILES],
            active=str(active) if active else None,
        ).resolve()


def personality_dir_from_env() -> Path:
    """Where profiles are kept: `CHAT_PERSONALITY_DIR`, or `data/personality`."""
    configured = os.getenv(PERSONALITY_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_PERSONALITY_DIR


class PersonalityStore:
    """The profiles, on disk: one file, read on every request.

    Structurally `LongTermStore` again - one file, a lock, write-to-temp and
    `os.replace` - and read just as often, because unlike a memory layer this
    one contributes to *every* request rather than to the ones it matches.
    That is also why there is no per-conversation copy of any of this: a
    profile is switched on once and applies everywhere, and a chat that
    remembered which personality it was started under would be a fourth scope
    nobody asked for.
    """

    FILENAME = "profiles.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else personality_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / self.FILENAME
        self._lock = threading.RLock()

    def load(self) -> Personality:
        """Everything on file. An unreadable file reads as no profiles at all."""
        with self._lock:
            if not self.path.exists():
                return Personality()
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                return Personality()
        return Personality.from_dict(data)

    def save(self, record: Personality) -> Personality:
        record.resolve()
        payload = json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        return record

    def seed(self, profiles: list) -> Personality:
        """Write these profiles, but only if nothing has ever been written.

        The condition is the *file*, not an empty list. Deleting every profile
        is a thing a person can decide, and an app that quietly put three back
        on the next restart would be overruling them once a day.
        """
        with self._lock:
            if self.path.exists():
                return self.load()
            return self.save(Personality(profiles=list(profiles), active=None))

    def upsert(self, profile: Profile) -> Personality:
        """File a profile, replacing any with the same id.

        Unlike `upsert_item` the replacement keeps its position in the list,
        because this list is read by a person choosing from it: a profile that
        jumped to the bottom every time it was edited would make the menu
        reorder itself under the cursor.
        """
        with self._lock:
            record = self.load()
            for index, existing in enumerate(record.profiles):
                if existing.id == profile.id:
                    profile.created_at = existing.created_at
                    record.profiles[index] = profile
                    return self.save(record)
            if len(record.profiles) >= MAX_PROFILES:
                raise StoreError(f"No room for another profile (limit {MAX_PROFILES})")
            record.profiles.append(profile)
            return self.save(record)

    def remove(self, profile_id: str) -> Personality:
        """Delete a profile. Deleting the active one switches personality off."""
        with self._lock:
            record = self.load()
            record.profiles = [p for p in record.profiles if p.id != profile_id]
            return self.save(record)

    def activate(self, profile_id: str | None) -> Personality:
        """Switch a profile on, or - with None - switch personality off."""
        with self._lock:
            record = self.load()
            record.active = profile_id or None
            return self.save(record)
