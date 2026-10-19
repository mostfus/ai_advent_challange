"""Personality profiles: how you want to be answered, as against what the agent knows.

Day 12. Five operations, and none of them takes a conversation id: a profile
is not a property of a chat, it is one object the whole app runs under.
Every write returns the whole panel (`report`) - a profile is small, and
redrawing from the reply is cheaper to get right than working out which half
of the screen an edit invalidated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import personality as personality_module
from core.errors import Invalid, NotFound
from storage import MAX_PROFILES, Personality, Profile, StoreError

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


class PersonalityService:
    """The profiles, over `rt.stores.personalities`."""

    def __init__(self, rt: "AgentRuntime") -> None:
        self.rt = rt

    @property
    def store(self):
        return self.rt.stores.personalities

    def seed(self) -> None:
        """Day 12's three seed profiles, written once - none of them switched on.

        Three profiles far enough apart that switching between them visibly
        changes an answer. The app still starts as an unconfigured assistant,
        because "before" is half of what there is to see.
        """
        self.store.seed(
            [
                Profile(
                    id=personality_module.slug(seed["name"]),
                    name=seed["name"],
                    fields=personality_module.clean_fields(seed["fields"]),
                )
                for seed in personality_module.SEED_PROFILES
            ]
        )

    @staticmethod
    def profile_report(profile: Profile | None) -> dict | None:
        """One profile, as the panel draws it.

        `rendered` is the profile as the model will actually see it, and it is
        here rather than being rebuilt in the browser for the reason every other
        readout in this file is served rather than recomputed: a page that shows
        its own idea of what was sent cannot be used to find out what was sent.
        """
        if profile is None:
            return None
        return {
            "id": profile.id,
            "name": profile.name,
            # Cleaned rather than raw: a profile written under the first shape of
            # this day has its four old fields folded into `style` by
            # `clean_fields`, and the form has to be handed what the prompt will
            # actually say, not what happens to be on disk.
            "fields": personality_module.clean_fields(profile.fields),
            "summary": personality_module.summary(profile),
            # Which of the three are filled in, so a half-written profile looks
            # half-written in the list instead of looking finished with a short
            # summary. The panel draws these as chips under the name.
            "filled": personality_module.filled(profile),
            "rendered": personality_module.render(profile),
            "updated_at": profile.updated_at,
        }

    def report(self, record: Personality | None = None) -> dict:
        """Every profile, which one is on, and the block that goes with requests.

        `block` is what the *next* request will carry rather than what the last
        one did, and the two are the same thing here - unlike every layer in
        `memory_report`, nothing about a profile depends on what was asked, so
        there is no version of this that is only knowable after the fact.

        Which is also why the whole of it comes back from every write below: a
        profile is small, the panel is one list, and redrawing from the reply is
        cheaper to get right than working out which half of the screen an edit
        invalidated (`memory` endpoints make the same trade, for the same reason).
        """
        record = record if record is not None else self.store.load()
        return {
            "profiles": [self.profile_report(profile) for profile in record.profiles],
            "active": record.active,
            "limit": MAX_PROFILES,
            "room": max(0, MAX_PROFILES - len(record.profiles)),
            "block": personality_module.render(record.current()),
        }

    def create(self, name: str = "", fields: dict | None = None) -> dict:
        """A new profile. Not activated by it - creating and switching are two
        decisions, and an edit that silently changed how every chat answers would
        be the surprise this whole day is arguing against."""
        name = personality_module.clean_name(name)
        if not name:
            raise Invalid("A profile needs a name")
        record = self.store.load()
        profile = Profile(
            id=personality_module.unique_id(name, [p.id for p in record.profiles]),
            name=name,
            fields=personality_module.clean_fields(fields or {}),
        )
        try:
            record = self.store.upsert(profile)
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        return self.report(record)

    def update(self, profile_id: str, name: str = "", fields: dict | None = None) -> dict:
        """Edit a profile in place, id and all.

        The id does not follow the name, and that is deliberate: renaming "Terse"
        to "Very terse" must not detach it from `active`, or renaming the profile
        you are using would quietly switch personality off.
        """
        record = self.store.load()
        existing = record.find(profile_id)
        if existing is None:
            raise NotFound(f"No such profile: {profile_id}")
        name = personality_module.clean_name(name) or existing.name
        if not name:
            raise Invalid("A profile needs a name")
        updated = Profile(
            id=existing.id,
            name=name,
            fields=personality_module.clean_fields(fields or {}),
            created_at=existing.created_at,
        )
        try:
            record = self.store.upsert(updated)
        except StoreError as exc:
            raise Invalid(str(exc)) from exc
        return self.report(record)

    def activate(self, profile_id: str | None) -> dict:
        """Switch a profile on, or switch personality off with a null id.

        Nothing is copied into any conversation by this, which is the part worth
        saying out loud: every chat on the machine answers differently from the
        next message onwards, including chats that were open at the time. A
        profile is applied at the moment a request is assembled and is stored in
        no transcript, so there is no such thing as a message that "was sent under
        the old profile" to keep consistent with.
        """
        profile_id = (profile_id or "").strip() or None
        if profile_id is not None and self.store.load().find(profile_id) is None:
            raise NotFound(f"No such profile: {profile_id}")
        return self.report(self.store.activate(profile_id))

    def delete(self, profile_id: str) -> dict:
        """Delete a profile. Deleting the active one leaves personality switched
        off rather than falling back to another - `Personality.resolve` does that,
        and silently answering under somebody else's profile would be worse than
        answering under none."""
        return self.report(self.store.remove(profile_id))
