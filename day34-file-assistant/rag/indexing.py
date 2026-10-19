"""Day 21: the document index as a service - the corpus, uploads, the build, search.

Everything the Index tab shows or changes goes through `IndexService`, and the
chat's RAG mode retrieves through it (`retrieve`) - so what is built here is
what the chat answers from. There is one index, `augment.INDEX`, cut by the
structural chunker (`rag.chunkers`).

    overview()                    the whole picture: embedder, corpus, the index, job
    upload / delete_upload        add or remove a file in <index dir>/uploads/
    preview(params)               chunk without embedding: stats + the chunks of one file
    build(params)                 chunk, embed, store - in the background (`job`)
    reset(name) / clear_cache()   drop the index; forget cached vectors
    switch(root)                  another project folder: the old project's chunks dropped
    chunks(name, ...)             browse what was stored
    search(query, k)              one query against the index
    retrieve(question, k)         the chat's search, its failures turned into refusals

Which files the corpus is made of is `corpus.json` (`rag.corpus`), not
something the tab edits; which folder it is read from is the open project
(`switch`, day 31). Uploads are always in, as `uploads/<name>`. Files live
under the index directory (default `data/index/`, or `INDEX_DIR`).
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from core.errors import Conflict, Invalid, NotFound, TooLarge, Unavailable

from . import augment
from .chunkers import DEFAULT_PARAMS, make_chunker
from .corpus import PROJECT_DIR, CorpusConfig
from .embedders import Embedder, EmbedderError, make_embedder
from .index_store import IndexStore
from .loaders import LOADERS
from .pipeline import Corpus, build_index, chunk_corpus, chunk_stats, load_corpus

DEFAULT_INDEX_DIR = PROJECT_DIR / "data" / "index"

MAX_UPLOAD_BYTES = 2_000_000
UPLOAD_NAME = re.compile(r"^[\w.\- ]{1,120}$")


def index_dir_from_env() -> Path:
    """Where the index lives: `INDEX_DIR`, or `data/index`."""
    configured = os.getenv("INDEX_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_INDEX_DIR


# ------------------------------------------------------------------- the job

class BuildJob:
    """One build at a time, in a thread - embedding a corpus takes minutes."""

    def __init__(self, service: "IndexService"):
        self.service = service
        self.lock = threading.Lock()
        self.state: dict = {"running": False}

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def update(self, **fields) -> None:
        with self.lock:
            self.state.update(fields)

    def start(self, params: dict) -> dict:
        with self.lock:
            if self.state.get("running"):
                raise Conflict("A build is already running")
            self.state = {"running": True, "stage": "load", "done": 0, "total": 0,
                          "started_at": time.time(), "error": None}
        threading.Thread(target=self.run, args=(params,), name="index-build", daemon=True).start()
        return self.snapshot()

    def run(self, params: dict) -> None:
        service = self.service
        try:
            loaded = service.corpus()
            if not loaded.documents:
                raise ValueError("The corpus is empty - no file matched the patterns")
            self.update(stage="chunk", done=0, total=0)
            build_index(augment.INDEX, loaded, make_chunker(params), service.embedder, service.store,
                        progress=lambda stage, done, total: self.update(stage=stage, done=done, total=total))
            self.update(running=False, stage="done", finished_at=time.time())
        except (EmbedderError, ValueError, OSError) as exc:
            self.update(running=False, stage="failed", error=str(exc), finished_at=time.time())


class IndexService:
    """The index and its corpus - one per runtime."""

    def __init__(self, directory: Path | str | None = None, corpus: CorpusConfig | None = None,
                 embedder: Embedder | None = None) -> None:
        self.dir = Path(directory) if directory else index_dir_from_env()
        self.uploads_dir = self.dir / "uploads"
        self.corpus_config = corpus if corpus is not None else CorpusConfig.load()
        self.store = IndexStore(self.dir / "index.sqlite")
        self.embedder = embedder if embedder is not None else make_embedder()
        self.job = BuildJob(self)

    # -------------------------------------------------------------- the corpus

    def corpus(self) -> Corpus:
        """The files `corpus.json` names, plus every upload - uploads always count."""
        loaded = self.corpus_config.load_corpus()
        if self.uploads_dir.exists():
            uploads = load_corpus(["uploads/*"], self.dir)
            taken = {d.source for d in loaded.documents}
            loaded.documents += [d for d in uploads.documents if d.source not in taken]
            loaded.skipped += [s for s in uploads.skipped if s["reason"] != "pattern matched nothing"]
            loaded.seconds += uploads.seconds
        return loaded

    # ------------------------------------------------------------- the reports

    def index_report(self, record: dict, corpus_sha: str) -> dict:
        return {**record, "stale": record["corpus_sha"] != corpus_sha,
                "embedder_changed": record["embedder"] != self.embedder.name}

    def overview(self) -> dict:
        loaded = self.corpus()
        return {
            "embedder": {**self.embedder.describe(), "status": self.embedder.status()},
            "corpus": {
                # Day 31: where the corpus comes from - `corpus.json`, shown, not edited here.
                **self.corpus_config.describe(),
                "sha": loaded.sha,
                "documents": [{"source": d.source, "title": d.title, "format": d.format,
                               "sections": len(d.sections), "words": d.n_words, "chars": len(d.text)}
                              for d in loaded.documents],
                "skipped": loaded.skipped,
                "words": sum(d.n_words for d in loaded.documents),
                "formats": sorted({d.format for d in loaded.documents}),
                "loaders": sorted(LOADERS),
                "load_seconds": round(loaded.seconds, 3),
                "uploads": sorted(p.name for p in self.uploads_dir.glob("*") if p.is_file())
                if self.uploads_dir.exists() else [],
            },
            "chunker": {"defaults": DEFAULT_PARAMS},
            # A list of the one index, empty until it is built. Other rows a
            # file may still hold from before are not the app's.
            "indexes": [self.index_report(i, loaded.sha) for i in self.store.indexes()
                        if i["name"] == augment.INDEX],
            "cache": self.store.cache_stats(),
            "db": {"path": str(self.store.path.relative_to(PROJECT_DIR))
                   if PROJECT_DIR in self.store.path.parents else str(self.store.path),
                   "bytes": self.store.size_bytes()},
            "job": self.job.snapshot(),
        }

    # ----------------------------------------------------------- the operations

    @staticmethod
    def chunker(params: dict):
        try:
            return make_chunker(params)
        except (ValueError, TypeError) as exc:
            raise Invalid(str(exc)) from exc

    def upload(self, name: str, content: str) -> dict:
        name = Path(name).name
        if not UPLOAD_NAME.match(name):
            raise Invalid("Use a plain file name: letters, digits, spaces, dots, dashes")
        if Path(name).suffix.lower() not in LOADERS:
            raise Invalid(f"No loader for {Path(name).suffix or 'files without an extension'} - "
                          f"supported: {', '.join(sorted(LOADERS))}")
        if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            raise TooLarge("File is larger than 2 MB")
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        (self.uploads_dir / name).write_text(content, encoding="utf-8")
        return self.overview()

    def delete_upload(self, name: str) -> dict:
        path = self.uploads_dir / Path(name).name
        if not path.is_file():
            raise NotFound("No such upload")
        path.unlink()
        return self.overview()

    def preview(self, params: dict, source: str | None = None, limit: int = 200) -> dict:
        """What the chunker would do with these settings - no model involved."""
        chunker = self.chunker(params)
        loaded = self.corpus()
        started = time.monotonic()
        chunks = chunk_corpus(loaded.documents, chunker)
        seconds = time.monotonic() - started
        shown = [c for c in chunks if c.source == source] if source else []
        return {"params": chunker.params(), "stats": chunk_stats(chunks, loaded.documents),
                "seconds": round(seconds, 3), "source": source,
                "chunks": [{**c.metadata(), "text": c.text,
                            "embed_prefix": c.embed_text[: len(c.embed_text) - len(c.text)]}
                           for c in shown[:limit]],
                "shown_of": len(shown)}

    def build(self, params: dict) -> dict:
        checked = self.chunker(params).params()
        status = self.embedder.status()
        if not status["ok"]:
            raise Unavailable(status["detail"])
        return self.job.start(checked)

    def ensure_idle(self) -> None:
        if self.job.snapshot().get("running"):
            raise Conflict("A build is running")

    def switch(self, root: Path) -> bool:
        """Day 31: point the corpus at another project folder. True if it moved.

        The old project's chunks are dropped with it - an index that answered
        about the last project would be presented as this one's. Vectors stay
        in the embedding cache, so going back to a project re-embeds nothing
        that did not change.
        """
        self.ensure_idle()
        root = Path(root).resolve()
        if root == self.corpus_config.root_path:
            return False
        self.corpus_config = self.corpus_config.at(root)
        self.store.delete_index(augment.INDEX)
        return True

    def built(self) -> dict | None:
        """The index's record, if there is one built with the current embedder."""
        record = self.store.index(augment.INDEX)
        return record if record is not None and record["embedder"] == self.embedder.name else None

    def reset(self, name: str | None = None) -> dict:
        """Drop one index's chunks and vectors - or, with no name, everything the file holds."""
        self.ensure_idle()
        self.store.delete_index(name)
        return self.overview()

    def clear_cache(self) -> dict:
        self.ensure_idle()
        self.store.clear_cache()
        return self.overview()

    def chunks(self, name: str, source: str | None = None, offset: int = 0, limit: int = 30) -> dict:
        if self.store.index(name) is None:
            raise NotFound(f"No index {name!r} - build it first")
        rows, total = self.store.chunks(name, source, max(0, offset), min(max(1, limit), 200))
        return {"index": name, "source": source, "offset": offset, "total": total, "chunks": rows}

    def search(self, query: str, k: int = 5, source: str | None = None) -> dict:
        query = query.strip()
        if not query:
            raise Invalid("Type a query")
        record = self.store.index(augment.INDEX)
        if record is None or record["embedder"] != self.embedder.name:
            raise Conflict("No index built with the current embedder yet")
        started = time.monotonic()
        try:
            vector = self.embedder.embed_query(query)
        except EmbedderError as exc:
            raise Unavailable(str(exc)) from exc
        embed_ms = round((time.monotonic() - started) * 1000)
        started = time.monotonic()
        hits = self.store.search(augment.INDEX, vector, k, source)
        return {"query": query, "index": augment.INDEX, "embed_ms": embed_ms,
                "ms": round((time.monotonic() - started) * 1000, 1), "hits": [h.as_dict() for h in hits]}

    def retrieve(self, question: str, k: int) -> augment.Retrieval:
        """The chat's search - the R of RAG - with its failures already turned into refusals.

        A missing index or a silent Ollama refuses the turn rather than letting
        it be answered without the documents.
        """
        try:
            return augment.retrieve(self.store, self.embedder, augment.INDEX, question, k)
        except augment.RetrievalError as exc:
            raise Conflict(str(exc)) from exc
        except EmbedderError as exc:
            raise Unavailable(str(exc)) from exc
