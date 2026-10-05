"""Day 22: the R and the A of RAG - find the chunks, put them in front of the question.

    retrieve(store, embedder, index, question, k)   the question -> the k nearest chunks
    render_excerpts(hits)                            those chunks -> numbered text for a prompt
    cited(answer, retrieval)                         which of them the answer says it used

The G is somebody else's: this module hands back text and never calls a
model, so the agent puts the excerpts in a request the same way it puts
every other block in one, and the package still imports nothing from the app
around it.

**Numbered, not quoted by id.** Each excerpt goes in as `[n] file › section`
and the model is asked to cite `[n]`. A number is two tokens and impossible
to misspell; `structural:README.md#0042` is neither. The number maps back to
the chunk here, after the answer, so a citation of an excerpt that was never
sent (`[9]` out of five) is dropped rather than shown as a source.

**Whole chunks, in rank order.** The chunk is what was embedded and scored;
trimming it to fit would send text the score was not computed on. k is the
budget: five structural chunks are ~900 words, which is the price of the mode
and is reported with every answer.

Day 23 asks the search for more than will be sent - `candidates`, twenty by
default - and lets `rerank` decide which of them go up. Day 22's mode is the
same search cut at k (`Retrieval.top`), so the two modes start from one list
of candidates and differ only in how it is cut. When the cut leaves nothing,
the excerpts block says so (`NO_EXCERPTS`) rather than disappearing: a
question that went up bare would read as a question asked without the
documents, which is the opposite of what happened.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .embedders import Embedder
from .index_store import IndexStore

DEFAULT_K = 5
MAX_K = 10
DEFAULT_INDEX = "structural"
# Day 23: how many the search returns before the second stage cuts it to k.
DEFAULT_CANDIDATES = 20
MAX_CANDIDATES = 30
NO_EXCERPTS = ("(none - the nearest chunks of the index were read for this question, "
               "and none of them was judged to be about it)")
CITATION = re.compile(r"\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]")
PREVIEW_CHARS = 240


class RetrievalError(RuntimeError):
    """Nothing to retrieve from - written for a person, like `LLMClientError`."""


@dataclass
class Retrieval:
    query: str
    index: str
    k: int
    hits: list[dict] = field(default_factory=list)   # SearchHit.as_dict(), rank 1 first
    embed_ms: int = 0
    search_ms: float = 0.0
    # Day 23: what the second stage did to get from the candidates to `hits`,
    # as `rerank.keep` reports it. None for day 22's plain cut.
    rerank: dict | None = None

    @property
    def words(self) -> int:
        return sum(h["n_words"] for h in self.hits)

    def top(self, k: int) -> "Retrieval":
        """Day 22's mode out of day 23's candidates: the first k, nothing read."""
        k = max(1, min(int(k), MAX_K))
        return Retrieval(self.query, self.index, k, self.hits[:k], self.embed_ms, self.search_ms)

    def report(self, answer: str | None = None) -> dict:
        """What goes to the page and onto the stored message."""
        used = set(cited(answer, self)) if answer is not None else set()
        out = {
            "index": self.index, "k": self.k, "query": self.query,
            "embed_ms": self.embed_ms, "search_ms": self.search_ms, "words": self.words,
            "cited": sorted(used),
            "hits": [{"n": h["rank"], "chunk_id": h["chunk_id"], "source": h["source"],
                      "section": h["section"], "score": h["score"], "n_words": h["n_words"],
                      "start": h["start"], "end": h["end"], "cited": h["rank"] in used,
                      "preview": preview(h),
                      **({"cos_rank": h["cos_rank"], "llm": h["llm"]} if "cos_rank" in h else {})}
                     for h in self.hits],
        }
        if self.rerank is not None:
            out["rerank"] = self.rerank
        return out


def preview(hit: dict) -> str:
    return " ".join(hit["text"].split())[:PREVIEW_CHARS]


def retrieve(store: IndexStore, embedder: Embedder, index: str, question: str, k: int = DEFAULT_K) -> Retrieval:
    """The question, embedded, against one built index. Raises `RetrievalError`.

    `k` may go up to `MAX_CANDIDATES`: since day 23 this is also how the
    candidates for the second stage are fetched. What is *sent* is held to
    `MAX_K` by `top` and by `rerank.keep`.
    """
    record = store.index(index)
    if record is None:
        raise RetrievalError(f"There is no {index!r} index yet - build it in the Index tab first.")
    if record["embedder"] != embedder.name:
        raise RetrievalError(f"The {index!r} index was built with {record['embedder']}, "
                             f"and the current embedder is {embedder.name} - rebuild it in the Index tab.")
    k = max(1, min(int(k), MAX_CANDIDATES))
    started = time.monotonic()
    vector = embedder.embed_query(question)          # EmbedderError goes up as it is
    embed_ms = round((time.monotonic() - started) * 1000)
    started = time.monotonic()
    hits = store.search(index, vector, k)
    search_ms = round((time.monotonic() - started) * 1000, 1)
    return Retrieval(question, index, k, [h.as_dict() for h in hits], embed_ms, search_ms)


def label(hit: dict) -> str:
    return f"{hit['source']} › {hit['section']}" if hit.get("section") else hit["source"]


def render_excerpts(retrieval: Retrieval) -> str:
    """`[n] file › section` and the chunk's text, for every hit, in rank order.

    Nothing kept is still something to say (day 23): `NO_EXCERPTS`.
    """
    if not retrieval.hits:
        return NO_EXCERPTS
    return "\n\n".join(f"[{h['rank']}] {label(h)}\n{h['text'].strip()}" for h in retrieval.hits)


def cited(answer: str | None, retrieval: Retrieval) -> list[int]:
    """The excerpt numbers the answer cites, in order of first mention.

    `[2]`, `[1, 3]` and `[1; 3]` all count; a number that was not sent does not.
    """
    sent = {h["rank"] for h in retrieval.hits}
    seen: list[int] = []
    for group in CITATION.findall(answer or ""):
        for part in re.split(r"[,;]", group):
            n = int(part.strip())
            if n in sent and n not in seen:
                seen.append(n)
    return seen
