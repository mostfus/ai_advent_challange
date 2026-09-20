"""Sticky facts: the handful of things about a conversation worth keeping.

Day 10's second strategy, and the one that is not a smaller copy of the
transcript. A summary (day 9) compresses *what was said*; facts record *what
is true as a result*, which turns out to be a much smaller and much more
stable thing:

    goal                  port the day-7 store to SQLite
    constraint.language   answer in Russian
    decision.schema       one table per conversation, messages as JSON
    preference.style      no apologies, no preamble

Four lines. They survive a hundred messages without being rewritten, they are
readable by the person whose conversation they describe, and - unlike a
paragraph - a wrong one can be deleted on its own.

The mechanics, and why they are what they are:

* **Updated after every user message, before the answer.** The requirement is
  "after each user message"; doing it before the turn rather than after means
  the fact the user has just stated is already in the block that answers them,
  instead of arriving one message late. It costs the same either way.

* **A patch, not a rewrite.** The model returns `{"set": {...}, "unset":
  [...]}`, so it says what *changed* rather than restating the whole block.
  A rewrite that quietly drops a fact looks identical to a correct one; a
  patch that drops a fact has to say `unset` out loud, and the debug panel
  shows it.

* **A constant-size prompt.** The extractor is sent the current facts, the
  previous turn and the new message - never the transcript. That is what
  makes this strategy affordable despite running on every single message: its
  request does not grow as the conversation does, which is the exact problem
  the whole day is about.

* **Caps on everything.** At most `MAX_FACTS` keys, each of them short. A
  fact block that is allowed to grow without limit is just the transcript
  again, with worse recall.

Two things live here: the request (`FactKeeper`), and the bookkeeping that
turns a patch into the next block (`apply_patch`, `render`). Neither knows
about conversations, files or HTTP - as with `compaction.py`, the client is
handed in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import tokens
from llm_client import LLMClient, LLMClientError

#: The instruction the extractor runs under. It is written to make the model
#: *decline* more often than not: most messages in a conversation establish
#: nothing durable, and a fact block that grows on every turn is a fact block
#: nobody can read.
FACTS_SYSTEM_PROMPT = (
    "You maintain a small key-value memory for a chat assistant.\n"
    "You are given the memory as it stands and the latest message from the user. "
    "Reply with JSON only, in the form "
    '{"set": {"key": "value"}, "unset": ["key"]}.\n'
    "Record only durable facts that a later reply would be wrong without:\n"
    "- goal: what the user is trying to achieve;\n"
    "- constraint: rules the answers must obey (language, format, length, tools, "
    "things to avoid);\n"
    "- preference: how the user wants to be addressed and answered;\n"
    "- decision: choices that have been made and are being built on;\n"
    "- agreement: what was promised, by either side;\n"
    "- profile: stable details about the user, their data or their project "
    "(names, versions, identifiers - keep them exact).\n"
    "Use lowercase dotted keys from those prefixes, for example goal, "
    "constraint.language, decision.database, profile.name. Values are short "
    "phrases, not sentences.\n"
    "Use 'set' for a new fact or to correct one that has changed - reusing an "
    "existing key overwrites it. Use 'unset' only when a fact has been "
    "explicitly retracted or is no longer true.\n"
    "Record nothing from a message that only asks a question, makes small talk "
    "or restates what is already in the memory. Never guess, never infer a "
    "preference from one example, and never copy the message into the memory. "
    'Returning {"set": {}, "unset": []} is the normal, expected answer.'
)

#: Reading, not writing: the same message should produce the same patch twice.
FACTS_TEMPERATURE = 0.0

#: A patch is a few short strings. Anything longer is the model writing prose
#: into a key-value store.
FACTS_MAX_TOKENS = 400

#: How much of the new message the extractor is shown. A pasted logfile does
#: not contain more facts than its first few thousand characters, and it would
#: otherwise make this the most expensive request in the app.
MESSAGE_EXCERPT_CHARS = 3000

#: The ceilings. `MAX_FACTS` is the important one: past a few dozen keys the
#: block stops being a summary of what matters and becomes a second transcript
#: with the connecting tissue removed. When it is reached the least recently
#: touched fact makes way, which is the only ordering the store has that means
#: anything.
MAX_FACTS = 40
MAX_KEY_CHARS = 60
MAX_VALUE_CHARS = 240

#: Keys become part of every future prompt, so they are normalised rather than
#: trusted: lowercase, dotted, no spaces, nothing that needs quoting.
KEY_CHARS = re.compile(r"[^a-z0-9._-]+")

@dataclass
class FactUpdate:
    """One extraction request - the patch it produced and what it cost.

    The same shape as `Compaction` and `AgentReply`, so the debug panel can
    draw it with the same code. `ok` is not "the patch changed something":
    an empty patch from a message that established nothing is the *expected*
    result, and a strategy that reported it as a failure would cry wolf on
    most turns.
    """

    set: dict = field(default_factory=dict)
    unset: list = field(default_factory=list)
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
        return self.error is None

    @property
    def changed(self) -> bool:
        return bool(self.set or self.unset)

    def describe(self) -> str:
        """What this patch did, for a debug-log label."""
        if self.error:
            return "failed"
        parts = []
        if self.set:
            parts.append(f"{len(self.set)} set")
        if self.unset:
            parts.append(f"{len(self.unset)} removed")
        return " · ".join(parts) if parts else "nothing to record"


def clean_key(key) -> str:
    """A key that is safe to put in a prompt and to compare against."""
    text = KEY_CHARS.sub("_", str(key).strip().lower()).strip("._-")
    return text[:MAX_KEY_CHARS]


def clean_value(value) -> str:
    """A value as one short line - never a paragraph, never a structure."""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = " ".join(str(value).split())
    return text[:MAX_VALUE_CHARS]


def apply_patch(facts: dict | None, update: FactUpdate) -> dict:
    """The fact block as it stands after one patch.

    A new dict rather than a mutation, so the caller still has the block that
    was in force when the request was built - the debug panel shows both.

    Re-setting a key moves it to the end. That is what gives the block a
    meaningful order to trim from when it is full: the front is what has not
    been mentioned for longest, which is the closest thing to "least likely to
    matter" that can be known without asking the model again.
    """
    result = dict(facts or {})

    for key in update.unset or []:
        result.pop(clean_key(key), None)

    for key, value in (update.set or {}).items():
        key = clean_key(key)
        value = clean_value(value)
        if not key or not value:
            continue
        result.pop(key, None)
        result[key] = value

    while len(result) > MAX_FACTS:
        result.pop(next(iter(result)))
    return result


def render(facts: dict | None) -> str:
    """The block as it goes into a request: one `key: value` per line.

    Not JSON. This is being read by a model in the middle of a prompt, where
    braces and quotes are noise that has to be paid for by the token and
    invites the model to answer in JSON; and not a sentence either, because
    the point of a key-value memory is that each line can be true or false on
    its own.
    """
    facts = facts or {}
    return "\n".join(f"{key}: {value}" for key, value in facts.items())


def parse_patch(payload: dict) -> tuple[dict, list, str | None]:
    """`{"set": ..., "unset": ...}` out of a completion - `(set, unset, error)`.

    Defensive, and deliberately forgiving in one direction only. A model that
    answers with a bare object of facts instead of the patch envelope is
    read as `set`, because that is unambiguous and it is the mistake that
    actually happens. Anything that is not JSON at all is an error: a fact
    block is sent with every future request, and guessing at what the model
    meant is how a wrong fact gets in and then never leaves.
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return {}, [], "the extractor returned no choices"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return {}, [], "the extractor returned nothing"

    # json_object mode is asked for, but a model that ignores it usually
    # answers with a fenced block rather than with prose.
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content

    try:
        data = json.loads(content)
    except ValueError:
        return {}, [], "the extractor did not return JSON"
    if not isinstance(data, dict):
        return {}, [], "the extractor did not return an object"

    if "set" in data or "unset" in data:
        raw_set = data.get("set")
        raw_unset = data.get("unset")
    else:
        raw_set, raw_unset = data, []

    to_set = {k: v for k, v in raw_set.items()} if isinstance(raw_set, dict) else {}
    to_unset = [k for k in raw_unset] if isinstance(raw_unset, list) else []
    return to_set, to_unset, None


class FactKeeper:
    """Keeps one conversation's fact block current, one message at a time.

    Configured with the conversation's own model, for the same reason
    `Compactor` is: whichever model the chat is talking to is the model that
    gets to decide what the chat is about, and nothing here quietly moves the
    work onto a different one.
    """

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, facts: dict | None, user_message: str, recent: list[dict]) -> dict:
        """The extraction request - and note what is *not* in it.

        Not the transcript. The current block, at most one previous turn (so
        that "yes, do that" has something to refer to) and the new message.
        Three short strings, whether the conversation is four messages old or
        four hundred, which is the entire reason this strategy can afford to
        run on every single turn.
        """
        block = render(facts)
        parts = [
            "Memory as it stands:\n\n" + (block if block else "(empty)"),
        ]
        if recent:
            parts.append(
                "The previous turn, for context only - do not record anything from "
                "it that is not confirmed by the new message:\n\n"
                + "\n\n".join(
                    f"{m.get('role', 'user')}: {excerpt(m.get('content'))}" for m in recent
                )
            )
        parts.append("New message from the user:\n\n" + excerpt(user_message))
        parts.append("Reply with the JSON patch now, and nothing else.")

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": FACTS_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": FACTS_TEMPERATURE,
            # The one place in this app that asks for JSON back, and it asks
            # for it because the answer is parsed rather than read.
            "response_format": {"type": "json_object"},
            # Thinking is billed as output (day 8) and there is nothing here to
            # think about that reading the message twice would not settle.
            "reasoning_effort": "none",
            "max_tokens": FACTS_MAX_TOKENS,
        }

    def update(self, facts: dict | None, user_message: str, recent: list[dict]) -> FactUpdate:
        """One request: the memory and a new message in, a patch out.

        Never raises. A failed extraction means this turn goes up with the
        fact block exactly as it was - which is a memory one message out of
        date, not a broken chat, and is reported rather than hidden.
        """
        request = self.build_request(facts, user_message, recent)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return FactUpdate(
                error=f"Error: {exc}",
                request=request,
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        to_set, to_unset, error = parse_patch(result.response)
        return FactUpdate(
            set=to_set,
            unset=to_unset,
            error=error,
            request=result.request,
            response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )


def excerpt(content) -> str:
    text = str(content or "")
    if len(text) > MESSAGE_EXCERPT_CHARS:
        return text[:MESSAGE_EXCERPT_CHARS] + " […trimmed]"
    return text
