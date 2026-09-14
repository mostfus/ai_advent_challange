"""The HTTP layer: how a request actually reaches an LLM.

Day 6 drops the `openai` SDK entirely. Not because the SDK is bad, but
because it hides the one thing this day is about: talking to an LLM is an
ordinary HTTP POST of a JSON body to a URL, and the answer is ordinary JSON
coming back. Everything that used to be `client.chat.completions.create(...)`
is spelled out here - the endpoint, the Authorization header, the body, the
status code, the timeout.

This module knows about HTTP and about one provider's quirks. It knows
nothing about agents, prompts or parameters: it is handed a finished request
body and hands back what came off the wire. The layer above (agent.py) is
what decides *what* to send.

`LLMClient` is deliberately generic and `DeepSeekClient` a five-line subclass:
most hosted LLMs speak the same OpenAI-compatible chat-completions protocol,
so adding a provider means a new subclass and nothing else.

    Agent  ->  LLMClient.complete(body)  ->  POST /chat/completions  ->  API
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

CHAT_COMPLETIONS_PATH = "/chat/completions"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Long enough for a reasoning model on a hard prompt: DeepSeek thinks before
# answering, and a `reasoning_effort: max` run can spend a while doing it.
DEFAULT_TIMEOUT_SECONDS = 120.0


class LLMClientError(RuntimeError):
    """Something went wrong before or during the HTTP call.

    Carries a message already written for a human to read in a chat bubble -
    the UI prints it as-is, so no caller has to translate status codes. When
    the failure happened *after* a response came back (a 401, a 429), the URL
    and status code ride along, so the debug panel can show the failed call
    on the same terms as a successful one.
    """

    def __init__(self, message: str, url: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.url = url
        self.status_code = status_code


@dataclass
class LLMResponse:
    """One HTTP exchange with an LLM API, as it actually happened.

    Unlike day 5 - which showed the kwargs it *meant* to send, reconstructed
    after the fact - `request` here is the exact JSON body that went out and
    `response` the exact JSON that came back, so the debug panel is a
    transcript rather than a retelling.
    """

    request: dict
    response: dict
    url: str
    status_code: int
    elapsed_ms: float
    request_headers: dict = field(default_factory=dict)


def _attempted_url(exc: httpx.RequestError) -> str | None:
    """The URL a failed request was aimed at, when httpx recorded one.

    `RequestError.request` raises rather than returning None when it was
    never set, so this is a question that has to be asked carefully.
    """
    try:
        return str(exc.request.url)
    except RuntimeError:
        return None


class LLMClient:
    """A minimal HTTP client for an OpenAI-compatible chat-completions API.

    One `httpx.Client` is kept open per instance so repeated calls reuse the
    same TCP/TLS connection instead of re-establishing one per message.
    """

    #: Shown in error messages, and used to name which key is missing.
    provider_name = "LLM"
    env_var = ""
    base_url = ""

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            **self.extra_headers(),
        }
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def extra_headers(self) -> dict:
        """Provider-specific headers, if any. Overridden by subclasses."""
        return {}

    @property
    def redacted_headers(self) -> dict:
        """The headers this client sends, with the bearer token blanked out.

        A property of the client rather than of one exchange, so a request
        that failed before returning anything can still be shown with the
        headers it was sent with. It is rendered in the browser's debug
        panel, which is precisely where an API key must not appear.
        """
        return {
            key: ("Bearer ***" if key == "Authorization" else value)
            for key, value in self._headers.items()
        }

    @classmethod
    def api_key(cls) -> str | None:
        load_dotenv()
        return os.getenv(cls.env_var)

    @classmethod
    def from_env(cls, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> "LLMClient":
        key = cls.api_key()
        if not key:
            raise LLMClientError(
                f"{cls.env_var} is not set. Add it to .env in this folder "
                f"(see .env.example) to use {cls.provider_name}."
            )
        return cls(key, timeout=timeout)

    def complete(self, body: dict) -> LLMResponse:
        """POST one chat-completions request and return what came back.

        Raises `LLMClientError` with a readable message for anything that is
        not a 200 with JSON in it - the agent above never sees a status code.
        """
        start = time.perf_counter()
        try:
            http_response = self._http.post(
                CHAT_COMPLETIONS_PATH, headers=self._headers, json=body
            )
        except httpx.TimeoutException as exc:
            raise LLMClientError(
                f"{self.provider_name} did not answer within "
                f"{self._http.timeout.read:.0f}s. Try a shorter prompt or a lower "
                "reasoning effort.",
                url=_attempted_url(exc),
            ) from exc
        except httpx.RequestError as exc:
            raise LLMClientError(
                f"could not reach {self.provider_name} ({exc.__class__.__name__}). "
                "Check your network connection.",
                url=_attempted_url(exc),
            ) from exc
        elapsed_ms = (time.perf_counter() - start) * 1000

        url = str(http_response.request.url)
        status = http_response.status_code
        if status != httpx.codes.OK:
            raise LLMClientError(self.describe_failure(http_response), url=url, status_code=status)

        try:
            payload = http_response.json()
        except ValueError as exc:
            raise LLMClientError(
                f"{self.provider_name} returned a non-JSON body (HTTP {status}).",
                url=url,
                status_code=status,
            ) from exc

        return LLMResponse(
            request=body,
            response=payload,
            url=url,
            status_code=http_response.status_code,
            elapsed_ms=elapsed_ms,
            request_headers=self.redacted_headers,
        )

    def describe_failure(self, http_response: httpx.Response) -> str:
        """Turn a non-200 into a sentence worth showing the user.

        The SDK used to do this via exception classes (AuthenticationError,
        RateLimitError, ...); with raw HTTP the status code is the whole of
        the information, so the mapping is written out here.
        """
        detail = self.error_detail(http_response)
        status = http_response.status_code

        if status in (401, 403):
            return (
                f"authentication failed (HTTP {status}). Check your {self.env_var} "
                f"in .env.{detail}"
            )
        if status == 429:
            return f"{self.provider_name} rate limit hit (HTTP 429).{detail}"
        if status >= 500:
            return (
                f"{self.provider_name} is having trouble (HTTP {status}). "
                f"Try again in a moment.{detail}"
            )
        return f"{self.provider_name} rejected the request (HTTP {status}).{detail}"

    @staticmethod
    def error_detail(http_response: httpx.Response) -> str:
        """The provider's own explanation, when it sent one.

        Both providers use `{"error": {"message": ...}}`, but a gateway
        failure can return HTML instead, so this never assumes JSON.
        """
        try:
            payload = http_response.json()
        except ValueError:
            text = http_response.text.strip()
            return f" {text[:200]}" if text else ""

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return f" {error['message']}"
            if isinstance(error, str):
                return f" {error}"
        return ""

    def close(self) -> None:
        self._http.close()


class DeepSeekClient(LLMClient):
    """DeepSeek - the provider this app talks to."""

    provider_name = "DeepSeek"
    env_var = "DEEPSEEK_API_KEY"
    base_url = DEEPSEEK_BASE_URL
