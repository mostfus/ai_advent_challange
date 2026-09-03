"""FastAPI backend for the DeepSeek chat GUI.

Thin HTTP wrapper around the same primitives used by the CLI in code.py
(models, system prompts, stop sequences, response format, ask_model). State
is kept in memory on the server - this is a single-user local tool, so a
module-level dict mirrors the CLI's local variables instead of e.g. a DB
or per-request auth.

Run with:
    uv run server.py
then open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import code as chat  # day3/code.py - the CLI module; reused as-is, not reimplemented

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="DeepSeek Chat")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

client = chat.load_client()

# Mirrors the CLI's current_model / current_system_prompt / stop / response_format.
state: dict = {
    "model": chat.DEFAULT_MODEL,
    "system_prompt": chat.DEFAULT_SYSTEM_PROMPT,
    "stop": None,
    "response_format": chat.DEFAULT_RESPONSE_FORMAT,
}


class ChatRequest(BaseModel):
    message: str


class SettingsRequest(BaseModel):
    model: str | None = None
    system_prompt: str | None = None
    stop: list[str] | None = None
    response_format: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    """Everything the frontend needs to render its settings panel."""
    return {
        "models": chat.MODELS,
        "system_prompts": [{"name": name, "prompt": prompt} for name, prompt in chat.SYSTEM_PROMPTS],
        "response_formats": chat.RESPONSE_FORMATS,
        "stop_sequences_limit": chat.STOP_SEQUENCES_LIMIT,
        "state": state,
    }


@app.post("/api/settings")
def update_settings(req: SettingsRequest) -> dict:
    if req.model is not None:
        if req.model not in chat.MODELS:
            raise HTTPException(400, f"Unknown model: {req.model}")
        state["model"] = req.model

    if req.system_prompt is not None:
        # Any non-empty string is accepted, same as the CLI's "custom prompt" option.
        state["system_prompt"] = req.system_prompt or chat.DEFAULT_SYSTEM_PROMPT

    if req.stop is not None:
        sequences = [s.strip() for s in req.stop if s.strip()][: chat.STOP_SEQUENCES_LIMIT]
        state["stop"] = sequences or None

    if req.response_format is not None:
        if req.response_format not in chat.RESPONSE_FORMATS:
            raise HTTPException(400, f"Unknown response format: {req.response_format}")
        state["response_format"] = req.response_format

    return {"state": state}


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message must not be empty")

    reply = chat.ask_model(
        client,
        state["model"],
        state["system_prompt"],
        message,
        state["stop"],
        state["response_format"],
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
