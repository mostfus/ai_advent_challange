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
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent as agent_module
import compaction
import store as store_module
import tokens
from agent import Agent, AgentConfig, AgentReply
from compaction import Compaction, Compactor
from llm_client import DeepSeekClient, LLMClientError
from store import Conversation, ConversationStore, StoreError, Summary

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="LLM Agent with saved context")
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


class Settings(BaseModel):
    """One agent's configuration, sent by the client with every message."""

    model: str = agent_module.DEFAULT_MODEL
    system_prompt: str = agent_module.DEFAULT_SYSTEM_PROMPT
    stop: list[str] = Field(default_factory=list)
    response_format: str = agent_module.DEFAULT_RESPONSE_FORMAT
    reasoning_effort: str = agent_module.DEFAULT_REASONING_EFFORT
    temperature: float = agent_module.DEFAULT_TEMPERATURE
    # Day 9. How many messages of the transcript are replayed verbatim, and
    # whether everything older than that is summarised or simply dropped.
    # Settings of the *conversation* rather than of the request - which is why
    # they live here, get stored with the chat, and are sent back up with
    # every message like every other parameter.
    context_messages: int = agent_module.DEFAULT_CONTEXT_MESSAGES
    context_compression: bool = agent_module.DEFAULT_CONTEXT_COMPRESSION


class ChatRequest(BaseModel):
    message: str
    # Which conversation this message belongs to. Defaulted rather than
    # required so a bare {"message": "..."} - curl, a script - still works and
    # lands in the single chat.
    conversation_id: str = store_module.SINGLE_CONVERSATION_ID
    view: str = store_module.DEFAULT_VIEW
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

    The whole of day 9's decision, made in one place before the agent is
    built: which messages go up verbatim, which paragraph stands in for the
    rest, and - when one had to be written - the summarisation exchange it
    took, so the debug panel can show a request the user did not type.
    """

    history: list[dict]
    summary: str | None = None
    covers: int = 0
    replayable: int = 0
    compaction: Compaction | None = None


def prepare_context(
    conversation: Conversation | None, settings: Settings, conversation_id: str
) -> ContextPlan:
    """Decide what the next request remembers - and pay for it if it has to.

    With compression off this is day 7 exactly: the last `context_messages`
    messages, and everything older simply not sent. The history is still all
    on disk; it is just not in the request.

    With it on, the same cut is made and the offcut is summarised instead of
    dropped. `compaction.split` says what that means concretely, and its
    `covers` return is what keeps the cost down: a summary that already
    reaches the cut is reused as it stands, so a hundred-message chat pays for
    a compression when it overflows, not on every message after that.

    A failed compression is not a failed turn. The summary that was already
    stored (if any) still goes up, the window still goes up, and the messages
    in between are missing from this one request the way day 7's were - the
    user gets their answer, and the debug panel says what happened.
    """
    replay = conversation.history() if conversation else []
    window = settings.context_messages

    if not settings.context_compression:
        return ContextPlan(history=replay[-window:] if window else [], replayable=len(replay))

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
            replayable=len(replay),
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
            replayable=len(replay),
            compaction=result,
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
    )

    return ContextPlan(
        history=kept,
        summary=result.text,
        covers=covers,
        replayable=len(replay),
        compaction=result,
    )


def build_agent(settings: Settings, plan: ContextPlan) -> Agent:
    """One short-lived agent, configured and handed the context to run on.

    Day 7's three lines, minus the third - except that what it is handed is no
    longer simply "the transcript so far". It is whatever `prepare_context`
    decided the transcript should look like from in here.
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
    )


def usage_report(conversation: Conversation | None) -> dict:
    """What this conversation has cost, out of what DeepSeek reported.

    Two figures, because they answer two different questions:

    * **last** - the turn that just happened: how big the request was
      (`prompt_tokens` - the system prompt, the replayed history and the new
      question together), how big the answer was (`completion_tokens`), and
      how much of that answer was thinking nobody sees.
    * **total** - the same fields added up over every turn in the chat. It is
      not the size of the conversation; it is the sum of every copy of the
      conversation that has been uploaded, once per turn. That is what makes
      a long chat expensive, and it is invisible in every other view here.

    `last` is None until something has actually been billed - a brand-new
    chat, or one whose only turns failed. Nothing is invented to fill the
    gap: a zero would say "this answer was free", which is a different claim
    than "there is no answer yet".
    """
    usages = conversation.usages() if conversation else []
    last = usages[-1] if usages else None
    summary = conversation.summary if conversation else None
    return {
        # Read back through the known fields rather than passed on whole:
        # these dicts come off disk, where an older version of this app may
        # have written keys that no longer mean anything.
        "last": {key: tokens.as_int(last.get(key)) for key in tokens.EMPTY_USAGE} if last else None,
        "total": tokens.sum_usage(usages),
        # Day 9, and reported on its own line rather than folded into the
        # total above. Compression is money spent on the app's own bookkeeping
        # instead of on an answer, and adding it to the turns would hide the
        # only comparison worth making: what the summaries cost against what
        # not sending those messages saves on every turn since.
        "compression": {
            **{
                key: tokens.as_int((summary.usage_total if summary else {}).get(key))
                for key in tokens.EMPTY_USAGE
            },
            # How many summarisation requests this chat has made - the count
            # that matters here, where `turns` is the count that matters above.
            "compressions": summary.compressions if summary else 0,
        },
        "messages": len(conversation.messages) if conversation else 0,
    }


def context_report(conversation: Conversation | None, plan: ContextPlan | None = None) -> dict:
    """What this chat's memory currently looks like from the outside.

    Answers, in one block, the three questions day 9 exists to make
    answerable: how much has been said, how much of it the next request will
    carry as itself, and what the rest has been compressed into. The summary
    text is included in full - it goes into every request from here on, so
    being able to read it is not a nicety.

    Built from the stored record, so the page draws the same thing on a reload
    as it did after the last turn; `plan` adds what is only true of the turn
    that has just happened.
    """
    settings = (conversation.settings if conversation else None) or {}
    summary = conversation.summary if conversation else None
    replayable = plan.replayable if plan else len(conversation.history()) if conversation else 0

    report = {
        "compression": bool(settings.get("context_compression", agent_module.DEFAULT_CONTEXT_COMPRESSION)),
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
    }
    if plan:
        # What actually went up a moment ago, as opposed to what the settings
        # say should go up next time.
        report["sent"] = {
            "messages": len(plan.history),
            "summarised": plan.covers,
            "summary_used": bool(plan.summary),
            "compressed_now": bool(plan.compaction and plan.compaction.ok),
            "error": plan.compaction.error if plan.compaction else None,
        }
    return report


def with_usage(conversation: Conversation) -> dict:
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


def as_json(reply: AgentReply, conversation: Conversation, plan: ContextPlan) -> dict:
    """The wire format the browser expects: the answer, the debug transcript,
    how much context now stands behind this chat, and what it has cost."""
    return {
        "answer": reply.answer,
        "conversation": {
            "id": conversation.id,
            "message_count": len(conversation.messages),
        },
        # Day 9: what this request remembered, and how. Read off the stored
        # conversation for the same reason the usage below is - so the readout
        # and the file cannot disagree.
        "context": context_report(conversation, plan),
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
            # None unless this turn had to compress something first.
            "compaction": compaction_debug(plan.compaction),
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
        "context_compression_default": agent_module.DEFAULT_CONTEXT_COMPRESSION,
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
        }
    return {
        "cleared": True,
        "messages": conversation.to_dict()["messages"],
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


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = require_message(req.message)
    settings = validate(req.settings)
    conversation_id = valid_id(req.conversation_id)

    # The three day-7 lines. Everything the agent knows about the past comes
    # from `history()`; everything the next process will know comes from
    # `append_turn`.
    saved = store.load(conversation_id)
    # Day 9 sits between the two: what the agent is handed is no longer
    # "everything", it is whatever the window and the summary come to. This is
    # also where a summarisation request may be made and stored, before the
    # question below is asked.
    plan = prepare_context(saved, settings, conversation_id)
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
    )
    return as_json(reply, conversation, plan)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
