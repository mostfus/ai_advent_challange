"""FastAPI backend for the chat GUI.

Thin HTTP wrapper around two independent providers:

  /api/chat       -> chat.py, DeepSeek, the full parameter set (day 1-4)
  /api/free-chat  -> openrouter.py, OpenRouter's free models, no parameters
                     at all (day 5)

They share nothing but this file and the debug-entry shape, on purpose: the
DeepSeek views keep working exactly as before, and a missing OpenRouter key
breaks only the tab that needs one.

The server holds no conversation or settings state: every /api/chat call
carries its own settings block. That is what lets the compare view run
several chats side by side, each with its own model and parameters, without
them stepping on each other - there is nothing shared to step on. /api/chat
is a plain `def`, so FastAPI runs it in its threadpool and the calls from
several panes really do overlap instead of queueing.

Run with:
    uv run server.py
then open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import chat  # DeepSeek primitives; server.py only adds HTTP on top
import openrouter  # OpenRouter's free models, day 5's tab

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="LLM Chat")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

client = chat.load_client()


class Settings(BaseModel):
    """One chat's request parameters, sent by the client with every message."""

    model: str = chat.DEFAULT_MODEL
    system_prompt: str = chat.DEFAULT_SYSTEM_PROMPT
    stop: list[str] = Field(default_factory=list)
    response_format: str = chat.DEFAULT_RESPONSE_FORMAT
    reasoning_effort: str = chat.DEFAULT_REASONING_EFFORT
    temperature: float = chat.DEFAULT_TEMPERATURE


class ChatRequest(BaseModel):
    message: str
    settings: Settings = Field(default_factory=Settings)


class FreeChatRequest(BaseModel):
    """A free-model request: which model, and what to ask it. That is all."""

    message: str
    model: str


def validate(settings: Settings) -> Settings:
    """Reject anything the DeepSeek API would reject, with a readable message."""
    if settings.model not in chat.MODELS:
        raise HTTPException(400, f"Unknown model: {settings.model}")
    if settings.response_format not in chat.RESPONSE_FORMATS:
        raise HTTPException(400, f"Unknown response format: {settings.response_format}")
    if settings.reasoning_effort not in chat.REASONING_EFFORTS:
        raise HTTPException(400, f"Unknown reasoning effort: {settings.reasoning_effort}")
    if not chat.TEMPERATURE_MIN <= settings.temperature <= chat.TEMPERATURE_MAX:
        raise HTTPException(
            400, f"Temperature must be between {chat.TEMPERATURE_MIN} and {chat.TEMPERATURE_MAX}"
        )

    settings.system_prompt = settings.system_prompt.strip() or chat.DEFAULT_SYSTEM_PROMPT
    settings.stop = [s.strip() for s in settings.stop if s.strip()][: chat.STOP_SEQUENCES_LIMIT]
    settings.temperature = chat.clamp_temperature(settings.temperature)
    return settings


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    """Everything the frontend needs to render a settings form."""
    return {
        "models": chat.MODELS,
        "system_prompts": [{"name": name, "prompt": prompt} for name, prompt in chat.SYSTEM_PROMPTS],
        "response_formats": chat.RESPONSE_FORMATS,
        "reasoning_efforts": chat.REASONING_EFFORTS,
        "stop_sequences_limit": chat.STOP_SEQUENCES_LIMIT,
        "temperature": {
            "min": chat.TEMPERATURE_MIN,
            "max": chat.TEMPERATURE_MAX,
            "step": chat.TEMPERATURE_STEP,
            "default": chat.DEFAULT_TEMPERATURE,
            "presets": [{"name": name, "value": value} for name, value in chat.TEMPERATURE_PRESETS],
        },
        "max_compare_chats": 5,
        "defaults": Settings().model_dump(),
    }


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message must not be empty")

    settings = validate(req.settings)
    reply = chat.ask_model(
        client,
        settings.model,
        settings.system_prompt,
        message,
        settings.stop or None,
        settings.response_format,
        settings.reasoning_effort,
        settings.temperature,
    )
    return {
        "answer": reply.answer,
        "debug": {
            "request": reply.request,
            "response": reply.response,
            "error": reply.error,
            "elapsed_ms": reply.elapsed_ms,
        },
    }


@app.get("/api/free-models")
def get_free_models() -> dict:
    """OpenRouter's free catalogue, for day 5's tab.

    Fetched lazily so the DeepSeek views load instantly and stay usable even
    when OpenRouter is unreachable - the browser only calls this the first
    time that tab is opened.
    """
    try:
        models = openrouter.free_models()
    except openrouter.OpenRouterError as exc:
        raise HTTPException(502, str(exc)) from exc

    return {
        "models": [
            {"id": model.id, "name": model.name, "context_length": model.context_length}
            for model in models
        ],
        "key_configured": openrouter.has_key(),
        "max_compare_chats": 5,
    }


@app.post("/api/free-chat")
def post_free_chat(req: FreeChatRequest) -> dict:
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message must not be empty")

    # The paid catalogue is 400+ models on the same endpoint, so "free" has to
    # be checked here: a browser tab left open across a catalogue change must
    # not be able to bill anyone.
    try:
        if not openrouter.is_free(req.model):
            raise HTTPException(400, f"{req.model} is not one of OpenRouter's free models")
    except openrouter.OpenRouterError as exc:
        raise HTTPException(502, str(exc)) from exc

    reply = openrouter.ask_model(req.model, message)
    return {
        "answer": reply.answer,
        "debug": {
            "request": reply.request,
            "response": reply.response,
            "error": reply.error,
            "elapsed_ms": reply.elapsed_ms,
        },
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
