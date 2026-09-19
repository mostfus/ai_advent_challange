"""FastAPI backend: transport, plus the wiring between store and agent.

    browser -> /api/chat -> store.load -> Agent(history=...) -> DeepSeek
                         -> store.append_turn

Day 6 wrote here: "the server keeps no conversation or settings state". That
is the line day 7 deletes. It keeps both now - not in memory, where a restart
would take them, but in `store.py`, which is a directory of JSON files.

What did *not* change is worth as much as what did. This file still describes
no request body, still maps no API error, still does not know that a system
prompt goes in `messages[0]`; and `agent.py` still knows nothing about files.
The server is the only place where "an agent" and "a saved transcript" are
introduced to each other, and the introduction is three lines long:

    conversation = store.load(id)
    agent = Agent(client, config, history=conversation.history())
    store.append_turn(id, message, reply.answer)

Each request is still served by a brand-new short-lived `Agent`. Memory did
not come from keeping objects alive - a long-running agent per chat would die
with the process, which is exactly the failure this day is about. It came
from writing the transcript down and handing it back.

Day 9 puts one more step in front of the agent, and it is the only place in
the app where the shape of a request is decided by policy rather than by what
was said:

    replay = conversation.history()
    to_compress, window, covers = compaction.split(replay, limit, already_covered)
    summary = Compactor(client, model).compress(...)   # only when there is
    store.save_summary(id, summary)                    # something new to fold
    agent = Agent(client, config, history=window, summary=summary.text)

Everything else about it follows from two rules. The summary is written
**before** the turn and saved immediately, so an answer that never arrives
cannot throw away a compression that has already been paid for. And it is
written only when the overflow has grown - `covers` says how far the stored
one reaches - so a long chat buys one paragraph per overflow rather than one
per message, which is the difference between compression that saves money and
compression that spends it.

Day 10 turns that one decision into three, and puts the choice in the
settings next to the model:

    strategy = settings.context_strategy       # window | facts | summary
    dropped, kept = strategies.split(replay, settings.context_messages)

`window` sends `kept` and nothing else. `facts` buys a small patch to the
chat's key-value memory first (`facts.py`) and sends that in front of the
window. `summary` is day 9 unchanged. All three write whatever they bought
down *before* the turn, for day 9's reason: the request has been paid for by
then, and an answer that never arrives must not make the next message buy the
same thing again.

And one decision that is not a strategy at all: **which** transcript this is.
A conversation is a tree since day 10, the handler is told (or looks up)
which branch a message belongs to, and everything above happens inside it.
The five endpoints under `/branches` and `/checkpoints` are the whole of it,
and none of them touch a message - forking records a parent and an offset,
switching writes one field.

Day 8 adds one thing: every conversation the browser is handed - after a
turn, and on the page that draws it back - carries a `usage` block saying
what it has cost. `usage_report` builds it out of the counts DeepSeek
reported and this app wrote down, which is the only arithmetic involved:
the last turn, and the sum over all of them.

The handler is a plain `def`, so FastAPI runs it in its threadpool and the
calls from several compare panes really do overlap instead of queueing; the
store takes a lock for the same reason.

Run with:
    uv run server.py
then open http://127.0.0.1:8000
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent as agent_module
import compaction
import facts as facts_module
import memory as memory_module
import store as store_module
import strategies
import tokens
from agent import Agent, AgentConfig, AgentReply
from compaction import Compaction, Compactor
from facts import FactKeeper, FactUpdate
from llm_client import DeepSeekClient, LLMClientError
from memory import MemoryProposer, ProposalRun
from store import (
    MAIN_BRANCH,
    TASK_OPEN,
    TASK_STATUSES,
    Conversation,
    ConversationStore,
    Facts,
    LongTermStore,
    MemoryItem,
    StoreError,
    Summary,
    Task,
    TaskStore,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="LLM Agent with context strategies and branches")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One client, reused by every agent this process builds - the HTTP connection
# pool is worth keeping, while the agent around it is cheap and per-request.
try:
    deepseek_client = DeepSeekClient.from_env()
except LLMClientError as exc:
    print(f"Error: {exc}")
    sys.exit(1)

# The conversations, on disk. Created here rather than per request: it makes
# the directory once and owns the lock that serialises writes.
store = ConversationStore()

# Day 11's two layers, beside it rather than inside it. Three stores because
# there are three lifetimes: a conversation's memory is deleted with the
# conversation, a task's outlives every chat about it, and the long-term
# layer outlives everything. One store holding all three would have to be
# asked, on every delete, which of those it was doing.
tasks = TaskStore()
long_term = LongTermStore()


class Settings(BaseModel):
    """One agent's configuration, sent by the client with every message."""

    model: str = agent_module.DEFAULT_MODEL
    system_prompt: str = agent_module.DEFAULT_SYSTEM_PROMPT
    stop: list[str] = Field(default_factory=list)
    response_format: str = agent_module.DEFAULT_RESPONSE_FORMAT
    reasoning_effort: str = agent_module.DEFAULT_REASONING_EFFORT
    temperature: float = agent_module.DEFAULT_TEMPERATURE
    # Days 9 and 10. How many messages of the transcript are replayed
    # verbatim, and what becomes of everything older than that. Settings of
    # the *conversation* rather than of the request - which is why they live
    # here, get stored with the chat, and are sent back up with every message
    # like every other parameter. Two columns of the compare view can
    # therefore be given two different strategies and asked the same thing,
    # which is the only way to see what the choice actually costs you.
    context_messages: int = agent_module.DEFAULT_CONTEXT_MESSAGES
    context_strategy: str = strategies.DEFAULT_STRATEGY
    # Day 11. Whether the agent looks at each turn and says what it would
    # file. A setting rather than a constant because it buys one extra
    # request per message and buys it *after* the answer - it is the one
    # thing in this app that costs money without making any reply better,
    # and that is a trade the person paying should get to decline.
    memory_proposals: bool = True


class ChatRequest(BaseModel):
    message: str
    # Which conversation this message belongs to. Defaulted rather than
    # required so a bare {"message": "..."} - curl, a script - still works and
    # lands in the single chat.
    conversation_id: str = store_module.SINGLE_CONVERSATION_ID
    view: str = store_module.DEFAULT_VIEW
    # Day 10: which branch of it. Optional - the stored record knows which
    # branch is active, and that is the one the page is showing - so a bare
    # `{"message": "..."}` still lands where the last one did.
    branch: str | None = None
    settings: Settings = Field(default_factory=Settings)


class ConversationMeta(BaseModel):
    """What a chat is, before anything has been said in it."""

    view: str = store_module.DEFAULT_VIEW
    settings: Settings = Field(default_factory=Settings)


def validate(settings: Settings) -> Settings:
    """Reject anything the DeepSeek API would reject, with a readable message."""
    if settings.model not in agent_module.MODELS:
        raise HTTPException(400, f"Unknown model: {settings.model}")
    if settings.response_format not in agent_module.RESPONSE_FORMATS:
        raise HTTPException(400, f"Unknown response format: {settings.response_format}")
    if settings.reasoning_effort not in agent_module.REASONING_EFFORTS:
        raise HTTPException(400, f"Unknown reasoning effort: {settings.reasoning_effort}")
    if not agent_module.TEMPERATURE_MIN <= settings.temperature <= agent_module.TEMPERATURE_MAX:
        raise HTTPException(
            400,
            f"Temperature must be between {agent_module.TEMPERATURE_MIN} "
            f"and {agent_module.TEMPERATURE_MAX}",
        )

    settings.system_prompt = settings.system_prompt.strip() or agent_module.DEFAULT_SYSTEM_PROMPT
    settings.stop = [s.strip() for s in settings.stop if s.strip()][
        : agent_module.STOP_SEQUENCES_LIMIT
    ]
    settings.temperature = agent_module.clamp_temperature(settings.temperature)
    settings.context_messages = agent_module.clamp_context_messages(settings.context_messages)
    # Unknown strategies fall back rather than 400: this field arrives from
    # records written by older versions of the app as well as from the form,
    # and a chat that cannot be opened because its settings mention a strategy
    # that has since been renamed is a worse outcome than one that quietly
    # uses the default.
    settings.context_strategy = strategies.normalise(settings.context_strategy)
    return settings


def valid_id(conversation_id: str) -> str:
    """A conversation id that is safe to turn into a file name."""
    try:
        return store_module.validate_id(conversation_id)
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc


@dataclass
class ContextPlan:
    """What this request is going to carry, and how that was decided.

    The whole of the context decision, made in one place before the agent is
    built: which branch it is being made in, which messages go up verbatim,
    what stands in for the rest, and - when something had to be bought first -
    the extra exchange it took, so the debug panel can show a request the user
    did not type.

    `summary` and `facts` are never both set. `strategies.py` says why; this
    is where it shows up as two fields that the three branches of
    `prepare_context` fill in one at a time.
    """

    history: list[dict]
    strategy: str = strategies.DEFAULT_STRATEGY
    branch: str = MAIN_BRANCH
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


def prepare_context(
    conversation: Conversation | None,
    settings: Settings,
    conversation_id: str,
    message: str,
    branch_id: str,
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
    second into the three branches of the first is how you end up with
    `window` quietly having no long-term memory because nobody remembered to
    add it to that branch too.
    """
    plan = plan_for_strategy(conversation, settings, conversation_id, message, branch_id)
    return attach_layers(plan, conversation, message)


def attach_layers(
    plan: ContextPlan, conversation: Conversation | None, message: str
) -> ContextPlan:
    """Fill in the two filed layers for this request.

    The long-term layer is read every time; the working layer only when this
    chat belongs to a task **and that task is open**. That second condition
    is the whole of what "working memory" means - a closed task's decisions
    stop being sent, while staying on disk and staying visible in the panel,
    which is what makes closing a task something a person will actually do
    rather than a button that loses their notes.

    A `task_id` pointing at a task that no longer exists is treated as no
    task at all. The alternative - refusing the turn - would mean deleting a
    task could break every chat that ever joined it.
    """
    held = long_term.load()
    plan.long_held = len(held)
    plan.long_sent = memory_module.recall(
        held, message, memory_module.RECALL_LIMIT[memory_module.LONG]
    )

    task = tasks.load(conversation.task_id) if conversation and conversation.task_id else None
    plan.task = task
    if task is not None:
        plan.working_held = len(task.items)
        if task.is_open:
            plan.working_sent = memory_module.recall(
                task.items, message, memory_module.RECALL_LIMIT[memory_module.WORKING]
            )
    return plan


def plan_for_strategy(
    conversation: Conversation | None,
    settings: Settings,
    conversation_id: str,
    message: str,
    branch_id: str,
) -> ContextPlan:
    """Decide what the next request remembers of *this conversation* - and pay
    for it if it has to.

    One cut, three answers to what happens in front of it. The cut is
    `strategies.split`: the last `context_messages` messages of this branch go
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
    replay = conversation.history(branch_id) if conversation else []
    window = settings.context_messages
    dropped, kept = strategies.split(replay, window)

    base = {
        "strategy": settings.context_strategy,
        "branch": branch_id,
        "replayable": len(replay),
        "dropped": len(dropped),
    }

    if settings.context_strategy == strategies.FACTS:
        return plan_with_facts(
            conversation, settings, conversation_id, message, branch_id, replay, kept, base
        )
    if settings.context_strategy == strategies.SUMMARY:
        return plan_with_summary(
            conversation, settings, conversation_id, branch_id, replay, window, base
        )

    # Sliding window: day 7 exactly. The history is still all on disk; it is
    # just not in the request.
    return ContextPlan(history=kept, **base)


def plan_with_facts(
    conversation: Conversation | None,
    settings: Settings,
    conversation_id: str,
    message: str,
    branch_id: str,
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
    branch = conversation.branch(branch_id) if conversation else None
    stored = branch.facts if branch else None
    items = dict(stored.items) if stored else {}

    update = FactKeeper(deepseek_client, settings.model).update(
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
        store.save_facts(
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
            branch_id,
        )

    return ContextPlan(history=kept, facts=updated or None, fact_update=update, **base)


def plan_with_summary(
    conversation: Conversation | None,
    settings: Settings,
    conversation_id: str,
    branch_id: str,
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
    branch = conversation.branch(branch_id) if conversation else None
    stored = branch.summary if branch else None
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

    result = Compactor(deepseek_client, settings.model).compress(
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
    store.save_summary(
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
        branch_id,
    )

    return ContextPlan(
        history=kept, summary=result.text, covers=covers, compaction=result, **base
    )


def build_agent(settings: Settings, plan: ContextPlan) -> Agent:
    """One short-lived agent, configured and handed the context to run on.

    Day 7's three lines, minus the third - except that what it is handed is no
    longer simply "the transcript so far". It is whatever `prepare_context`
    decided the transcript should look like from in here: one branch of it,
    cut to the window, with at most one block standing in for the rest.
    """
    return Agent(
        deepseek_client,
        AgentConfig(
            model=settings.model,
            system_prompt=settings.system_prompt,
            stop=settings.stop or None,
            response_format=settings.response_format,
            reasoning_effort=settings.reasoning_effort,
            temperature=settings.temperature,
        ),
        history=plan.history,
        context_messages=settings.context_messages,
        summary=plan.summary,
        # Rendered here rather than in the agent: `key: value` lines are this
        # app's idea of what a fact block looks like in a prompt, and the agent
        # is deliberately given text it does not have to interpret.
        facts=facts_module.render(plan.facts),
        # The same arrangement for day 11's two, down to the rendering
        # happening here. `memory.render` produces the same `key: value`
        # shape `facts.render` does, so all three blocks read alike in the
        # prompt and the agent cannot tell which layer it was handed.
        long_term=memory_module.render(plan.long_sent),
        working=memory_module.render(plan.working_sent),
        task_title=plan.task.title if plan.task else None,
    )


def usage_block(usage: dict | None) -> dict:
    """One usage dict, read back through the fields this app knows about.

    Read back rather than passed on whole: these come off disk, where an older
    version may have written keys that no longer mean anything.
    """
    usage = usage or {}
    return {key: tokens.as_int(usage.get(key)) for key in tokens.EMPTY_USAGE}


def usage_report(conversation: Conversation | None, branch_id: str | None = None) -> dict:
    """What this conversation has cost, out of what DeepSeek reported.

    Four figures now, because they answer four different questions:

    * **last** - the turn that just happened: how big the request was
      (`prompt_tokens` - the system prompt, the replayed history and the new
      question together), how big the answer was (`completion_tokens`), and
      how much of that answer was thinking nobody sees.
    * **total** - the same fields added up over every turn of the branch you
      are reading. It is not the size of the conversation; it is the sum of
      every copy of the conversation that has been uploaded, once per turn.
      That is what makes a long chat expensive, and it is invisible in every
      other view here.
    * **compression** and **facts** - what the two memory strategies have
      spent on their own bookkeeping. Reported on their own lines rather than
      folded into the total above, because this is money spent on the app's
      memory rather than on an answer, and adding it to the turns would hide
      the only comparison worth making: what a strategy costs against what not
      sending those messages saves on every turn since.

    `last` is None until something has actually been billed - a brand-new
    chat, or one whose only turns failed. Nothing is invented to fill the
    gap: a zero would say "this answer was free", which is a different claim
    than "there is no answer yet".
    """
    # `branch_id` is the branch a request was *made in*, which is normally the
    # active one and is only different when the caller named another
    # explicitly. Threaded through rather than assumed, so a curl that talks to
    # a branch it has not switched to gets a readout of that branch.
    branch = conversation.branch(branch_id) or conversation.active() if conversation else None
    usages = conversation.usages(branch.id) if conversation else []
    last = usages[-1] if usages else None
    summary = branch.summary if branch else None
    facts = branch.facts if branch else None
    return {
        "last": usage_block(last) if last else None,
        "total": tokens.sum_usage(usages),
        # Day 10. What the whole tree has cost, with the shared prefix counted
        # once however many branches were made from it. Equal to `total` for a
        # conversation that has never been forked, which is why it is only
        # worth drawing when it is not.
        "all_branches": tokens.sum_usage(conversation.all_usages()) if conversation else
            tokens.sum_usage([]),
        "compression": {
            **usage_block(summary.usage_total if summary else None),
            # How many summarisation requests this branch has made - the count
            # that matters here, where `turns` is the count that matters above.
            "compressions": summary.compressions if summary else 0,
        },
        "facts": {
            **usage_block(facts.usage_total if facts else None),
            # The count that tells the story of this strategy: it goes up by
            # one on every message, where the one above goes up by one per
            # overflow.
            "updates": facts.updates if facts else 0,
        },
        "messages": len(conversation.transcript(branch.id)) if conversation else 0,
    }


def facts_report(facts: Facts | None) -> dict | None:
    """The key-value memory as the strip draws it - in full, never counted.

    In full for the same reason day 9 shows the whole summary: every line of
    it goes into every request from here on, so being able to read them is not
    a nicety. Unlike the summary, they can be judged one at a time, which is
    what makes the strategy worth having.
    """
    if facts is None or not facts.items:
        return None
    return {
        "items": dict(facts.items),
        "count": len(facts.items),
        "updated_at": facts.updated_at,
        "model": facts.model,
        "updates": facts.updates,
        "usage_total": facts.usage_total or {},
    }


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


def task_report(task: Task | None) -> dict | None:
    """One task, with its layer, for the selector and the memory panel."""
    if task is None:
        return None
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "open": task.is_open,
        "items": [item_report(item) for item in task.items],
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def memory_report(conversation: Conversation | None, plan: ContextPlan | None = None) -> dict:
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
        tasks.load(conversation.task_id) if conversation and conversation.task_id else None
    )
    held = long_term.load()

    report = {
        "layers": memory_module.LAYER_INFO,
        "task": task_report(task),
        "long_term": [item_report(item) for item in held],
        "proposals": list(conversation.proposals) if conversation else [],
    }
    if plan:
        report["sent"] = {
            "long_term": [item.key for item in plan.long_sent],
            "long_held": plan.long_held,
            "working": [item.key for item in plan.working_sent],
            "working_held": plan.working_held,
            # A task that is closed holds memory and sends none. Said out
            # loud because otherwise "0 of 7 sent" looks like a bug.
            "task_closed": bool(task is not None and not task.is_open),
        }
    return report


def branch_report(conversation: Conversation | None) -> dict:
    """The shape of the tree, for the branch bar - day 10.

    Two counts per branch, and the pair is the argument for how branching is
    stored here. `messages` is what the branch shows you: its inherited past
    and its own. `own` is what it actually costs to keep: only the messages
    said in it. A fork of a hundred-message chat reads as a hundred-message
    conversation and occupies two lines of JSON.
    """
    if conversation is None:
        return {"active": MAIN_BRANCH, "branches": [], "checkpoints": []}
    return {
        "active": conversation.active_branch,
        "branches": [
            {
                "id": branch.id,
                "name": branch.name or branch.id,
                "parent": branch.parent,
                "forked_at": branch.forked_at,
                "messages": len(conversation.transcript(branch.id)),
                "own": len(branch.messages),
                "created_at": branch.created_at,
                # Whether it can be deleted, decided where the rule lives
                # rather than guessed at by the button.
                "has_children": bool(conversation.children(branch.id)),
            }
            for branch in conversation.branches
        ],
        "checkpoints": [c.to_dict() for c in conversation.checkpoints],
    }


def context_report(conversation: Conversation | None, plan: ContextPlan | None = None) -> dict:
    """What this chat's memory currently looks like from the outside.

    Answers, in one block, the questions the last two days exist to make
    answerable: how much has been said, how much of it the next request will
    carry as itself, what happens to the rest under the strategy this chat is
    set to, and - since day 10 - which of several transcripts any of that is
    about.

    Built from the stored record, so the page draws the same thing on a reload
    as it did after the last turn; `plan` adds what is only true of the turn
    that has just happened.
    """
    settings = (conversation.settings if conversation else None) or {}
    branch = (conversation.branch(plan.branch if plan else None) or conversation.active()) \
        if conversation else None
    summary = branch.summary if branch else None
    replayable = plan.replayable if plan else \
        len(conversation.history(branch.id)) if conversation else 0

    report = {
        "strategy": plan.strategy if plan else strategies.from_settings(settings),
        "window": agent_module.clamp_context_messages(
            settings.get("context_messages", agent_module.DEFAULT_CONTEXT_MESSAGES)
        ),
        "messages": len(conversation.transcript(branch.id)) if conversation else 0,
        # Complete turns only: a question whose answer never arrived is on
        # screen but is not part of what gets replayed, so counting it here
        # would make the window look fuller than it is.
        "replayable": replayable,
        "summary": {
            "text": summary.text,
            "covers": summary.covers,
            "updated_at": summary.updated_at,
            "model": summary.model,
            "compressions": summary.compressions,
            "usage": summary.usage,
            "usage_total": summary.usage_total,
        } if summary else None,
        "facts": facts_report(branch.facts if branch else None),
        "branch": branch.id if branch else MAIN_BRANCH,
        "branches": len(conversation.branches) if conversation else 1,
    }
    if plan:
        # What actually went up a moment ago, as opposed to what the settings
        # say should go up next time.
        update = plan.fact_update
        report["sent"] = {
            "messages": len(plan.history),
            "summarised": plan.covers,
            "summary_used": bool(plan.summary),
            "compressed_now": bool(plan.compaction and plan.compaction.ok),
            # Under the sliding window this is the number that matters and the
            # only one anywhere that says what the chat has just forgotten.
            "dropped": max(0, plan.dropped - plan.covers),
            "facts_used": bool(plan.facts),
            "facts_changed": bool(update and update.ok and update.changed),
            "error": (plan.compaction.error if plan.compaction else None)
                or (update.error if update else None),
        }
    return report


def with_usage(conversation: Conversation) -> dict:
    """A conversation as the browser wants it: the record, plus what it cost.

    Attached here rather than in `Conversation.to_dict` on purpose - that
    method is also what gets written to disk, and a total that can be
    recomputed from the turns beneath it has no business being stored next
    to them.

    `messages` is overwritten for a second, day-10 reason: on disk that key is
    the *main* branch, because that is where messages have always been written
    and every older file is still readable as a result. On the wire it is the
    branch the page is showing.
    """
    return {
        **conversation.to_dict(),
        "messages": [m.to_dict() for m in conversation.messages],
        "branching": branch_report(conversation),
        "usage": usage_report(conversation),
        "context": context_report(conversation),
        # Day 11, and this is what makes a reload restore the memory panel
        # rather than an empty one: the layers are read off disk here with no
        # `plan`, so the page draws what is on file even when this chat has
        # not been spoken to since the process started.
        "memory": memory_report(conversation),
    }


def compaction_debug(result: Compaction | None) -> dict | None:
    """The summarisation exchange, in the same shape as an ordinary turn's.

    Shown in the debug panel next to the request it made room for. A call
    charged to the user and made without them asking for it is exactly the
    kind of thing that should not be invisible - and reading the request is
    also the only way to see what the summariser was actually given.
    """
    if result is None:
        return None
    return {
        "request": result.request,
        "response": result.response,
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
        "url": result.url,
        "status_code": result.status_code,
        "request_headers": result.request_headers,
        "usage": result.usage,
        "covers": result.covers,
    }


def facts_debug(update: FactUpdate | None) -> dict | None:
    """The extraction exchange, in that same shape - day 10.

    Carrying the patch as well as the JSON it came in, because the patch is
    the thing worth reading: `set` and `unset` are what changed about what
    every future request will be told, and they are two lines rather than a
    response body.
    """
    if update is None:
        return None
    return {
        "request": update.request,
        "response": update.response,
        "error": update.error,
        "elapsed_ms": update.elapsed_ms,
        "url": update.url,
        "status_code": update.status_code,
        "request_headers": update.request_headers,
        "usage": update.usage,
        "set": update.set,
        "unset": update.unset,
        "summary": update.describe(),
    }


def proposals_debug(run: ProposalRun | None) -> dict | None:
    """One proposal request, for the debug log - the same shape as the others.

    None when no run was made, which is most turns under most settings. A
    run that suggested nothing is *not* None: it happened, it was billed,
    and a panel that hid it would make the proposer look free.
    """
    if run is None:
        return None
    return {
        "request": run.request,
        "response": run.response,
        "error": run.error,
        "elapsed_ms": run.elapsed_ms,
        "url": run.url,
        "status_code": run.status_code,
        "request_headers": run.request_headers,
        "usage": run.usage,
        "proposals": [p.to_dict() for p in run.proposals],
        "summary": run.describe(),
    }


def as_json(
    reply: AgentReply,
    conversation: Conversation,
    plan: ContextPlan,
    proposals: ProposalRun | None = None,
) -> dict:
    """The wire format the browser expects: the answer, the debug transcript,
    how much context now stands behind this chat, and what it has cost."""
    return {
        "answer": reply.answer,
        "conversation": {
            "id": conversation.id,
            "message_count": len(conversation.transcript(plan.branch)),
            "branch": plan.branch,
        },
        # What this request remembered, and how. Read off the stored
        # conversation for the same reason the usage below is - so the readout
        # and the file cannot disagree.
        "context": context_report(conversation, plan),
        # Day 11: the same question asked of the other two layers - what is on
        # file, and which of it this request actually carried.
        "memory": memory_report(conversation, plan),
        "branching": branch_report(conversation),
        # Read back *after* the turn was stored, so the readout the browser
        # draws describes the conversation as it now is - one turn longer,
        # and one turn more expensive to continue.
        "usage": usage_report(conversation, plan.branch),
        "debug": {
            "request": reply.request,
            "response": reply.response,
            "error": reply.error,
            "elapsed_ms": reply.elapsed_ms,
            "url": reply.url,
            "status_code": reply.status_code,
            "request_headers": reply.request_headers,
            "usage": reply.usage,
            # Each None unless this turn had to buy something first. Never
            # both: the strategies that produce them are exclusive.
            "compaction": compaction_debug(plan.compaction),
            "facts": facts_debug(plan.fact_update),
            "proposals": proposals_debug(proposals),
        },
    }


def require_message(message: str) -> str:
    message = message.strip()
    if not message:
        raise HTTPException(400, "Message must not be empty")
    return message


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    """Everything the frontend needs to render a settings form."""
    return {
        "models": agent_module.MODELS,
        "system_prompts": [
            {"name": name, "prompt": prompt} for name, prompt in agent_module.SYSTEM_PROMPTS
        ],
        "response_formats": agent_module.RESPONSE_FORMATS,
        "reasoning_efforts": agent_module.REASONING_EFFORTS,
        "stop_sequences_limit": agent_module.STOP_SEQUENCES_LIMIT,
        "temperature": {
            "min": agent_module.TEMPERATURE_MIN,
            "max": agent_module.TEMPERATURE_MAX,
            "step": agent_module.TEMPERATURE_STEP,
            "default": agent_module.DEFAULT_TEMPERATURE,
        },
        "max_compare_chats": 5,
        "defaults": Settings().model_dump(),
        "single_conversation_id": store_module.SINGLE_CONVERSATION_ID,
        "context_messages": {
            "min": agent_module.CONTEXT_MESSAGES_MIN,
            "max": agent_module.CONTEXT_MESSAGES_MAX,
            "default": agent_module.DEFAULT_CONTEXT_MESSAGES,
        },
        # Day 10. The selector is built from this rather than from a list in
        # the page, so a strategy cannot exist in the UI and not on the server
        # - or, worse, the other way round.
        "context_strategies": [
            {"id": name, **strategies.STRATEGY_INFO[name]} for name in strategies.STRATEGIES
        ],
        "context_strategy_default": strategies.DEFAULT_STRATEGY,
        # Day 11. Same trick as the strategies above, for the same reason: the
        # memory panel's three headings, their scopes and - the column that
        # matters - who writes to each, come from `memory.py` rather than from
        # a list in the page.
        "memory_layers": [
            {"id": name, **memory_module.LAYER_INFO[name]} for name in memory_module.LAYERS
        ],
        "memory_writable": list(memory_module.WRITABLE),
    }


# --------------------------------------------------------------------------
# Conversations: the day-7 endpoints
# --------------------------------------------------------------------------


@app.get("/api/conversations")
def list_conversations() -> dict:
    """Every saved chat, oldest first - what the page is rebuilt from.

    The browser asks for this once on load and puts back what it finds: the
    single chat's transcript, and one compare column per saved column, in the
    order they were created. Nothing about which chats exist lives in
    `localStorage`, so the same conversations come back in a different
    browser, and clearing site data loses nothing.
    """
    return {"conversations": [with_usage(c) for c in store.all()]}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    conversation = store.load(valid_id(conversation_id))
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    return with_usage(conversation)


@app.put("/api/conversations/{conversation_id}")
def put_conversation(conversation_id: str, meta: ConversationMeta) -> dict:
    """Create a chat, or update its settings, without touching its messages.

    This is how a compare column registers itself the moment it is added, so
    an empty column - and, more to the point, the parameters someone just set
    on it - still exists after a restart.
    """
    settings = validate(meta.settings)
    conversation = store.upsert(valid_id(conversation_id), meta.view, settings.model_dump())
    return with_usage(conversation)


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    """Forget a chat completely. Sent when a compare column is closed."""
    return {"deleted": store.delete(valid_id(conversation_id))}


@app.delete("/api/conversations/{conversation_id}/messages")
def clear_conversation(conversation_id: str) -> dict:
    """Empty a chat but keep it. What the "Clear context" button calls.

    Deliberately not the same as deleting the conversation: the chat, and the
    settings it runs under, are still there - it simply has nothing left to
    remember, so the next message starts a conversation rather than continuing
    one.
    """
    conversation = store.clear(valid_id(conversation_id))
    if conversation is None:
        # Nothing stored under that id is the state the caller asked for.
        return {
            "cleared": True,
            "messages": [],
            "usage": usage_report(None),
            "context": context_report(None),
            "branching": branch_report(None),
        }
    return {
        "cleared": True,
        "messages": [],
        # Day 10: the branches and the checkpoints went with the messages, so
        # the bar has to be redrawn back down to a single empty `main`.
        "branching": branch_report(conversation),
        # Day 9: emptied of its summary as well, which the strip has to be
        # told about - it was the one thing a cleared chat could still know.
        "context": context_report(conversation),
        # Emptied along with the messages: the usage was theirs, not the
        # chat's. What was spent is spent, but it was spent on a conversation
        # that no longer exists here.
        "usage": usage_report(conversation),
    }


@app.delete("/api/conversations/{conversation_id}/summary")
def clear_summary(conversation_id: str) -> dict:
    """Throw away the compression, keep every message - day 9's own undo.

    Deliberately a different button from "Clear context": that one forgets the
    conversation, this one forgets only what the app made of it. The next
    message that overflows the window summarises the transcript again from the
    beginning, which is what you want after changing the window, or after a
    summary that came out wrong and has been quietly shaping every answer
    since.
    """
    conversation = store.clear_summary(valid_id(conversation_id))
    if conversation is None:
        return {"cleared": True, "context": context_report(None), "usage": usage_report(None)}
    return {
        "cleared": True,
        "context": context_report(conversation),
        # The compressions it paid for go with it: what is reported has to
        # describe the summary that exists, and there is no longer one.
        "usage": usage_report(conversation),
    }


@app.delete("/api/conversations/{conversation_id}/facts")
def clear_facts(conversation_id: str) -> dict:
    """Throw away the key-value memory, keep every message - day 10's undo.

    The same button as the one above, for the other strategy, and needed a
    little more sharply. A summary is rewritten from the transcript on the
    next overflow, so a bad one has a limited life; a fact is carried forward
    untouched for as long as it stands, is never re-derived from what was
    actually said, and shapes every answer from here on. This is the way out.
    """
    conversation = store.clear_facts(valid_id(conversation_id))
    if conversation is None:
        return {"cleared": True, "context": context_report(None), "usage": usage_report(None)}
    return {
        "cleared": True,
        "context": context_report(conversation),
        "usage": usage_report(conversation),
    }


# --------------------------------------------------------------------------
# Branching: checkpoints, forks, and which one is being spoken to (day 10)
# --------------------------------------------------------------------------


class CheckpointRequest(BaseModel):
    """Where to put a bookmark. Both fields optional: with neither, it lands
    at the end of the branch on screen, which is what pressing "save here"
    means."""

    label: str = ""
    at: int | None = None
    branch: str | None = None


class BranchRequest(BaseModel):
    """Where to fork from. `checkpoint` is the way the page does it - the
    exercise is two branches from *one* place, and a checkpoint is what makes
    that one place a thing that can be named twice rather than an index typed
    twice."""

    checkpoint: str | None = None
    at: int | None = None
    branch: str | None = None
    name: str = ""


def branch_state(conversation: Conversation | None) -> dict:
    """What every branching endpoint returns: the tree, and the chat as it now
    reads. One shape for all of them, because they all change the same two
    things - which transcript is on screen, and what it contains."""
    if conversation is None:
        raise HTTPException(404, "No such conversation")
    return {
        "branching": branch_report(conversation),
        "messages": [m.to_dict() for m in conversation.messages],
        "context": context_report(conversation),
        "usage": usage_report(conversation),
    }


@app.post("/api/conversations/{conversation_id}/checkpoints")
def add_checkpoint(conversation_id: str, req: CheckpointRequest) -> dict:
    """Mark a place worth coming back to. Stores a position, not a copy."""
    result = store.add_checkpoint(
        valid_id(conversation_id), label=req.label, at=req.at, branch=req.branch
    )
    if result is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    conversation, checkpoint = result
    return {**branch_state(conversation), "checkpoint": checkpoint.to_dict()}


@app.delete("/api/conversations/{conversation_id}/checkpoints/{checkpoint_id}")
def delete_checkpoint(conversation_id: str, checkpoint_id: str) -> dict:
    conversation = store.delete_checkpoint(valid_id(conversation_id), checkpoint_id)
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    return branch_state(conversation)


@app.post("/api/conversations/{conversation_id}/branches")
def create_branch(conversation_id: str, req: BranchRequest) -> dict:
    """Fork the conversation and start talking in the fork.

    Call it twice with the same checkpoint and you have the two independent
    continuations the exercise asks for, sharing one copy of everything said
    before the fork.
    """
    try:
        result = store.create_branch(
            valid_id(conversation_id),
            checkpoint=req.checkpoint,
            at=req.at,
            branch=req.branch,
            name=req.name,
        )
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    conversation, branch = result
    return {**branch_state(conversation), "branch": branch.id}


@app.post("/api/conversations/{conversation_id}/branches/{branch_id}/activate")
def activate_branch(conversation_id: str, branch_id: str) -> dict:
    """Switch branches. One stored field, and a different transcript comes
    back - nothing is unloaded, merged or recomputed."""
    try:
        conversation = store.activate_branch(valid_id(conversation_id), branch_id)
    except StoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    return branch_state(conversation)


@app.delete("/api/conversations/{conversation_id}/branches/{branch_id}")
def delete_branch(conversation_id: str, branch_id: str) -> dict:
    """Abandon a fork. Refused for `main`, and for any branch something else
    was forked from - see `store.delete_branch`."""
    try:
        conversation = store.delete_branch(valid_id(conversation_id), branch_id)
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    return branch_state(conversation)


# --------------------------------------------------------------------------
# Memory layers: the day-11 endpoints
# --------------------------------------------------------------------------


class TaskRequest(BaseModel):
    """A new piece of work. The title is what the selector shows and what the
    prompt names, so it is the only field there is."""

    title: str = ""


class TaskStatusRequest(BaseModel):
    status: str = TASK_OPEN


class AttachRequest(BaseModel):
    """Which task a chat belongs to. `null` detaches it."""

    task_id: str | None = None


class ItemRequest(BaseModel):
    """One thing to file, in the words it was said in.

    `key` is optional and normally absent. A person types a note, not a
    key - `memory.slug` derives one so that filing the same thing twice
    corrects it instead of leaving two lines that disagree in the prompt.
    The extractor and the proposer still send their own dotted keys, which
    is what the field is for.

    `source` says which conversation the note came out of. The browser sends
    it rather than the server inferring it, and a memory that cannot say
    where it came from is one nobody will ever dare prune (see
    `store.MemoryItem`).
    """

    value: str
    key: str = ""
    source: dict = Field(default_factory=dict)
    pinned: bool = False


class ProposalDecision(BaseModel):
    """What to do with one of the agent's suggestions. `layer` is here so
    that accepting can also *move* it - the agent proposed long-term, you
    decide it belongs to the task, and that correction is the point."""

    layer: str | None = None


def clean_item(req: ItemRequest, default_source: dict | None = None) -> MemoryItem:
    """An `ItemRequest` as a storable item, normalised the way facts are.

    `facts.clean_key` and `clean_value` rather than a second pair of rules:
    a key filed by hand and the same key proposed out of a day-10 fact block
    have to normalise identically, or the app ends up holding both and
    sending two contradictory lines.

    A request with no key gets one derived from what it says. A request whose
    key is one of the rule prefixes is filed always-relevant without being
    asked - that is the whole of what those prefixes now do, and doing it
    here means `recall` has one flag to read instead of two rules.
    """
    value = facts_module.clean_value(req.value)
    if not value:
        raise HTTPException(400, "There is nothing to remember here")
    key = facts_module.clean_key(req.key) if req.key else memory_module.slug(value)
    if not key:
        raise HTTPException(400, "That note has no words to name it by")
    source = req.source if isinstance(req.source, dict) else {}
    return MemoryItem(
        key=key,
        value=value,
        source=source or (default_source or {}),
        pinned=bool(req.pinned) or memory_module.always(key),
    )


def tasks_report() -> dict:
    """Every task, newest first, plus the long-term layer beside them.

    One shape returned by every day-11 endpoint, for the same reason
    `branch_state` is: they all change the same thing - what is on file - and
    a panel that had to work out which half of itself to refresh after each
    call would get it wrong eventually.
    """
    return {
        "tasks": [task_report(task) for task in tasks.all()],
        "long_term": [item_report(item) for item in long_term.load()],
    }


@app.get("/api/tasks")
def list_tasks() -> dict:
    return tasks_report()


@app.post("/api/tasks")
def create_task(req: TaskRequest) -> dict:
    task = tasks.create(req.title)
    return {**tasks_report(), "task": task_report(task)}


@app.post("/api/tasks/{task_id}/status")
def set_task_status(task_id: str, req: TaskStatusRequest) -> dict:
    """Close a task, or reopen it. Deletes nothing either way."""
    if req.status not in TASK_STATUSES:
        raise HTTPException(400, f"Unknown task status: {req.status}")
    task = tasks.set_status(task_id, req.status)
    if task is None:
        raise HTTPException(404, f"No such task: {task_id}")
    return {**tasks_report(), "task": task_report(task)}


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str) -> dict:
    """Forget a task and everything it learned.

    The chats that pointed at it are left alone rather than hunted down and
    edited: `attach_layers` treats a `task_id` with no task behind it as no
    task at all, so they read back as ordinary chats with no working layer.
    Rewriting every conversation that ever joined a task would be a lot of
    writes to achieve the same thing less safely.
    """
    if not tasks.delete(task_id):
        raise HTTPException(404, f"No such task: {task_id}")
    return tasks_report()


@app.post("/api/tasks/{task_id}/memory")
def add_task_item(task_id: str, req: ItemRequest) -> dict:
    """File something in a task's working memory."""
    task = tasks.add_item(task_id, clean_item(req))
    if task is None:
        raise HTTPException(404, f"No such task: {task_id}")
    return {**tasks_report(), "task": task_report(task)}


@app.delete("/api/tasks/{task_id}/memory/{key}")
def delete_task_item(task_id: str, key: str) -> dict:
    task = tasks.remove_item(task_id, key)
    if task is None:
        raise HTTPException(404, f"No such task: {task_id}")
    return {**tasks_report(), "task": task_report(task)}


@app.get("/api/memory")
def get_long_term() -> dict:
    return tasks_report()


@app.post("/api/memory")
def add_long_term(req: ItemRequest) -> dict:
    """File something in the layer that has no scope and no expiry."""
    long_term.add_item(clean_item(req))
    return tasks_report()


@app.delete("/api/memory/{key}")
def delete_long_term(key: str) -> dict:
    long_term.remove_item(key)
    return tasks_report()


@app.post("/api/conversations/{conversation_id}/task")
def attach_task(conversation_id: str, req: AttachRequest) -> dict:
    """Join this chat to a task, or take it out of one.

    Joining is what makes the second conversation about a piece of work
    start where the first one left off, and it is the only step in this app
    that connects two chats to each other at all.
    """
    if req.task_id and tasks.load(req.task_id) is None:
        raise HTTPException(404, f"No such task: {req.task_id}")
    conversation = store.set_task(valid_id(conversation_id), req.task_id)
    return {**tasks_report(), "memory": memory_report(conversation)}


@app.post("/api/conversations/{conversation_id}/proposals/{index}")
def decide_proposal(conversation_id: str, index: int, req: ProposalDecision) -> dict:
    """Accept one of the agent's suggestions, into whichever layer you say.

    Accepting with no `layer` files it where the agent proposed; naming one
    overrides that. Either way this is the only path by which anything the
    agent thought of reaches a layer, and it runs because a person clicked.
    """
    conversation = store.load(valid_id(conversation_id))
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    if not 0 <= index < len(conversation.proposals):
        raise HTTPException(404, "No such proposal")

    proposal = conversation.proposals[index]
    layer = req.layer or proposal.get("layer")
    if layer not in memory_module.WRITABLE:
        raise HTTPException(400, f"Cannot file into layer: {layer}")

    item = clean_item(
        # The agent's key is kept rather than re-derived: it is the only
        # thing that knows this is `constraint.language` and not a note that
        # happens to begin "answer in". `clean_item` turns that into a pin.
        ItemRequest(key=proposal["key"], value=proposal["value"]),
        # Unlike a button press there is no one message to point at - the
        # suggestion came from a turn, not a bubble - so the record says the
        # conversation and says the agent proposed it.
        default_source={"conversation": conversation.id, "proposed": True},
    )
    if layer == memory_module.LONG:
        long_term.add_item(item)
    else:
        if not conversation.task_id or tasks.load(conversation.task_id) is None:
            raise HTTPException(400, "This chat is not part of a task")
        tasks.add_item(conversation.task_id, item)

    remaining = [p for i, p in enumerate(conversation.proposals) if i != index]
    conversation = store.set_proposals(conversation.id, remaining) or conversation
    return {**tasks_report(), "memory": memory_report(conversation)}


@app.delete("/api/conversations/{conversation_id}/proposals/{index}")
def dismiss_proposal(conversation_id: str, index: int) -> dict:
    """Say no to a suggestion. It is dropped, not remembered as refused.

    Remembering refusals would need a fourth store and would buy little: the
    proposer is shown the keys already on file and told not to repeat them,
    and the ones it invents that you did not want are usually not the same
    ones twice.
    """
    conversation = store.load(valid_id(conversation_id))
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    if not 0 <= index < len(conversation.proposals):
        raise HTTPException(404, "No such proposal")
    remaining = [p for i, p in enumerate(conversation.proposals) if i != index]
    conversation = store.set_proposals(conversation.id, remaining) or conversation
    return {**tasks_report(), "memory": memory_report(conversation)}


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = require_message(req.message)
    settings = validate(req.settings)
    conversation_id = valid_id(req.conversation_id)

    # The three day-7 lines. Everything the agent knows about the past comes
    # from `history()`; everything the next process will know comes from
    # `append_turn`.
    saved = store.load(conversation_id)
    # Day 10 decides *which* transcript first. The request may name a branch;
    # otherwise the stored record knows which one is active, which is also the
    # one the page is showing.
    branch_id = req.branch or (saved.active_branch if saved else MAIN_BRANCH)
    if saved and saved.branch(branch_id) is None:
        raise HTTPException(400, f"No such branch: {branch_id}")
    # Days 9 and 10 sit between the two: what the agent is handed is no longer
    # "everything", it is whatever the window and the chosen strategy come to.
    # This is also where an extraction or a summarisation request may be made
    # and stored, before the question below is asked.
    plan = prepare_context(saved, settings, conversation_id, message, branch_id)
    agent = build_agent(settings, plan)

    reply = agent.ask(message)

    # Failed turns are written down too, so a reload shows the same screen you
    # were looking at - but `Conversation.history()` leaves them out of what
    # gets replayed, so an error never becomes something the model "said".
    conversation = store.append_turn(
        conversation_id,
        message,
        reply.answer,
        settings=settings.model_dump(),
        error=reply.error is not None,
        view=req.view,
        # Day 8: the provider's own count for this turn, saved with the answer
        # it paid for. A failed call has no usage at all - nothing was billed,
        # so nothing is recorded.
        usage=reply.usage,
        branch=branch_id,
    )

    # Day 11, and deliberately the last thing that happens. The proposer runs
    # *after* the answer has been produced and stored, so a turn that fails
    # here still leaves the user with their reply and their transcript - and
    # so the proposer can be shown what was answered, which is often where
    # the fact worth filing actually is ("use SQLite then" is a decision; the
    # message that prompted it was a question).
    #
    # Nothing it returns is written to a layer. It is stored on the
    # conversation as a suggestion and waits there for somebody to accept it,
    # which is what "you chose explicitly what goes where" means when the
    # agent is allowed to have an opinion.
    proposals = run_proposer(settings, conversation, plan, message, reply)
    if proposals is not None and proposals.ok:
        conversation = store.set_proposals(
            conversation_id, [p.to_dict() for p in proposals.proposals]
        ) or conversation

    return as_json(reply, conversation, plan, proposals)


def run_proposer(
    settings: Settings,
    conversation: Conversation,
    plan: ContextPlan,
    message: str,
    reply: AgentReply,
) -> ProposalRun | None:
    """Ask the agent what it would file, or don't. Never raises.

    Skipped entirely when the setting is off, and when the turn failed:
    there is no point paying to extract durable facts from an exchange whose
    assistant half is an error message.

    The keys already on file are sent so the model does not propose them
    again - from both layers at once, because a fact that is already
    long-term should not come back as a suggestion for the working one.
    """
    if not settings.memory_proposals or reply.error is not None:
        return None
    filed = long_term.load() + (plan.task.items if plan.task else [])
    return MemoryProposer(deepseek_client, settings.model).propose(
        message, reply.answer, filed
    )


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
