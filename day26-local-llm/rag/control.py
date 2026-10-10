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

Day 23 adds a third mode - the same candidates, cut by a judge model and a
threshold instead of at k - and a second kind of question: one the documents
**cannot** answer (`answerable: false`). For those the keyword line is a list
of ways to say "the documents do not cover this", and the number that
matters is how many excerpts went up with the question - a filter that
works sends none. Two more measures follow from the stored scores:

    precision   share of the excerpts sent that lie in an expected place
    sweep       every threshold 0..10 replayed over the run's stored scores:
                what each would have sent, without asking anything again
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .evaluation import Expected, matches, slug
from .index_store import IndexStore
from .rerank import MAX_SCORE

FULL, PARTIAL, MISS = "full", "partial", "miss"
MODES = ("plain", "rag", "rerank")


@dataclass
class ControlQuestion:
    id: str
    question: str
    expected: str = ""
    keywords: list[list[str]] = field(default_factory=list)   # lines of alternatives
    sources: list[Expected] = field(default_factory=list)
    # Day 23: False for a question the documents do not answer. Its keyword
    # line is a way of saying so, and it has no sources to be found in.
    answerable: bool = True

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
                                   keywords=keyword_lines(item.get("keywords")), sources=sources,
                                   answerable=item.get("answerable", True) is not False))
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


def expected_ranges(store: IndexStore, sources: list[Expected]) -> tuple[list[tuple], list[Expected]]:
    """The places an answer lives, as (source, start, end) - and the ones not in the corpus."""
    targets, missing = [], []
    for e in sources:
        found = store.section_range(e.source, e.section)
        (targets.append((e.source, *found)) if found else missing.append(e))
    return targets, missing


def score_sources(store: IndexStore, sources: list[Expected], rag: dict) -> dict:
    """Did the excerpts - and the ones the answer cited - come from where the answer lives?

    `rag` is `Retrieval.report(answer)`. Places are compared by character
    range, exactly as the retrieval check compares them.
    """
    if not sources:
        return {"checked": False, "reason": "no expected source given"}
    targets, missing = expected_ranges(store, sources)
    if not targets:
        return {"checked": False, "reason": "expected place is not in the corpus: " +
                "; ".join(f"{e.source} › {e.section}" if e.section else e.source for e in missing)}
    right = [h["n"] for h in rag["hits"] if any(matches(h, *t) for t in targets)]
    cited_right = [n for n in rag["cited"] if n in right]
    return {"checked": True, "retrieved": bool(right), "right": right, "first_rank": min(right) if right else None,
            "cited": bool(cited_right), "cited_right": cited_right, "cited_any": bool(rag["cited"]),
            "sent": len(rag["hits"]),
            "precision": round(len(right) / len(rag["hits"]), 3) if rag["hits"] else None}


def mark_expected(store: IndexStore, sources: list[Expected], rerank: dict) -> None:
    """Day 23: flag every judged candidate that lies in an expected place, for the sweep."""
    targets, _ = expected_ranges(store, sources) if sources else ([], [])
    rerank["expected_checked"] = bool(targets)
    for c in rerank["scored"]:
        c["expected"] = any(matches(c, *t) for t in targets)


def mean(values: list, digits: int = 3):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None


def summarise(rows: list[dict]) -> dict:
    """The comparison table: one column per mode, out of the rows that ran.

    Answerable and off-topic questions are counted apart - "said the right
    thing" and "said there is nothing to say" are different successes, and a
    mean over both would hide which one moved.
    """
    out = {}
    for mode in MODES:
        done = [(r, r[mode]) for r in rows if r.get(mode) and not r[mode].get("error")]
        on = [d for r, d in done if r.get("answerable", True)]
        off = [d for r, d in done if not r.get("answerable", True)]
        scored = [d["keywords"] for d in on if (d.get("keywords") or {}).get("coverage") is not None]
        usage = [d.get("usage") or {} for _, d in done]
        judge = [((d.get("rag") or {}).get("rerank") or {}) for _, d in done]
        column = {
            "answered": len(done),
            "answerable": len(on),
            "coverage": mean([s["coverage"] for s in scored]),
            FULL: sum(1 for s in scored if s["verdict"] == FULL),
            PARTIAL: sum(1 for s in scored if s["verdict"] == PARTIAL),
            MISS: sum(1 for s in scored if s["verdict"] == MISS),
            "offtopic": len(off),
            "refused": sum(1 for d in off if (d.get("keywords") or {}).get("verdict") == FULL),
            "prompt_tokens": round(mean([u.get("prompt_tokens") or 0 for u in usage], 0) or 0),
            "completion_tokens": round(mean([u.get("completion_tokens") or 0 for u in usage], 0) or 0),
            # The judge's request is part of what a reranked answer cost.
            "rerank_tokens": round(mean([(j.get("usage") or {}).get("total_tokens") or 0 for j in judge], 0) or 0)
            if mode == "rerank" else None,
            "total_tokens": round(mean([(u.get("total_tokens") or 0) + ((j.get("usage") or {}).get("total_tokens") or 0)
                                        for u, j in zip(usage, judge)], 0) or 0),
            "seconds": mean([((d.get("elapsed_ms") or 0) + (j.get("elapsed_ms") or 0)) / 1000
                             for (_, d), j in zip(done, judge)], 1),
        }
        if mode != "plain":
            checked = [d["sources"] for d in on if (d.get("sources") or {}).get("checked")]
            column.update({
                "sources_checked": len(checked),
                "retrieved": sum(1 for s in checked if s["retrieved"]),
                "cited": sum(1 for s in checked if s["cited"]),
                "cited_any": sum(1 for d in on if d.get("rag", {}).get("cited")),
                "precision": mean([s.get("precision") for s in checked]),
                "excerpts": mean([len(d["rag"]["hits"]) for d in on], 1),
                "excerpts_offtopic": mean([len(d["rag"]["hits"]) for d in off], 1),
                "emptied": sum(1 for d in off if not d["rag"]["hits"]),
                "words": round(mean([d["rag"]["words"] for _, d in done], 0) or 0),
                "candidates": mean([j.get("candidates") for j in judge], 1) if mode == "rerank" else None,
            })
        out[mode] = column
    return out


def cut(scored: list[dict], threshold: float | None, k: int) -> list[dict]:
    """What `rerank.keep` would send at this threshold; None means day 22's cut, by cosine."""
    if threshold is None:
        return sorted(scored, key=lambda c: c["n"])[:k]
    passed = [c for c in scored if c.get("llm") is not None and c["llm"] >= threshold]
    return sorted(passed, key=lambda c: (-c["llm"], c["n"]))[:k]


def sweep(rows: list[dict], candidates: int | None = None, k: int | None = None) -> dict | None:
    """Every threshold 0..10, replayed over the judge's stored scores - no request made.

    It answers *what would have been sent*: how many excerpts, whether the
    expected place survived the cut, how much of what was sent was from it,
    and how many off-topic questions were left with nothing. What the model
    would have *said* is not in it - answers exist only for the settings the
    run used.

    `candidates` and `k` replay the two other knobs: only the first
    `candidates` by cosine are looked at (no more than the run judged), and at
    most `k` are sent. Left out, they are what the run used.
    """
    judged = [(r, r["rerank"]["rag"]["rerank"]) for r in rows
              if not (r.get("rerank") or {}).get("error") and ((r.get("rerank") or {}).get("rag") or {}).get("rerank")]
    if not judged:
        return None
    judged_n = max(len(rr["scored"]) for _, rr in judged)
    n = max(1, min(int(candidates), judged_n)) if candidates else judged_n
    run_k = {rr["k"] for _, rr in judged}
    run_k = run_k.pop() if len(run_k) == 1 else None

    def line(threshold):
        sent, found, checked, precision, emptied, offtopic = [], 0, 0, [], 0, 0
        for r, rr in judged:
            kept = cut([c for c in rr["scored"] if c["n"] <= n], threshold, k or rr["k"])
            if not r.get("answerable", True):
                offtopic += 1
                emptied += not kept
                continue
            sent.append(len(kept))
            if rr.get("expected_checked"):
                checked += 1
                right = [c for c in kept if c.get("expected")]
                found += bool(right)
                if kept:
                    precision.append(len(right) / len(kept))
        return {"threshold": threshold, "sent": mean(sent, 1), "found": found, "checked": checked,
                "precision": mean(precision), "emptied": emptied, "offtopic": offtopic}

    return {"cosine": line(None), "thresholds": [line(t) for t in range(0, MAX_SCORE + 1)],
            "used": sorted({rr["threshold"] for _, rr in judged}),
            "candidates": n, "k": k or run_k, "max_candidates": judged_n,
            "as_run": n == judged_n and (not k or k == run_k)}
