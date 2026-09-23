"""Turning the part of a conversation nobody replays any more into a paragraph.

Day 9's subject. Day 7 gave the agent a memory by replaying the transcript
into every request; day 8 put a number on what that costs. This module is the
third move: **keep all of it, send a summary of most of it.**

The shape of the problem is fixed by the API. A chat-completions request is
stateless, so the whole context goes up every single time, and it is billed
every single time. Day 7's answer was a hard cut - the last 40 messages, the
rest simply not sent - which is cheap and lossy in the worst way: the chat
forgets, silently, and the user finds out by being asked their own name again.

The answer here is to cut in the same place but not throw the offcut away:

    [system prompt]
    [system: summary of the first N messages]   <- this module
    [the last K messages, verbatim]             <- the window
    [the new question]

The summary is written by the model itself, in one extra request, and then
**stored** - so it is written once per overflow rather than once per turn.
That is what makes this cheaper rather than more expensive: a summary that
had to be regenerated on every message would cost more than the history it
replaces.

Two functions and a class:

    split(history, window)      -> what to summarise, what to keep verbatim
    Compactor.compress(...)     -> one summarisation request
    Compaction                  -> what came back, plus what it cost

`split` is pure and network-free, which is where the actual policy lives and
the only part worth testing without an API key. The class around it does the
HTTP, and knows nothing about conversations, files or the web app - it is
handed an `LLMClient`, exactly as the agent is.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import tokens
from llm_client import LLMClient, LLMClientError

#: The instruction the summariser runs under. Written for a reader that is a
#: model about to continue the conversation, not a human skimming a log: it
#: asks for the facts that a later turn can be expected to depend on, and for
#: nothing that reads well but carries no information.
SUMMARY_SYSTEM_PROMPT = (
    "You compress chat transcripts so another model can carry the conversation "
    "on without having read them.\n"
    "Write a dense briefing in plain prose, in the third person ('The user...', "
    "'The assistant...'), covering:\n"
    "- facts the user stated about themselves, their data, their code or their goal "
    "(names, numbers, identifiers, file names, preferences - keep them exact);\n"
    "- what was decided, concluded or agreed, and anything the assistant promised;\n"
    "- questions that were raised and never answered, and work left unfinished;\n"
    "- constraints, corrections and instructions the user gave about how to answer.\n"
    "Leave out greetings, apologies, restatements and anything the transcript does "
    "not actually say. Never invent, never speculate, and never address the user - "
    "you are writing notes, not a reply. If an earlier summary is provided, merge it "
    "with the new messages into one summary that replaces it, keeping every fact from "
    "both. Be brief but complete: prefer a lost adjective to a lost fact."
)

#: Summarisation is a reading job, not a creative one: the same transcript
#: should compress to the same briefing twice in a row, and every word that is
#: not in the transcript is a word that can be wrong.
SUMMARY_TEMPERATURE = 0.0

#: A hard ceiling on the summary itself. Compression that is allowed to run
#: long stops being compression - the whole point is that this paragraph is
#: smaller than the fifty messages it stands in for, on every future request.
SUMMARY_MAX_TOKENS = 700

#: One message this long is already more than a summary should carry; past
#: this it is cut, with a marker, so a single pasted logfile cannot make the
#: compression request bigger than the conversation it is compressing.
MESSAGE_EXCERPT_CHARS = 4000


@dataclass
class Compaction:
    """One summarisation exchange - what it produced and what it cost.

    Deliberately the same shape as `AgentReply`: the debug panel draws this
    next to the ordinary turn, because a request made on the user's behalf and
    billed to them should be as visible as the one they asked for.

    `text` is None when the call failed. There is no half-summary and no
    placeholder: a failed compression means this turn is sent with the plain
    window and no summary at all, which is worse memory but never a wrong one.
    """

    text: str | None
    covers: int = 0
    error: str | None = None
    request: dict | None = None
    response: dict | None = None
    usage: dict | None = None
    elapsed_ms: float | None = None
    url: str | None = None
    status_code: int | None = None
    request_headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text)


def split(history: list[dict], window: int, already_covered: int = 0) -> tuple[list[dict], list[dict], int]:
    """Where the line falls: `(to_summarise, to_send_verbatim, covers)`.

    `window` is how many messages may go into the request as themselves.
    Everything before that is the summary's job. `already_covered` is how many
    messages the stored summary speaks for, so a chat that has overflowed
    before only pays to compress what has happened since.

    Three cases, in the order they come up:

    * the chat fits in the window - nothing to summarise and nothing to
      summarise *with*: every message goes up as itself, and a summary left
      over from when the window was narrower is simply not used, since the
      messages it stands for are all in the request anyway;
    * it overflows and the stored summary is already behind the cut - the new
      messages in between are what gets compressed;
    * it overflows but the stored summary already reaches past the cut (the
      window was widened after the fact) - nothing new to compress, and the
      verbatim part starts where the summary stops rather than at the cut, so
      no message is ever both summarised and sent.

    `covers` is what the summary will speak for afterwards, which is exactly
    where the verbatim part begins. That identity is the invariant this whole
    module rests on: every message is in the summary or in the window, never
    in neither and never in both.
    """
    if window is None or window < 0:
        window = 0
    already_covered = max(0, min(already_covered, len(history)))

    cut = len(history) - window
    if cut <= 0:
        # Nothing overflows: the verbatim window is the whole conversation, so
        # a stored summary would only repeat what is already there. It stays on
        # disk - the window can narrow again - but it does not go up.
        return [], list(history), 0

    if cut <= already_covered:
        # The window was widened after a compression: the summary reaches past
        # where the cut now falls, so the verbatim part starts where the
        # summary stops rather than at the cut. Fewer messages than the window
        # allows, and not one of them said twice.
        return [], history[already_covered:], already_covered

    return history[already_covered:cut], history[cut:], cut


def render_transcript(messages: list[dict]) -> str:
    """The messages to be compressed, as the text of one prompt.

    Roles are labelled rather than sent as real turns: this is a document to
    be read and summarised, not a conversation to be continued. Sending it as
    a genuine `messages` array invites the model to answer the last question
    in it instead of describing it.
    """
    lines = []
    for message in messages:
        content = str(message.get("content") or "")
        if len(content) > MESSAGE_EXCERPT_CHARS:
            content = content[:MESSAGE_EXCERPT_CHARS] + " […trimmed]"
        lines.append(f"{message.get('role', 'user')}: {content}")
    return "\n\n".join(lines)


class Compactor:
    """Writes the summary, using the same provider the conversation runs on.

    Configured with a model rather than picking one: whichever model a chat is
    talking to is the model that gets to describe it, so compression never
    quietly moves the conversation onto a different one - and never fails
    because a cheaper model was hard-coded here and the account cannot use it.
    """

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, previous_summary: str | None, messages: list[dict]) -> dict:
        parts = []
        if previous_summary:
            parts.append(
                "Summary of the conversation so far (replace it with an updated "
                f"one that also covers the messages below):\n\n{previous_summary}"
            )
        parts.append(
            "Messages to fold into the summary, oldest first:\n\n"
            + render_transcript(messages)
        )
        parts.append("Write the updated summary now, and nothing else.")

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": SUMMARY_TEMPERATURE,
            # Thinking is billed as output (day 8) and a summariser has
            # nothing to think about that reading the transcript twice would
            # not answer, so it is switched off for this call only.
            "reasoning_effort": "none",
            "max_tokens": SUMMARY_MAX_TOKENS,
        }

    def compress(
        self, previous_summary: str | None, messages: list[dict], covers: int
    ) -> Compaction:
        """One request: previous summary + new messages -> one new summary.

        Never raises. A conversation that cannot be compressed is still a
        conversation the user is trying to have, and the caller's fallback -
        send the window without a summary - is a worse memory, not a broken
        app.
        """
        if not messages:
            return Compaction(text=previous_summary, covers=covers)

        request = self.build_request(previous_summary, messages)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return Compaction(
                text=None,
                error=f"Error: {exc}",
                request=request,
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        text = extract_summary(result.response)
        if not text:
            return Compaction(
                text=None,
                error="the summariser returned nothing usable",
                request=result.request,
                response=result.response,
                usage=tokens.usage_from_response(result.response),
                elapsed_ms=result.elapsed_ms,
                url=result.url,
                status_code=result.status_code,
                request_headers=result.request_headers,
            )

        return Compaction(
            text=text,
            covers=covers,
            request=result.request,
            response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )


def extract_summary(payload: dict) -> str:
    """The text of a summarisation response, or "" if there is none.

    Stricter than `Agent.extract_answer` on purpose. That one has to put
    *something* in a chat bubble, so it falls back to the reasoning trace; this
    one is writing to the context of every future request, where a fallback
    would be a paragraph of the model's own thinking presented to it later as
    fact. Nothing usable means no summary.
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()
