"""The three layers an agent remembers in, and the rules for reading them.

Day 10 gave this app three *strategies* - `window`, `facts`, `summary` - and
they all answer the same question: how do you fit one conversation into one
request. That question is about **size**. This module is about a different
one, and the two are orthogonal:

    how long does a thing stay true, and who is allowed to see it?

The answer is three layers, and they are not three kinds of data. They are
three answers to "when should this be forgotten":

    short     scope: this branch of this chat
              lives:  until the chat ends
              writer: the agent, automatically

    working   scope: one task, across as many chats as it takes
              lives:  until the task is closed
              writer: a person, on purpose

    long      scope: everything, forever
              lives:  until someone deletes it
              writer: a person, on purpose

**The short layer is day 10, entire.** That is worth saying plainly, because
it is what keeps this day from being a rewrite. A fact block from `facts.py`
outlives the window but dies with the conversation - by the test above that
is short-term memory, and the three strategies are three ways of building
it. Nothing in `strategies.py`, `facts.py` or `compaction.py` changes here;
this module adds the two layers above them.

**The writer column is the point.** The requirement is that what goes where
is chosen *explicitly*, and the honest way to do that is that a person puts
it there - in this app, by typing `/task` or `/remember` in the message box
they were already typing in. The agent still reads every turn and still says
what it thinks is worth keeping (`MemoryProposer`), but a proposal is not a
write: it sits there until somebody accepts it. An agent that files things
by itself is a fourth kind of `facts.py`, not a memory model.

**And the person writing is not asked to invent a key.** Keys survive
because filing the same thing twice has to correct it rather than leave the
prompt holding two lines that disagree - but that is an identity, not a
label, and `slug()` derives it from what was written. The one thing a key
used to decide that a person could not - whether a memory rides on every
request - is now a flag you can see and click.

**Reading is the hard half, not writing.** Three layers that are all sent in
full on every request are one layer with three headings, and they would be
indistinguishable in the answers - which is exactly what the day asks you to
check. So the long layer is filtered against the question being asked
(`recall`). But filtering by relevance alone has a hole big enough to lose
the app in:

    question:  "what would be a better name for this function?"
    in memory: constraint.language = answer in Russian
    overlap:   none. The fact stays home. The agent answers in English.

The filter did its job and the memory broke anyway, because facts come in
two sorts. `constraint.*` and `preference.*` are rules about the *shape* of
any answer - there are few of them and they apply to every question ever
asked, so scoring them for relevance is a category error. Everything else -
`decision.*`, `profile.*`, `knowledge.*` - is subject matter, there can be a
lot of it, and only some of it bears on what was just asked. Hence
`ALWAYS_PREFIXES`, and hence the two-track `recall` below.

As with `facts.py` and `compaction.py`, nothing here knows about files, HTTP
or conversations. `recall` and `render` take any object with `key`, `value`,
`pinned` and `created_at`; `store.py` is what decides those live in JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import tokens
from facts import clean_key, clean_value, excerpt
from llm_client import LLMClient, LLMClientError

# --------------------------------------------------------------------------
# The layers

#: Day 10's three strategies, collectively. Named here so that the rest of
#: the app can talk about "the short layer" without caring which of them is
#: currently building it.
SHORT = "short"
WORKING = "working"
LONG = "long"

#: The two a person writes to. The short layer has no write API on purpose -
#: the way you put something in it is to say it in the chat.
WRITABLE = (WORKING, LONG)
LAYERS = (SHORT, WORKING, LONG)

#: What each layer is, sent to the frontend so the panel and this file cannot
#: drift apart - the same trick `strategies.STRATEGY_INFO` plays, and for the
#: same reason. `writer` is the column that makes the day's requirement
#: visible on screen: two of these say "you", and that is what "you chose
#: explicitly what goes where" looks like in a UI.
LAYER_INFO = {
    SHORT: {
        "label": "Short-term",
        "scope": "This branch of this chat",
        "lifetime": "Until the chat ends",
        "writer": "The agent, automatically",
        "hint": "The window, and whatever stands in for what fell out of it - "
                "day 10's strategy, whichever one is selected. Nothing is filed "
                "here by hand; you put something in it by saying it.",
    },
    WORKING: {
        "label": "Working",
        "scope": "One task, across every chat about it",
        "lifetime": "Until the task is closed",
        "writer": "You, with /task",
        "hint": "What this particular piece of work has established. It follows "
                "the task rather than the conversation, which is what lets you "
                "open a second chat about the same thing and carry on.",
    },
    LONG: {
        "label": "Long-term",
        "scope": "Everything, always",
        "lifetime": "Until you delete it",
        "writer": "You, with /remember",
        "hint": "What stays true after the task is done - who you are, how you "
                "want to be answered, decisions you keep building on.",
    },
}

#: Key prefixes that mean "this is a rule about how to answer, not subject
#: matter" - so the question it is relevant to is all of them.
#:
#: This used to be checked inside `recall`, which meant the always-rule was
#: a property of how a key was *spelled*. That was fine while every key came
#: from the extractor, which is told to use these prefixes. It stopped being
#: fine when people started filing memories in their own words: nobody types
#: `constraint.language`, and a rule that only fires for keys the agent
#: happened to invent is not a rule.
#:
#: So it is applied once, at the moment something is filed, and what it sets
#: is `pinned`. There is now exactly one thing `recall` looks at, and a
#: person can set it with one click instead of by guessing a prefix.
ALWAYS_PREFIXES = ("constraint.", "preference.")


def always(key: str) -> bool:
    """Whether a key names a rule about answering rather than a subject."""
    return str(key or "").startswith(ALWAYS_PREFIXES)


#: A key derived from text keeps this many words. Long enough that two
#: different notes rarely collide, short enough that the key stays a name
#: rather than becoming a second copy of the value.
SLUG_WORDS = 6
SLUG_CHARS = 60


def slug(text: str) -> str:
    """A key for a memory nobody wants to name.

    Keys exist for one reason that survives the simplification: filing the
    same thing twice should correct it rather than leave the prompt holding
    two lines that disagree. That needs an identity, and identity is all a
    key ever was.

    What it should *not* be is something a person has to invent, remember,
    and keep consistent with whatever they invented last month. So it is
    derived: the first few words of the note, lowercased and joined. Two
    notes that begin alike overwrite each other, which is very nearly the
    definition of a correction; two that do not, coexist.
    """
    parts = [p for p in WORD_SPLIT.split(str(text or "").lower()) if p]
    return "-".join(parts[:SLUG_WORDS])[:SLUG_CHARS].strip("-")

#: What a layer sends when the question matches nothing in it: the most
#: recently filed few, rather than nothing at all.
#:
#: This exists because of a failure the stemmer above cannot fix. Keys are
#: English by convention - `decision.schema`, `knowledge.deploy` - and this
#: app is mostly used in Russian, so "напомни схему" scores zero against
#: `decision.schema` no matter how the words are cut. Scoring worked
#: correctly and the memory still did not arrive.
#:
#: Falling back to recency is not a relevance judgement and does not pretend
#: to be one. It is the same asymmetry `STEM_CHARS` is argued from: three
#: extra lines in a prompt cost a rounding error, and a layer that silently
#: sends nothing looks exactly like a layer that is broken. Three, so that
#: the fallback can never be mistaken for "send it all".
RECALL_FALLBACK = 3

#: How many *scored* items a layer contributes on top of the unconditional
#: ones. The working layer gets more because a task's memory is small and
#: uniformly relevant - one task is one subject - while the long layer is
#: everything the user has ever filed and is the one that actually needs
#: narrowing.
RECALL_LIMIT = {WORKING: 12, LONG: 6}

#: Recency only breaks ties. A fact filed today is marginally more likely to
#: matter than one filed in March, but not enough to outrank an actual word
#: match - if this number ever exceeds 1.0 it starts selecting on age alone.
RECENCY_WEIGHT = 0.5

#: Words carrying no topic. Short and bilingual on purpose: this is a tie-
#: breaker for word overlap, not linguistics, and a long stoplist in the
#: wrong language is worse than none.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those is are was were be
    been being do does did done of in on at to for from with without by as it
    its i you he she we they me my your our their not no yes can could should
    would will shall may might must have has had what which who whom how why
    when where all any some more most other into about over under again
    и в во не на он она оно они мы вы ты я что это эта этот эти тот та те как
    но или если то же бы ли за из от до по при для над под без через о об
    у же вот так уже еще ещё был была были быть есть нет да там тут где когда
    кто чем чём чтобы который которая которые всё все весь вся мой моя твой
    наш ваш их его её ее себя свой
    """.split()
)

#: Keys are dotted (`constraint.language`), so a key has to be broken on its
#: punctuation before its words can be compared with a question's.
WORD_SPLIT = re.compile(r"[^0-9a-zA-Zа-яёА-ЯЁ]+")

#: How much of a word is kept for comparison. This is a stemmer, and calling
#: it anything grander would oversell four characters - but it is the
#: difference between this working in Russian and not working at all.
#:
#: Matching whole words is fine in English, where "schema" appears as
#: "schema". Russian inflects: a memory that says `decision.schema = одна
#: таблица на чат` has to be found by "какая у нас схема таблиц", and
#: "таблиц" is not "таблица". Neither is a prefix of the other once you
#: reach "схема"/"схемы", so prefix matching does not save it either.
#: Truncating both sides to a stem does: таблиц|а -> табл, схем|ы -> схем.
#:
#: Four rather than five because Russian stems are short and the endings
#: start early; five keeps "схема" and "схемы" apart, which is exactly the
#: case this exists for. The cost is the occasional unrelated pair sharing
#: four letters, and that asymmetry is the right way round - a false match
#: puts one extra line in a prompt, a missed one loses the memory.
#:
#: What it deliberately does *not* do is cross the alphabet: "деплой" will
#: never find `knowledge.deploy`. Fixing that needs a dictionary, not a
#: heuristic, and pretending otherwise would be worse than the gap.
STEM_CHARS = 4


def words(text) -> set[str]:
    """The comparable stems in a string - lowercased, stopwords removed.

    Stems rather than words; see `STEM_CHARS` for why, and note that the
    stoplist is applied to the *whole* word before truncation, since a
    four-letter prefix is not something you can recognise as a stopword.

    One-character tokens go too. They are almost always punctuation fallout
    or a stray initial, and a single letter matching a single letter is not
    evidence of anything.
    """
    return {
        part[:STEM_CHARS]
        for part in WORD_SPLIT.split(str(text or "").lower())
        if len(part) > 1 and part not in STOPWORDS
    }


def score(item, asked: set[str]) -> float:
    """How much this item looks like it is about the question.

    Word overlap, counted over the key and the value together, because both
    carry topic: `decision.schema = one table per chat` should be found by
    "schema" and by "table" alike.

    Deliberately not normalised by length. A long value that matches three
    of the question's words is more likely to be wanted than a short one
    that matches one, and dividing by size would say the opposite.
    """
    if not asked:
        return 0.0
    mine = words(item.key) | words(item.value)
    return float(len(mine & asked))


def recall(items, question: str, limit: int) -> list:
    """The items from one layer that this request should carry.

    Two tracks, and the split between them is the whole design:

    * **unconditional** - anything `pinned`. These go every time, are not
      counted against `limit`, and are the reason the agent does not forget
      what language to answer in the moment you ask it something off-topic.
      What sets that flag is a click, or `always()` at filing time.

    * **scored** - everything else, ranked by word overlap with the question,
      cut at `limit`, and *required to score above zero*. That last condition
      matters more than the ranking: without it "top 6" means six arbitrary
      items whenever nothing matches, and the layer would leak unrelated
      facts into every request while looking like it was being selective.

    When nothing scores at all, `RECALL_FALLBACK` recently-filed items go
    instead of an empty block - see that constant for the failure it exists
    to prevent, which is a real one and not a hypothetical.

    The result comes back in stored order rather than in score order. Two
    reasons: a block whose lines rearrange themselves every turn is noise to
    read and noise to diff in the debug panel, and a prompt prefix that is
    byte-identical between turns is one the provider can cache.
    """
    items = list(items or [])
    asked = words(question)

    keep = set()
    scored = []
    for index, item in enumerate(items):
        if item.pinned:
            keep.add(index)
            continue
        value = score(item, asked)
        if value > 0:
            # Later-filed items win ties; see RECENCY_WEIGHT.
            recency = RECENCY_WEIGHT * (index + 1) / len(items)
            scored.append((value + recency, index))

    if scored:
        scored.sort(key=lambda pair: pair[0], reverse=True)
        keep.update(index for _, index in scored[: max(0, limit)])
    else:
        # Nothing matched. Take the most recently filed of what is left,
        # newest first, capped by both the fallback and the caller's limit.
        rest = [index for index in range(len(items)) if index not in keep]
        keep.update(rest[-min(RECALL_FALLBACK, max(0, limit)):])

    return [item for index, item in enumerate(items) if index in keep]


def render(items) -> str:
    """A layer as it goes into a request: one `key: value` per line.

    The same shape `facts.render` produces, and that is intentional - three
    blocks in one prompt that each invented their own formatting would read
    as three unrelated systems talking at the model.
    """
    return "\n".join(f"{item.key}: {item.value}" for item in items or [])


# --------------------------------------------------------------------------
# What the agent thinks is worth keeping (a proposal is not a write)

PROPOSAL_TEMPERATURE = 0.0
PROPOSAL_MAX_TOKENS = 400

#: At most this many proposals per turn, and at most this many waiting at
#: once. A panel with thirty suggestions in it is one nobody reads, and the
#: whole value of this feature is that somebody reads it.
MAX_PROPOSALS_PER_TURN = 3

PROPOSAL_SYSTEM_PROMPT = (
    "You watch a conversation and point out the few things worth filing in a "
    "long-lived memory. You never file anything yourself - a person decides.\n"
    "Reply with JSON only, in the form "
    '{"propose": [{"key": "...", "value": "...", "layer": "working|long", '
    '"why": "..."}]}.\n'
    "There are two layers to choose between, and choosing correctly is the "
    "whole job:\n"
    "- 'working': true for the task currently being worked on, and worthless "
    "once it is done. Decisions about this piece of work, its constraints, the "
    "shape of the thing being built.\n"
    "- 'long': still true after this task is finished and forgotten. Who the "
    "user is, how they want to be addressed and answered, tools and versions "
    "they always use, standing decisions.\n"
    "If a fact would be equally true a year from now on a different project, "
    "it is 'long'. Otherwise it is 'working'.\n"
    "Use lowercase dotted keys: goal, constraint.language, preference.style, "
    "decision.schema, profile.name, knowledge.deploy. A 'constraint.' or "
    "'preference.' key means a rule the answers must always obey, and is "
    "filed as always-relevant - use those two prefixes only for rules, never "
    "for subject matter. Values are short phrases, not sentences. 'why' is "
    "at most eight words.\n"
    "Propose nothing at all from a message that asks a question, makes small "
    "talk, or only restates what is already filed - an empty list is the "
    "normal answer and the expected one. Never guess, never infer a fact the "
    "user did not state, and never propose more than three at once."
)


@dataclass
class Proposal:
    """One thing the agent suggests filing, and where it suggests filing it.

    `accepted` and `dismissed` are not set here - they are what the person
    does to it later, and `store.py` keeps them. What this object knows is
    what was suggested and why.
    """

    key: str = ""
    value: str = ""
    layer: str = WORKING
    why: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "layer": self.layer, "why": self.why}


@dataclass
class ProposalRun:
    """One proposal request - what it suggested and what it cost.

    The same shape as `facts.FactUpdate` and `compaction.Compaction`, so the
    debug panel draws it with the code it already has. As there, `ok` does
    not mean "suggested something": most turns should suggest nothing, and a
    run that reported that as a failure would cry wolf constantly.
    """

    proposals: list = field(default_factory=list)
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

    def describe(self) -> str:
        if self.error:
            return "failed"
        if not self.proposals:
            return "nothing to file"
        return f"{len(self.proposals)} proposed"


def parse_proposals(payload: dict) -> tuple[list, str | None]:
    """`{"propose": [...]}` out of a completion - `(proposals, error)`.

    Forgiving in exactly one direction, as `facts.parse_patch` is: a model
    that answers with a bare list instead of the envelope is read as the
    list, because that is unambiguous and it is the mistake that happens.
    Anything that is not JSON is an error rather than a guess.

    Every field is cleaned rather than trusted - `clean_key` and
    `clean_value` are imported from `facts.py` rather than reimplemented
    precisely because a proposal can be promoted out of a day-10 fact block,
    and two normalisers that disagree would make the same fact two keys.
    """
    choices = (payload or {}).get("choices") or []
    if not choices:
        return [], "the proposer returned no choices"
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return [], "the proposer returned nothing"

    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[-1] if content.lower().startswith("json") else content

    try:
        data = json.loads(content)
    except ValueError:
        return [], "the proposer did not return JSON"

    raw = data.get("propose") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return [], "the proposer did not return a list"

    out = []
    for entry in raw[:MAX_PROPOSALS_PER_TURN]:
        if not isinstance(entry, dict):
            continue
        key = clean_key(entry.get("key"))
        value = clean_value(entry.get("value"))
        if not key or not value:
            continue
        layer = entry.get("layer")
        out.append(
            Proposal(
                key=key,
                value=value,
                # An unrecognised layer becomes `working`, the narrower of the
                # two. Guessing wide would quietly put a fact about today's
                # task into every conversation the user ever has again.
                layer=layer if layer in WRITABLE else WORKING,
                why=clean_value(entry.get("why"))[:80],
            )
        )
    return out, None


class MemoryProposer:
    """Reads one turn and says what it would file, without filing it.

    Built on the same three ideas as `facts.FactKeeper`, which is why it
    looks like it: the client is handed in, the prompt is a constant size
    whatever the conversation has grown to, and the answer is parsed rather
    than read. The differences are the two that matter for this day - it is
    told about the layers and has to choose between them, and its output goes
    to a person rather than to a file.
    """

    def __init__(self, client: LLMClient, model: str) -> None:
        self.client = client
        self.model = model

    def build_request(self, message: str, answer: str, filed: list) -> dict:
        """The proposal request - and, as with the extractor, note the size.

        The message, the answer it got, and the keys already on file. Not the
        transcript, and not the filed *values* either: the point of sending
        the keys is so the model does not propose something that is already
        there, and for that a list of names is enough.
        """
        parts = ["The user said:\n\n" + excerpt(message)]
        if answer:
            parts.append("You answered:\n\n" + excerpt(answer))
        if filed:
            parts.append(
                "Already on file, do not propose these again:\n\n"
                + ", ".join(sorted({item.key for item in filed}))
            )
        parts.append("Reply with the JSON now, and nothing else.")

        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": PROPOSAL_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(parts)},
            ],
            "temperature": PROPOSAL_TEMPERATURE,
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "max_tokens": PROPOSAL_MAX_TOKENS,
        }

    def propose(self, message: str, answer: str, filed: list) -> ProposalRun:
        """One request: a turn in, suggestions out. Never raises.

        A failed run costs the user nothing at all - they have their answer
        already, and this ran after it. It is reported rather than hidden
        because a proposer that silently stops working looks exactly like a
        conversation in which nothing worth filing was said.
        """
        request = self.build_request(message, answer, filed)
        try:
            result = self.client.complete(request)
        except LLMClientError as exc:
            return ProposalRun(
                error=f"Error: {exc}",
                request=request,
                url=exc.url,
                status_code=exc.status_code,
                request_headers=self.client.redacted_headers,
            )

        proposals, error = parse_proposals(result.response)
        return ProposalRun(
            proposals=proposals,
            error=error,
            request=result.request,
            response=result.response,
            usage=tokens.usage_from_response(result.response),
            elapsed_ms=result.elapsed_ms,
            url=result.url,
            status_code=result.status_code,
            request_headers=result.request_headers,
        )
