"""FastAPI backend: transport only.

Everything this file used to know about DeepSeek is gone. It no longer builds
a request body, no longer maps API errors, no longer knows that a system
prompt goes in `messages[0]`. It does three things: accept HTTP, construct an
Agent for the settings it was given, and hand the agent's reply back as JSON.

    browser -> /api/chat -> Agent(DeepSeekClient) -> DeepSeek

Swapping DeepSeek for another provider is a different `LLMClient` passed to
the same `Agent` on the line below - nothing in this file describes how a
request is shaped.

The server keeps no conversation or settings state: every call carries its
own settings block, which is what lets the compare view run several chats
side by side without them stepping on each other. The handler is a plain
`def`, so FastAPI runs it in its threadpool and the calls from several panes
really do overlap instead of queueing.

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
from agent import Agent, AgentConfig, AgentReply
from llm_client import DeepSeekClient, LLMClientError

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="LLM Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One client, reused by every agent this process builds - the HTTP connection
# pool is worth keeping, while the agent around it is cheap and per-request.
try:
    deepseek_client = DeepSeekClient.from_env()
except LLMClientError as exc:
    print(f"Error: {exc}")
    sys.exit(1)


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


def as_json(reply: AgentReply) -> dict:
    """The wire format the debug panel expects."""
    return {
        "answer": reply.answer,
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
    }


@app.post("/api/chat")
def post_chat(req: ChatRequest) -> dict:
    message = require_message(req.message)
    settings = validate(req.settings)

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
    )
    return as_json(agent.ask(message))


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
