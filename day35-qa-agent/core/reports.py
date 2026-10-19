"""What the page is handed: the conversation, a turn, and what each has cost - as JSON.

The readouts every chat response carries, built from the stored records (so
a reload draws the same thing the last turn did) plus, after a turn, what is
only true of that turn - the `ContextPlan` it was built from and the side
requests it made. Pure functions over records; the two that need more than
the conversation (`with_usage`, `turn_report`) are handed the runtime.

    usage_report      what the conversation cost
    context_report    the window, the summary or facts, the task state
    with_usage        a conversation as the page wants it
    turn_report       the reply to POST /api/chat
    *_debug           the side requests, for the debug panel
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import agent as agent_module
from core import strategies, tokens
from core import task_state as task_state_module
from core.compaction import Compaction
from core.context import ContextPlan
from core.facts import FactUpdate
from core.invariants import CheckRun
from core.memory import ProposalRun
from core.phases import AdviceRun
from core.task_state import StateUpdate
from storage import Conversation, Facts, TaskState

if TYPE_CHECKING:
    from core.runtime import AgentRuntime
    from core.turn import TurnResult


def usage_block(usage: dict | None) -> dict:
    """One usage dict, read back through the fields this app knows about.

    Read back rather than passed on whole: these come off disk, where an older
    version may have written keys that no longer mean anything.
    """
    usage = usage or {}
    return {key: tokens.as_int(usage.get(key)) for key in tokens.EMPTY_USAGE}


def usage_report(conversation: Conversation | None) -> dict:
    """What this conversation has cost, out of what DeepSeek reported.

    Four figures now, because they answer four different questions:

    * **last** - the turn that just happened: how big the request was
      (`prompt_tokens` - the system prompt, the replayed history and the new
      question together), how big the answer was (`completion_tokens`), and
      how much of that answer was thinking nobody sees.
    * **total** - the same fields added up over every turn of the chat. It
      is not the size of the conversation; it is the sum of
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
    usages = conversation.usages() if conversation else []
    last = usages[-1] if usages else None
    summary = conversation.summary if conversation else None
    facts = conversation.facts if conversation else None
    return {
        "last": usage_block(last) if last else None,
        "total": tokens.sum_usage(usages),
        "compression": {
            **usage_block(summary.usage_total if summary else None),
            # How many summarisation requests this chat has made - the count
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
        "messages": len(conversation.messages) if conversation else 0,
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


def task_state_report(state: TaskState | None) -> dict | None:
    """Day 25: the task state as the panel draws it - every item, and what keeping it costs."""
    if state is None:
        return None
    content = task_state_module.normalise(state.content)
    return {
        **content,
        "count": task_state_module.count(content),
        "updated_at": state.updated_at,
        "model": state.model,
        "updates": state.updates,
        "usage_total": state.usage_total or {},
    }


def context_report(conversation: Conversation | None, plan: ContextPlan | None = None) -> dict:
    """What this chat's memory currently looks like from the outside.

    Answers, in one block, the questions the last two days exist to make
    answerable: how much has been said, how much of it the next request will
    carry as itself, and what happens to the rest under the strategy this chat
    is set to.

    Built from the stored record, so the page draws the same thing on a reload
    as it did after the last turn; `plan` adds what is only true of the turn
    that has just happened.
    """
    settings = (conversation.settings if conversation else None) or {}
    summary = conversation.summary if conversation else None
    replayable = plan.replayable if plan else \
        len(conversation.history()) if conversation else 0

    report = {
        "strategy": plan.strategy if plan else strategies.from_settings(settings),
        "window": agent_module.clamp_context_messages(
            settings.get("context_messages", agent_module.DEFAULT_CONTEXT_MESSAGES)
        ),
        "messages": len(conversation.messages) if conversation else 0,
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
        "facts": facts_report(conversation.facts if conversation else None),
        # Day 25: the RAG chat's task state, in full - it goes up with every
        # RAG request and into every search.
        "task_state": task_state_report(conversation.task_state if conversation else None),
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
            "task_state_changes": plan.task_state_changes,
            "task_state_error": plan.task_state_update.error if plan.task_state_update else None,
        }
    return report


def with_usage(rt: "AgentRuntime", conversation: Conversation) -> dict:
    """A conversation as the browser wants it: the record, plus what it cost.

    Attached here rather than in `Conversation.to_dict` on purpose - that
    method is also what gets written to disk, and a total that can be
    recomputed from the turns beneath it has no business being stored next
    to them.
    """
    return {
        **conversation.to_dict(),
        "usage": usage_report(conversation),
        "context": context_report(conversation),
        # Day 11, and this is what makes a reload restore the memory panel
        # rather than an empty one: the layers are read off disk here with no
        # `plan`, so the page draws what is on file even when this chat has
        # not been spoken to since the process started.
        "memory": rt.memory.memory_report(conversation),
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


def task_state_debug(update: StateUpdate | None, changes: list) -> dict | None:
    """Day 25: the task-state exchange, in the shape of every other one - with what it changed."""
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
        "changes": changes,
        "summary": "failed" if update.error else
            (f"{len(changes)} " + ("change" if len(changes) == 1 else "changes")) if changes else "nothing new",
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


def invariants_debug(run: CheckRun | None) -> dict | None:
    """One checker request, for the debug log - the same shape as the others.

    `hits` is served beside `violations` because the pair is the day in one
    line. Three markers found and none of them a violation is the checker
    working perfectly, and it is indistinguishable from the checker doing
    nothing unless both halves are on screen.
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
        "violations": [v.to_dict() for v in run.violations],
        "hits": [h.to_dict() for h in run.hits],
        "retried": run.retried,
        "describe": run.describe(),
    }


def state_debug(run: AdviceRun | None) -> dict | None:
    """One watcher request, for the debug log - the same shape as the others.

    Two extra fields, and they are the day in the debug panel: what the model
    reported, and what the machine did about it. Either alone is unreadable.
    "the agent said done" next to "refused: execution cannot go straight to
    done" is the whole argument for having a machine at all, and it is one
    line.
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
        "advice": run.advice.to_dict() if run.advice else None,
        "suggested": run.suggested,
        # Whether the answer this run judged was thrown away and asked for
        # again. The one field here that describes something the user saw -
        # or rather, did not see.
        "retried": run.retried,
        "ticked": list(run.ticked),
        "decision": (
            {"ok": run.decision.ok, "code": run.decision.code, "reason": run.decision.reason}
            if run.decision is not None
            else None
        ),
        "summary": run.describe(),
    }


def turn_report(rt: "AgentRuntime", result: "TurnResult") -> dict:
    """The wire format the browser expects: the answer, the debug transcript,
    how much context now stands behind this chat, and what it has cost."""
    reply, conversation, plan = result.reply, result.conversation, result.plan
    proposals, advice, check = result.proposals, result.advice, result.check
    return {
        "answer": reply.answer,
        "conversation": {
            "id": conversation.id,
            "message_count": len(conversation.messages),
        },
        # What this request remembered, and how. Read off the stored
        # conversation for the same reason the usage below is - so the readout
        # and the file cannot disagree.
        "context": context_report(conversation, plan),
        # Day 11: the same question asked of the other two layers - what is on
        # file, and which of it this request actually carried.
        "memory": rt.memory.memory_report(conversation, plan),
        # Day 12: which profile this request was answered under. Sent with
        # every reply so that a profile switched on in another tab - or, more
        # to the point, the fact that none is switched on - is visible on the
        # screen that is showing the answer it produced.
        "personality": rt.personality.report(result.personality),
        # Day 14: which rules this request was answered under, sent with every
        # reply for `personality`'s reason - a rule switched off in another tab
        # should be visible on the screen showing the answer it stopped
        # constraining.
        "invariants": rt.invariants.report(result.invariant_record),
        # What was broken this turn, if anything, and enough to draw the
        # receipt under the answer with: which rule, whose side it came from,
        # and whether the answer on screen is the second one.
        "violations": (
            {
                "found": [v.to_dict() for v in check.violations],
                "retried": check.retried,
                "describe": check.describe(),
            }
            if check is not None and check.violations
            else None
        ),
        # Day 13. What the agent has asked to do about the stage, if anything
        # - raised in the transcript where the exchange that prompted it is,
        # as well as in the panel. `memory.task.suggested` is the same record;
        # this one says it came out of *this* turn, which is what lets it be
        # shown once rather than on every redraw.
        "suggestion": (
            plan.task.suggested
            if advice is not None and advice.suggested and plan.task is not None
            else None
        ),
        # Day 17. What this turn actually ran, in order - drawn under the
        # answer as the cards and the call receipts. It is the same list that
        # was just stored on the message, so a reload shows the same thing.
        "tools": reply.tool_calls,
        # Day 22. What the index returned for this question and which of it
        # the answer cites - drawn under the answer as its sources. None when
        # the chat is not in RAG mode.
        "rag": result.sources,
        # Day 34. What an agentic-code turn's writes did to the project, one
        # diff per file - the Changes button under the answer. None when the
        # turn changed nothing (or was not agentic).
        "changes": result.changes or None,
        # And what was on offer: how many tools the request carried, from how
        # many servers. Sent even when nothing was called, because "it had
        # them and chose not to" and "it had none" are different answers to
        # the only question anybody asks of a tool-using agent.
        "toolbox": {
            "offered": len(reply.request.get("tools") or []) if reply.request else 0,
        },
        # Read back *after* the turn was stored, so the readout the browser
        # draws describes the conversation as it now is - one turn longer,
        # and one turn more expensive to continue.
        "usage": usage_report(conversation),
        "debug": {
            "request": reply.request,
            "response": reply.response,
            "error": reply.error,
            "elapsed_ms": reply.elapsed_ms,
            "url": reply.url,
            "status_code": reply.status_code,
            "request_headers": reply.request_headers,
            "usage": reply.usage,
            # Day 17: the rounds of the tool loop *before* the one that
            # produced the answer. The request above is the last one; these
            # are the ones that asked for a tool instead, and they were billed
            # too - which is the part of tool use that is easiest to hide.
            "rounds": reply.rounds,
            # Each None unless this turn had to buy something first. Never
            # both: the strategies that produce them are exclusive.
            "compaction": compaction_debug(plan.compaction),
            "facts": facts_debug(plan.fact_update),
            # Day 25: the task-state request, made before the search it fed.
            "task_state": task_state_debug(plan.task_state_update, plan.task_state_changes),
            "proposals": proposals_debug(proposals),
            # Day 13: the second after-the-answer request, and the only one in
            # this app whose result changes something on disk without a person
            # seeing it first. That is exactly why it is in here.
            "state": state_debug(advice),
            # Day 14: the other after-the-answer request, and the only one in
            # this app that can replace the answer outright. Day 13's changes
            # a field on disk; this one changes what you are reading.
            "invariants": invariants_debug(check),
        },
    }
