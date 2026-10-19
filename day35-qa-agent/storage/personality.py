"""Day 12: the personality profiles - one file, read on every request.

`data/personality/profiles.json`, or wherever `CHAT_PERSONALITY_DIR` points.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from storage.common import DATA_DIR, StoreError, now_iso

#: Day 12, and a directory of its own rather than a corner of `data/memory/`.
#: The long-term layer and the personality profiles have the same lifetime -
#: both last until somebody deletes them - but they are not the same kind of
#: thing, and `data/memory/` is where a reader goes to find out what the agent
#: *knows*.
PERSONALITY_DIR_ENV = "CHAT_PERSONALITY_DIR"
DEFAULT_PERSONALITY_DIR = DATA_DIR / "personality"

#: How many personality profiles are kept. Low on purpose, and for a different
#: reason than `MAX_ITEMS`: that one is a backstop against a layer growing
#: into a second transcript, while this is a claim about what profiles are for.
#: One is switched to deliberately, by name, from a list you can read at a
#: glance - thirty of them would be a settings file with a worse interface.
MAX_PROFILES = 12


@dataclass
class Profile:
    """One named set of standing instructions about how to answer.

    A record with no behaviour in it. What the fields *mean*, which of them
    exist and how they are rendered into a prompt all live in
    `core.personality`, which this file does not import - the stores import
    nothing from the app but `core.phases`, and keeping it that way is what
    lets the tests build records without an API key. So `fields` is a plain
    `dict[str, str]` here: whatever the core cleaned and handed down, written
    back out unchanged.

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

