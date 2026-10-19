"""What the next request remembers - decided, and paid for, before the agent is built.

Days 9 to 11 and 25, moved out of the web handler so a turn can be run with no
web app in sight. Everything the agent is handed about the past comes out of
here as one `ContextPlan`:

    replay = conversation.history()                     # day 7: the transcript
    dropped, kept = strategies.split(replay, window)    # day 9: the window
    window | facts | summary                            # day 10: what stands in for the rest
    long-term + working layers, filtered by the question  # day 11
    the RAG chat's task state                           # day 25

Each strategy writes whatever it bought down *before* the turn: the request
has been paid for by then, and an answer that never arrives must not make the
next message buy the same thing again. None of them is allowed to cost the
user their answer - a failed extraction or compression sends what was already
stored, and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core import compaction, memory as memory_module, strategies, tokens
from core import facts as facts_module
from core import task_state as task_state_module
from core.compaction import Compaction, Compactor
from core.facts import FactKeeper, FactUpdate
from core.settings import ChatSettings
from core.task_state import StateUpdate, TaskStateKeeper
from storage import Conversation, Facts, Summary, Task, TaskState

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


@dataclass
class ContextPlan:
    """What this request is going to carry, and how that was decided.

    The whole of the context decision, made in one place before the agent is
    built: which messages go up verbatim, what stands in for the rest, and -
    when something had to be bought first - the extra exchange it took, so the
    debug panel can show a request the user did not type.

    `summary` and `facts` are never both set. `strategies.py` says why; this
    is where it shows up as two fields that the three strategies in
    `plan_for_strategy` fill in one at a time.
    """

    history: list[dict]
    strategy: str = strategies.DEFAULT_STRATEGY
    summary: str | None = None
    covers: int = 0
    facts: dict | None = None
    fact_update: FactUpdate | None = None
    replayable: int = 0
    dropped: int = 0
    compaction: Compaction | None = None
    # Day 11. The two layers that are not about this conversation, as the
    # items actually selected for *this* request - not everything on file.
    # `long_held` and `working_held` are what was on file, so the readout can
    # say "3 of 11 sent" and the number means something.
    task: Task | None = None
    long_sent: list = field(default_factory=list)
    working_sent: list = field(default_factory=list)
    long_held: int = 0
    working_held: int = 0
    # Day 25. The RAG chat's task state after this message, the request that
    # moved it there, and what it changed - None in a chat without RAG.
    task_state: dict | None = None
    task_state_update: StateUpdate | None = None
    task_state_changes: list = field(default_factory=list)


def prepare_context(
    rt: "AgentRuntime",
    conversation: Conversation | None,
    settings: ChatSettings,
    conversation_id: str,
    message: str,
) -> ContextPlan:
    """Everything the next request will remember, from all three layers.

    Two steps, and they are deliberately not folded together:

    1. `plan_for_strategy` - the short-term layer, which is day 10 entire.
       How much of *this conversation* goes up, and what stands in for the
       rest. This is the step that may cost an extra API call.
    2. `attach_layers` - the working and long-term layers, read off disk and
       filtered against the question. No API call, and no dependence on which
       strategy step 1 chose.

    The independence is the point. A strategy decides how to fit one
    conversation into one request; a layer decides how long something stays
    true and who can see it. Those are different questions, and wiring the
    second into the three cases of the first is how you end up with
    `window` quietly having no long-term memory because nobody remembered to
    add it to that case too.
    """
    plan = plan_for_strategy(rt, conversation, settings, conversation_id, message)
    return attach_layers(rt, plan, conversation, message)


def attach_layers(
    rt: "AgentRuntime", plan: ContextPlan, conversation: Conversation | None, message: str
) -> ContextPlan:
    """Fill in the two filed layers for this request.

    The long-term layer is read every time; the working layer only when this
    chat belongs to a task **that has not reached `done`**. That second
    condition is the whole of what "working memory" means - a finished task's
    decisions stop being sent, while staying on disk and staying visible in
    the panel, which is what makes finishing a task something a person will
    actually do rather than a button that loses their notes.

    Note which stages that includes: all of them but the last, paused ones
    among them. Pausing stops the machine and not the memory, which is what
    lets a task picked up after a fortnight carry on rather than be explained
    again.

    A `task_id` pointing at a task that no longer exists is treated as no
    task at all. The alternative - refusing the turn - would mean deleting a
    task could break every chat that ever joined it.
    """
    held = rt.stores.long_term.load()
    plan.long_held = len(held)
    plan.long_sent = memory_module.recall(
        held, message, memory_module.RECALL_LIMIT[memory_module.LONG]
    )

    task = rt.stores.tasks.load(conversation.task_id) if conversation and conversation.task_id else None
    plan.task = task
    if task is not None:
        plan.working_held = len(task.items)
        if task.is_active:
            plan.working_sent = memory_module.recall(
                task.items, message, memory_module.RECALL_LIMIT[memory_module.WORKING]
            )
    return plan


def plan_task_state(
    rt: "AgentRuntime",
    plan: ContextPlan,
    conversation: Conversation | None,
    settings: ChatSettings,
    message: str,
) -> ContextPlan:
    """Day 25: move the RAG chat's task state on by this message - before the search.

    Before, because the clarification just given is what the search needs:
    "the reranker's" is found with "what is the default threshold", or not at
    all. On in every RAG chat, whatever the strategy - it is not a way of
    fitting the transcript into a request, it is what the conversation is for.

    Computed here and *written* after the turn (`ConversationStore.save_task_state`
    says why). A failed update sends the state as it stood and says so.
    """
    stored = conversation.task_state if conversation else None
    current = task_state_module.normalise(stored.content if stored else None)
    replay = conversation.history() if conversation else []
    update = TaskStateKeeper(rt.llm, settings.model).update(current, message, replay[-2:])
    plan.task_state_update = update
    if not update.ok:
        plan.task_state = current
        return plan
    plan.task_state, plan.task_state_changes = task_state_module.apply_patch(current, update)
    return plan


def stored_task_state(plan: ContextPlan, conversation: Conversation | None, model: str) -> TaskState | None:
    """The record `plan.task_state` is written down as - with what keeping it has cost."""
    stored = conversation.task_state if conversation else None
    update = plan.task_state_update
    usage = update.usage if update else None
    return TaskState(
        content=plan.task_state or task_state_module.empty(),
        model=model,
        updates=(stored.updates if stored else 0) + (1 if update else 0),
        usage=usage,
        usage_total=tokens.sum_usage(
            [u for u in ((stored.usage_total if stored else None), usage) if u]
        ),
    )


def plan_for_strategy(
    rt: "AgentRuntime",
    conversation: Conversation | None,
    settings: ChatSettings,
    conversation_id: str,
    message: str,
) -> ContextPlan:
    """Decide what the next request remembers of *this conversation* - and pay
    for it if it has to.

    One cut, three answers to what happens in front of it. The cut is
    `strategies.split`: the last `context_messages` messages of the chat go
    up as themselves, always, under every strategy. What changes is the rest:

    * **window** - nothing. The oldest messages are not in the request, and
      the chat cannot answer questions about them. No extra call, and the
      readout says plainly how many messages were left out, because a chat
      that forgets silently is the failure mode this whole day exists to make
      visible.
    * **facts** - a key-value block, patched from the message that has just
      arrived and sent in front of the window. One small extra request per
      message, on a prompt that does not grow with the conversation.
    * **summary** - day 9, unchanged: the offcut compressed into a paragraph,
      written once per overflow and re-used until the next one.

    None of the three is allowed to cost the user their answer. A failed
    extraction sends the facts as they already stood; a failed compression
    sends the window and whatever summary was already stored. Both say so.
    """
    replay = conversation.history() if conversation else []
    window = settings.context_messages
    dropped, kept = strategies.split(replay, window)

    base = {
        "strategy": settings.context_strategy,
        "replayable": len(replay),
        "dropped": len(dropped),
    }

    if settings.context_strategy == strategies.FACTS:
        return plan_with_facts(
            rt, conversation, settings, conversation_id, message, replay, kept, base
        )
    if settings.context_strategy == strategies.SUMMARY:
        return plan_with_summary(
            rt, conversation, settings, conversation_id, replay, window, base
        )

    # Sliding window: day 7 exactly. The history is still all on disk; it is
    # just not in the request.
    return ContextPlan(history=kept, **base)


def plan_with_facts(
    rt: "AgentRuntime",
    conversation: Conversation | None,
    settings: ChatSettings,
    conversation_id: str,
    message: str,
    replay: list[dict],
    kept: list[dict],
    base: dict,
) -> ContextPlan:
    """Strategy 2: update the key-value memory, then send it with the window.

    The update runs on **every** message, not only on the ones that overflow,
    and that is deliberate in both directions. It is what the requirement asks
    for - facts are kept current after each user message - and it is also the
    only way the block is ever ready when it is needed: a memory that started
    being written at the moment the window overflowed would have nothing to
    say about the fifty messages that caused it.

    What it costs is a request per message; what makes that affordable is that
    the request does not grow. `FactKeeper.build_request` is sent the block,
    the previous turn and the new message - three short strings at message
    four hundred exactly as at message four - while the thing it replaces, a
    transcript replayed in full, grows without limit.
    """
    stored = conversation.facts if conversation else None
    items = dict(stored.items) if stored else {}

    update = FactKeeper(rt.llm, settings.model).update(
        # The last completed turn, so that "yes, do that one" has something to
        # resolve against. Not the transcript: see the docstring.
        items, message, replay[-2:]
    )

    if not update.ok:
        # The memory is one message out of date. That is a worse memory, not a
        # broken chat, and the turn goes up on the block as it stood.
        return ContextPlan(history=kept, facts=items or None, fact_update=update, **base)

    updated = facts_module.apply_patch(items, update)
    if update.usage or updated != items:
        # Written down before the answer is asked for, exactly as day 9's
        # summary is: this patch has been paid for whatever happens next. The
        # `or` is the unglamorous half - a message that established nothing
        # still cost a request, and a total that left those out would flatter
        # this strategy precisely where it is most expensive.
        rt.stores.conversations.save_facts(
            conversation_id,
            Facts(
                items=updated,
                model=settings.model,
                updates=(stored.updates if stored else 0) + 1,
                usage=update.usage,
                usage_total=tokens.sum_usage(
                    [u for u in ((stored.usage_total if stored else None), update.usage) if u]
                ),
            ),
        )

    return ContextPlan(history=kept, facts=updated or None, fact_update=update, **base)


def plan_with_summary(
    rt: "AgentRuntime",
    conversation: Conversation | None,
    settings: ChatSettings,
    conversation_id: str,
    replay: list[dict],
    window: int,
    base: dict,
) -> ContextPlan:
    """Strategy 3: day 9, moved into a function and otherwise untouched.

    The same cut is made and the offcut is summarised instead of dropped.
    `compaction.split` says what that means concretely, and its `covers`
    return is what keeps the cost down: a summary that already reaches the cut
    is reused as it stands, so a hundred-message chat pays for a compression
    when it overflows, not on every message after that.

    A failed compression is not a failed turn. The summary that was already
    stored (if any) still goes up, the window still goes up, and the messages
    in between are missing from this one request the way the sliding window's
    are - the user gets their answer, and the debug panel says what happened.
    """
    stored = conversation.summary if conversation else None
    to_compress, kept, covers = compaction.split(
        replay, window, stored.covers if stored else 0
    )

    if not to_compress:
        # Either nothing has overflowed yet, or the stored summary already
        # speaks for everything outside the window. Nothing to buy.
        return ContextPlan(
            history=kept,
            summary=stored.text if (stored and covers) else None,
            covers=covers,
            **base,
        )

    result = Compactor(rt.llm, settings.model).compress(
        stored.text if stored else None, to_compress, covers
    )

    if not result.ok:
        covered = stored.covers if stored else 0
        return ContextPlan(
            # Never further back than the summary reaches: a message that is
            # already inside the paragraph must not also arrive as itself.
            history=replay[covered:][-window:] if window else [],
            summary=stored.text if (stored and covered) else None,
            covers=covered,
            compaction=result,
            **base,
        )

    # Written down before the answer is even asked for. The compression has
    # been paid for at this point whatever happens next, and a turn that fails
    # must not make the next one buy the same paragraph again.
    rt.stores.conversations.save_summary(
        conversation_id,
        Summary(
            text=result.text,
            covers=covers,
            model=settings.model,
            compressions=(stored.compressions if stored else 0) + 1,
            usage=result.usage,
            usage_total=tokens.sum_usage(
                [u for u in ((stored.usage_total if stored else None), result.usage) if u]
            ),
        ),
    )

    return ContextPlan(
        history=kept, summary=result.text, covers=covers, compaction=result, **base
    )
