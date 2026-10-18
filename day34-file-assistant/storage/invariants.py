"""Day 14: the invariants - the rules an answer may not break. One file, read on every request.

`data/invariants/rules.json`, or wherever `CHAT_INVARIANTS_DIR` points.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from storage.common import DATA_DIR, StoreError, now_iso

#: Day 14, and a directory of its own for the same reason profiles have one.
#: An invariant has a profile's lifetime and a profile's author, so the
#: temptation is to file it next to one - and the whole of day 14 is that
#: those two are not the same kind of thing. `data/personality/` holds what
#: somebody wants; this holds what the work is committed to.
INVARIANTS_DIR_ENV = "CHAT_INVARIANTS_DIR"
DEFAULT_INVARIANTS_DIR = DATA_DIR / "invariants"

#: How many invariants are kept, and the argument is not `MAX_PROFILES`' one.
#: A profile list is capped because a menu of thirty is unreadable. A rule list
#: is capped because forty rules are not a stricter agent - they are a set that
#: contradicts itself somewhere, with nobody able to say where. Twelve is about
#: as many as one person can hold in mind well enough to notice when two of
#: them disagree. `invariants.MAX_RULES` says the same number for the half of
#: the app that is allowed to have an opinion about why.
MAX_RULES = 12


@dataclass
class Rule:
    """One invariant: a sentence the work is committed to, and why.

    A record with no behaviour in it, exactly as `Profile` is. What the kinds
    *are*, how a rule is rendered into a prompt and what counts as breaking
    one all live in `core.invariants`, which this file does not import - so
    `kind` is a plain string here and `markers` a plain list of them, whatever
    the core cleaned and handed down, written back out unchanged.

    `because` is stored beside the rule rather than folded into its text
    because the two are read by different people at different times. The text
    is what an answer is held against; the `because` is what a refusal quotes
    when somebody asks why not, and a rule whose reason nobody wrote down is
    one that gets argued with every time it bites.
    """

    id: str
    kind: str = ""
    text: str = ""
    because: str = ""
    markers: list = field(default_factory=list)
    enabled: bool = True
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "because": self.because,
            "markers": list(self.markers or []),
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Rule | None":
        """One rule off disk, or None if it is not one.

        A rule with no id is dropped for `Profile.from_dict`'s reason - every
        way of addressing one goes through the id. A rule with no *text* is
        dropped too, which is where this differs: a half-filled profile is a
        legitimate state somebody is in the middle of, while a rule with
        nothing in it would be numbered, sent and enforced against, and there
        is nothing there to enforce.
        """
        if not isinstance(data, dict):
            return None
        rule_id = str(data.get("id") or "").strip()
        text = str(data.get("text") or "").strip()
        if not rule_id or not text:
            return None
        raw = data.get("markers")
        markers = [str(m).strip() for m in (raw if isinstance(raw, list) else ()) if str(m).strip()]
        created = str(data.get("created_at") or "") or now_iso()
        return cls(
            id=rule_id,
            kind=str(data.get("kind") or "").strip(),
            text=text,
            because=str(data.get("because") or "").strip(),
            markers=markers,
            enabled=bool(data.get("enabled", True)),
            created_at=created,
            updated_at=str(data.get("updated_at") or "") or created,
        )


@dataclass
class Invariants:
    """Every rule, and whether the set as a whole is in force.

    One record rather than a list, for the reason `Personality` keeps `active`
    beside its profiles: the switch is meaningless without the thing it
    switches. It is two switches deep on purpose - the set has one and each
    rule has one - and the two answer different questions. A rule switched off
    is one that has stopped being true; the set switched off is the day
    itself turned off, which is how it gets demonstrated: ask with the rules
    off, switch them on, ask again.
    """

    rules: list = field(default_factory=list)
    enabled: bool = True

    def find(self, rule_id: str | None):
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None

    def to_dict(self) -> dict:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "enabled": bool(self.enabled),
            "updated_at": now_iso(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Invariants":
        if not isinstance(data, dict):
            return cls()
        raw = data.get("rules")
        rules = []
        for entry in raw if isinstance(raw, list) else ():
            rule = Rule.from_dict(entry)
            if rule is not None:
                rules.append(rule)
        return cls(
            rules=rules[:MAX_RULES],
            # Absent reads as on. A file written before this field existed is
            # a file whose rules somebody meant, and defaulting it to off
            # would silently stop enforcing them.
            enabled=bool(data.get("enabled", True)),
        )


def invariants_dir_from_env() -> Path:
    """Where the rules are kept: `CHAT_INVARIANTS_DIR`, or `data/invariants`."""
    configured = os.getenv(INVARIANTS_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_INVARIANTS_DIR


class InvariantStore:
    """The rules, on disk: one file, read on every request.

    Structurally `PersonalityStore` again - one file, a lock, write-to-temp
    and `os.replace` - and read exactly as often, because this one also
    contributes to every request rather than to the ones it matches. There is
    no per-conversation copy for the same reason there is none of a profile:
    a rule the work is committed to does not stop applying because you opened
    a new chat, and a chat that remembered which rules were in force when it
    started would let you escape one by starting another.
    """

    FILENAME = "rules.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else invariants_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / self.FILENAME
        self._lock = threading.RLock()

    def load(self) -> Invariants:
        """Everything on file. An unreadable file reads as no rules at all.

        Which is the one read in this class that is arguably the wrong way
        round: everywhere else today an invariant fails closed. It cannot here
        - a file that will not parse has no rules *to* enforce, and the
        alternative to returning an empty set is refusing to answer anything
        at all until somebody fixes a file by hand.
        """
        with self._lock:
            if not self.path.exists():
                return Invariants()
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                return Invariants()
        return Invariants.from_dict(data)

    def save(self, record: Invariants) -> Invariants:
        payload = json.dumps(record.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        return record

    def seed(self, rules: list) -> Invariants:
        """Write these rules, but only if nothing has ever been written.

        The condition is the *file*, not an empty list - `PersonalityStore.
        seed`'s rule, and it matters more here. Deleting every shipped rule is
        exactly what somebody does on their way to writing their own, and an
        app that put two back on the next restart would be adding rules to
        their project that nobody agreed to.
        """
        with self._lock:
            if self.path.exists():
                return self.load()
            # Off, and see `invariants.SEED_RULES` for why: these describe the
            # app they shipped with, and enforcing them against whatever the
            # user is actually building would be the wrong default in the one
            # direction this day cannot afford to be wrong in.
            return self.save(Invariants(rules=list(rules), enabled=False))

    def upsert(self, rule: Rule) -> Invariants:
        """File a rule, replacing any with the same id.

        The replacement keeps its position, as `PersonalityStore.upsert` does,
        and here that is not only about a menu holding still: position within
        a kind is what the prompt numbers, so a rule that jumped to the end
        when it was edited would renumber the rules either side of it and make
        every earlier refusal in the transcript cite the wrong line.
        """
        with self._lock:
            record = self.load()
            for index, existing in enumerate(record.rules):
                if existing.id == rule.id:
                    rule.created_at = existing.created_at
                    record.rules[index] = rule
                    return self.save(record)
            if len(record.rules) >= MAX_RULES:
                raise StoreError(f"No room for another invariant (limit {MAX_RULES})")
            record.rules.append(rule)
            return self.save(record)

    def remove(self, rule_id: str) -> Invariants:
        """Delete a rule. There is nothing else to fix up: unlike a profile,
        no pointer anywhere names one."""
        with self._lock:
            record = self.load()
            record.rules = [r for r in record.rules if r.id != rule_id]
            return self.save(record)

    def set_enabled(self, enabled: bool) -> Invariants:
        """Switch the whole set on or off."""
        with self._lock:
            record = self.load()
            record.enabled = bool(enabled)
            return self.save(record)

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> Invariants:
        """Switch one rule on or off, leaving it on file either way."""
        with self._lock:
            record = self.load()
            rule = record.find(rule_id)
            if rule is None:
                return record
            rule.enabled = bool(enabled)
            rule.updated_at = now_iso()
            return self.save(record)

