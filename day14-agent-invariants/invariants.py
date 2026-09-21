"""Day 14: the rules an answer is not allowed to break.

Day 12 already lets you write "Python + FastAPI, free APIs only" into a
profile, and day 12's own README concedes what is wrong with that: those
lines "are the closest thing here to a specification, and the easiest to
check an answer against." Nothing checks them. The profile is **stated**, the
model complies at whatever rate models comply, and the only party who ever
finds out it did not is the person reading the answer, afterwards.

That is the gap this file is for, and the distinction is not what the rule
says - it is what happens when the rule is ignored:

    a preference    the answer reads wrong, and you notice eventually
    an invariant    the answer *is* wrong, and the code notices first

So an invariant is not another block of standing text with a firmer tone. It
is day 13's argument applied to a different object:

    day 13   the model reports where work stands   the code rules   you click
    day 14   the model answers                     the code reads it, and an
                                                   answer that broke a rule is
                                                   thrown away and asked again

Which gives this file two halves that are deliberately unlike each other.
`render` puts the rules in the prompt, because a rule the model never sees is
a rule it can only break by luck. `scan` and `InvariantWatcher` read the
answer back, because a rule that lives *only* in a prompt is a request. The
two are not alternatives, for exactly the reason `phases.py` gives: prompts
are followed at whatever rate prompts are followed, and cannot be tested.

Like `memory.py`, `personality.py` and `phases.py`, this file knows nothing
about files, HTTP or conversations. It takes any object with `rules` and
`enabled` on it; `store.py` is what decides those live in JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import tokens
from facts import excerpt
from llm_client import LLMClient, LLMClientError

# --------------------------------------------------------------------------
# The four kinds

ARCHITECTURE = "architecture"
DECISION = "decision"
STACK = "stack"
BUSINESS = "business"

#: In the order they are grouped in the panel and numbered in the prompt.
#:
#: Four, and they are the four the exercise names - which turns out to be the
#: right cut for a reason the exercise does not give. They are not four topics,
#: they are four different **sources of authority**, and that is what a model
#: needs in order to weigh one: an architecture rule is a shape the code
#: already has, a decision is an argument somebody already had, a stack limit
#: is a boundary somebody else drew, and a business rule is not technical at
#: all and cannot be traded off against any of the other three.
KINDS = (ARCHITECTURE, DECISION, STACK, BUSINESS)

DEFAULT_KIND = ARCHITECTURE

#: What each kind is, sent to the frontend so the form and this file cannot
#: drift apart - the same trick `personality.FIELD_INFO`, `memory.LAYER_INFO`
#: and `phases.PHASE_INFO` play, and for the same reason.
#:
#: `prompt_label` is what the model sees and `label` is what the person sees.
KIND_INFO = {
    ARCHITECTURE: {
        "label": "Architecture",
        "prompt_label": "ARCHITECTURE",
        "hint": "The shape the system already has. Not what would be nice - "
                "what is already built this way and is not being rebuilt.",
        "placeholder": "Storage is JSON files on disk. No database, no ORM, "
                       "no migrations.",
    },
    DECISION: {
        "label": "Technical decision",
        "prompt_label": "DECISIONS ALREADY TAKEN",
        "hint": "An argument that already happened and is not being reopened. "
                "The one kind where saying why it was settled matters most.",
        "placeholder": "Authentication is a signed cookie, not JWT.",
    },
    STACK: {
        "label": "Stack limit",
        "prompt_label": "STACK",
        "hint": "What may be depended on, and what may not. A boundary drawn "
                "by somebody who is not in this conversation.",
        "placeholder": "Python and FastAPI on the server, vanilla JS on the "
                       "page. No frontend framework.",
    },
    BUSINESS: {
        "label": "Business rule",
        "prompt_label": "BUSINESS RULES",
        "hint": "A rule about the product rather than the code. The kind no "
                "amount of technical elegance is allowed to trade away.",
        "placeholder": "Nothing the user writes leaves their machine.",
    },
}

# --------------------------------------------------------------------------
# How big a rule, and how many

#: The whole set is sent with every single request, exactly as a personality
#: profile is, so its size is a standing cost rather than an occasional one.
#: Twelve short rules come to roughly 250-400 tokens on every message forever.
#:
#: The cap is the design rather than a backstop, and the argument is sharper
#: here than it was for a profile: a list of forty rules is not a stricter
#: agent, it is an agent whose rules contradict each other somewhere and
#: nobody knows where. Twelve is about as many as a person can hold in mind
#: well enough to notice when two of them disagree.
MAX_RULES = 12

#: One rule, one or two lines. A rule that needs a paragraph has stopped being
#: an invariant and become a design document, and there is a box for those -
#: `system_prompt`, three panels along.
MAX_TEXT_CHARS = 240

#: Why the rule exists. Shorter than the rule on purpose: it is a reminder,
#: not the minutes of the meeting.
MAX_BECAUSE_CHARS = 160

#: Literal words that, if they turn up, are worth a second look. See `scan` -
#: these are a signal and never a verdict, which is why there can be a few of
#: them without any of them having to be right.
MAX_MARKERS = 12
MAX_MARKER_CHARS = 40

MAX_ID_CHARS = 40
ID_WORDS = 5

WORD_SPLIT = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")


def slug(text: str) -> str:
    """An id derived from a rule's text - the same derivation `personality.slug`
    uses, and for the same reason: an id nobody has to invent."""
    parts = [p for p in WORD_SPLIT.split(str(text or "").lower()) if p]
    return "-".join(parts[:ID_WORDS])[:MAX_ID_CHARS].strip("-")


def unique_id(text: str, taken) -> str:
    """`slug(text)`, with a number on the end if that is already somebody's."""
    base = slug(text) or "rule"
    taken = set(taken or ())
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    return base


# --------------------------------------------------------------------------
# Cleaning what came off the wire


def clean_kind(value) -> str:
    """One of the four, or the default. A kind this file has never heard of is
    not stored: it would group nowhere and label nothing."""
    kind = str(value or "").strip().lower()
    return kind if kind in KIND_INFO else DEFAULT_KIND


def clean_text(value, limit: int = MAX_TEXT_CHARS) -> str:
    """A rule, squeezed onto one line and capped.

    One line rather than several, which is the one place this differs from
    `personality.clean_field`. A profile field is a list of independent
    preferences; a rule is a single statement, and a rule broken over three
    lines is either three rules or one rule with a subclause nobody will read.
    """
    text = " ".join(str(value or "").split())
    return text[:limit]


def clean_markers(value) -> list:
    """The marker list: lower-cased, deduplicated, short, and few.

    Accepts either a list or the comma-separated string the form sends, so the
    page can keep one plain text input rather than growing a tag widget.
    """
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        return []
    out = []
    for entry in raw:
        marker = " ".join(str(entry or "").split()).lower()[:MAX_MARKER_CHARS]
        if marker and marker not in out:
            out.append(marker)
    return out[:MAX_MARKERS]


def clean_rule(raw) -> dict:
    """One rule off the wire, in the shape `store.Rule` stores.

    Unknown keys are discarded for `personality.clean_fields`' reason: a rule
    is what this file says it is, and a field the renderer has never heard of
    would be stored forever, shown nowhere and sent to nobody.
    """
    if not isinstance(raw, dict):
        return {}
    text = clean_text(raw.get("text"))
    if not text:
        return {}
    return {
        "kind": clean_kind(raw.get("kind")),
        "text": text,
        "because": clean_text(raw.get("because"), MAX_BECAUSE_CHARS),
        "markers": clean_markers(raw.get("markers")),
        "enabled": bool(raw.get("enabled", True)),
    }


# --------------------------------------------------------------------------
# Reading a record


def _rules(record) -> list:
    return list(getattr(record, "rules", None) or ())


def is_on(record) -> bool:
    """Whether the set as a whole is switched on.

    Off is a first-class state, not a missing value - day 12's rule, and here
    it is how the day is demonstrated: ask with the rules off, switch them on,
    ask again.
    """
    return bool(record is not None and getattr(record, "enabled", False))


def active(record) -> list:
    """The rules in force, grouped in `KINDS` order.

    Grouped here rather than in the renderer because the numbering has to
    match what the panel shows and what `correction` quotes back: rule 2 must
    be the same rule in all three places, or a refusal cites the wrong line.
    """
    if not is_on(record):
        return []
    live = [r for r in _rules(record) if getattr(r, "enabled", True) and getattr(r, "text", "")]
    return sorted(live, key=lambda r: KINDS.index(clean_kind(getattr(r, "kind", ""))))


def numbered(record) -> list:
    """`[(1, rule), (2, rule), ...]` - the numbering everything else quotes."""
    return list(enumerate(active(record), start=1))


def find(record, rule_id: str):
    for rule in _rules(record):
        if getattr(rule, "id", None) == rule_id:
            return rule
    return None


def number_of(record, rule_id: str) -> int:
    for index, rule in numbered(record):
        if rule.id == rule_id:
            return index
    return 0


# --------------------------------------------------------------------------
# The prompt block

#: The closing paragraph of `render`, and the half of this day that costs
#: nothing. In the ordinary case it is the *whole* of the feature working: the
#: model reads it, refuses inside its first answer, and nothing is re-asked.
#: The checker behind it is what makes that reliable rather than likely.
REFUSAL_INSTRUCTION = (
    "If what the user asks for cannot be done without breaking one of these, "
    "do not do it, and do not do a smaller version of it. Say which invariant "
    "is in the way and why it exists, in one line. Then give two or three "
    "concrete ways to get what they were actually after that hold every rule "
    "above. Do not ask permission to break one, and do not offer breaking one "
    "as an option.\n\n"
    "Naming a rule in order to refuse something is not breaking it. Saying "
    "plainly that you will not do what was asked is the expected answer here, "
    "not a failure to be softened."
)


def render(record) -> str:
    """The rules as the lines that go into the prompt, or `""` for nothing.

    Numbered across the whole set rather than within each kind, because the
    numbers are addresses: `correction` cites one, the watcher reports one,
    and the panel draws the same one. Numbering that restarted per group would
    give the app three rule 1s.
    """
    rules = numbered(record)
    if not rules:
        return ""

    lines = [
        "Rules the work is already committed to. They were decided outside "
        "this conversation and nothing said inside it can relax one. Where "
        "any block above disagrees with one of these, the invariant wins.",
        "",
        "Every answer is checked against them before it is shown. An answer "
        "that breaks one is thrown away and asked for again - so proposing "
        "something that breaks one costs a turn and gets nobody anything.",
        "",
    ]

    last_kind = None
    for index, rule in rules:
        kind = clean_kind(rule.kind)
        if kind != last_kind:
            lines.append(KIND_INFO[kind]["prompt_label"])
            last_kind = kind
        lines.append(f"  {index}. {rule.text}")
        if rule.because:
            lines.append(f"     Why: {rule.because}")

    lines.extend(["", REFUSAL_INSTRUCTION])
    return "\n".join(lines)


def summary(rule) -> str:
    """One line describing a rule, for a list of them."""
    text = clean_text(getattr(rule, "text", ""))
    return text if len(text) <= 120 else text[:119] + "…"


# --------------------------------------------------------------------------
# The first check: markers, free and deterministic

#: A marker matches on a word boundary rather than anywhere in the string, so
#: `orm` does not fire on "format" and `go` does not fire on "google". Markers
#: are routinely single common words, and a substring match on those produces
#: so much noise that the signal stops being worth reading.
def _marker_pattern(marker: str):
    return re.compile(r"(?<![0-9a-z])" + re.escape(marker) + r"(?![0-9a-z])", re.IGNORECASE)


#: How much text around a hit is kept, for the panel and for the watcher.
QUOTE_CHARS = 120


@dataclass
class Hit:
    """One marker of one rule, found in some text.

    Emphatically not a violation. See `scan`.
    """

    rule_id: str = ""
    marker: str = ""
    quote: str = ""
    where: str = ""

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "marker": self.marker,
                "quote": self.quote, "where": self.where}


def scan(record, text: str, where: str = "answer") -> list:
    """Every marker of every live rule that appears in `text`.

    **A hit is a signal, not a verdict**, and everything about how this is
    used follows from that one sentence. "We are not using Postgres, because
    the app has to run from a clone" contains `postgres` and breaks no rule -
    it *obeys* one, out loud. A scan that ruled on its own would flag the best
    answer the agent can give.

    So it does two jobs, neither of which is ruling:

    1. it tells the watcher **where to look**, which is worth a great deal to
       a judge running at `reasoning_effort: none` - pointing at the clause
       makes a cheap call accurate in a way no amount of prompting does;
    2. it is the only part of this day that works **with no network at all**,
       which is what makes the day testable: the UI suite runs against a dummy
       key where every model call is a 401.
    """
    haystack = str(text or "")
    if not haystack.strip():
        return []
    hits = []
    for _, rule in numbered(record):
        for marker in rule.markers or ():
            match = _marker_pattern(marker).search(haystack)
            if match is None:
                continue
            start = max(0, match.start() - QUOTE_CHARS // 2)
            quote = " ".join(haystack[start:start + QUOTE_CHARS].split())
            hits.append(Hit(rule_id=rule.id, marker=marker, quote=quote, where=where))
            break  # one hit per rule is enough to say "look here"
    return hits


# --------------------------------------------------------------------------
# What a violation is

#: Where the violation came from, and the distinction the whole day turns on.
#: The same forbidden thing means two different things depending on who said
#: it first, and the two want opposite answers - one is a mistake to redo
#: quietly, the other is a refusal to deliver out loud.
ANSWER = "answer"
REQUEST = "request"
WHERES = (REQUEST, ANSWER)


@dataclass
class Violation:
    """One rule, broken once, somewhere."""

    rule_id: str = ""
    where: str = ANSWER
    quote: str = ""
    why: str = ""
    #: Whether the deterministic half saw this too. Carried so the panel and
    #: the debug entry can say which half caught it, and so a marker hit can
    #: stand on its own when the watcher never answered.
    marker: str = ""

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "where": self.where,
                "quote": self.quote, "why": self.why, "marker": self.marker}


# --------------------------------------------------------------------------
# The second check: the watcher

WATCH_TEMPERATURE = 0.0
WATCH_MAX_TOKENS = 400

#: The watcher is given the rules and the exchange, and nothing about what
#: happens next. It does not know that a violation costs a retry, because a
#: judge that knows the cost of its verdict is a judge with a reason to
#: soften it - `phases.ADVICE_SYSTEM_PROMPT` withholds `TRANSITIONS` for the
#: same reason.
#:
#: The third paragraph is the one that took two attempts to get right. Without
#: it, every correct refusal is reported as a violation: the answer that says
#: "I will not move this to Postgres, because rule 1" contains the whole of
#: rule 1's subject matter, and a judge reading for topic rather than for
#: proposal flags the single best answer the agent produced all day.
WATCH_SYSTEM_PROMPT = (
    "You are checking one exchange against a list of rules. You are not doing "
    "the work, you are not talking to the user, and you are not being asked "
    "whether the rules are good ones.\n\n"
    "You are given the numbered rules, what the user said, and what the "
    "assistant answered. Reply with JSON and nothing else:\n\n"
    '{"violations": [{"rule": 1, "where": "answer", "quote": "six or eight '
    'words, copied exactly", "why": "one short clause"}]}\n\n'
    "  rule   - the number of the rule that was broken. Only numbers you "
    "were given.\n"
    "  where  - \"request\" if the *user* asked for the thing the rule "
    "forbids; \"answer\" if the *assistant* proposed it unprompted. If the "
    "user asked for it and the assistant went along, that is \"request\".\n"
    "  quote  - the words that break it, copied from the text, not "
    "paraphrased.\n"
    "  why    - one short clause saying what the rule forbids and what was "
    "done anyway.\n\n"
    "Report nothing at all - `{\"violations\": []}` - unless a rule was "
    "actually broken. Three things in particular are NOT violations:\n"
    "  - naming the forbidden thing in order to rule it out. \"We will not "
    "use Postgres here\" obeys the rule about databases, out loud.\n"
    "  - listing it as a rejected alternative, or explaining why it was "
    "rejected.\n"
    "  - the user merely mentioning it, asking about it, or asking whether "
    "the rule could change. A question is not a request to break anything.\n\n"
    "Some markers may be pointed out to you: words from a rule that literally "
    "appear in the text. They are a place to look and nothing more. Most of "
    "them will be innocent, and a marker you check and find innocent should "
    "be reported as no violation at all."
)


@dataclass
class CheckRun:
    """One watcher request - what it found, what was done about it, the cost.

    The same shape as `phases.AdviceRun` and `memory.ProposalRun`, so the
    debug panel draws it with the code it already has.
    """

    violations: list = field(default_factory=list)
    hits: list = field(default_factory=list)
    #: Whether the answer this run judged was thrown away and asked for again.
    #: Set by the caller, as `AdviceRun.retried` is.
    retried: bool = False
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
    def rejected(self) -> bool:
        """Whether the answer this run read should be thrown away and re-asked.

        Any violation at all, which is where this differs from
        `AdviceRun.rejected` and the difference is the point. A stage that is
        merely one confirmation away is the ordinary end of a stage, so day 13
        turns it into an offer. There is no ordinary end of an invariant and
        nothing here is one confirmation away from being allowed: a rule was
        broken or it was not.
        """
        return bool(self.violations)

    @property
    def by_request(self) -> list:
        return [v for v in self.violations if v.where == REQUEST]

    def describe(self) -> str:
        if self.error and not self.violations:
            return "failed"
        if not self.violations:
            return f"clear - {len(self.hits)} marker{'' if len(self.hits) == 1 else 's'} checked" \
                if self.hits else "clear"
        count = len(self.violations)
        word = "invariant" if count == 1 else "invariants"
        if self.retried:
            return f"answer re-asked - broke {count} {word}"
        if self.by_request:
            return f"request breaks {count} {word}"
        return f"{count} {word} broken"

    def to_dict(self) -> dict:
        return {
            "violations": [v.to_dict() for v in self.violations],
            "hits": [h.to_dict() for h in self.hits],
            "retried": self.retried,
            "error": self.error,
            "describe": self.describe(),
        }


def parse_verdict(payload: dict, record) -> tuple[list, str | None]:
    """`{"violations": [...]}` out of a completion.

    Forgiving about shape in the one direction `phases.parse_advice` is, and
    unforgiving about content: a rule number that was never sent is dropped
    rather than guessed at, and a `where` that is neither of the two is
    dropped rather than defaulted. The watcher is reporting on a list it was
    shown - a number it invented is not an observation, it is a token that
    scored well.
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return [], "the watcher returned no choices"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return [], "the watcher returned nothing"

    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content

    try:
        data = json.loads(content)
    except ValueError:
        return [], "the watcher did not return JSON"
    if not isinstance(data, dict):
        return [], "the watcher did not return an object"

    by_number = {index: rule for index, rule in numbered(record)}
    raw = data.get("violations")
    out = []
    seen = set()
    for entry in raw if isinstance(raw, list) else ():
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("rule"))
        except (TypeError, ValueError):
            continue
        rule = by_number.get(number)
        if rule is None or rule.id in seen:
            continue
        where = str(entry.get("where") or "").strip().lower()
        seen.add(rule.id)
        out.append(
            Violation(
                rule_id=rule.id,
                where=where if where in WHERES else ANSWER,
                quote=clean_text(entry.get("quote"), QUOTE_CHARS),
                why=clean_text(entry.get("why"), 120),
            )
        )
    return out, None


class InvariantWatcher:
    """Reads one turn and says which rules it broke.

    Built like `phases.PhaseAdvisor` - client handed in, prompt a constant
    size whatever the conversation has grown to, answer parsed rather than
    read - and it differs from it in what happens to a bad verdict. Day 13's
    watcher reports a stage and `check` may well refuse to act on it, because
    the machine knows things the model does not. There is no such machine
    here: a rule is a sentence a person wrote, and nothing in code can tell
    whether an answer honoured it. So this one's verdict *is* the verdict,
    which is exactly why the prompt spends most of its length on what does not
    count.
    """

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, record, message: str, answer: str, hits: list) -> dict:
        rules = []
        for index, rule in numbered(record):
            line = f"{index}. [{KIND_INFO[clean_kind(rule.kind)]['label']}] {rule.text}"
            if rule.because:
                line += f"\n   Why it exists: {rule.because}"
            rules.append(line)

        parts = [
            "The rules:\n\n" + "\n".join(rules),
            "The user said:\n\n" + excerpt(message),
            "The assistant answered:\n\n" + excerpt(answer),
        ]
        if hits:
            noted = "\n".join(
                f"- rule {number_of(record, hit.rule_id)} ({hit.marker}) in the "
                f"{hit.where}: “{hit.quote}”"
                for hit in hits
            )
            parts.append(
                "Words from these rules literally appear in the text. Check "
                "each one and say so if it turns out to be innocent:\n\n" + noted
            )
        parts.append("Reply with the JSON now, and nothing else.")

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": WATCH_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": WATCH_TEMPERATURE,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "max_tokens": WATCH_MAX_TOKENS,
        }

    def check(self, record, message: str, answer: str) -> CheckRun:
        """One request: a turn in, a list of broken rules out. Never raises.

        The two halves meet here, and the way they meet is the one place this
        day chooses **fail-closed** where day 13 chose fail-open. A failed
        watcher leaves behind whatever the markers saw, as violations in their
        own right. That is not symmetrical with `PhaseAdvisor`, and should not
        be: a state machine that stands still is a defensible state a person
        can fix with one click, while a rule that stops being enforced the
        moment a call fails is not a rule, it is a note.

        The cost of being wrong that way is one wasted retry on an answer that
        mentioned a forbidden word innocently. The cost of being wrong the
        other way is the day not working.
        """
        if not numbered(record):
            return CheckRun()
        hits = scan(record, message, REQUEST) + scan(record, answer, ANSWER)

        request = self.build_request(record, message, answer, hits)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return CheckRun(
                violations=self._from_hits(hits),
                hits=hits,
                error=f"Error: {exc}",
                request=request,
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        violations, error = parse_verdict(result.response, record)
        if error:
            violations = self._from_hits(hits)
        else:
            violations = self._merge(violations, hits)
        return CheckRun(
            violations=violations,
            hits=hits,
            error=error,
            request=result.request,
            response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )

    @staticmethod
    def _from_hits(hits: list) -> list:
        """Marker hits as violations, for when the watcher never answered."""
        out, seen = [], set()
        for hit in hits:
            if hit.rule_id in seen:
                continue
            seen.add(hit.rule_id)
            out.append(
                Violation(
                    rule_id=hit.rule_id,
                    where=hit.where,
                    quote=hit.quote,
                    why=f"“{hit.marker}” appears in the {hit.where}",
                    marker=hit.marker,
                )
            )
        return out

    @staticmethod
    def _merge(violations: list, hits: list) -> list:
        """The watcher's verdict, noting which ones the markers saw as well.

        The watcher's list is the list. A marker it looked at and did not
        report is a marker it **cleared**, and adding it back would undo the
        one thing the watcher is for.
        """
        by_rule = {hit.rule_id: hit.marker for hit in hits}
        for violation in violations:
            violation.marker = by_rule.get(violation.rule_id, "")
        return violations


# --------------------------------------------------------------------------
# What to tell the model when it broke one


def _cite(record, violation) -> str:
    rule = find(record, violation.rule_id)
    if rule is None:
        return ""
    number = number_of(record, violation.rule_id)
    line = f"{number}. “{rule.text}”"
    if rule.because:
        line += f"\n   Why it exists: {rule.because}"
    return line


def correction(record, violations: list) -> str:
    """What to tell the model when its answer broke a rule it was given.

    Modelled on `phases.correction` down to its three parts - what the last
    answer did, why that is refused, what to do instead - and split in two by
    `where`, because the same broken rule wants opposite answers depending on
    who reached for it first.

    When the *agent* volunteered it, the fix is invisible: answer again,
    properly, and never mention that there was a first attempt. When the
    *user* asked for it, the fix is the opposite of invisible - the whole
    point is to say no where they can see it, and to hand them something else.
    So the request case leads when both are present: an answer that quietly
    fixed its own slip while ignoring what the user actually asked for would
    be the worst of the two.
    """
    if not violations:
        return ""

    by_request = [v for v in violations if v.where == REQUEST]
    by_answer = [v for v in violations if v.where == ANSWER]

    lines = ["--- Your last answer was rejected before it was shown ---", ""]

    if by_request:
        cited = "\n".join(_cite(record, v) for v in by_request if _cite(record, v))
        lines += [
            "What the user asked for cannot be done without breaking an "
            "invariant they set themselves:",
            "",
            cited,
            "",
            "Do not do it, and do not do a smaller version of it.",
        ]

    if by_answer:
        cited = "\n".join(_cite(record, v) for v in by_answer if _cite(record, v))
        quoted = "; ".join(f"“{v.quote}”" for v in by_answer if v.quote)
        lines += [
            ("Your answer also broke an invariant:" if by_request
             else "It broke an invariant the user set:"),
            "",
            cited,
            "",
            (f"You proposed: {quoted}" if quoted else ""),
        ]

    lines += [
        "",
        "That decision was made by code and is not open to discussion. The "
        "user has not seen the rejected answer, so do not refer to it, do not "
        "apologise for it and do not offer it again in a smaller form.",
        "",
    ]

    if by_request:
        lines += [
            "Answer the user's message again. Say in one line which invariant "
            "is in the way and why it exists. Then give two or three concrete "
            "ways to get what they were actually after that hold every "
            "invariant. Do not ask permission to break it, and do not present "
            "breaking it as one of the options.",
        ]
    else:
        lines += [
            "Answer the user's message again, doing the same job inside the "
            "rules.",
        ]

    return "\n".join(line for line in lines if line is not None)


# --------------------------------------------------------------------------
# What the app ships with

#: Two, written to disk the first time it runs and never again - deleting
#: either is allowed to stick, exactly as `personality.SEED_PROFILES` is.
#:
#: Two rather than none because an empty panel does not teach the shape, and
#: two rather than six because these are meant to be *read and deleted*: they
#: describe this app, so they are true enough to demonstrate the day with
#: while nobody could mistake them for rules about their own project. Both
#: carry a `because`, because that field is the one people skip and the one
#: that makes a refusal land.
#:
#: And the set they land in is switched **off**, which is day 12's rule about
#: seeded profiles and a stronger version of the same argument. There, an
#: unconfigured assistant was a sensible thing to be and "before" was half the
#: demonstration. Here there is a second reason that is not about
#: demonstrating anything: these two rules are true of *this* app, and an app
#: that enforced them on first run would be holding somebody's unrelated
#: project to the architecture of the thing they had just cloned. So they are
#: written down where they can be read, and nothing is in force until somebody
#: says so.
SEED_RULES = (
    {
        "kind": ARCHITECTURE,
        "text": "Storage is JSON files on disk. No database, no ORM, no migrations.",
        "because": "the app has to run from a clone with no services to start first",
        "markers": ["postgres", "postgresql", "mysql", "sqlite", "mongodb",
                    "redis", "orm", "sqlalchemy", "alembic"],
    },
    {
        "kind": STACK,
        "text": "Python and FastAPI on the server, vanilla JS on the page. "
                "No frontend framework and no build step.",
        "because": "the page is one file you can open and read; a toolchain would end that",
        "markers": ["react", "vue", "svelte", "angular", "webpack",
                    "vite", "next.js", "typescript", "npm run build"],
    },
)
