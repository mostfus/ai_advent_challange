"""Day 24: an answer that shows its evidence - sources, quotes, and "I don't know".

    TEMPLATE                      the excerpts and the question, a JSON reply asked for
    gate(retrieval)               nothing reached the threshold -> what the search did find, for the refusal
    render_gate(gate)             that, as text for the prompt
    UNKNOWN_TEMPLATE              the same JSON, with the answer forbidden
    parse(reply)                  the model's reply -> Grounded: status, answer, sources, quotes, clarification
    check(grounded, retrieval)    every quote looked up in the excerpt it names -> the grounding report
    with_correction(template, problems)   what the one retry is told
    report(retrieval, answer, grounding)  everything the chat stores under the answer

Day 22 asked for citations - `[n]` after a claim - and got them most of the
time. A citation says *which* excerpt; it does not say *what in it*, and
nothing checked that the excerpt said anything of the kind. So the answer
now comes back as JSON with three parts the brief asks for: the answer, the
excerpts it rests on, and **quotes** - fragments copied out of those
excerpts. The sources are the model's to name and this module's to resolve:
the model says `[2]`, the code turns it into the file, the section and the
chunk id, so a source cannot be misspelt.

**A quote is looked up, not trusted.** `locate` searches the excerpt for it -
ignoring case, runs of whitespace, typographic quotes and dashes, and the
markdown and comment marks (`*`, a backtick, `#`) a model drops when it
copies prose - and keeps where it was found, so the page can highlight it in
the chunk. A quote that is not there is the one hallucination this module
can catch for certain, and it is reported as such rather than shown as
evidence.

**"Answered" has to carry its evidence.** No source, no quote, a quote that
is not in its excerpt, an excerpt number that was never sent: each is a
`problem`, and the chat asks again once with the list (day 14's rule: one
retry, never two). What is still wrong after the retry is shown under the
answer, not hidden.

**"I don't know" is decided twice.** By code first: when the reranker kept
nothing - no candidate reached the threshold - the model is not asked the
question at all. It is told that the documents do not hold enough, shown
the titles of the nearest sections (not their text: there is nothing in
them to answer from), and asked to say it does not know and ask one
question back (`UNKNOWN_TEMPLATE`). And by the model: excerpts that passed
the threshold can still lack the answer, or the question can mean two
things, and then it returns `status: "unknown"` with a clarifying question.
In both cases sources and quotes may be empty - there is nothing to quote.

Like the rest of the package this module calls no model and imports nothing
from the app: the templates are text for the agent's knowledge block, and
everything else reads what came back.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .augment import Retrieval, cited, label

ANSWERED, UNKNOWN = "answered", "unknown"
NEAREST = 5              # sections named to the model when the gate trips

# Both templates go through `str.format(excerpts=..., question=...)` in the
# agent, so every literal brace in them is doubled.
SHAPE = (
    '{{"status": "answered" or "unknown",\n'
    ' "answer": "the answer in Markdown, in the language of the question, citing the excerpts inline as [n]",\n'
    ' "sources": [the numbers of the excerpts the answer rests on],\n'
    ' "quotes": [{{"n": <excerpt number>, "quote": "<a fragment copied from that excerpt>"}}],\n'
    ' "clarification": "one question back to the user - only when status is unknown, otherwise empty"}}'
)

TEMPLATE = (
    "--- Excerpts from the project's documents, found for this question ---\n"
    "{excerpts}\n"
    "--- End of excerpts ---\n\n"
    "Answer the question below from these excerpts only, and show your evidence. "
    "Reply with one JSON object and nothing else:\n\n"
    f"{SHAPE}\n\n"
    "Rules:\n"
    "- Every claim in the answer rests on a quote. A quote is copied character for character "
    "from the excerpt it names, in that excerpt's own language: never translated, paraphrased, "
    "shortened with an ellipsis or joined from two places. Keep each one short - a sentence, "
    "a line of code, a table row.\n"
    "- An answered question has at least one source and one quote, and every source has a quote.\n"
    "- If the excerpts do not hold the answer, or the question could mean several things, "
    'set "status" to "unknown": say plainly in "answer" that you do not know, leave "sources" '
    'and "quotes" empty, and put in "clarification" one question that would let you answer.\n'
    "- Do not fill gaps from general knowledge.\n\n"
    "Question: {question}"
)

UNKNOWN_TEMPLATE = (
    "--- What the project's documents hold for this question ---\n"
    "{excerpts}\n"
    "--- End ---\n\n"
    "Do not answer the question below: the documents do not hold enough for it, and an answer "
    "from general knowledge would be a guess presented as the project's. "
    "Reply with one JSON object and nothing else:\n\n"
    f"{SHAPE}\n\n"
    'with "status": "unknown", "sources": [] and "quotes": []. In "answer", say plainly, in the '
    "language of the question, that you do not know - the project's documents do not cover this. "
    'In "clarification", ask one question that would help: what exactly the user means, or which '
    "part of the project they are asking about. You may name one of the nearest sections above "
    "if it could be what they meant.\n\n"
    "Question: {question}"
)

CORRECTION = (
    "\n\nYour previous reply to this question was rejected:\n{problems}\n"
    "Reply again with the same JSON object, with these fixed."
)

# What `locate` ignores on both sides: typographic quotes and dashes are
# folded, these are dropped. `*` and a backtick are markdown a model leaves
# out when it copies prose; `#` starts every line of a Python comment.
FOLD = {"“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
        "‘": "'", "’": "'", "–": "-", "—": "-", "‐": "-", "−": "-",
        "…": "...", "ё": "е", " ": " "}
DROP = set("*`#")
ELLIPSIS = re.compile(r"\s*\.{3,}\s*")
EDGES = " \t\n\"'.,;:!?…"


@dataclass
class Grounded:
    """The model's reply, read. `error` says why it is not the JSON asked for."""

    status: str
    answer: str
    sources: list[int] = field(default_factory=list)
    quotes: list[dict] = field(default_factory=list)     # {"n": int, "quote": str}
    clarification: str = ""
    error: str | None = None

    @property
    def text(self) -> str:
        """What the chat shows and stores: the answer, then the question back, if any."""
        if self.status == UNKNOWN and self.clarification and self.clarification not in self.answer:
            return f"{self.answer}\n\n{self.clarification}".strip()
        return self.answer


# --------------------------------------------------------------- the gate

def gate(retrieval: Retrieval) -> dict | None:
    """When the reranker kept nothing: what it judged, for the refusal. None when it kept something.

    Only a reranked retrieval has a relevance to fall below - day 22's top k
    sends the nearest chunks whatever their cosine, and day 23 showed that
    the cosine does not separate an answerable question from an off-topic one.
    """
    rr = retrieval.rerank
    if rr is None or retrieval.hits:
        return None
    judged = [c for c in rr["scored"] if c.get("llm") is not None]
    # Named only when the judge saw something of the question in them: a 0
    # is "unrelated", and a list of unrelated titles is something a model
    # will dutifully suggest. One line per section, at its best score.
    nearest: list[dict] = []
    for c in sorted(judged, key=lambda c: (-c["llm"], c["n"])):
        if c["llm"] > 0 and len(nearest) < NEAREST and \
                not any((n["source"], n["section"]) == (c["source"], c["section"]) for n in nearest):
            nearest.append({"source": c["source"], "section": c["section"], "llm": c["llm"]})
    return {"best": max((c["llm"] for c in judged), default=None), "threshold": rr["threshold"],
            "candidates": rr["candidates"], "nearest": nearest}


def render_gate(g: dict) -> str:
    best = "nothing" if g["best"] is None else f"{g['best']:g}/10"
    lines = [f"The search read the {g['candidates']} nearest chunks of the index, and none was judged "
             f"relevant enough to answer from: the best scored {best}, and an answer needs "
             f"{g['threshold']}/10."]
    if g["nearest"]:
        lines.append("The nearest sections, by title only - on the subject at most, and judged not to "
                     "answer the question:")
        lines += [f"- {label(c)} ({c['llm']:g}/10)" for c in g["nearest"]]
    else:
        lines.append("None of them was even on the subject of the question.")
    return "\n".join(lines)


# --------------------------------------------------------------- reading the reply

def loads(text: str):
    try:
        return json.loads(text)
    except ValueError:
        pass
    inner = re.search(r"\{.*\}", text, re.S)
    if inner:
        try:
            return json.loads(inner.group(0))
        except ValueError:
            pass
    return None


def as_number(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        found = re.fullmatch(r"\s*\[?\s*(\d{1,2})\s*\]?\s*", value)
        return int(found.group(1)) if found else None
    if isinstance(value, dict):
        return as_number(value.get("n", value.get("id")))
    return None


def parse(reply: str) -> Grounded:
    """The reply as the JSON asked for - or, when it is not, as plain text with that said.

    Lenient about shape, since a model that bends it usually still means the
    same thing: `"[2]"` and `{"n": 2}` are both source 2, `"text"` will do for
    `"quote"`, a status of `"answer"` is "answered". What it cannot read it
    keeps as the answer, so nothing the model said is lost.
    """
    text = (reply or "").strip()
    data = loads(text)
    if not isinstance(data, dict):
        return Grounded(ANSWERED, text, error="The reply was not the JSON object asked for.")
    answer = str(data.get("answer") or "").strip()
    clarification = str(data.get("clarification") or "").strip()
    raw_status = str(data.get("status") or "").strip().lower().replace("'", "").replace(" ", "_")
    if raw_status in ("unknown", "dont_know", "do_not_know", "not_found", "insufficient"):
        status = UNKNOWN
    elif raw_status in ("answered", "answer", "known", "found", "ok"):
        status = ANSWERED
    else:
        status = ANSWERED if answer and not clarification else UNKNOWN
    sources = []
    for item in data.get("sources") or []:
        n = as_number(item)
        if n is not None and n not in sources:
            sources.append(n)
    quotes = []
    for item in data.get("quotes") or []:
        if not isinstance(item, dict):
            continue
        n = as_number(item.get("n", item.get("source")))
        quote = str(item.get("quote") or item.get("text") or "").strip()
        if n is not None and quote:
            quotes.append({"n": n, "quote": quote})
    return Grounded(status, answer, sources, quotes, clarification)


# --------------------------------------------------------------- finding a quote

def folded(text: str) -> tuple[str, list[int]]:
    """`text` as `locate` compares it, and for every character of that, where it came from."""
    out: list[str] = []
    where: list[int] = []
    space = True                         # leading whitespace goes
    for i, ch in enumerate(text):
        if ch in DROP:
            continue
        ch = FOLD.get(ch, ch)
        if ch.isspace():
            if not space:
                out.append(" ")
                where.append(i)
            space = True
            continue
        space = False
        for c in ch.lower():
            out.append(c)
            where.append(i)
    while out and out[-1] == " ":
        out.pop()
        where.pop()
    return "".join(out), where


def locate(quote: str, text: str) -> list[tuple[int, int]]:
    """Where `quote` is in `text`, as character ranges of `text` - [] when it is not there.

    One range per part: a quote the model shortened with `...` despite being
    asked not to is found if every part is, in order.
    """
    body, where = folded(text)
    q, _ = folded(quote)
    parts = [p.strip(EDGES) for p in ELLIPSIS.split(q)]
    parts = [p for p in parts if p]
    if not parts or not body:
        return []
    spans, at = [], 0
    for part in parts:
        i = body.find(part, at)
        if i < 0:
            return []
        end = i + len(part)
        spans.append((where[i], where[end - 1] + 1))
        at = end
    return spans


# --------------------------------------------------------------- checking the answer

def short(text: str, n: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def check(g: Grounded, retrieval: Retrieval, gate_info: dict | None = None) -> dict:
    """The reply against the excerpts it was given: what it rests on, and what is wrong with it.

    The sources are resolved by code: every excerpt the model named, quoted
    from (when the quote was found) or cited inline as `[n]`, in order of
    first mention - so a source the model forgot to list but quoted from is
    still listed, and one it listed but was never sent is not.
    """
    sent = {h["rank"]: h for h in retrieval.hits}
    problems: list[str] = []
    if g.error:
        problems.append(g.error)
    status = g.status
    if gate_info is not None and status != UNKNOWN:
        problems.append("No excerpt reached the relevance threshold, so the answer must be "
                        '"I do not know" with a clarifying question - but the reply answered.')
        status = UNKNOWN       # the gate is code's decision, not the model's

    quotes = []
    for q in g.quotes:
        hit = sent.get(q["n"])
        spans = locate(q["quote"], hit["text"]) if hit else []
        quotes.append({"n": q["n"], "quote": q["quote"], "found": bool(spans), "spans": spans,
                       **({"source": hit["source"], "section": hit["section"], "chunk_id": hit["chunk_id"]}
                          if hit else {})})
        if hit is None:
            problems.append(f"A quote names excerpt [{q['n']}], which was never sent: \"{short(q['quote'])}\"")
        elif not spans:
            problems.append(f"This quote is not in excerpt [{q['n']}] - copy it exactly, "
                            f"in the excerpt's language: \"{short(q['quote'])}\"")

    named = [n for n in g.sources if n in sent]
    for n in g.sources:
        if n not in sent:
            problems.append(f"Source [{n}] was never sent - only [1]-[{len(sent)}] were." if sent
                            else f"Source [{n}] was never sent - no excerpt was.")
    used: list[int] = []
    for n in named + [q["n"] for q in quotes if q["found"]] + cited(g.answer, retrieval):
        if n not in used:
            used.append(n)
    quoted = {q["n"] for q in quotes if q["found"]}

    if not g.answer:
        problems.append('The "answer" is empty.')
    if status == ANSWERED and not g.error:
        if not used:
            problems.append("No sources: an answered question names the excerpts it rests on.")
        if not quotes:
            problems.append("No quotes: every claim needs a fragment copied from its excerpt.")
        unquoted = [n for n in named if n not in quoted]
        if quotes and unquoted:
            problems.append("These sources have no quote found in them: " +
                            " ".join(f"[{n}]" for n in unquoted) + ".")
    if status == UNKNOWN and not g.clarification:
        problems.append('"I do not know" needs a clarifying question in "clarification".')

    return {
        "status": status,
        "answer": g.answer,
        "clarification": g.clarification if status == UNKNOWN else "",
        "sources": [{"n": n, "chunk_id": sent[n]["chunk_id"], "source": sent[n]["source"],
                     "section": sent[n]["section"], "score": sent[n]["score"], "llm": sent[n].get("llm"),
                     "quoted": n in quoted, "named": n in named}
                    for n in used],
        "quotes": quotes,
        "problems": problems,
        "gate": gate_info,
    }


def with_correction(template: str, problems: list[str]) -> str:
    """The template once more, with what was wrong appended after the question - braces escaped."""
    listed = "\n".join(f"- {p}" for p in problems).replace("{", "{{").replace("}", "}}")
    return template + CORRECTION.format(problems=listed)


def report(retrieval: Retrieval, answer: str | None, grounding: dict | None) -> dict:
    """Day 22's sources block, with every excerpt's full text and the grounding report.

    The full text is for the popup a quote opens - the chunk, with the quote
    highlighted where `locate` found it. Five excerpts of ~180 words ride on
    the stored message; the candidates the judge cut keep their preview only.
    """
    out = retrieval.report(answer)
    for item, hit in zip(out["hits"], retrieval.hits):
        item["text"] = hit["text"]
    if grounding is not None:
        out["grounding"] = grounding
    return out
