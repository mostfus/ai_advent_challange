"""Day 22: control questions - what an answer must say, and where it should come from.

Day 21's check asked *did retrieval find the place*. This one asks the next
question - *did the answer say the right thing* - and asks it twice, of the
same model, with and without the excerpts in front of it.

A control question carries three things:

    expected    what a correct answer says, in a sentence - for the person reading the table
    keywords    the facts it must contain, one line each; `a | b` on a line means either
    sources     where the answer lives (file + section, day 21's `Expected`)

and an answer is scored by code, no model involved:

    coverage    share of keyword lines the answer contains          (both modes)
    verdict     full = every line, partial = at least half, miss = less
    retrieved   an excerpt sent to the model lies in an expected place   (RAG only)
    cited       an excerpt the answer cites lies in an expected place   (RAG only)

**Why keywords and not a judge model.** A judge is a third answer that
nobody checks, priced per question, and it grades an answer that says the
right thing in other words as kindly as one that copied the source. A
keyword line is a claim a person wrote down before the run, checked the same
way every time, free - and when it is wrong the fix is to edit the line, which
is visible, rather than to argue with a prompt. The cost is paraphrase: so
each line is a stem (`многоязыч`, not `многоязычная`) and the alternatives
are on the same line, in both languages when the answer may come in either.

Matching ignores case, treats `ё` as `е`, and folds dashes and runs of
whitespace into one space, so `bge-m3`, `BGE m3` and `MCP-сервер` / `MCP сервер`
are one keyword each. Underscores are kept: `__` is how a tool name says
which server it belongs to, and that is worth being able to ask about.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evaluation import Expected, matches, slug
from .index_store import IndexStore

FULL, PARTIAL, MISS = "full", "partial", "miss"


@dataclass
class ControlQuestion:
    id: str
    question: str
    expected: str = ""
    keywords: list[list[str]] = field(default_factory=list)   # lines of alternatives
    sources: list[Expected] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def keyword_lines(raw) -> list[list[str]]:
    """`["bge-m3", "многоязыч | multilingual"]` or `[["bge-m3"], [...]]` -> lines of alternatives."""
    lines: list[list[str]] = []
    for item in raw or []:
        parts = item if isinstance(item, list) else str(item).split("|")
        alternatives = [str(p).strip() for p in parts if str(p).strip()]
        if alternatives:
            lines.append(alternatives)
    return lines


def clean_questions(raw: list[dict]) -> list[ControlQuestion]:
    out: list[ControlQuestion] = []
    taken: set[str] = set()
    for item in raw or []:
        text = str(item.get("question", "")).strip()
        if not text:
            continue
        qid = str(item.get("id") or "").strip()
        qid = qid if qid and qid not in taken else slug(text, taken)
        taken.add(qid)
        sources = [Expected(source=str(s.get("source", "")).strip(), section=str(s.get("section", "")).strip())
                   for s in item.get("sources") or [] if str(s.get("source", "")).strip()]
        out.append(ControlQuestion(id=qid, question=text, expected=str(item.get("expected", "")).strip(),
                                   keywords=keyword_lines(item.get("keywords")), sources=sources))
    return out


def load_questions(path: Path) -> list[ControlQuestion]:
    if not path.exists():
        return []
    return clean_questions(json.loads(path.read_text(encoding="utf-8")))


def save_questions(path: Path, questions: list[ControlQuestion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([q.as_dict() for q in questions], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def fold(text: str) -> str:
    return re.sub(r"[\s\-‐-―]+", " ", (text or "").lower().replace("ё", "е")).strip()


def score_keywords(answer: str, lines: list[list[str]]) -> dict:
    """Which keyword lines the answer contains, and what that comes to."""
    body = fold(answer)
    found = [next((alt for alt in line if fold(alt) and fold(alt) in body), None) for line in lines]
    hit = sum(1 for f in found if f is not None)
    coverage = round(hit / len(lines), 3) if lines else None
    if coverage is None:
        verdict = None
    elif coverage >= 1:
        verdict = FULL
    elif coverage >= 0.5:
        verdict = PARTIAL
    else:
        verdict = MISS
    return {"coverage": coverage, "verdict": verdict, "hit": hit, "of": len(lines),
            "lines": [{"alternatives": line, "found": f} for line, f in zip(lines, found)]}


def score_sources(store: IndexStore, sources: list[Expected], rag: dict) -> dict:
    """Did the excerpts - and the ones the answer cited - come from where the answer lives?

    `rag` is `Retrieval.report(answer)`. Places are compared by character
    range, exactly as the retrieval check compares them.
    """
    targets, missing = [], []
    for e in sources:
        found = store.section_range(e.source, e.section)
        (targets.append((e.source, *found)) if found else missing.append(e))
    if not sources:
        return {"checked": False, "reason": "no expected source given"}
    if not targets:
        return {"checked": False, "reason": "expected place is not in the corpus: " +
                "; ".join(f"{e.source} › {e.section}" if e.section else e.source for e in missing)}
    right = [h["n"] for h in rag["hits"] if any(matches(h, *t) for t in targets)]
    cited_right = [n for n in rag["cited"] if n in right]
    return {"checked": True, "retrieved": bool(right), "right": right, "first_rank": min(right) if right else None,
            "cited": bool(cited_right), "cited_right": cited_right, "cited_any": bool(rag["cited"])}


def summarise(rows: list[dict]) -> dict:
    """The comparison table: one column per mode, out of the rows that ran."""
    out = {}
    for mode in ("plain", "rag"):
        done = [r[mode] for r in rows if r.get(mode) and not r[mode].get("error")]
        scored = [d["keywords"] for d in done if d["keywords"]["coverage"] is not None]
        n = len(scored) or 1
        usage = [d.get("usage") or {} for d in done]
        column = {
            "answered": len(done),
            "coverage": round(sum(s["coverage"] for s in scored) / n, 3) if scored else None,
            FULL: sum(1 for s in scored if s["verdict"] == FULL),
            PARTIAL: sum(1 for s in scored if s["verdict"] == PARTIAL),
            MISS: sum(1 for s in scored if s["verdict"] == MISS),
            "prompt_tokens": round(sum(u.get("prompt_tokens") or 0 for u in usage) / (len(usage) or 1)),
            "completion_tokens": round(sum(u.get("completion_tokens") or 0 for u in usage) / (len(usage) or 1)),
            "seconds": round(sum((d.get("elapsed_ms") or 0) for d in done) / 1000 / (len(done) or 1), 1),
        }
        if mode == "rag":
            checked = [d["sources"] for d in done if d.get("sources", {}).get("checked")]
            column["sources_checked"] = len(checked)
            column["retrieved"] = sum(1 for s in checked if s["retrieved"])
            column["cited"] = sum(1 for s in checked if s["cited"])
            column["cited_any"] = sum(1 for d in done if d.get("rag", {}).get("cited"))
        out[mode] = column
    return out
