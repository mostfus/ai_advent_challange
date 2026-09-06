"""Talking to OpenRouter's free models.

Day 5 asks one question - how do different models answer the same prompt? -
so this module is deliberately the opposite of chat.py. No temperature, no
stop sequences, no response format, no reasoning effort: those are day 4's
subject, and the free catalogue does not support them uniformly anyway
(`response_format` is offered by 7 of the 18 free models, `stop` by 6,
`reasoning_effort` by 4). Sending nothing but the message is what keeps the
columns comparable - every difference you see is the model, not a knob.

"Free" is enforced here rather than in the browser: the catalogue is filtered
down to what OpenRouter prices at 0/0, and `ask_model` refuses any id that is
not on that list, so a stale dropdown in an open tab cannot spend money.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import (
    OpenAI,
    APIConnectionError,
    APIError,
    AuthenticationError,
    RateLimitError,
)

from chat import ModelReply  # same shape the debug panel already knows how to render

BASE_URL = "https://openrouter.ai/api/v1"
MODELS_URL = f"{BASE_URL}/models"

# Per OpenRouter's rate-limit docs, free models are capped per account, not
# per model: 20 requests/minute, and 50/day until at least $10 of credits has
# been bought (1000/day after that). Nothing counts them - they are here so
# that a 429 can be explained in the answer bubble instead of leaking through
# as a bare status code.
RATE_LIMIT_PER_MINUTE = 20
RATE_LIMIT_PER_DAY = 50
RATE_LIMIT_PER_DAY_WITH_CREDITS = 1000

# The catalogue changes on the order of days; re-fetching it per request would
# add a round trip to every page load for nothing.
MODELS_CACHE_TTL_SECONDS = 3600

# Optional attribution headers, per OpenRouter's docs.
APP_URL = "http://127.0.0.1:8000"
APP_TITLE = "AI Advent - day 5"


class OpenRouterError(RuntimeError):
    """No API key, or the catalogue could not be fetched."""


@dataclass(frozen=True)
class FreeModel:
    id: str
    name: str
    context_length: int | None


def _is_free_chat_model(model: dict) -> bool:
    """True for zero-priced text models - and only those.

    Zero-priced alone is not enough: OpenRouter also lists free *media* models
    (`google/lyria-*`, audio out) and `openrouter/free`, an auto-router that
    picks a free model for you. Neither belongs in a column that is supposed to
    name the model that answered.
    """
    pricing = model.get("pricing") or {}
    try:
        if any(float(pricing.get(key) or 0) > 0 for key in ("prompt", "completion")):
            return False
    except (TypeError, ValueError):
        return False  # unparseable price - treat as not free rather than guess

    if not str(model.get("id", "")).endswith(":free"):
        return False

    output_modalities = (model.get("architecture") or {}).get("output_modalities") or []
    return "text" in output_modalities


_models_cache: tuple[float, list[FreeModel]] | None = None


def free_models(force_refresh: bool = False) -> list[FreeModel]:
    """The free chat models OpenRouter is serving right now.

    Unauthenticated - /models is public, so the tab can render its dropdown
    even before an API key is configured.
    """
    global _models_cache

    now = time.monotonic()
    if not force_refresh and _models_cache and now - _models_cache[0] < MODELS_CACHE_TTL_SECONDS:
        return _models_cache[1]

    try:
        with urllib.request.urlopen(MODELS_URL, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OpenRouterError(f"could not fetch OpenRouter's model list: {exc}") from exc

    models = [
        FreeModel(
            id=entry["id"],
            name=entry.get("name") or entry["id"],
            context_length=entry.get("context_length"),
        )
        for entry in payload.get("data", [])
        if _is_free_chat_model(entry)
    ]
    models.sort(key=lambda model: model.name.lower())

    _models_cache = (now, models)
    return models


def is_free(model_id: str) -> bool:
    return any(model.id == model_id for model in free_models())


_client: OpenAI | None = None


def has_key() -> bool:
    load_dotenv()
    return bool(os.getenv("OPENROUTER_API_KEY"))


def load_client() -> OpenAI:
    """Built on first use, never at import.

    chat.load_client() exits the process when its key is missing, which is
    right for a single-provider app; here a missing key must break only this
    one tab and leave the DeepSeek ones working, so the failure is an
    exception raised at request time instead.
    """
    global _client

    if _client is None:
        load_dotenv()
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise OpenRouterError(
                "OPENROUTER_API_KEY is not set. Add it to .env (see .env.example); "
                "get a key at https://openrouter.ai/keys."
            )
        _client = OpenAI(
            api_key=api_key,
            base_url=BASE_URL,
            default_headers={"HTTP-Referer": APP_URL, "X-Title": APP_TITLE},
        )
    return _client


def ask_model(model: str, user_message: str) -> ModelReply:
    """One message, one model, nothing else on the wire."""
    request_kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": user_message}],
    }

    start = time.perf_counter()
    try:
        client = load_client()
        response = client.chat.completions.create(**request_kwargs)
    except OpenRouterError as exc:
        response, error = None, f"Error: {exc}"
    except AuthenticationError:
        response = None
        error = "Error: authentication failed. Check your OPENROUTER_API_KEY."
    except RateLimitError:
        response = None
        error = (
            "Error: OpenRouter rate limit hit. Free models allow "
            f"{RATE_LIMIT_PER_MINUTE} requests/minute and {RATE_LIMIT_PER_DAY} per day "
            f"({RATE_LIMIT_PER_DAY_WITH_CREDITS}/day once you have bought $10 of credits). "
            "Wait a moment and try again, or send to fewer models at once."
        )
    except APIConnectionError:
        response = None
        error = "Error: could not connect to OpenRouter. Check your network connection."
    except APIError as exc:
        response, error = None, f"Error: OpenRouter returned an error: {exc}"
    except Exception as exc:  # noqa: BLE001 - surface any unexpected error to the user
        response, error = None, f"Error: unexpected error: {exc}"
    else:
        error = None
    elapsed_ms = (time.perf_counter() - start) * 1000

    if error is not None:
        return ModelReply(answer=error, request=request_kwargs, error=error, elapsed_ms=elapsed_ms)

    # Reasoning models sometimes put everything in `reasoning` and leave
    # `content` empty; showing a blank bubble would read as a bug rather than
    # as what the model actually did.
    message = response.choices[0].message
    answer = (message.content or "").strip()
    if not answer:
        answer = (getattr(message, "reasoning", None) or "").strip()
        answer = f"(no content - the model returned only reasoning)\n\n{answer}" if answer else "(empty response)"

    return ModelReply(
        answer=answer,
        request=request_kwargs,
        response=response.model_dump(mode="json"),
        elapsed_ms=elapsed_ms,
    )
