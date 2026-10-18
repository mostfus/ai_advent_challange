"""Invariants: the rules an answer may not break - the operations on them.

Day 14. The whole set comes back from every write (`report`): switching a
single rule off renumbers all the rules after it, so there is no such thing
as a partial update to this panel. What the rules do to a turn - sent with
every request, and the answer read back against them - is `core.turn`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import invariants as invariants_module
from core.errors import Invalid, NotFound
from storage import MAX_RULES, Invariants, Rule, StoreError

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


class InvariantService:
    """The rules, over `rt.stores.invariants`."""

    def __init__(self, rt: "AgentRuntime") -> None:
        self.rt = rt

    @property
    def store(self):
        return self.rt.stores.invariants

    def seed(self) -> None:
        """Day 14's two seed rules, written once - and switched off.

        They are there so the panel teaches the shape of a rule before anybody
        has written one - and not in force, because they describe *this* app
        and would otherwise hold somebody's unrelated project to the
        architecture of the thing they just cloned.
        """
        self.store.seed(
            [
                Rule(
                    id=invariants_module.unique_id(seed["text"], []),
                    **invariants_module.clean_rule(seed),
                )
                for seed in invariants_module.SEED_RULES
            ]
        )

    @staticmethod
    def rule_report(record: Invariants, rule: Rule) -> dict:
        """One invariant, as the panel draws it.

        `number` is served rather than derived in the browser, and it is the one
        field here that would be easy to get wrong locally. The numbering is
        across the whole live set in `KINDS` order, it skips rules that are
        switched off, and it is what the prompt, the watcher's verdict and every
        refusal in the transcript all cite. A page that counted its own rows would
        eventually label a rule `3` while the model was being told it was `2`.
        """
        return {
            "id": rule.id,
            "kind": invariants_module.clean_kind(rule.kind),
            "text": rule.text,
            "because": rule.because,
            "markers": list(rule.markers or []),
            "enabled": bool(rule.enabled),
            "number": invariants_module.number_of(record, rule.id),
            "summary": invariants_module.summary(rule),
            "updated_at": rule.updated_at,
        }

    def report(self, record: Invariants | None = None) -> dict:
        """Every rule, whether the set is on, and the block that goes with requests.

        The whole of it comes back from every write below, for `personality_report`'s
        reason and one more of its own: switching a single rule off renumbers all
        the rules after it, so there is no such thing as a partial update to this
        panel. Returning a delta would mean the page recomputing the numbering,
        which is the one thing it must not do.
        """
        record = record if record is not None else self.store.load()
        return {
            "rules": [self.rule_report(record, rule) for rule in record.rules],
            "enabled": bool(record.enabled),
            "in_force": len(invariants_module.active(record)),
            "limit": MAX_RULES,
            "room": max(0, MAX_RULES - len(record.rules)),
            "block": invariants_module.render(record),
        }

    def create(self, data: dict) -> dict:
        """Write a new invariant. Created in force, unlike a personality profile.

        That asymmetry is deliberate and it is about what the two things are. A
        profile created and not activated is a draft you are trying out; an
        invariant is a statement that the work is *already* committed to
        something, and one filed switched-off would be a rule nobody is keeping
        that also is not being enforced - a note, written in the panel for rules.
        """
        cleaned = invariants_module.clean_rule(data)
        if not cleaned:
            raise Invalid("An invariant needs something in it")
        record = self.store.load()
        rule = Rule(
            id=invariants_module.unique_id(cleaned["text"], [r.id for r in record.rules]),
            **cleaned,
        )
        try:
            return self.report(self.store.upsert(rule))
        except StoreError as exc:
            raise Invalid(str(exc))

    def update(self, rule_id: str, data: dict) -> dict:
        """Edit an invariant, keeping its id.

        The id survives a rewrite for `update_profile`'s reason and a sharper one:
        an id here is derived from the rule's own text, so re-deriving it on every
        edit would mean fixing a typo in a rule silently replaced it with a
        different rule - and `upsert` would then file the new one at the end,
        renumbering everything after it.
        """
        record = self.store.load()
        existing = record.find(rule_id)
        if existing is None:
            raise NotFound(f"No such invariant: {rule_id}")
        cleaned = invariants_module.clean_rule(data)
        if not cleaned:
            raise Invalid("An invariant needs something in it")
        rule = Rule(id=existing.id, created_at=existing.created_at, **cleaned)
        try:
            return self.report(self.store.upsert(rule))
        except StoreError as exc:
            raise Invalid(str(exc))

    def toggle(self, rule_id: str, enabled: bool) -> dict:
        """Switch one rule off without deleting it.

        The difference between this and `delete_rule` is the difference between a
        rule that has stopped being true and one that was never right. Both leave
        the answer unconstrained; only one of them is still on the screen a
        fortnight later when somebody asks why the agent started proposing
        databases again.
        """
        if self.store.load().find(rule_id) is None:
            raise NotFound(f"No such invariant: {rule_id}")
        return self.report(self.store.set_rule_enabled(rule_id, enabled))

    def set_enabled(self, enabled: bool) -> dict:
        """Switch the whole set on or off.

        Off is a state rather than an empty list - day 12's rule about `active`,
        and here it is what makes the day demonstrable at all: ask a question with
        the rules off, switch them on, ask it again. Nothing is deleted either
        way, so switching back on restores exactly the set that was there.
        """
        return self.report(self.store.set_enabled(enabled))

    def delete(self, rule_id: str) -> dict:
        """Delete a rule. Nothing points at one, so nothing has to be fixed up."""
        return self.report(self.store.remove(rule_id))

    def scan(self, text: str) -> dict:
        """What the deterministic half alone sees in some text.

        The smallest endpoint in the file and the one that does the most to keep
        this day honest. `invariants.scan` is the part that costs nothing and
        needs no model, so it is the part that can be *tested* - the UI suite runs
        against a dummy key where every model call is a 401, and without this
        there would be no way to assert that any of the checking works.

        It returns hits, not violations, and the difference is the module's whole
        argument: "we are not using Postgres here" hits the marker and breaks no
        rule. What this says is *where to look*, which is exactly what it says to
        the watcher.
        """
        record = self.store.load()
        hits = invariants_module.scan(record, text)
        return {
            "hits": [
                {**hit.to_dict(), "number": invariants_module.number_of(record, hit.rule_id)}
                for hit in hits
            ],
            "checked": len(invariants_module.active(record)),
        }
