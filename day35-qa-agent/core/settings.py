"""The settings a chat runs under - one agent's configuration, checked.

Sent by the page with every message and stored with the conversation, so each
chat keeps its own model, strategy and RAG setup.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from core import agent as agent_module
from core import strategies
from core.errors import Invalid
from rag import augment as augment_module
from rag import rerank as rerank_module


@dataclass
class ChatSettings:
    """One agent's configuration, sent by the client with every message.

    A plain dataclass rather than a web model: the core builds and checks it
    (`validate_settings`), a script can make one by hand, and the web layer
    lets FastAPI validate request bodies straight into it.
    """

    model: str = agent_module.DEFAULT_MODEL
    system_prompt: str = agent_module.DEFAULT_SYSTEM_PROMPT
    stop: list[str] = field(default_factory=list)
    response_format: str = agent_module.DEFAULT_RESPONSE_FORMAT
    reasoning_effort: str = agent_module.DEFAULT_REASONING_EFFORT
    temperature: float = agent_module.DEFAULT_TEMPERATURE
    # Days 9 and 10. How many messages of the transcript are replayed
    # verbatim, and what becomes of everything older than that. Settings of
    # the *conversation* rather than of the request - which is why they live
    # here, get stored with the chat, and are sent back up with every message
    # like every other parameter.
    context_messages: int = agent_module.DEFAULT_CONTEXT_MESSAGES
    context_strategy: str = strategies.DEFAULT_STRATEGY
    # Day 11. Whether the agent looks at each turn and says what it would
    # file. A setting rather than a constant because it buys one extra
    # request per message and buys it *after* the answer - it is the one
    # thing in this app that costs money without making any reply better,
    # and that is a trade the person paying should get to decline.
    memory_proposals: bool = True
    # Day 13. Whether the agent gets a say in where the task has got to. Off,
    # the state machine is still there and still enforced - it is just moved
    # by hand. On, each turn buys one more request after the answer, and what
    # comes back goes through `phases.check` like any other move.
    #
    # A setting for the same reason `memory_proposals` is one: it is paid for
    # per message and makes no answer better. It differs in what it buys -
    # a proposal waits for a person, while this moves the task - so the
    # argument for being able to decline it is if anything stronger.
    task_state: bool = True

    # Day 14. Whether anything reads the answer back against the invariants.
    #
    # The exact parallel of the switch above, and it governs the *checking*
    # rather than the rules. Off, the rules still ride on every request and
    # the model is still told it may not break one - what stops is the second
    # request that reads the answer, and with it the retry. So turning it off
    # does not relax an invariant, it demotes one: from a rule that is
    # enforced back to a rule that is merely stated, which is precisely what
    # day 12 already had and what this day exists to improve on.
    invariant_check: bool = True

    # Day 22. Whether the question is answered from the document index:
    # retrieve the `rag_k` nearest chunks, join them onto the question, ask.
    # Off by default, so every chat opened before this day answers exactly as
    # it did. A setting of the chat, like the strategy.
    rag: bool = False
    rag_k: int = augment_module.DEFAULT_K
    # Day 23. The second stage: fetch `rag_candidates`, have the model score
    # each one 0-10 for this question, send the ones at `rag_threshold` or
    # above, at most `rag_k`. On by default for a chat switched to RAG; off
    # is day 22's top k.
    rag_rerank: bool = True
    rag_candidates: int = augment_module.DEFAULT_CANDIDATES
    rag_threshold: int = rerank_module.DEFAULT_THRESHOLD
    # Day 34. Agentic code: every message is a task in the open project, done
    # with the project's file and git tools (`core.coder`) - the files are
    # read, searched and changed by the model itself. Off by default, so a
    # chat opened before this day answers as it did. When on, it wins over
    # `rag`: the model searches the index itself, as a tool.
    agentic_code: bool = False


def validate_settings(settings: ChatSettings) -> ChatSettings:
    """Reject anything the DeepSeek API would reject, with a readable message."""
    if settings.model not in agent_module.MODELS:
        raise Invalid(f"Unknown model: {settings.model}")
    if settings.response_format not in agent_module.RESPONSE_FORMATS:
        raise Invalid(f"Unknown response format: {settings.response_format}")
    if settings.reasoning_effort not in agent_module.REASONING_EFFORTS:
        raise Invalid(f"Unknown reasoning effort: {settings.reasoning_effort}")
    if not agent_module.TEMPERATURE_MIN <= settings.temperature <= agent_module.TEMPERATURE_MAX:
        raise Invalid(
            f"Temperature must be between {agent_module.TEMPERATURE_MIN} "
            f"and {agent_module.TEMPERATURE_MAX}",
        )

    settings.system_prompt = settings.system_prompt.strip() or agent_module.DEFAULT_SYSTEM_PROMPT
    settings.stop = [s.strip() for s in settings.stop if s.strip()][
        : agent_module.STOP_SEQUENCES_LIMIT
    ]
    settings.temperature = agent_module.clamp_temperature(settings.temperature)
    settings.context_messages = agent_module.clamp_context_messages(settings.context_messages)
    # Unknown strategies fall back rather than 400: this field arrives from
    # records written by older versions of the app as well as from the form,
    # and a chat that cannot be opened because its settings mention a strategy
    # that has since been renamed is a worse outcome than one that quietly
    # uses the default.
    settings.context_strategy = strategies.normalise(settings.context_strategy)
    # Day 22: clamped rather than refused, for the strategy's reason above.
    # Whether the index exists is not checked here - it is checked when it is
    # used, so a chat whose index was reset still opens.
    settings.rag_k = max(1, min(int(settings.rag_k), augment_module.MAX_K))
    settings.rag_candidates = max(settings.rag_k, min(int(settings.rag_candidates), augment_module.MAX_CANDIDATES))
    settings.rag_threshold = max(0, min(int(settings.rag_threshold), rerank_module.MAX_SCORE))
    return settings


def as_dict(settings: ChatSettings) -> dict:
    """The settings as they are stored with a conversation and sent to the page."""
    return asdict(settings)
