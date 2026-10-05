"""Checking retrieval: questions with a known answer, and where each index put it.

A question names where its answer lives - a file, and optionally a section
path in it (`"Day 20 › Routing: the name is the route"`) meaning that
section *and everything under it*. Each index is searched, and the rank of the
first chunk that is **inside** that place is what gets scored:

    hit@k      the answer was among the top k chunks
    MRR        mean of 1/rank of the first right chunk (0 when not in the top `depth`)
    words      words a model would have to read, top down, to reach it

"Inside" is decided by character range, not by comparing labels - which is
what makes the check fair to the fixed strategy, whose chunks start wherever
the count ran out and carry the label of wherever that was. A chunk counts
when at least half of it lies in the expected range, or when it covers at
least half of that range: it is mostly *about* the answer, or it mostly
*contains* it. A chunk that only brushes the section with a few words does
not count.

A question whose expected place no longer exists in the corpus (the file was
removed, the heading renamed) is reported as such and left out of the
averages rather than counted as a miss - it is the question that is wrong
then, not the index.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .embedders import Embedder
from .index_store import IndexStore

KS = (1, 3, 5)
DEPTH = 10


@dataclass
class Expected:
    source: str
    section: str = ""          # a section label; "" means anywhere in the file


@dataclass
class Question:
    id: str
    question: str
    expected: list[Expected] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9а-яё]+", "-", text.lower()).strip("-")[:40] or "q"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def clean_questions(raw: list[dict]) -> list[Question]:
    """Questions as sent by a UI or read from a file - trimmed, deduplicated ids."""
    out: list[Question] = []
    taken: set[str] = set()
    for item in raw or []:
        text = str(item.get("question", "")).strip()
        if not text:
            continue
        expected = [Expected(source=str(e.get("source", "")).strip(), section=str(e.get("section", "")).strip())
                    for e in item.get("expected") or [] if str(e.get("source", "")).strip()]
        qid = str(item.get("id") or "").strip()
        qid = qid if qid and qid not in taken else slug(text, taken)
        taken.add(qid)
        out.append(Question(id=qid, question=text, expected=expected, note=str(item.get("note", "")).strip()))
    return out


def load_questions(path: Path) -> list[Question]:
    if not path.exists():
        return []
    return clean_questions(json.loads(path.read_text(encoding="utf-8")))


def save_questions(path: Path, questions: list[Question]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([q.as_dict() for q in questions], ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def matches(chunk: dict, source: str, start: int, end: int) -> bool:
    if chunk["source"] != source:
        return False
    overlap = min(chunk["end"], end) - max(chunk["start"], start)
    if overlap <= 0:
        return False
    return overlap >= 0.5 * (chunk["end"] - chunk["start"]) or overlap >= 0.5 * (end - start)


def evaluate(store: IndexStore, embedder: Embedder, questions: list[Question],
             index_names: list[str], depth: int = DEPTH) -> dict:
    started = time.monotonic()
    indexes = {i["name"]: i for i in store.indexes() if i["name"] in index_names}
    usable = [n for n in index_names if n in indexes and indexes[n]["embedder"] == embedder.name]
    skipped = {n: ("not built" if n not in indexes else
                   f"built with {indexes[n]['embedder']}, the current embedder is {embedder.name}")
               for n in index_names if n not in usable}

    rows = []
    for q in questions:
        targets = []
        missing = []
        for e in q.expected:
            found = store.section_range(e.source, e.section)
            if found:
                targets.append((e.source, *found))
            else:
                missing.append(e)
        row = {"id": q.id, "question": q.question, "expected": [asdict(e) for e in q.expected],
               "results": {}, "valid": bool(targets),
               "problem": "" if targets else ("no expected place given" if not q.expected else
                                              "expected place is not in the corpus: " +
                                              "; ".join(f"{e.source} › {e.section}" if e.section else e.source
                                                        for e in missing))}
        if targets and usable:
            vector = embedder.embed_query(q.question)
            for name in usable:
                hits = store.search(name, vector, depth)
                rank, words = None, 0
                top = []
                for hit in hits:
                    ok = any(matches(hit.chunk, *t) for t in targets)
                    if rank is None:
                        words += hit.chunk["n_words"]
                        if ok:
                            rank = hit.rank
                    top.append({"rank": hit.rank, "score": round(hit.score, 4), "chunk_id": hit.chunk["chunk_id"],
                                "source": hit.chunk["source"], "section": hit.chunk["section"],
                                "n_words": hit.chunk["n_words"], "match": ok,
                                "preview": " ".join(hit.chunk["text"].split())[:220]})
                row["results"][name] = {"rank": rank, "words_to_hit": words if rank else None, "top": top}
        rows.append(row)

    summary = {}
    valid = [r for r in rows if r["valid"]]
    for name in usable:
        ranks = [r["results"][name]["rank"] for r in valid]
        n = len(ranks) or 1
        found_words = [r["results"][name]["words_to_hit"] for r in valid if r["results"][name]["rank"]]
        summary[name] = {
            **{f"hit@{k}": round(sum(1 for x in ranks if x and x <= k) / n, 3) for k in KS},
            "mrr": round(sum(1 / x for x in ranks if x) / n, 3),
            "not_found": sum(1 for x in ranks if not x),
            "mean_words_to_hit": round(sum(found_words) / len(found_words)) if found_words else None,
            "questions": len(ranks),
        }
    return {"summary": summary, "skipped": skipped, "questions": rows, "depth": depth,
            "embedder": embedder.name, "seconds": round(time.monotonic() - started, 2),
            "invalid": sum(1 for r in rows if not r["valid"])}
