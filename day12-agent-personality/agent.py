"""The agent: the thing that owns a conversation with an LLM.

Day 6 made this an entity rather than a call site:

  * it is **configured once** (`AgentConfig`) and then simply asked things,
  * it **owns** its role, its parameters and its transcript,
  * it **assembles** the request body and **interprets** the reply itself,
  * it is **provider-agnostic** - it is handed an `LLMClient` and never
    learns which company is on the other end.

Day 7 changes one thing here: **an agent can now be born remembering.**
`Agent(client, config, history=[...])` starts with a transcript it did not
live through, which is what lets a process that has just started up carry on
a conversation the previous process was having. The rest of the class did
not have to change - `build_messages` already replayed `self.history`, and
day 6 simply never put anything in it.

Day 9 adds one more, `Agent(summary=...)`: a paragraph standing in for the
part of the transcript that is no longer being replayed. The agent does not
write it, does not decide when it is needed and does not know it was produced
by the same provider it is about to call - it is handed a string, exactly as
it is handed a history and a client, and it puts it in the one place a
statement about the past belongs: a system message, after the role and before
the window. Who wrote it, what it cost and how far through the conversation it
reaches are `compaction.py`'s and `server.py`'s business.

Day 10 adds a second one of exactly the same kind, `Agent(facts=...)`: a
key-value block instead of a paragraph. The agent's side of both is the same
three lines, and that is the point of putting them here rather than in the
strategies that produce them - as far as the agent is concerned there is one
question, "is there anything standing in for the messages I am not being
given", and at most one answer to it.

Day 8 adds one field, `AgentReply.usage`: what the provider says the
exchange cost, lifted out of the response and normalised. It is read, never
computed - the agent assembles the request and the API reports what it came
to, and those are two different jobs belonging to two different machines.

The agent still has no idea where that history comes from. It is handed a
list, exactly as it is handed a client; `store.py` is what knows about files
on disk, and this module imports no web framework, no HTTP library and no
storage layer. It runs fine with no server in sight:

    from agent import Agent, AgentConfig
    from llm_client import DeepSeekClient

    agent = Agent(DeepSeekClient.from_env(), AgentConfig(model="deepseek-v4-flash"))
    agent.ask("My name is Maksim. Reply with just OK.")
    print(agent.ask("What is my name?").answer)   # -> Maksim
"""

from __future__ import annotations

from dataclasses import dataclass, field

import tokens
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

# How many past messages (not turns - a turn is two) an agent replays into a
# request. Everything said is *kept* on disk; this is only how far back the
# model is made to look, because a conversation grows without limit while a
# request has to be paid for by the token every single time. 40 messages is
# 20 exchanges - far more than DeepSeek's 1M-token window needs to worry
# about, and small enough that a year-old chat does not quietly cost more per
# message than a new one.
DEFAULT_CONTEXT_MESSAGES = 40

# Day 9 makes that number a setting rather than a constant, so the bounds have
# to be somewhere. Two is the smallest window that can hold a complete turn -
# one question and its answer - and below it "remembering" means nothing; the
# upper end is a guard rather than a recommendation, since every message in the
# window is paid for again on every single turn.
CONTEXT_MESSAGES_MIN = 2
CONTEXT_MESSAGES_MAX = 200

# What happens to the messages that fall outside that window - dropped, kept
# as facts, or summarised. The names and the default live in `strategies.py`,
# which is where the three are described; the agent only ever sees the result.

# How the summary is introduced to the model. A system message rather than an
# assistant one, because it is not something anyone said: it is a statement
# about the conversation, on the same footing as the role the model has been
# given. Labelled, so the model can tell a compressed account of the past from
# the verbatim messages that follow it.
SUMMARY_PREFIX = (
    "Summary of the earlier part of this conversation, which is no longer "
    "included verbatim. Treat it as established fact:\n\n"
)

# Day 10's equivalent for the other strategy. Same placement, same role, and
# the wording does the one job the label has to do: it says that these lines
# are current rather than historical, because a fact block - unlike a summary -
# is not a description of the past. It is what is true now, as far as anyone
# has said so.
FACTS_PREFIX = (
    "Established facts about this conversation, carried forward from earlier "
    "messages that are no longer included verbatim. Treat them as current:\n\n"
)

# Day 11's two, and they are a different kind of thing from the two above.
# Those stand in for messages that were removed from *this* request - they say
# "here is what you have forgotten". These say "here is what you know", and
# what they carry was never in this conversation at all: it was filed by a
# person, possibly in another chat, possibly months ago.
#
# The labels are written to make that difference audible, because the model
# has no other way to tell them apart and the two want different treatment. A
# summary is an account of a conversation and can be argued with by what came
# after it; a filed memory is a standing instruction and cannot.
#
# Day 12 takes four words out of this label. It used to say "and how they want
# to be answered", which was true of the layer's contents and is now the job
# of the block below - and a request that described two of its blocks the same
# way would be asking the model to work out which one meant it.
LONG_TERM_PREFIX = (
    "What you know about this user, filed by them and carried into every "
    "conversation. These are established facts, not a record of anything said "
    "here - treat them as current unless this conversation overrides one:\n\n"
)

# The task's name goes in this one, so `{task}` is filled in rather than
# concatenated: "what this piece of work has established" means very little
# without saying which piece of work.
WORKING_PREFIX = (
    "What has been established about the task you are working on"
    "{task}. Filed by the user across however many conversations this task "
    "has taken, and still in force:\n\n"
)

# Day 12, and the only block in a request that is not a statement about the
# past. The four above stand in for something - messages that were dropped, a
# layer that was filed elsewhere. This one stands in for nothing: it is what
# the user told the app about themselves before any of this was said.
#
# The label carries two jobs, and both are load-bearing. It says **who wrote
# this**, because a request now has two authors in it - whoever built the app
# and whoever is using it - and the second must never read as though the first
# said it. And it says **which block wins**, because the layers above can hold
# something that looks like a preference (day 11 lets you file one and pin it
# to every request), and two blocks giving different orders with no stated
# precedence is the one failure a prompt cannot recover from on its own.
PERSONALITY_PREFIX = (
    "--- Personalisation, written by the user ---\n"
    "What the person you are talking to has told you about themselves, their "
    "work, and how they want to be answered. They set this themselves: it was "
    "not said in this conversation and it is not something you worked out. It "
    "applies to every answer whatever is asked, and where it disagrees with "
    "anything above, it wins:\n\n"
)


def clamp_context_messages(value: int) -> int:
    """Keep a context window inside the bounds above."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_MESSAGES
    return min(CONTEXT_MESSAGES_MAX, max(CONTEXT_MESSAGES_MIN, number))

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
    # Day 8. The provider's own count for this exchange, pulled out of the
    # response and normalised (`tokens.usage_from_response`). None when the
    # call failed: nothing was billed, so there is nothing to report - and a
    # zero would be a different statement than "no answer was paid for".
    usage: dict | None = None


class Agent:
    """One configured conversational agent over one LLM provider."""

    def __init__(
        self,
        client: LLMClient,
        config: AgentConfig | None = None,
        history: list[dict] | None = None,
        context_messages: int = DEFAULT_CONTEXT_MESSAGES,
        summary: str | None = None,
        facts: str | None = None,
        long_term: str | None = None,
        working: str | None = None,
        task_title: str | None = None,
        personality: str | None = None,
    ) -> None:
        self.client = client
        self.config = config or AgentConfig()
        # The agent's transcript: `[{"role": ..., "content": ...}, ...]`.
        #
        # Day 6 always started this empty, because the only thing that ever
        # appended to it was `remember`, and the server threw the agent away
        # after one question. Day 7 hands it in at construction. An agent is
        # still short-lived - one per request - but it now *inherits* the
        # conversation instead of starting one, which is the whole difference
        # between a chat that forgets and a chat that does not.
        self.history: list[dict] = list(history or [])
        # How much of that transcript actually goes into a request. See
        # DEFAULT_CONTEXT_MESSAGES: the store keeps everything, the agent
        # decides how far back to look.
        self.context_messages = context_messages
        # Day 9: what the messages *before* that window came to, in one
        # paragraph. None means there is nothing older, or that nobody asked
        # for it to be compressed - either way the agent behaves exactly as it
        # did on day 8.
        self.summary = (summary or "").strip() or None
        # Day 10: the same thing in the other shape - `key: value` lines,
        # already rendered. The agent is handed text either way and never
        # learns which strategy produced it, which is what keeps adding a
        # fourth one a change to `strategies.py` and not to this class.
        self.facts = (facts or "").strip() or None
        # Day 11: the two layers that are not about this conversation. Handed
        # in already rendered, exactly as `summary` and `facts` are, and for
        # exactly the same reason - the agent's job is to put a block of text
        # in the right place in a request, not to know which store it came out
        # of or why those particular lines were the ones selected.
        self.long_term = (long_term or "").strip() or None
        self.working = (working or "").strip() or None
        self.task_title = (task_title or "").strip() or None
        # Day 12: the user's standing instructions about how to answer,
        # rendered elsewhere exactly as the four above are. The agent is given
        # text and a place to put it, and does not know that this particular
        # block came from a profile that can be switched - which is what keeps
        # "there are now two profiles" a change to `personality.py` and the UI,
        # and no change at all to this class.
        self.personality = (personality or "").strip() or None

    # -- building the request ------------------------------------------------

    def build_messages(self, user_message: str) -> list[dict]:
        """`[system] + [the layers] + the window + the question`.

        The system prompt is always first and the new message always last;
        only the middle is trimmed, and only from the front, so the model
        loses the oldest exchanges rather than the most recent ones.

        Between them, since day 11, sit up to three blocks, and they go
        **widest scope first**:

            long-term     true everywhere, always
            working       true for this task
            summary|facts true for this conversation
            personality   not about the past at all - who is asking
            the window    said in this conversation

        That order is the one thing here worth arguing about, so: it is the
        order of narrowing, and it puts each block after everything it might
        need to contradict. A task that has settled on English is stated
        after a user who generally prefers Russian; this conversation's own
        facts are stated after both. Models weight later context more
        heavily, so the narrower scope wins by construction rather than by a
        rule written in prose that something has to enforce - which is what
        the alternative would have cost, since three blocks that can disagree
        need a precedence and nowhere obvious to put it.

        Day 12's block is in that list and not in that argument, because it
        is not a scope. The three above it say what is true; it says who is
        asking and how they want to be answered, and the two cannot narrow
        each other.

        It goes **last** for a different reason, and the reason is the same
        mechanism: what the user typed into a profile has to outrank a
        preference the agent inferred and filed months ago, and later context
        is weighted more heavily, so last is where "the declaration beats the
        inference" is enforced rather than merely hoped for. Sitting closest
        to the question is the same argument twice - it is also where a
        standing instruction is most reliably obeyed.

        The alternative was to join it onto the system prompt, one message
        with the developer's half and the user's half in it, which reads
        beautifully as "one configured agent" and loses exactly the
        precedence above. It also loses the thing every other block here has:
        its own message, so the debug panel can show what contributed what.
        The prefix carries the framing instead, by naming its author - which
        is cheaper than a placement and does the same job.

        They are three separate system messages rather than one joined block
        for the same reason they are three layers: so that the request in the
        debug panel shows which layer contributed what. Concatenating them
        would be cheaper by a few tokens and would make the day unprovable.

        At most one of `summary` and `facts` ever appears - the strategies
        that produce them are exclusive (`strategies.py`), and they have to
        be: they are two descriptions of the same missing messages, and a
        request carrying both would ask the model to work out which had gone
        stale. The `elif` is where that guarantee is enforced rather than
        merely intended. The day-11 blocks are under no such rule, because
        they are not descriptions of the same thing: they have different
        scopes, and a fact can be true at one scope and absent from another
        without either being wrong.
        """
        messages: list[dict] = []
        if self.config.system_prompt:
            messages.append({"role": "system", "content": self.config.system_prompt})
        if self.long_term:
            messages.append({"role": "system", "content": LONG_TERM_PREFIX + self.long_term})
        if self.working:
            task = f' - "{self.task_title}"' if self.task_title else ""
            messages.append(
                {"role": "system", "content": WORKING_PREFIX.format(task=task) + self.working}
            )
        if self.summary:
            messages.append({"role": "system", "content": SUMMARY_PREFIX + self.summary})
        elif self.facts:
            messages.append({"role": "system", "content": FACTS_PREFIX + self.facts})
        if self.personality:
            messages.append({"role": "system", "content": PERSONALITY_PREFIX + self.personality})
        messages.extend(self.recalled())
        messages.append({"role": "user", "content": user_message})
        return messages

    def recalled(self) -> list[dict]:
        """The slice of the transcript this request will carry."""
        if self.context_messages is None or self.context_messages <= 0:
            return []
        return self.history[-self.context_messages :]

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
            usage=tokens.usage_from_response(result.response),
        )

    def remember(self, user_message: str, answer: str) -> None:
        """Append one completed turn to this agent's own transcript.

        In-memory only: it is what makes a long-lived agent (the script in the
        module docstring) hold a conversation. Writing the turn down so the
        *next* process can have it is the caller's job - in this app,
        `server.py` handing it to `store.py` - because an agent that knew
        about files would be an agent that knew about one way of storing them.
        """
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
