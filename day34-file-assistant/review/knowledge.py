"""RAG for a review: the project's docs and code, indexed, searched for each changed file.

    Knowledge.build(root, prefix, cache_dir, embedder)   the project at head -> an index
    Knowledge.for_batch(batch, ...)                       the chunks closest to a batch's changes

It is the app's own pipeline (`rag`): the same loaders (Markdown, Python,
Swift, schemas), the structural chunker, bge-m3 over Ollama and the SQLite
store, with its embedding cache - which is what makes a second review of the
same repository cheap: only the chunks that changed are embedded again. The
cache lives outside the checkout (`--cache-dir`), one folder per repository.

**One query per changed file**: its path, the functions git names in its hunk
headers, and its added lines - what the change is about, in the words of the
code. The hits are pooled, and a chunk of a file that is already in the
request is dropped: the model has that file in front of it. What is left is
the rest of the project - the architecture doc the change should follow, the
helper it should have reused, the caller it may break - closest first, cut to
`excerpts_max` and `excerpt_chars`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from rag.chunkers import make_chunker
from rag.corpus import CorpusConfig
from rag.embedders import Embedder, EmbedderError
from rag.index_store import IndexStore
from rag.pipeline import build_index

from .context import Batch
from .diff import ADDED, REMOVED, FileDiff

INDEX = "review"
QUERY_CHARS = 1500


class KnowledgeError(RuntimeError):
    """No index for this review - written for the summary."""


def query_for(file: FileDiff) -> str:
    """What a changed file is about: its path, the enclosing functions, the lines it adds."""
    kind = REMOVED if file.status == "deleted" else ADDED
    changed = [line.text.strip() for h in file.hunks for line in h.lines
               if line.kind == kind and line.text.strip()]
    parts = [file.path, *file.headers()[:8], "\n".join(changed)]
    return "\n".join(p for p in parts if p)[:QUERY_CHARS]


@dataclass
class Knowledge:
    store: IndexStore
    embedder: Embedder
    prefix: str                  # the project folder inside the repository, "" or "app/"
    record: dict
    seconds: float = 0.0
    vectors: dict = field(default_factory=dict)     # query -> its vector, asked once per review

    @classmethod
    def build(cls, root: Path, prefix: str, cache_dir: Path, embedder: Embedder,
              corpus: CorpusConfig | None = None, log=print) -> "Knowledge":
        started = time.monotonic()
        config = (corpus or CorpusConfig.load()).at(root)
        loaded = config.load_corpus()
        if not loaded.documents:
            raise KnowledgeError(f"no file in `{prefix or '.'}` matches the corpus patterns")
        status = embedder.status()
        if not status.get("ok", True):
            raise KnowledgeError(status.get("detail") or "the embedder is not available")
        cache_dir.mkdir(parents=True, exist_ok=True)
        store = IndexStore(cache_dir / "index.sqlite")
        log(f"[review ] indexing {len(loaded.documents)} files of `{prefix or '.'}` with {embedder.name}")

        def progress(stage: str, done: int, total: int) -> None:
            if stage == "embed" and total and (done == total or done % 128 == 0):
                log(f"[review ]   embedded {done}/{total} new chunks")

        try:
            record = build_index(INDEX, loaded, make_chunker({}), embedder, store, progress=progress)
        except EmbedderError as exc:
            raise KnowledgeError(str(exc)) from exc
        return cls(store, embedder, prefix, record, time.monotonic() - started)

    def describe(self) -> dict:
        stats = self.record.get("stats") or {}
        return {"root": self.prefix.rstrip("/") or ".", "embedder": self.embedder.name,
                "documents": stats.get("documents", 0), "chunks": stats.get("chunks", 0),
                "embedded_now": stats.get("embedded_now", 0), "seconds": round(self.seconds, 1)}

    def repo_path(self, source: str) -> str:
        return f"{self.prefix}{source}"

    def search(self, query: str, k: int) -> list[dict]:
        if query not in self.vectors:
            try:
                self.vectors[query] = self.embedder.embed_query(query)
            except EmbedderError as exc:
                raise KnowledgeError(str(exc)) from exc
        return [h.as_dict() for h in self.store.search(INDEX, self.vectors[query], k)]

    def for_batch(self, batch: Batch, per_file: int, max_hits: int, max_chars: int,
                  extra_query: str = "") -> list[dict]:
        """The batch's excerpts, closest first, ranks renumbered from 1."""
        in_request = set(batch.paths)
        queries = [query_for(f.file) for f in batch.files]
        if extra_query.strip():
            queries.append(extra_query.strip()[:QUERY_CHARS])
        best: dict[str, dict] = {}
        for query in queries:
            # Asked for more than are kept: some of the closest are the batch's own files.
            hits = [{**h, "path": self.repo_path(h["source"])} for h in self.search(query, per_file + 6)]
            for hit in [h for h in hits if h["path"] not in in_request][:per_file]:
                kept = best.get(hit["chunk_id"])
                if kept is None or hit["score"] > kept["score"]:
                    best[hit["chunk_id"]] = hit
        chosen, used = [], 0
        for hit in sorted(best.values(), key=lambda h: -h["score"]):
            size = len(hit["text"])
            if len(chosen) >= max_hits or (chosen and used + size > max_chars):
                continue
            chosen.append(hit)
            used += size
        return [{**hit, "rank": n} for n, hit in enumerate(chosen, 1)]

