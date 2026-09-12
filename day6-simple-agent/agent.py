"""The agent: the thing that owns a conversation with an LLM.

Day 6's whole point. Through day 5 there was `ask_model(client, model,
system_prompt, message, stop, response_format, reasoning_effort,
temperature)` - a function whose eight arguments meant the *caller* had to
know how a DeepSeek request is shaped. There was no "agent" anywhere; there
was a web handler that happened to build an API call.

An Agent is an entity instead:

  * it is **configured once** (`AgentConfig`) and then simply asked things,
  * it **owns** its role, its parameters and its transcript,
  * it **assembles** the request body and **interprets** the reply itself,
  * it is **provider-agnostic** - it is handed an `LLMClient` and never
    learns which company is on the other end.

That last point is the load-bearing one: nothing below names a provider, so
pointing this agent at a different API is a different `LLMClient` handed to
the constructor and no change here at all. In day 5 the provider was baked
into the module that built the request.

This module imports no web framework and no HTTP library. It runs fine with
no server in sight:

    from agent import Agent, AgentConfig
    from llm_client import DeepSeekClient

    agent = Agent(DeepSeekClient.from_env(), AgentConfig(model="deepseek-v4-flash"))
    print(agent.ask("Say hello in five words.").answer)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm_client import LLMClient, LLMClientError, LLMResponse

# --------------------------------------------------------------------------
# What an agent can be configured with (the DeepSeek parameter set, days 1-4)
# --------------------------------------------------------------------------

MODELS = [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
]

# Per DeepSeek API docs (response_format): only "text" and "json_object".
RESPONSE_FORMATS = ["text", "json_object"]

# Per DeepSeek API docs (stop): up to 16 sequences are accepted.
STOP_SEQUENCES_LIMIT = 16

# DeepSeek's models think by default even with no reasoning_effort sent at all
# (confirmed live: reasoning_content and usage.completion_tokens_details
# .reasoning_tokens show up unprompted). "off" is our own label for that
# off-switch - on the wire it is reasoning_effort="none", the one value that
# actually disables thinking; the rest (low/high/max) tune its budget.
REASONING_EFFORTS = ["off", "low", "high", "max"]
REASONING_EFFORT_OFF = "off"
REASONING_EFFORT_NONE = "none"

# Per DeepSeek API docs (temperature): 0..2, default 1. Lower = more
# deterministic, higher = more varied.
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
TEMPERATURE_STEP = 0.1
DEFAULT_TEMPERATURE = 1.0

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


def clamp_temperature(value: float) -> float:
    """Keep a temperature inside the API's accepted range, rounded to one decimal."""
    return round(min(TEMPERATURE_MAX, max(TEMPERATURE_MIN, float(value))), 1)


@dataclass
class AgentConfig:
    """An agent's standing settings - who it is and how it should answer.

    Every field except `model` is optional, and an unset field is simply left
    out of the request body. An agent built with nothing but a model id
    therefore sends nothing but the message - which is what keeps this class
    usable against providers that do not accept DeepSeek's parameter set.
    """

    model: str = DEFAULT_MODEL
    system_prompt: str | None = None
    stop: list[str] | None = None
    response_format: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None


@dataclass
class AgentReply:
    """Everything about one exchange - not just the text answer.

    `request` and `response` are the real JSON that crossed the wire, so the
    debug panel shows a transcript rather than a reconstruction.
    """

    answer: str
    request: dict
    response: dict | None = None
    error: str | None = None
    elapsed_ms: float | None = None
    url: str | None = None
    status_code: int | None = None
    request_headers: dict = field(default_factory=dict)


class Agent:
    """One configured conversational agent over one LLM provider."""

    def __init__(self, client: LLMClient, config: AgentConfig | None = None) -> None:
        self.client = client
        self.config = config or AgentConfig()
        # The agent's transcript. Day 6 creates a fresh agent per request, so
        # in practice this is always empty when a request is built and every
        # question is answered from scratch - the statelessness the compare
        # views rely on. The seam is here on purpose: keeping one Agent alive
        # across turns is all it takes to give it memory, and nothing else in
        # the request path has to change.
        self.history: list[dict] = []

    # -- building the request ------------------------------------------------

    def build_messages(self, user_message: str) -> list[dict]:
        messages: list[dict] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_message})
        return messages

    def build_request(self, user_message: str) -> dict:
        """The exact JSON body this agent would POST for `user_message`.

        Separate from `ask` so it can be inspected (and tested) without
        spending a token.
        """
        body: dict = {
            "model": self.config.model,
            "messages": self.build_messages(user_message),
        }

        if self.config.stop:
            body["stop"] = self.config.stop
        if self.config.response_format and self.config.response_format != "text":
            body["response_format"] = {"type": self.config.response_format}
        if self.config.reasoning_effort:
            # "off" is our label; DeepSeek's actual off-switch value is "none".
            body["reasoning_effort"] = (
                REASONING_EFFORT_NONE
                if self.config.reasoning_effort == REASONING_EFFORT_OFF
                else self.config.reasoning_effort
            )
        if self.config.temperature is not None:
            # Sent even at the API's own default of 1.0, so the debug panel
            # shows the value that actually produced the answer.
            body["temperature"] = clamp_temperature(self.config.temperature)

        return body

    # -- the exchange --------------------------------------------------------

    def ask(self, user_message: str) -> AgentReply:
        """Ask the model something and return the whole exchange.

        Errors never propagate: a failed call comes back as an `AgentReply`
        whose `answer` is the explanation, because every caller here needs to
        put *something* in a chat bubble.
        """
        request_body = self.build_request(user_message)

        try:
            result = self.client.complete(request_body)
        except LLMClientError as exc:
            return AgentReply(
                answer=f"Error: {exc}",
                request=request_body,
                error=f"Error: {exc}",
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        answer = self.extract_answer(result.response)
        self.remember(user_message, answer)

        return AgentReply(
            answer=answer,
            request=result.request,
            response=result.response,
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )

    def remember(self, user_message: str, answer: str) -> None:
        """Append one completed turn to the transcript."""
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": answer})

    # -- reading the reply ---------------------------------------------------

    @staticmethod
    def extract_answer(payload: dict) -> str:
        """Pull the text out of a chat-completions response.

        Defensive on purpose: this now parses raw JSON rather than an SDK
        object. A model that puts everything in `reasoning` and leaves
        `content` empty would otherwise render as a blank bubble and read as a
        bug in this app rather than as what the model actually did.
        """
        choices = payload.get("choices") or []
        if not choices:
            return "(the API returned no choices)"

        message = choices[0].get("message") or {}
        answer = (message.get("content") or "").strip()
        if answer:
            return answer

        reasoning = (message.get("reasoning") or "").strip()
        if reasoning:
            return f"(no content - the model returned only reasoning)\n\n{reasoning}"
        return "(empty response)"
