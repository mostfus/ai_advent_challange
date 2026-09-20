"""Day 12: how the user wants to be answered - kept apart from what the agent knows.

Day 11 gave the agent three memories and a rule for telling them apart: *when
should this be forgotten?* That rule sorts facts. It does not sort this.

    "You are a helpful assistant."          set by whoever built the app
    "Answer in Russian, no preamble."       set by whoever is using it

Both end up in a system message, both are about how the model should behave,
and until today the app had one slot for the pair of them - `system_prompt`,
a free-text box in the chat's settings, inherited from day 3, where it held a
*reasoning style*. So there was nowhere for the second line to go that was not
also the place the first one lived, and no way to keep two sets of preferences
and switch between them without retyping.

This file is the second line, and the distinction it rests on is not lifetime
but **refutability**:

    a memory       is something the agent learned, and it can be wrong
    a personality  is something you declared, and it cannot be wrong -
                   it can only stop being what you want

Everything else follows from that one difference:

- **It is not recalled, it is applied.** `memory.recall` scores items against
  the question and sends the few that match, because a memory layer grows
  without bound and most of it is irrelevant to any one question. A profile is
  relevant to every question by construction. There is no scoring in this file
  and there is deliberately nothing to import from `memory.py`.
- **Only a person writes it.** `MemoryProposer` exists because the agent
  notices facts. Nothing here is ever proposed: a wrong fact spoils one answer,
  a wrong standing instruction spoils every answer after it.
- **It is bounded.** A memory layer is capped at 120 items as a backstop. A
  profile is four fields, and that is not a backstop - it is the design. A
  profile that needs a fifth paragraph has stopped being a preference and
  become a prompt, which is what the box above it is already for.
- **It is not stored in the conversation.** Nothing a profile says is written
  into a message, so switching the active profile changes every future request
  in every chat, including the ones already on screen. That is the whole point
  of making it a separate object: the transcript records what was said, and
  this records how you want to be spoken to, and the second one is allowed to
  change its mind about the first.

Four named fields rather than one textarea, and that is the only part of this
worth arguing about. A single free-text field would be strictly more
expressive - and would be, exactly, a second custom system prompt sitting
underneath the first one, which is the duplication this day exists to remove.
Named fields buy two things a blank box does not: a form people actually fill
in, and two fields (`language`, `format`) concrete enough that an answer can
be checked against them afterwards.
"""

from __future__ import annotations

import re

#: The fields of a profile, in the order they are shown and rendered.
#:
#: `language` is first because it is the one instruction whose breach is
#: unmistakable, and the one people most often file into memory instead -
#: `constraint.language: answer in russian` is in day 11's own README, in the
#: fact block, which is the misfiling this file corrects.
LANGUAGE = "language"
TONE = "tone"
FORMAT = "format"
CONSTRAINTS = "constraints"

FIELDS = (LANGUAGE, TONE, FORMAT, CONSTRAINTS)

#: What each field is, sent to the frontend so the form and this file cannot
#: drift apart - the same trick `memory.LAYER_INFO` and `strategies.
#: STRATEGY_INFO` play, and for the same reason.
#:
#: `prompt_label` is what the model sees and `label` is what the person sees,
#: because they are addressed differently: the form asks "Tone", the prompt
#: says "Tone:" about somebody who is not in the room.
FIELD_INFO = {
    LANGUAGE: {
        "label": "Language",
        "prompt_label": "Language",
        "hint": "Which language answers come back in, whatever language the "
                "question was asked in.",
        "placeholder": "Russian",
        "lines": 1,
    },
    TONE: {
        "label": "Tone",
        "prompt_label": "Tone",
        "hint": "How it should sound. The softest of the four - a model can "
                "drift here without anything being wrong.",
        "placeholder": "direct, no hedging, no enthusiasm",
        "lines": 2,
    },
    FORMAT: {
        "label": "Format",
        "prompt_label": "Format",
        "hint": "Shape and length. Checkable after the fact, which is what "
                "makes it worth stating precisely.",
        "placeholder": "short; bullet points; at most five sentences",
        "lines": 2,
    },
    CONSTRAINTS: {
        "label": "Never do",
        "prompt_label": "Never",
        "hint": "The hard half. One per line - breaking one of these is a "
                "fault, not a matter of taste.",
        "placeholder": "apologise\nopen with a preamble\noffer alternatives "
                       "nobody asked for",
        "lines": 3,
    },
}

#: A profile is sent whole with every single request, so its size is a
#: standing cost rather than an occasional one - unlike a recalled memory,
#: which is paid for only when it matches. Four fields at this length is
#: roughly 120 tokens on every message, which is the budget this day is
#: willing to spend; the number exists to stop a field quietly becoming an
#: essay, not to stop anyone saying what they mean.
MAX_FIELD_CHARS = 400
MAX_NAME_CHARS = 60

#: How many lines one field contributes. A `constraints` field with forty
#: rules in it is a prompt, and the argument in the module docstring applies.
MAX_FIELD_LINES = 8

WORD_SPLIT = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")

#: A profile id keeps this many words of its name - the same derivation
#: `memory.slug` uses, and for the same reason: an id nobody has to invent.
#: Unlike a memory key it is *not* an identity that corrections collapse onto,
#: because two profiles called "Short" are two profiles; `unique_id` breaks
#: the tie instead of overwriting.
ID_WORDS = 4
ID_CHARS = 40


def slug(text: str) -> str:
    """An id derived from a profile's name."""
    parts = [p for p in WORD_SPLIT.split(str(text or "").lower()) if p]
    return "-".join(parts[:ID_WORDS])[:ID_CHARS].strip("-")


def unique_id(name: str, taken) -> str:
    """`slug(name)`, with a number on the end if that is already somebody's."""
    base = slug(name) or "profile"
    taken = set(taken or ())
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    return base


def clean_name(name: str) -> str:
    """A profile's name, trimmed to one line - it is a label on a button."""
    text = " ".join(str(name or "").split())
    return text[:MAX_NAME_CHARS]


def clean_field(value: str) -> str:
    """One field, trimmed - blank lines dropped, each line squeezed.

    Lines are kept as lines rather than joined here: `constraints` is a list
    of prohibitions and reads as one, both in the form and in the prompt. What
    is thrown away is only whitespace and emptiness, so nothing a person typed
    comes back differently from how they meant it.
    """
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    lines = [line for line in lines if line][:MAX_FIELD_LINES]
    return "\n".join(lines)[:MAX_FIELD_CHARS]


def clean_fields(raw) -> dict:
    """The four fields, cleaned, with everything else dropped.

    Unknown keys are discarded rather than kept: a profile is what this file
    says it is, and a field the renderer has never heard of would be stored
    forever, shown nowhere and sent to nobody.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name in FIELDS:
        value = clean_field(raw.get(name))
        if value:
            out[name] = value
    return out


def is_empty(fields) -> bool:
    """Whether a profile would contribute nothing to a request."""
    return not clean_fields(fields)


def render(profile) -> str:
    """A profile as the lines that go into the prompt, or `""` for nothing.

    Labelled `key: value` lines, the same shape `facts.render` and
    `memory.render` produce - so every block in a request reads alike and the
    agent cannot tell which of them it was handed. The labels are the only
    thing that separates this block from those, and the prefix in `agent.py`
    is what says it is an instruction rather than a statement of fact.

    A multi-line field becomes an indented list under its label, because the
    alternative - joining prohibitions with semicolons - reliably produces one
    long sentence that a model reads as a single rule with qualifications.
    """
    fields = clean_fields(getattr(profile, "fields", None) if profile is not None else None)
    if not fields:
        return ""
    out = []
    for name in FIELDS:
        value = fields.get(name)
        if not value:
            continue
        label = FIELD_INFO[name]["prompt_label"]
        lines = value.splitlines()
        if len(lines) == 1:
            out.append(f"{label}: {lines[0]}")
        else:
            out.append(f"{label}:")
            out.extend(f"- {line}" for line in lines)
    return "\n".join(out)


def summary(profile) -> str:
    """One line describing a profile, for a list of them."""
    fields = clean_fields(getattr(profile, "fields", None) if profile is not None else None)
    parts = []
    for name in FIELDS:
        value = fields.get(name)
        if value:
            parts.append(value.replace("\n", ", "))
    line = " · ".join(parts)
    return line if len(line) <= 120 else line[:119] + "…"


#: What the app ships with, written to disk the first time it runs and never
#: again - deleting them is allowed to stick.
#:
#: Three rather than one because the day's claim is not "the agent can be
#: configured", it is "switching this changes the answer", and a single
#: profile cannot show that. They are deliberately far apart: the same
#: question under `terse` and under `tutor` should not come back looking
#: remotely similar, which is the whole demonstration.
#:
#: None of them is active to begin with. The before-and-after is the point,
#: and an app that starts with a personality already applied has nothing to
#: compare against.
SEED_PROFILES = (
    {
        "name": "Terse",
        "fields": {
            TONE: "plain and direct; state the conclusion first",
            FORMAT: "at most five sentences, unless asked for more",
            CONSTRAINTS: "apologise\nopen with a preamble\nrestate the question\n"
                         "end with an offer of further help",
        },
    },
    {
        "name": "Tutor",
        "fields": {
            TONE: "patient; explain the reasoning rather than only the result",
            FORMAT: "numbered steps, then the answer on its own line at the end",
            CONSTRAINTS: "skip an intermediate step\nuse a term without defining it once",
        },
    },
    {
        "name": "Russian reviewer",
        "fields": {
            LANGUAGE: "Russian",
            TONE: "blunt and critical; disagree when there is reason to",
            FORMAT: "bullet points; the weakest point first",
            CONSTRAINTS: "compliment the question\nhedge a judgement you can justify",
        },
    },
)
