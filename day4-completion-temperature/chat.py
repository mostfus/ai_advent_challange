"""Core primitives for talking to the DeepSeek LLM API.

Everything the web GUI needs - the option lists it renders, and `ask_model`,
the single call that goes over the wire. No interactive interface lives here
anymore: the CLI that shipped through day 3 is gone, the browser UI in
server.py + static/index.html is the only front end now.
"""

import os
import sys
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI, APIError, APIConnectionError, AuthenticationError

MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
]

# Per DeepSeek API docs (response_format): only "text" and "json_object" are supported.
RESPONSE_FORMATS = ["text", "json_object"]

# Per DeepSeek API docs (stop): up to 16 sequences are accepted.
STOP_SEQUENCES_LIMIT = 16

# DeepSeek's models think by default even with no reasoning_effort param sent
# at all (confirmed live: reasoning_content and usage.completion_tokens_details
# .reasoning_tokens show up unprompted). "off" is our own label for that
# off-switch - on the wire it's reasoning_effort="none", the one value that
# actually disables thinking; the rest (low/high/max) tune its budget.
REASONING_EFFORTS = ["off", "low", "high", "max"]
REASONING_EFFORT_OFF = "off"

# Per DeepSeek API docs (temperature): 0..2, default 1. Lower = more
# deterministic/repetitive, higher = more varied. The presets below are
# DeepSeek's own recommended values per use case; they're only shortcuts for
# the slider, any value in range can be sent.
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
TEMPERATURE_STEP = 0.1
DEFAULT_TEMPERATURE = 1.0
TEMPERATURE_PRESETS = [
    ("Coding / math", 0.0),
    ("Data analysis", 1.0),
    ("Conversation", 1.3),
    ("Creative writing", 1.5),
]

SYSTEM_PROMPTS = [
    ("Default", "You are a helpful assistant."),
    (
        "Step-by-step reasoning",
        "You are a careful, methodical assistant. Break every problem down into "
        "clear, numbered steps and work through them one at a time before giving "
        "your final answer. Show your reasoning, then clearly state the final "
        "answer at the end.",
    ),
]

DEFAULT_MODEL = MODELS[0]
DEFAULT_RESPONSE_FORMAT = RESPONSE_FORMATS[0]
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPTS[0][1]
DEFAULT_REASONING_EFFORT = "low"
BASE_URL = "https://api.deepseek.com"


def load_client() -> OpenAI:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY is not set.")
        print("Create a .env file in this folder (see .env.example) and add your DeepSeek API key.")
        sys.exit(1)
    return OpenAI(api_key=api_key, base_url=BASE_URL)


def clamp_temperature(value: float) -> float:
    """Keep a temperature inside the API's accepted range, rounded to one decimal."""
    return round(min(TEMPERATURE_MAX, max(TEMPERATURE_MIN, float(value))), 1)


@dataclass
class ModelReply:
    """Everything about one exchange with the model - not just the text answer.

    `request` and `response` are plain JSON-able dicts (the exact kwargs sent
    to the API, and the API's raw response) so callers - namely the web GUI's
    debug panel - can display exactly what went over the wire, alongside
    token usage (inside `response["usage"]`) and timing.
    """

    answer: str
    request: dict
    response: dict | None = None
    error: str | None = None
    elapsed_ms: float | None = None


def ask_model(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_message: str,
    stop: list[str] | None,
    response_format: str,
    reasoning_effort: str,
    temperature: float,
) -> ModelReply:
    request_kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    if stop:
        request_kwargs["stop"] = stop
    if response_format != "text":
        request_kwargs["response_format"] = {"type": response_format}
    # "off" is our label; DeepSeek's actual off-switch value is "none".
    request_kwargs["reasoning_effort"] = "none" if reasoning_effort == REASONING_EFFORT_OFF else reasoning_effort
    # Always sent, including the API's own default of 1.0, so the debug panel
    # shows the value that actually produced the answer instead of hiding it.
    request_kwargs["temperature"] = clamp_temperature(temperature)

    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**request_kwargs)
    except AuthenticationError:
        response = None
        error = "Error: authentication failed. Check your DEEPSEEK_API_KEY."
    except APIConnectionError:
        response = None
        error = "Error: could not connect to the DeepSeek API. Check your network connection."
    except APIError as exc:
        response = None
        error = f"Error: DeepSeek API returned an error: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error to the user
        response = None
        error = f"Error: unexpected error: {exc}"
    else:
        error = None
    elapsed_ms = (time.perf_counter() - start) * 1000

    if error is not None:
        return ModelReply(answer=error, request=request_kwargs, error=error, elapsed_ms=elapsed_ms)

    return ModelReply(
        answer=response.choices[0].message.content,
        request=request_kwargs,
        response=response.model_dump(mode="json"),
        elapsed_ms=elapsed_ms,
    )
