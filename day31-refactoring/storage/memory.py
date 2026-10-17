"""The two memory layers that outlive a conversation: tasks and the long-term layer.

Day 11. Everything in `storage.conversations` is scoped to one chat and dies
with it; a `Task` is scoped to a piece of work that may take several chats,
and the long-term layer is scoped to nothing at all - it is simply always
there. They are stored the same way for the same reasons (one JSON file,
written whole, replaced atomically), in `data/tasks/` and
`data/memory/long_term.json`, and they hold the same `MemoryItem` because the
difference between the two layers is where the file is, not what is in it.
`core.memory` decides what gets read back out of them; this module only keeps
them. `CHAT_TASKS_DIR` and `CHAT_MEMORY_DIR` point them elsewhere.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The one module this package imports out of the app, and day 13 is why. A
# task can now be in a stage, and which stage may follow which is a rule
# rather than a field - so the rule has to be enforced on the way *into* the
# file, not by whoever remembered to ask first. `TaskStore.move` calls
# `phases.check`, which means there is no way to write a stage to disk that
# the machine did not allow.
from core import phases
from storage.common import DATA_DIR, StoreError, now_iso, validate_id

#: Day 11's two layers, and where they live. Separate environment variables
#: rather than one, so the UI tests can redirect each independently.
TASKS_DIR_ENV = "CHAT_TASKS_DIR"
DEFAULT_TASKS_DIR = DATA_DIR / "tasks"
MEMORY_DIR_ENV = "CHAT_MEMORY_DIR"
DEFAULT_MEMORY_DIR = DATA_DIR / "memory"

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

