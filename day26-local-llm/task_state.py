"""Day 25: the task state - what a RAG conversation is for, and what it has settled.

Every RAG turn so far was a question on its own. The answer saw the
transcript, but the *search* saw only the last message - so the follow-up
that every real conversation is made of found nothing:

    - What is the reranker's default threshold?      search: that sentence     -> [1][2]
    - And why that number?                           search: "and why that number"  -> nothing
    - Not sure. The reranker's, or chunking's?
    - The reranker's.                                search: "the reranker's"  -> nothing useful

The task state is the conversation's working memory of itself - the three
parts the brief names, and two that place them:

    goal          what the user is trying to find out or do here       one phrase
    focus         what the latest question is about, so a follow-up    one phrase
                  ("and why that number?") is searched as one
    clarified     what they have narrowed down: answers to the         a list
                  assistant's questions, which part they mean, what
                  they are and are not after
    constraints   rules for the answers: language, length, format,     a list
                  what to leave out, what to rely on
    terms         words that mean something fixed in this chat         term -> meaning

It is used in two places, and the second is the point:

* **The answer's prompt** - a block of its own (`render`), so the question
  is read in its light, a clarified point is not asked about again, and the
  constraints hold from the message they were stated in to the last one.
* **The search** - `search_query` joins the focus, the goal, the
  clarifications and the terms onto the question, by code. Not a rewrite by a model (day 23 left
  that out on purpose): the question goes first and as typed, and the rest
  is appended so that "and why that number?" is searched for together with
  "the reranker's default threshold". The constraints are not joined: "answer
  in Russian" is about the answer, and in a search it is noise.

The mechanics are `facts.py`'s, and for its reasons:

* **Updated after every user message, before the answer** - the
  clarification just given has to be in the search it is for.
* **A patch, not a rewrite** - `{"goal", "focus", "add", "remove"}`. A rewrite that
  drops a clarification looks like a correct one; a patch has to say so.
* **A constant-size prompt** - the state, the previous turn (so an answer
  to "the reranker's or chunking's?" has its question) and the new message.
* **Caps on everything** - a state that grows without limit is the
  transcript again.

What it is *not* is day 10's facts. Those are one of three strategies for
fitting a long chat into a request, a key-value block with no fixed shape,
and they are off unless that strategy is picked; this is on in every RAG
chat, whatever the strategy, has the five parts above and nothing else, and
feeds the search. Nor is it day 11's working memory or day 13's stages:
those belong to a task a person opened by hand and span chats; this belongs
to one branch of one conversation and is written by the model as it goes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import tokens
from facts import excerpt
from llm_client import LLMClient, LLMClientError

GOAL = "goal"
FOCUS = "focus"
CLARIFIED = "clarified"
CONSTRAINTS = "constraints"
TERMS = "terms"

#: The two list sections - `terms` is a mapping, `goal` and `focus` single phrases.
PHRASES = (GOAL, FOCUS)
LISTS = (CLARIFIED, CONSTRAINTS)
SECTIONS = (GOAL, FOCUS, CLARIFIED, CONSTRAINTS, TERMS)

#: Per section, not in total: ten clarifications are a long conversation, and
#: an eleventh pushes out the oldest, which is the one least likely to matter.
MAX_ITEMS = 10
MAX_CHARS = 240

#: Reading, not writing - and a patch is a few short strings.
TEMPERATURE = 0.0
MAX_TOKENS = 500

SYSTEM_PROMPT = (
    "You keep the task state of a conversation between a user and an assistant that "
    "answers questions about one software project from its documents (README and Python "
    "modules).\n"
    "You are given the state as it stands, the previous turn and the user's new message. "
    "Reply with JSON only, in the form\n"
    '{"goal": "", "focus": "", "add": {"clarified": [], "constraints": [], "terms": {}}, '
    '"remove": {"clarified": [], "constraints": [], "terms": []}}\n\n'
    "The five parts:\n"
    "- goal: what the user is trying to find out or achieve in this conversation, as one "
    "short phrase. Set it from the first substantive message. Replace it only when the user "
    "moves on to a different goal; leave it \"\" to keep it as it is.\n"
    "- focus: the specific thing the latest substantive question is about, as one phrase "
    "that reads on its own - \"the reranker's default threshold (6)\", not \"the "
    "threshold\". Replace it when the user asks about something else. A follow-up that "
    "refers back (\"and why?\", \"what about the other one?\") keeps it: leave it \"\".\n"
    "- clarified: what the user has narrowed down about what they mean - an answer to a "
    "question the assistant asked, which component, file, mode or version they are asking "
    "about, what they are or are not interested in, a correction of how the assistant read "
    "them. Each item must read on its own, without the conversation: write \"the threshold "
    "asked about is the reranker's, not chunking's\", never \"the reranker's\".\n"
    "- constraints: rules the answers must follow - language, length, format, what to leave "
    "out, which sources to rely on.\n"
    "- terms: words or abbreviations that have a fixed meaning in this conversation, as "
    "term -> meaning, using the user's own word as the term. Only meanings the user "
    "stated or confirmed - never a definition you inferred yourself.\n\n"
    "Rules:\n"
    "- If the previous assistant message asked a clarifying question and the new message "
    "answers it, add the answer to clarified, combined with what was asked.\n"
    "- A plain question establishes at most the goal and the focus. Do not copy the "
    "question into clarified.\n"
    "- When the goal changes to an unrelated one, remove the clarified items and terms that "
    "belonged to the old goal; keep constraints unless the user withdrew them.\n"
    "- To remove an item, copy it exactly as it appears in the state; for a term, give the "
    "term. Use remove when the user retracts or contradicts something.\n"
    "- Each thing goes into one part only: a word the user defined is a term, not also a "
    "clarification.\n"
    "- Nothing from the assistant's answers goes in unless the user confirmed it.\n"
    "- Write in the language the user writes in. Short phrases, not sentences of "
    "explanation.\n"
    "- An empty patch is the normal answer for a message that settles nothing new."
)


# --------------------------------------------------------------------------
# The state as data

def empty() -> dict:
    return {GOAL: "", FOCUS: "", CLARIFIED: [], CONSTRAINTS: [], TERMS: {}}


def clean(text) -> str:
    """One line, trimmed, capped - the state is sent with every request."""
    text = " ".join(str(text or "").split())
    return text[:MAX_CHARS].rstrip()


def same(a: str, b: str) -> bool:
    """Equal for a person: case and spacing do not make two items."""
    return clean(a).casefold() == clean(b).casefold()


def normalise(state: dict | None) -> dict:
    """A stored or hand-edited state, back into the shape the rest relies on."""
    out = empty()
    state = state if isinstance(state, dict) else {}
    for phrase in PHRASES:
        out[phrase] = clean(state.get(phrase))
    for section in LISTS:
        raw = state.get(section)
        for item in raw if isinstance(raw, list) else []:
            item = clean(item)
            if item and not any(same(item, x) for x in out[section]):
                out[section].append(item)
        out[section] = out[section][-MAX_ITEMS:]
    raw = state.get(TERMS)
    if isinstance(raw, dict):
        for term, meaning in raw.items():
            term, meaning = clean(term), clean(meaning)
            if term and meaning:
                out[TERMS][term] = meaning
    out[TERMS] = dict(list(out[TERMS].items())[-MAX_ITEMS:])
    return out


def is_empty(state: dict | None) -> bool:
    state = normalise(state)
    return not (state[GOAL] or state[FOCUS] or state[CLARIFIED] or state[CONSTRAINTS] or state[TERMS])


def count(state: dict | None) -> int:
    state = normalise(state)
    return sum(1 for p in PHRASES if state[p]) + len(state[CLARIFIED]) + len(state[CONSTRAINTS]) + len(state[TERMS])


# --------------------------------------------------------------------------
# The patch

@dataclass
class StateUpdate:
    """One extraction request - the patch it produced and what it cost.

    `FactUpdate`'s shape, so the debug panel draws it with the same code.
    An empty patch is the expected result, not a failure.
    """

    goal: str = ""
    focus: str = ""
    add: dict = field(default_factory=dict)
    remove: dict = field(default_factory=dict)
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


def parse_patch(payload: dict) -> tuple[dict, dict, dict, str | None]:
    """`(phrases, add, remove, error)` out of a completion - forgiving about shape, not about JSON.

    `phrases` is `{"goal": ..., "focus": ...}`, "" for "keep it".
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return {}, {}, {}, "the extractor returned no choices"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except ValueError:
        return {}, {}, {}, "the extractor did not return JSON"
    if not isinstance(data, dict):
        return {}, {}, {}, "the extractor did not return an object"

    phrases = {p: clean(data.get(p)) if isinstance(data.get(p), str) else "" for p in PHRASES}
    add = data.get("add") if isinstance(data.get("add"), dict) else {}
    remove = data.get("remove") if isinstance(data.get("remove"), dict) else {}
    # A model that answers with the sections at the top level instead of under
    # "add" means the same thing - the mistake that actually happens.
    for section in (CLARIFIED, CONSTRAINTS, TERMS):
        if section in data and section not in add:
            add[section] = data[section]
    return phrases, add, remove, None


def as_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [clean(v) for v in value if clean(v)] if isinstance(value, list) else []


def apply_patch(state: dict | None, update: StateUpdate) -> tuple[dict, list[dict]]:
    """The next state, and what changed - `[{"section", "op", "text"}]`, op in `+ - ~`.

    The changes are what was *done*, not what was asked: a removal that names
    nothing in the state, or an addition already there, is not a change, and
    the message's record under its bubble does not claim one.
    """
    state = normalise(state)
    changes: list[dict] = []

    for phrase in PHRASES:
        value = clean(getattr(update, phrase))
        if value and not same(value, state[phrase]):
            changes.append({"section": phrase, "op": "~" if state[phrase] else "+", "text": value})
            state[phrase] = value

    for section in LISTS:
        for item in as_list(update.remove.get(section)):
            kept = [x for x in state[section] if not same(x, item)]
            if len(kept) < len(state[section]):
                gone = next(x for x in state[section] if same(x, item))
                changes.append({"section": section, "op": "-", "text": gone})
                state[section] = kept
        for item in as_list(update.add.get(section)):
            if not any(same(item, x) for x in state[section]):
                state[section].append(item)
                changes.append({"section": section, "op": "+", "text": item})
        state[section] = state[section][-MAX_ITEMS:]

    for term in as_list(update.remove.get(TERMS)):
        key = next((t for t in state[TERMS] if same(t, term)), None)
        if key is not None:
            changes.append({"section": TERMS, "op": "-", "text": f"{key} = {state[TERMS].pop(key)}"})
    raw = update.add.get(TERMS)
    for term, meaning in (raw.items() if isinstance(raw, dict) else []):
        term, meaning = clean(term), clean(meaning)
        if not term or not meaning:
            continue
        key = next((t for t in state[TERMS] if same(t, term)), term)
        if state[TERMS].get(key) == meaning:
            continue
        changes.append({"section": TERMS, "op": "~" if key in state[TERMS] else "+", "text": f"{key} = {meaning}"})
        state[TERMS].pop(key, None)
        state[TERMS][key] = meaning
    state[TERMS] = dict(list(state[TERMS].items())[-MAX_ITEMS:])
    return state, changes


def forget(state: dict | None, section: str, item: str) -> dict:
    """The state without one item - the panel's ×. A phrase is forgotten whatever `item` says."""
    state = normalise(state)
    if section in PHRASES:
        state[section] = ""
    elif section in LISTS:
        state[section] = [x for x in state[section] if not same(x, item)]
    elif section == TERMS:
        state[TERMS] = {t: m for t, m in state[TERMS].items() if not same(t, item)}
    return state


# --------------------------------------------------------------------------
# Where it is used

def render(state: dict | None) -> str:
    """The block the answer is asked under. Plain lines, the way every block here reads."""
    state = normalise(state)
    lines = []
    if state[GOAL]:
        lines.append(f"Goal: {state[GOAL]}")
    if state[FOCUS]:
        lines.append(f"The latest question is about: {state[FOCUS]}")
    if state[CLARIFIED]:
        lines.append("Clarified by the user:\n" + "\n".join(f"- {x}" for x in state[CLARIFIED]))
    if state[CONSTRAINTS]:
        lines.append("Constraints on the answers:\n" + "\n".join(f"- {x}" for x in state[CONSTRAINTS]))
    if state[TERMS]:
        lines.append("Terms:\n" + "\n".join(f"- {t}: {m}" for t, m in state[TERMS].items()))
    return "\n".join(lines)


def search_query(question: str, state: dict | None) -> str:
    """What the index is searched with: the question, then what the conversation has settled.

    The question first and as typed - the judge reads its first line as the
    question - and the rest on a second line, labelled, so that the judge
    reads it as context and the embedding gets the words. The focus comes
    first, being the most specific: it is what a follow-up refers back to.
    No constraints:
    they are about the answer, not about where it is.
    """
    state = normalise(state)
    context = [state[p] for p in (FOCUS, GOAL) if state[p]] + state[CLARIFIED] + \
        [f"{t} = {m}" for t, m in state[TERMS].items()]
    if not context:
        return question
    return f"{question}\nConversation context: " + "; ".join(context)


# --------------------------------------------------------------------------
# The request

class TaskStateKeeper:
    """Keeps one branch's task state current, one message at a time - `FactKeeper`'s twin."""

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, state: dict | None, user_message: str, recent: list[dict]) -> dict:
        block = render(state)
        parts = ["Task state as it stands:\n\n" + (block if block else "(empty)")]
        if recent:
            parts.append(
                "The previous turn, for context - record nothing from it the new message does "
                "not confirm or answer:\n\n"
                + "\n\n".join(f"{m.get('role', 'user')}: {excerpt(m.get('content'))}" for m in recent)
            )
        parts.append("New message from the user:\n\n" + excerpt(user_message))
        parts.append("Reply with the JSON patch now, and nothing else.")
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": TEMPERATURE,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "max_tokens": MAX_TOKENS,
        }

    def update(self, state: dict | None, user_message: str, recent: list[dict]) -> StateUpdate:
        """One request. Never raises: a failed update means the turn goes up on the state as it stood."""
        request = self.build_request(state, user_message, recent)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return StateUpdate(error=f"Error: {exc}", request=request, url=exc.url,
                               status_code=exc.status_code, request_headers=self.client.redacted_headers)
        phrases, add, remove, error = parse_patch(result.response)
        return StateUpdate(
            goal=phrases.get(GOAL, ""), focus=phrases.get(FOCUS, ""), add=add, remove=remove, error=error,
            request=result.request, response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms, url=result.url, status_code=result.status_code,
            request_headers=result.request_headers,
        )
