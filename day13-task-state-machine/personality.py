"""Day 12: what the user told the agent about themselves, their work and their taste.

Day 11 gave the agent three memories and a rule for telling them apart: *when
should this be forgotten?* That rule sorts facts. It does not sort this.

    "You are a helpful assistant."          set by whoever built the app
    "Senior dev, voice assistant, team      set by whoever is using it
     of 3, two weeks left. Python only,
     free APIs. Answer in Russian, no
     preamble."

Both end up in the system prompt, both shape every answer, and until today the
app had one slot for the pair of them - `system_prompt`, a free-text box in the
chat's settings, inherited from day 3 where it held a *reasoning style*. So the
second block had nowhere to go that was not also where the first one lived, no
way to be kept between chats, and no way to be switched.

This file is the second block. The distinction it rests on is not lifetime but
**refutability**:

    a memory       is something the agent learned, and it can be wrong
    a personality  is something you declared, and it cannot be wrong -
                   it can only stop being what you want

Three fields, because a person configuring an assistant is answering three
different questions and they behave differently once written down:

    style         how answers should be written        taste, softest
    preferences   standing choices for any topic       rules, checkable
    context       who you are and what you are on      facts about you

`context` is the one that makes this worth building. "Senior developer" changes
what may be skipped; "team of 3" changes what is worth automating; "deadline in
two weeks" changes which of two designs gets recommended. None of that is
inferable from the question, none of it is worth retyping into every chat, and
none of it belongs in a memory layer - nothing here was learned, and nothing
here can be contradicted by something said later.

Everything else follows from the refutability line:

- **It is not recalled, it is applied.** `memory.recall` scores items against
  the question and sends the few that match, because a memory layer grows
  without bound and most of it is irrelevant to any one question. A profile is
  relevant to every question by construction. There is no scoring in this file
  and there is deliberately nothing imported from `memory.py`.
- **Only a person writes it.** `MemoryProposer` exists because the agent
  notices facts. Nothing here is ever proposed: a wrong fact spoils one answer,
  a wrong standing instruction spoils every answer after it.
- **It is bounded.** A memory layer is capped at 120 items as a backstop.
  A profile is three fields, and that is the design rather than a backstop.
- **It is not stored in the conversation.** Nothing a profile says is written
  into a message, so switching the active profile changes every future request
  in every chat, including the ones already on screen.

Three named fields rather than one textarea, and that is the part worth
arguing about. A single free-text field would be strictly more expressive -
and would be, exactly, a second custom system prompt sitting underneath the
first one, which is the duplication this day exists to remove. Named fields
buy a form people actually fill in, and a shape that says what is being asked
for: an empty box labelled "preferences" gets you nothing, while a box labelled
"who you are and what you are working on" gets you "senior dev, voice
assistant, team of 3".
"""

from __future__ import annotations

import re

#: The three fields of a profile, in the order they are shown and rendered.
STYLE = "style"
PREFERENCES = "preferences"
CONTEXT = "context"

FIELDS = (STYLE, PREFERENCES, CONTEXT)

#: What day 12 looked like before these three: four fields that were all, on
#: inspection, one question asked four ways - `language`, `tone`, `format` and
#: `constraints` are every one of them "how should this be written". They fold
#: into `style` on read (see `clean_fields`) rather than being dropped,
#: because by the time that was obvious there were profiles on disk with
#: somebody's actual preferences in them.
LEGACY_STYLE_FIELDS = ("language", "tone", "format", "constraints")

#: `constraints` was the one of the four that did not say what it meant. It
#: held a list of things *never to do* - the label above the box said so, and
#: the lines in it did not: "restate the question", "skip an intermediate
#: step". Folded into a flat style block those read as instructions to do
#: exactly that, so the prohibition has to be put back on each line as it
#: moves. A migration that silently inverts four of somebody's rules is worse
#: than one that drops them.
LEGACY_NEGATED_FIELD = "constraints"
NEGATION = "never "

#: What each field is, sent to the frontend so the form and this file cannot
#: drift apart - the same trick `memory.LAYER_INFO` and `strategies.
#: STRATEGY_INFO` play, and for the same reason.
#:
#: `prompt_label` is what the model sees and `label` is what the person sees,
#: because they are addressed differently: the form asks *you* about your
#: project, and the prompt describes a third party who is not in the room.
FIELD_INFO = {
    STYLE: {
        "label": "Style",
        "prompt_label": "How they want answers written",
        "hint": "How an answer should read - language, length, tone, what "
                "never to do. The softest of the three: a model can drift "
                "here without anything being wrong.",
        "placeholder": "in Russian\nshort and direct, no preamble\ncode first, "
                       "explanation after\nnever apologise",
        "lines": 4,
    },
    PREFERENCES: {
        "label": "Preferences",
        "prompt_label": "Standing preferences, whatever the topic",
        "hint": "Choices already made, that any answer should respect - the "
                "stack, the architecture, what is out of bounds. Not about "
                "one question; about all of them.",
        "placeholder": "Python + FastAPI, no heavy frameworks\nminimum "
                       "dependencies\nfree APIs only\nfiles over a database "
                       "until it hurts",
        "lines": 5,
    },
    CONTEXT: {
        "label": "Context",
        "prompt_label": "Who they are and what they are working on",
        "hint": "Who is asking, and what for. This is what lets the agent "
                "skip what you already know and weigh a recommendation "
                "against the time you actually have.",
        "placeholder": "senior developer, 10 years\nbuilding a voice "
                       "assistant\nteam of 3\ndeadline in two weeks",
        "lines": 5,
    },
}

#: A profile is sent whole with every single request, so its size is a
#: standing cost rather than an occasional one - unlike a recalled memory,
#: which is paid for only when it matches. Three fields at this length come to
#: roughly 300-400 tokens on every message, which is the budget this day is
#: willing to spend. The number exists to stop a field quietly becoming an
#: essay, not to stop anyone saying what they mean.
MAX_FIELD_CHARS = 600
MAX_NAME_CHARS = 60

#: How many lines one field contributes. These fields are lists - four or five
#: short lines each is the shape they are for - and a `preferences` block with
#: forty rules in it has stopped being a preference and become a prompt.
MAX_FIELD_LINES = 12

WORD_SPLIT = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")

#: A profile id keeps this many words of its name - the same derivation
#: `memory.slug` uses, and for the same reason: an id nobody has to invent.
#: Unlike a memory key it is *not* an identity that corrections collapse onto,
#: because two profiles called "Work" are two profiles; `unique_id` breaks the
#: tie instead of overwriting.
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

    Lines are kept as lines rather than joined: all three fields are lists in
    practice, and read as lists both in the form and in the prompt. What is
    thrown away is whitespace and emptiness, so nothing a person typed comes
    back differently from how they meant it.
    """
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    lines = [line for line in lines if line][:MAX_FIELD_LINES]
    return "\n".join(lines)[:MAX_FIELD_CHARS]


def clean_fields(raw) -> dict:
    """The three fields, cleaned, with everything else folded in or dropped.

    Unknown keys are discarded rather than kept: a profile is what this file
    says it is, and a field the renderer has never heard of would be stored
    forever, shown nowhere and sent to nobody.

    The four keys of the first cut of this day are the exception. They were
    all "how should this be written", so they append to `style` - a profile
    written under the old shape keeps working and reads correctly, and is
    rewritten into the new one the first time it is saved.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name in FIELDS:
        value = clean_field(raw.get(name))
        if value:
            out[name] = value
    legacy = []
    for name in LEGACY_STYLE_FIELDS:
        value = clean_field(raw.get(name))
        if not value:
            continue
        for line in value.splitlines():
            negate = name == LEGACY_NEGATED_FIELD and not line.lower().startswith(NEGATION)
            legacy.append(NEGATION + line if negate else line)
    if legacy:
        merged = ([out[STYLE]] if STYLE in out else []) + legacy
        out[STYLE] = clean_field("\n".join(merged))
    return out


def is_empty(fields) -> bool:
    """Whether a profile would contribute nothing to a request."""
    return not clean_fields(fields)


def render(profile) -> str:
    """A profile as the lines that go into the prompt, or `""` for nothing.

    Labelled blocks rather than the `key: value` lines `facts.render` and
    `memory.render` produce, and that difference is on purpose: those two are
    lists of small independent statements, while these are three paragraphs
    about three different subjects, and running them together would make the
    model read a preference about the stack as a preference about prose.

    Every field is a list, so every field renders as one. A single-line field
    is still a list of one, and writing it as `Label: value` would make one
    field look like a different kind of thing from its neighbours.
    """
    fields = clean_fields(getattr(profile, "fields", None) if profile is not None else None)
    if not fields:
        return ""
    blocks = []
    for name in FIELDS:
        value = fields.get(name)
        if not value:
            continue
        lines = "\n".join(f"- {line}" for line in value.splitlines())
        blocks.append(f"{FIELD_INFO[name]['prompt_label']}:\n{lines}")
    return "\n\n".join(blocks)


def summary(profile) -> str:
    """One line describing a profile, for a list of them."""
    fields = clean_fields(getattr(profile, "fields", None) if profile is not None else None)
    parts = [fields[name].replace("\n", ", ") for name in FIELDS if fields.get(name)]
    line = " · ".join(parts)
    return line if len(line) <= 120 else line[:119] + "…"


def filled(profile) -> list:
    """Which of the three fields a profile has anything in.

    The panel uses it to say `style · context` under a name, so a half-filled
    profile is visibly half-filled rather than looking like a finished one
    whose summary happened to be short.
    """
    fields = clean_fields(getattr(profile, "fields", None) if profile is not None else None)
    return [name for name in FIELDS if fields.get(name)]


#: What the app ships with, written to disk the first time it runs and never
#: again - deleting either is allowed to stick. Neither is switched on.
#:
#: Two, and they are two different kinds of thing, which is the whole of why
#: there are not six. The first cut of this day shipped three writing styles
#: ("Terse", "Tutor", "Russian reviewer") because a profile *was* a writing
#: style. A profile is now largely about a particular person - their
#: seniority, their project, their team - and shipping invented people would
#: be noise in the one list that is supposed to be yours.
#:
#: But `style` is the one field of the three that is **not** about a
#: particular person. Nothing about "answer first, then say what the question
#: leaves out" belongs to anybody, which is what makes a style-only profile
#: shippable when a filled-in `context` is not. So:
#:
#:   Example    all three fields, so the shape is visible before you have
#:              filled any of them in. Read it, then delete it.
#:   Rational   style only, and actually usable as it stands.
SEED_PROFILES = (
    {
        "name": "Example",
        "fields": {
            STYLE: "short and direct, no preamble\ncode first, explanation after",
            PREFERENCES: "Python + FastAPI\nminimum dependencies\nfree APIs only",
            CONTEXT: "senior developer\nbuilding a voice assistant\n"
                     "team of 3\ndeadline in two weeks",
        },
    },
    {
        # Written as instructions about the *shape of an answer* rather than
        # as a character ("you are a rigorous analyst"), because a character
        # is something a model performs and a shape is something it can be
        # held to. "Name the assumption the question rests on" either happened
        # or it did not; "be rigorous" cannot be checked by anyone.
        "name": "Rational",
        "fields": {
            STYLE: "answer the question first, in as few sentences as it takes\n"
                   "then name what the question leaves out: the assumption it "
                   "rests on, the option not considered, the cost not priced\n"
                   "give the strongest case against the answer just given, and "
                   "say what would change it\n"
                   "weigh a trade-off out loud rather than picking a side quietly\n"
                   "say when there is not enough information to answer, and name "
                   "exactly what is missing\n"
                   "never open with a preamble, restate the question, or close "
                   "with an offer of further help",
        },
    },
)
