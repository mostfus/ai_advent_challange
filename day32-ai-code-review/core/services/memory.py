"""The memory layers' operations: tasks and their state machine, the long-term layer, proposals.

Days 11 and 13. Everything here is a person's decision - filing a note,
moving a task, accepting one of the agent's suggestions - and each returns
the whole panel (`tasks_report`), because a panel that had to work out which
half of itself to refresh after each call would get it wrong eventually.
The agent itself never writes to a layer: `core.turn` stores its suggestions
on the conversation and they wait here for somebody to accept one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import facts as facts_module
from core import memory as memory_module
from core import phases as phases_module
from core.context import ContextPlan
from core.errors import Conflict, Invalid, NotFound
from core.services.conversations import valid_id
from storage import Conversation, MemoryItem, Task

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


class MemoryService:
    """Tasks, the long-term layer and the proposals, over the stores in `rt.stores`."""

    def __init__(self, rt: "AgentRuntime") -> None:
        self.rt = rt

    @property
    def tasks(self):
        return self.rt.stores.tasks

    @property
    def long_term(self):
        return self.rt.stores.long_term

    @property
    def conversations(self):
        return self.rt.stores.conversations

    @staticmethod
    def item_report(item: MemoryItem) -> dict:
        """One filed item as the panel draws it - including where it came from."""
        return {
            "key": item.key,
            "value": item.value,
            "source": item.source,
            "created_at": item.created_at,
            "pinned": item.pinned,
            # Whether this one rides on every request regardless of the question.
            # One flag, set either by a click or by `memory.always` at filing
            # time - so what the panel draws is exactly what `recall` reads.
            "always": item.pinned,
        }

    def task_report(self, task: Task | None) -> dict | None:
        """One task, with its layer and its state machine, for the panel.

        `moves` is the part worth pointing at. It carries a verdict for all four
        stages rather than a list of the reachable ones, because the panel draws
        all four and the refused ones are the day's argument: a button greyed out
        with "planning cannot go straight to done" on it teaches the machine in a
        way a button that is simply absent cannot.

        `expected` is derived here rather than stored, by the same function that
        writes it into the prompt - so what the screen says is next and what the
        model was told is next cannot come apart.
        """
        if task is None:
            return None
        phase = phases_module.phase_of(task)
        ticked = set(task.ticked(phase))
        return {
            "id": task.id,
            "title": task.title,
            "phase": phase,
            "active": task.is_active,
            "paused": bool(task.paused),
            "note": task.note,
            "suggested": task.suggested,
            "expected": phases_module.expected_action(task),
            "steps": [
                {**step.to_dict(), "done": step.key in ticked}
                for step in phases_module.STEPS.get(phase, ())
            ],
            # Every stage, with the reason it is or is not reachable from here.
            "moves": {
                name: {"ok": decision.ok, "code": decision.code, "reason": decision.reason}
                for name, decision in phases_module.allowed(task).items()
            },
            "log": list(task.log or [])[-12:],
            "idle_hours": phases_module.idle_for(task),
            "items": [self.item_report(item) for item in task.items],
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }

    def memory_report(self, conversation: Conversation | None, plan: ContextPlan | None = None) -> dict:
        """The three layers, as the panel shows them.

        Built to answer the two questions the day asks you to check, and they
        want different numbers. *What has accumulated in each layer* is `items`,
        read off disk, the same on a reload as after a turn. *What reached the
        model* is `sent`, which only exists when a turn has just happened - and
        the gap between the two is the entire argument for filtering at all.

        The short layer has no `items` of its own here. It is the conversation,
        and `context_report` already describes it in more detail than a list of
        keys could; repeating a worse version of it under a second name would
        invite the two to disagree.
        """
        task = plan.task if plan else (
            self.tasks.load(conversation.task_id) if conversation and conversation.task_id else None
        )
        held = self.long_term.load()

        report = {
            "layers": memory_module.LAYER_INFO,
            "task": self.task_report(task),
            "long_term": [self.item_report(item) for item in held],
            "proposals": list(conversation.proposals) if conversation else [],
        }
        if plan:
            report["sent"] = {
                "long_term": [item.key for item in plan.long_sent],
                "long_held": plan.long_held,
                "working": [item.key for item in plan.working_sent],
                "working_held": plan.working_held,
                # A task in `done` holds memory and sends none. Said out loud
                # because otherwise "0 of 7 sent" looks like a bug.
                "task_done": bool(task is not None and not task.is_active),
            }
        return report

    @staticmethod
    def clean_item(value: str, key: str = "", source: dict | None = None, pinned: bool = False,
                   default_source: dict | None = None) -> MemoryItem:
        """One thing to file, as a storable item, normalised the way facts are.

        `facts.clean_key` and `clean_value` rather than a second pair of rules:
        a key filed by hand and the same key proposed out of a day-10 fact block
        have to normalise identically, or the app ends up holding both and
        sending two contradictory lines.

        A request with no key gets one derived from what it says. A request whose
        key is one of the rule prefixes is filed always-relevant without being
        asked - that is the whole of what those prefixes now do, and doing it
        here means `recall` has one flag to read instead of two rules.
        """
        value = facts_module.clean_value(value)
        if not value:
            raise Invalid("There is nothing to remember here")
        key = facts_module.clean_key(key) if key else memory_module.slug(value)
        if not key:
            raise Invalid("That note has no words to name it by")
        source = source if isinstance(source, dict) else {}
        return MemoryItem(
            key=key,
            value=value,
            source=source or (default_source or {}),
            pinned=bool(pinned) or memory_module.always(key),
        )

    def tasks_report(self) -> dict:
        """Every task, newest first, plus the long-term layer beside them.

        One shape returned by every day-11 endpoint: they all change the same
        thing - what is on file - and a panel that had to work out which half of
        itself to refresh after each call would get it wrong eventually.
        """
        return {
            "tasks": [self.task_report(task) for task in self.tasks.all()],
            "long_term": [self.item_report(item) for item in self.long_term.load()],
        }

    def create_task(self, title: str = "") -> dict:
        task = self.tasks.create(title)
        return {**self.tasks_report(), "task": self.task_report(task)}

    def move_task(self, task_id: str, to: str = "", why: str = "", confirm: bool = False) -> dict:
        """Move a task to another stage, if the machine allows it.

        A refusal comes back as 409 with the machine's own sentence in it, and
        that status is the right one: the request was well formed and nameable,
        and the reason it failed is the state the task is in. Nothing on the
        panel offers a refused move - the rail only reports them, an offer is
        only ever raised for a move `check` has already allowed - so the ways to
        reach this line are `curl`, a page that has gone stale, and the by-hand
        menu a chat gets when the agent is not reading its answers. All three
        should be told what the rule is rather than that something went wrong.

        The refusal is already in the task's log by the time this returns, written
        by `move`. That is deliberate: a state machine that only records the moves
        it allowed cannot show you the one moment worth seeing.
        """
        task, decision = self.tasks.move(
            task_id, to, by=phases_module.BY_USER, why=why, confirm=confirm
        )
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        if not decision.ok:
            raise Conflict(decision.reason)
        return {**self.tasks_report(), "task": self.task_report(task)}

    def set_task_step(self, task_id: str, key: str, done: bool = True) -> dict:
        """Tick or untick one step of the stage the task is in.

        Unticking is as much a part of this as ticking. The required steps hold
        the exit forwards, so a box ticked by the agent on a turn that had not
        really settled anything has to be takeable back - otherwise the gate is
        only ever as good as the model's optimism.
        """
        task = self.tasks.set_step(task_id, key, bool(done))
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def pause_task(self, task_id: str, paused: bool = True, note: str = "") -> dict:
        """Freeze this task's machine, or let it go again.

        Works in every stage, which is the whole requirement: the point of a
        pause is that work stops where it happens to be, not where it would be
        convenient to stop it.
        """
        task = self.tasks.set_paused(task_id, bool(paused), note)
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def dismiss_suggestion(self, task_id: str) -> dict:
        """“Not yet.” Nothing is recorded, because nothing happened."""
        task = self.tasks.dismiss_suggestion(task_id)
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def set_task_note(self, task_id: str, note: str = "") -> dict:
        """What the work is waiting on, in a line."""
        task = self.tasks.set_note(task_id, note)
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def delete_task(self, task_id: str) -> dict:
        """Forget a task and everything it learned.

        The chats that pointed at it are left alone rather than hunted down and
        edited: `attach_layers` treats a `task_id` with no task behind it as no
        task at all, so they read back as ordinary chats with no working layer.
        Rewriting every conversation that ever joined a task would be a lot of
        writes to achieve the same thing less safely.
        """
        if not self.tasks.delete(task_id):
            raise NotFound(f"No such task: {task_id}")
        return self.tasks_report()

    def add_task_item(self, task_id: str, value: str, key: str = "", source: dict | None = None,
                      pinned: bool = False) -> dict:
        """File something in a task's working memory."""
        task = self.tasks.add_item(task_id, self.clean_item(value, key, source, pinned))
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def delete_task_item(self, task_id: str, key: str) -> dict:
        task = self.tasks.remove_item(task_id, key)
        if task is None:
            raise NotFound(f"No such task: {task_id}")
        return {**self.tasks_report(), "task": self.task_report(task)}

    def add_long_term(self, value: str, key: str = "", source: dict | None = None, pinned: bool = False) -> dict:
        """File something in the layer that has no scope and no expiry."""
        self.long_term.add_item(self.clean_item(value, key, source, pinned))
        return self.tasks_report()

    def delete_long_term(self, key: str) -> dict:
        self.long_term.remove_item(key)
        return self.tasks_report()

    def attach_task(self, conversation_id: str, task_id: str | None) -> dict:
        """Join this chat to a task, or take it out of one.

        Joining is what makes the second conversation about a piece of work
        start where the first one left off, and it is the only step in this app
        that connects two chats to each other at all.
        """
        if task_id and self.tasks.load(task_id) is None:
            raise NotFound(f"No such task: {task_id}")
        conversation = self.conversations.set_task(valid_id(conversation_id), task_id)
        return {**self.tasks_report(), "memory": self.memory_report(conversation)}

    def decide_proposal(self, conversation_id: str, index: int, layer: str | None = None) -> dict:
        """Accept one of the agent's suggestions, into whichever layer you say.

        Accepting with no `layer` files it where the agent proposed; naming one
        overrides that. Either way this is the only path by which anything the
        agent thought of reaches a layer, and it runs because a person clicked.
        """
        conversation = self.conversations.load(valid_id(conversation_id))
        if conversation is None:
            raise NotFound(f"No saved conversation: {conversation_id}")
        if not 0 <= index < len(conversation.proposals):
            raise NotFound("No such proposal")

        proposal = conversation.proposals[index]
        layer = layer or proposal.get("layer")
        if layer not in memory_module.WRITABLE:
            raise Invalid(f"Cannot file into layer: {layer}")

        item = self.clean_item(
            # The agent's key is kept rather than re-derived: it is the only
            # thing that knows this is `constraint.language` and not a note that
            # happens to begin "answer in". `clean_item` turns that into a pin.
            proposal["value"], proposal["key"],
            # Unlike a button press there is no one message to point at - the
            # suggestion came from a turn, not a bubble - so the record says the
            # conversation and says the agent proposed it.
            default_source={"conversation": conversation.id, "proposed": True},
        )
        if layer == memory_module.LONG:
            self.long_term.add_item(item)
        else:
            if not conversation.task_id or self.tasks.load(conversation.task_id) is None:
                raise Invalid("This chat is not part of a task")
            self.tasks.add_item(conversation.task_id, item)

        remaining = [p for i, p in enumerate(conversation.proposals) if i != index]
        conversation = self.conversations.set_proposals(conversation.id, remaining) or conversation
        return {**self.tasks_report(), "memory": self.memory_report(conversation)}

    def dismiss_proposal(self, conversation_id: str, index: int) -> dict:
        """Say no to a suggestion. It is dropped, not remembered as refused.

        Remembering refusals would need a fourth store and would buy little: the
        proposer is shown the keys already on file and told not to repeat them,
        and the ones it invents that you did not want are usually not the same
        ones twice.
        """
        conversation = self.conversations.load(valid_id(conversation_id))
        if conversation is None:
            raise NotFound(f"No saved conversation: {conversation_id}")
        if not 0 <= index < len(conversation.proposals):
            raise NotFound("No such proposal")
        remaining = [p for i, p in enumerate(conversation.proposals) if i != index]
        conversation = self.conversations.set_proposals(conversation.id, remaining) or conversation
        return {**self.tasks_report(), "memory": self.memory_report(conversation)}
