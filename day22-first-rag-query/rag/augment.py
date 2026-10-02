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

    @property
    def words(self) -> int:
        return sum(h["n_words"] for h in self.hits)

    def report(self, answer: str | None = None) -> dict:
        """What goes to the page and onto the stored message."""
        used = set(cited(answer, self)) if answer is not None else set()
        return {
            "index": self.index, "k": self.k, "query": self.query,
            "embed_ms": self.embed_ms, "search_ms": self.search_ms, "words": self.words,
            "cited": sorted(used),
            "hits": [{"n": h["rank"], "chunk_id": h["chunk_id"], "source": h["source"],
                      "section": h["section"], "score": h["score"], "n_words": h["n_words"],
                      "start": h["start"], "end": h["end"], "cited": h["rank"] in used,
                      "preview": " ".join(h["text"].split())[:PREVIEW_CHARS]}
                     for h in self.hits],
        }


def retrieve(store: IndexStore, embedder: Embedder, index: str, question: str, k: int = DEFAULT_K) -> Retrieval:
    """The question, embedded, against one built index. Raises `RetrievalError`."""
    record = store.index(index)
    if record is None:
        raise RetrievalError(f"There is no {index!r} index yet - build it in the Index tab first.")
    if record["embedder"] != embedder.name:
        raise RetrievalError(f"The {index!r} index was built with {record['embedder']}, "
                             f"and the current embedder is {embedder.name} - rebuild it in the Index tab.")
    k = max(1, min(int(k), MAX_K))
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
    """`[n] file › section` and the chunk's text, for every hit, in rank order."""
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
