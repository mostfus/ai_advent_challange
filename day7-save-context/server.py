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

The handler is a plain `def`, so FastAPI runs it in its threadpool and the
calls from several compare panes really do overlap instead of queueing; the
store takes a lock for the same reason.

Run with:
    uv run server.py
then open http://127.0.0.1:8000
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import agent as agent_module
import store as store_module
from agent import Agent, AgentConfig, AgentReply
from llm_client import DeepSeekClient, LLMClientError
from store import Conversation, ConversationStore, StoreError

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
    return settings


def valid_id(conversation_id: str) -> str:
    """A conversation id that is safe to turn into a file name."""
    try:
        return store_module.validate_id(conversation_id)
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc


def as_json(reply: AgentReply, conversation: Conversation) -> dict:
    """The wire format the browser expects: the answer, the debug transcript,
    and how much context now stands behind this chat."""
    return {
        "answer": reply.answer,
        "conversation": {
            "id": conversation.id,
            "message_count": len(conversation.messages),
        },
        "debug": {
            "request": reply.request,
            "response": reply.response,
            "error": reply.error,
            "elapsed_ms": reply.elapsed_ms,
            "url": reply.url,
            "status_code": reply.status_code,
            "request_headers": reply.request_headers,
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
        "context_messages": agent_module.DEFAULT_CONTEXT_MESSAGES,
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
    return {"conversations": [c.to_dict() for c in store.all()]}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    conversation = store.load(valid_id(conversation_id))
    if conversation is None:
        raise HTTPException(404, f"No saved conversation: {conversation_id}")
    return conversation.to_dict()


@app.put("/api/conversations/{conversation_id}")
def put_conversation(conversation_id: str, meta: ConversationMeta) -> dict:
    """Create a chat, or update its settings, without touching its messages.

    This is how a compare column registers itself the moment it is added, so
    an empty column - and, more to the point, the parameters someone just set
    on it - still exists after a restart.
    """
    settings = validate(meta.settings)
    conversation = store.upsert(valid_id(conversation_id), meta.view, settings.model_dump())
    return conversation.to_dict()


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
        return {"cleared": True, "messages": []}
    return {"cleared": True, "messages": conversation.to_dict()["messages"]}


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = require_message(req.message)
    settings = validate(req.settings)
    conversation_id = valid_id(req.conversation_id)

    # The three day-7 lines. Everything the agent knows about the past comes
    # from `history()`; everything the next process will know comes from
    # `append_turn`.
    saved = store.load(conversation_id)
    agent = Agent(
        deepseek_client,
        AgentConfig(
            model=settings.model,
            system_prompt=settings.system_prompt,
            stop=settings.stop or None,
            response_format=settings.response_format,
            reasoning_effort=settings.reasoning_effort,
            temperature=settings.temperature,
        ),
        history=saved.history() if saved else None,
    )

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
    )
    return as_json(reply, conversation)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
