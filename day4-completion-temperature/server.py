"""FastAPI backend for the DeepSeek chat GUI.

Thin HTTP wrapper around the primitives in chat.py (models, system prompts,
stop sequences, response format, reasoning effort, temperature, ask_model).

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

import chat  # the API primitives; server.py only adds HTTP on top

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="DeepSeek Chat")
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


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
