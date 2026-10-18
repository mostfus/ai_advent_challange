"""The index on disk: one SQLite file, vectors and metadata in the same row.

    indexes          name, strategy, params, embedder, ...  - one row per built index
    chunks           index_name, chunk_id, metadata, text, embedding BLOB
    embedding_cache  embedder, sha1(text) -> vector

**Why SQLite and not FAISS or JSON.** At a few thousand chunks a brute-force
cosine over a numpy matrix takes a millisecond, so an ANN index buys nothing
and costs a binary dependency plus a second file to keep in step with the
metadata. JSON would hold the vectors as decimal text several times their
size and can be filtered only by loading all of it. Here a vector sits in
the same row as the source and section it belongs to, a filter is a `WHERE`,
and `sqlite3 data/index/index.sqlite` is the whole debugging story.

The app builds and reads one index (`rag.augment.INDEX`); the store keeps
rows by name, so a file that holds other indexes from before still opens and
they are simply never read.

**The embedding cache** is keyed by embedder and by the exact text embedded.
Rebuild with a different overlap and only the chunks whose text changed go to
the model; the rest come out of the cache. Resetting an index deletes its
chunks and leaves the cache alone - clearing the cache is its own button,
for when the point is to make the model do all of it again.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunkers import Chunk
from .documents import Document

SCHEMA = """
CREATE TABLE IF NOT EXISTS indexes (
    name TEXT PRIMARY KEY, strategy TEXT, params TEXT, embedder TEXT, dim INTEGER,
    corpus_sha TEXT, built_at REAL, stats TEXT, timings TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    index_name TEXT, chunk_id TEXT, source TEXT, title TEXT, section TEXT, title_path TEXT,
    position INTEGER, start INTEGER, "end" INTEGER, n_words INTEGER,
    sections_spanned INTEGER, merged INTEGER, text TEXT, embed_text TEXT, embedding BLOB,
    PRIMARY KEY (index_name, chunk_id)
);
CREATE INDEX IF NOT EXISTS chunks_by_source ON chunks (index_name, source, position);
CREATE TABLE IF NOT EXISTS embedding_cache (
    embedder TEXT, text_sha TEXT, dim INTEGER, vector BLOB, PRIMARY KEY (embedder, text_sha)
);
"""

CHUNK_COLUMNS = ("chunk_id", "source", "title", "section", "title_path", "position", "start", "end",
                 "n_words", "sections_spanned", "merged", "text")


def text_sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def corpus_sha(documents: list[Document]) -> str:
    joined = "\n".join(f"{d.source}:{d.sha}" for d in sorted(documents, key=lambda d: d.source))
    return text_sha(joined)[:12]


@dataclass
class SearchHit:
    rank: int
    score: float
    chunk: dict

    def as_dict(self) -> dict:
        return {"rank": self.rank, "score": round(self.score, 4), **self.chunk}


class IndexStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        # name -> (built_at, rows of metadata, matrix) - reloaded when built_at moves.
        self._matrices: dict[str, tuple[float, list[dict], np.ndarray]] = {}
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # ----------------------------------------------------------------- cache

    def cached(self, embedder: str, texts: list[str]) -> dict[str, np.ndarray]:
        shas = list({text_sha(t) for t in texts})
        found: dict[str, np.ndarray] = {}
        with self.connect() as db:
            for i in range(0, len(shas), 500):
                batch = shas[i:i + 500]
                marks = ",".join("?" * len(batch))
                for row in db.execute(
                        f"SELECT text_sha, vector FROM embedding_cache WHERE embedder = ? AND text_sha IN ({marks})",
                        (embedder, *batch)):
                    found[row["text_sha"]] = np.frombuffer(row["vector"], dtype=np.float32)
        return found

    def remember(self, embedder: str, texts: list[str], vectors: np.ndarray) -> None:
        with self.lock, self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?)",
                [(embedder, text_sha(t), int(v.shape[0]), v.astype(np.float32).tobytes())
                 for t, v in zip(texts, vectors)],
            )

    def cache_stats(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT embedder, COUNT(*) AS vectors, MAX(dim) AS dim FROM embedding_cache GROUP BY embedder")]

    def clear_cache(self) -> int:
        with self.lock, self.connect() as db:
            return db.execute("DELETE FROM embedding_cache").rowcount

    # --------------------------------------------------------------- indexes

    def save_index(self, name: str, strategy: str, params: dict, embedder: str, corpus: str,
                   chunks: list[Chunk], vectors: np.ndarray, stats: dict, timings: dict) -> None:
        dim = int(vectors.shape[1]) if len(chunks) else 0
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM chunks WHERE index_name = ?", (name,))
            db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(name, c.chunk_id, c.source, c.title, c.section, json.dumps(c.title_path, ensure_ascii=False),
                  c.position, c.start, c.end, c.n_words, c.sections_spanned, c.merged, c.text, c.embed_text,
                  v.astype(np.float32).tobytes())
                 for c, v in zip(chunks, vectors)],
            )
            db.execute("INSERT OR REPLACE INTO indexes VALUES (?,?,?,?,?,?,?,?,?)",
                       (name, strategy, json.dumps(params), embedder, dim, corpus, time.time(),
                        json.dumps(stats), json.dumps(timings)))
        self._matrices.pop(name, None)

    def set_timings(self, name: str, timings: dict) -> None:
        with self.lock, self.connect() as db:
            db.execute("UPDATE indexes SET timings = ? WHERE name = ?", (json.dumps(timings), name))

    def indexes(self) -> list[dict]:
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM indexes ORDER BY name")]
        for row in rows:
            for key in ("params", "stats", "timings"):
                row[key] = json.loads(row[key] or "{}")
        return rows

    def index(self, name: str) -> dict | None:
        return next((i for i in self.indexes() if i["name"] == name), None)

    def delete_index(self, name: str | None = None) -> int:
        """Drop one index's chunks and vectors - or every index's, with no name."""
        with self.lock, self.connect() as db:
            if name is None:
                n = db.execute("DELETE FROM chunks").rowcount
                db.execute("DELETE FROM indexes")
            else:
                n = db.execute("DELETE FROM chunks WHERE index_name = ?", (name,)).rowcount
                db.execute("DELETE FROM indexes WHERE name = ?", (name,))
        self._matrices.clear()
        with self.connect() as db:
            db.execute("VACUUM")
        return n

    def chunks(self, name: str, source: str | None = None, offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
        where, args = "index_name = ?", [name]
        if source:
            where += " AND source = ?"
            args.append(source)
        cols = ", ".join(f'"{c}"' for c in CHUNK_COLUMNS)
        with self.connect() as db:
            total = db.execute(f"SELECT COUNT(*) FROM chunks WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"SELECT {cols} FROM chunks WHERE {where} ORDER BY source, position LIMIT ? OFFSET ?",
                              (*args, limit, offset))
            return [self._chunk_row(r) for r in rows], total

    @staticmethod
    def _chunk_row(row: sqlite3.Row) -> dict:
        out = {c: row[c] for c in CHUNK_COLUMNS}
        out["title_path"] = json.loads(out["title_path"])
        return out

    def matrix(self, name: str) -> tuple[list[dict], np.ndarray]:
        meta = self.index(name)
        if meta is None:
            raise KeyError(name)
        cached = self._matrices.get(name)
        if cached and cached[0] == meta["built_at"]:
            return cached[1], cached[2]
        cols = ", ".join(f'"{c}"' for c in CHUNK_COLUMNS)
        with self.connect() as db:
            rows = list(db.execute(f"SELECT {cols}, embedding FROM chunks WHERE index_name = ? ORDER BY source, position",
                                   (name,)))
        chunks = [self._chunk_row(r) for r in rows]
        matrix = (np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
                  if rows else np.zeros((0, meta["dim"] or 1), dtype=np.float32))
        self._matrices[name] = (meta["built_at"], chunks, matrix)
        return chunks, matrix

    def search(self, name: str, query: np.ndarray, k: int = 5, source: str | None = None) -> list[SearchHit]:
        """Top `k` chunks by cosine - the vectors are unit length, so a dot product."""
        chunks, matrix = self.matrix(name)
        if not chunks:
            return []
        scores = matrix @ query.astype(np.float32)
        if source:
            mask = np.array([c["source"] == source for c in chunks])
            scores = np.where(mask, scores, -np.inf)
        k = min(k, len(chunks))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [SearchHit(rank=r + 1, score=float(scores[i]), chunk=chunks[i])
                for r, i in enumerate(top) if np.isfinite(scores[i])]

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
