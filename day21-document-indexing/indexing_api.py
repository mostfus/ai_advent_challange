"""Day 21: the Index tab's backend - the `rag` package, over HTTP.

Everything the tab shows or changes goes through here, and nothing here
reaches into the chat: the router is mounted on the same app for convenience
and shares no state with it. Plugging retrieval into the agent is a later
day; this one builds the index and measures it.

    GET    /api/index                       the whole picture: embedder, corpus, indexes, job
    PUT    /api/index/corpus                which files the corpus is made of (glob patterns)
    POST   /api/index/uploads               add a file to data/index/uploads/
    DELETE /api/index/uploads/{name}
    GET    /api/index/sections?source=      a document's outline - for the question editor
    POST   /api/index/preview               chunk without embedding: stats + the chunks of one file
    POST   /api/index/build                 chunk, embed, store - in the background
    GET    /api/index/job                   how far the build has got
    DELETE /api/index/indexes[/{name}]      reset: drop the chunks and vectors of one index, or all
    DELETE /api/index/cache                 forget cached vectors, so a rebuild re-embeds everything
    GET    /api/index/indexes/{name}/chunks browse what was stored
    POST   /api/index/search                one query against every built index, side by side
    GET    /api/index/questions             the retrieval check's questions
    PUT    /api/index/questions
    POST   /api/index/evaluate              run them; the result is kept for the next page load

Files live under `INDEX_DIR` (default `data/index/`), which the test suite
points at a temp directory the way it does every other store.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from rag import evaluation
from rag.chunkers import DEFAULT_PARAMS, STRATEGIES, make_chunker
from rag.embedders import EmbedderError, make_embedder
from rag.index_store import IndexStore
from rag.loaders import LOADERS
from rag.pipeline import Corpus, build_index, chunk_corpus, chunk_stats, load_corpus

ROOT = Path(__file__).parent
INDEX_DIR = Path(os.getenv("INDEX_DIR") or ROOT / "data" / "index")
UPLOADS_DIR = INDEX_DIR / "uploads"
CORPUS_FILE = INDEX_DIR / "corpus.json"
QUESTIONS_FILE = INDEX_DIR / "questions.json"
LAST_EVAL_FILE = INDEX_DIR / "last_eval.json"
SEED_QUESTIONS = ROOT / "seed_questions.json"

# The README of this day holds every day's text (each README is the last one
# plus a chapter), and the modules hold the docstrings that explain the code -
# two formats, one corpus, no day indexed twenty times over. Uploads are not a
# pattern: they are always in, as `uploads/<name>`.
DEFAULT_PATTERNS = ["README.md", "*.py"]
MAX_UPLOAD_BYTES = 2_000_000
UPLOAD_NAME = re.compile(r"^[\w.\- ]{1,120}$")

router = APIRouter(prefix="/api/index")
store = IndexStore(INDEX_DIR / "index.sqlite")
embedder = make_embedder()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def patterns() -> list[str]:
    return read_json(CORPUS_FILE, {}).get("patterns") or list(DEFAULT_PATTERNS)


def corpus() -> Corpus:
    """The project files the patterns name, plus every upload - uploads always count."""
    loaded = load_corpus(patterns(), ROOT)
    if UPLOADS_DIR.exists():
        uploads = load_corpus(["uploads/*"], INDEX_DIR)
        taken = {d.source for d in loaded.documents}
        loaded.documents += [d for d in uploads.documents if d.source not in taken]
        loaded.skipped += [s for s in uploads.skipped if s["reason"] != "pattern matched nothing"]
        loaded.seconds += uploads.seconds
    return loaded


def questions() -> list[evaluation.Question]:
    if not QUESTIONS_FILE.exists() and SEED_QUESTIONS.exists():
        # First start: the seeded set, so the check has something to run on
        # before anybody has written a question of their own.
        evaluation.save_questions(QUESTIONS_FILE, evaluation.load_questions(SEED_QUESTIONS))
    return evaluation.load_questions(QUESTIONS_FILE)


# ------------------------------------------------------------------- the job

class BuildJob:
    """One build at a time, in a thread - embedding a corpus takes minutes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state: dict = {"running": False}

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def update(self, **fields) -> None:
        with self.lock:
            self.state.update(fields)

    def start(self, items: list[tuple[str, dict]]) -> dict:
        with self.lock:
            if self.state.get("running"):
                raise HTTPException(409, "A build is already running")
            self.state = {"running": True, "queue": [s for s, _ in items], "current": None, "stage": "load",
                          "done": 0, "total": 0, "started_at": time.time(), "error": None, "built": []}
        threading.Thread(target=self.run, args=(items,), name="index-build", daemon=True).start()
        return self.snapshot()

    def run(self, items: list[tuple[str, dict]]) -> None:
        try:
            loaded = corpus()
            if not loaded.documents:
                raise ValueError("The corpus is empty - no file matched the patterns")
            for strategy, params in items:
                self.update(current=strategy, stage="chunk", done=0, total=0)
                chunker = make_chunker(strategy, params)
                build_index(strategy, loaded, chunker, embedder, store,
                            progress=lambda stage, done, total: self.update(stage=stage, done=done, total=total))
                with self.lock:
                    self.state["built"].append(strategy)
            self.update(running=False, stage="done", finished_at=time.time())
        except (EmbedderError, ValueError, OSError) as exc:
            self.update(running=False, stage="failed", error=str(exc), finished_at=time.time())


job = BuildJob()


# --------------------------------------------------------------- the reports

def index_report(record: dict, corpus_sha: str) -> dict:
    return {**record, "stale": record["corpus_sha"] != corpus_sha,
            "embedder_changed": record["embedder"] != embedder.name}


def overview() -> dict:
    loaded = corpus()
    last_eval = read_json(LAST_EVAL_FILE, None)
    return {
        "embedder": {**embedder.describe(), "status": embedder.status()},
        "corpus": {
            "patterns": patterns(),
            "sha": loaded.sha,
            "documents": [{"source": d.source, "title": d.title, "format": d.format,
                           "sections": len(d.sections), "words": d.n_words, "chars": len(d.text)}
                          for d in loaded.documents],
            "skipped": loaded.skipped,
            "words": sum(d.n_words for d in loaded.documents),
            "formats": sorted({d.format for d in loaded.documents}),
            "loaders": sorted(LOADERS),
            "load_seconds": round(loaded.seconds, 3),
            "uploads": sorted(p.name for p in UPLOADS_DIR.glob("*") if p.is_file()) if UPLOADS_DIR.exists() else [],
        },
        "strategies": {name: {"defaults": DEFAULT_PARAMS[name]} for name in STRATEGIES},
        "indexes": [index_report(i, loaded.sha) for i in store.indexes()],
        "cache": store.cache_stats(),
        "db": {"path": str(store.path.relative_to(ROOT)) if ROOT in store.path.parents else str(store.path),
               "bytes": store.size_bytes()},
        "job": job.snapshot(),
        "questions": len(questions()),
        "last_eval": last_eval and {k: last_eval[k] for k in ("summary", "at", "depth", "invalid", "embedder")
                                    if k in last_eval},
    }


# ------------------------------------------------------------------ requests

class CorpusRequest(BaseModel):
    patterns: list[str] = Field(default_factory=list, max_length=50)


class UploadRequest(BaseModel):
    name: str
    content: str


class ChunkingRequest(BaseModel):
    strategy: str
    params: dict = Field(default_factory=dict)
    source: str | None = None
    limit: int = 200


class BuildItem(BaseModel):
    strategy: str
    params: dict = Field(default_factory=dict)


class BuildRequest(BaseModel):
    items: list[BuildItem] = Field(min_length=1, max_length=len(STRATEGIES))


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=20)
    source: str | None = None


class QuestionsRequest(BaseModel):
    questions: list[dict] = Field(default_factory=list, max_length=200)


class EvaluateRequest(BaseModel):
    depth: int = Field(default=evaluation.DEPTH, ge=1, le=50)


def chunker_or_400(strategy: str, params: dict):
    try:
        return make_chunker(strategy, params)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


# ----------------------------------------------------------------- endpoints

@router.get("")
def get_overview() -> dict:
    return overview()


@router.put("/corpus")
def put_corpus(req: CorpusRequest) -> dict:
    cleaned = [p.strip() for p in req.patterns if p.strip()]
    for pattern in cleaned:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise HTTPException(400, f"{pattern!r}: patterns are relative to the project folder and stay inside it")
    write_json(CORPUS_FILE, {"patterns": cleaned or list(DEFAULT_PATTERNS)})
    return overview()


@router.post("/uploads")
def upload(req: UploadRequest) -> dict:
    name = Path(req.name).name
    if not UPLOAD_NAME.match(name):
        raise HTTPException(400, "Use a plain file name: letters, digits, spaces, dots, dashes")
    if Path(name).suffix.lower() not in LOADERS:
        raise HTTPException(400, f"No loader for {Path(name).suffix or 'files without an extension'} - "
                                 f"supported: {', '.join(sorted(LOADERS))}")
    if len(req.content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 2 MB")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOADS_DIR / name).write_text(req.content, encoding="utf-8")
    return overview()


@router.delete("/uploads/{name}")
def delete_upload(name: str) -> dict:
    path = UPLOADS_DIR / Path(name).name
    if not path.is_file():
        raise HTTPException(404, "No such upload")
    path.unlink()
    return overview()


@router.get("/sections")
def get_sections(source: str) -> dict:
    document = next((d for d in corpus().documents if d.source == source), None)
    if document is None:
        raise HTTPException(404, f"{source} is not in the corpus")
    return {"source": source, "title": document.title,
            "sections": [{"label": s.label, "depth": len(s.title_path), "kind": s.kind,
                          "words": len(document.section_text(s).split())} for s in document.sections]}


@router.post("/preview")
def preview(req: ChunkingRequest) -> dict:
    """What a strategy would do with these settings - no model involved."""
    chunker = chunker_or_400(req.strategy, req.params)
    loaded = corpus()
    started = time.monotonic()
    chunks = chunk_corpus(loaded.documents, chunker)
    seconds = time.monotonic() - started
    shown = [c for c in chunks if c.source == req.source] if req.source else []
    return {"strategy": req.strategy, "params": chunker.params(), "stats": chunk_stats(chunks, loaded.documents),
            "seconds": round(seconds, 3), "source": req.source,
            "chunks": [{**c.metadata(), "text": c.text, "embed_prefix": c.embed_text[: len(c.embed_text) - len(c.text)]}
                       for c in shown[: req.limit]],
            "shown_of": len(shown)}


@router.post("/build")
def build(req: BuildRequest) -> dict:
    items = []
    for item in req.items:
        items.append((item.strategy, chunker_or_400(item.strategy, item.params).params()))
    status = embedder.status()
    if not status["ok"]:
        raise HTTPException(503, status["detail"])
    return job.start(items)


@router.get("/job")
def get_job() -> dict:
    return job.snapshot()


@router.delete("/indexes")
def reset_all() -> dict:
    if job.snapshot().get("running"):
        raise HTTPException(409, "A build is running")
    store.delete_index(None)
    return overview()


@router.delete("/indexes/{name}")
def reset_index(name: str) -> dict:
    if job.snapshot().get("running"):
        raise HTTPException(409, "A build is running")
    store.delete_index(name)
    return overview()


@router.delete("/cache")
def clear_cache() -> dict:
    if job.snapshot().get("running"):
        raise HTTPException(409, "A build is running")
    store.clear_cache()
    return overview()


@router.get("/indexes/{name}/chunks")
def list_chunks(name: str, source: str | None = None, offset: int = 0, limit: int = 30) -> dict:
    if store.index(name) is None:
        raise HTTPException(404, f"No index {name!r} - build it first")
    rows, total = store.chunks(name, source, max(0, offset), min(max(1, limit), 200))
    return {"index": name, "source": source, "offset": offset, "total": total, "chunks": rows}


@router.post("/search")
def search(req: SearchRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(400, "Type a query")
    built = [i for i in store.indexes() if i["embedder"] == embedder.name]
    if not built:
        raise HTTPException(409, "No index built with the current embedder yet")
    started = time.monotonic()
    try:
        vector = embedder.embed_query(query)
    except EmbedderError as exc:
        raise HTTPException(503, str(exc)) from exc
    embed_ms = round((time.monotonic() - started) * 1000)
    results = {}
    for record in built:
        started = time.monotonic()
        hits = store.search(record["name"], vector, req.k, req.source)
        results[record["name"]] = {"ms": round((time.monotonic() - started) * 1000, 1),
                                   "hits": [h.as_dict() for h in hits]}
    return {"query": query, "embed_ms": embed_ms, "results": results}


@router.get("/questions")
def get_questions() -> dict:
    return {"questions": [q.as_dict() for q in questions()]}


@router.put("/questions")
def put_questions(req: QuestionsRequest) -> dict:
    cleaned = evaluation.clean_questions(req.questions)
    evaluation.save_questions(QUESTIONS_FILE, cleaned)
    return {"questions": [q.as_dict() for q in cleaned]}


@router.post("/evaluate")
def evaluate(req: EvaluateRequest) -> dict:
    names = [i["name"] for i in store.indexes()]
    if not names:
        raise HTTPException(409, "Build at least one index first")
    try:
        result = evaluation.evaluate(store, embedder, questions(), names, req.depth)
    except EmbedderError as exc:
        raise HTTPException(503, str(exc)) from exc
    result["at"] = time.time()
    result["indexes"] = {i["name"]: {"params": i["params"], "built_at": i["built_at"]} for i in store.indexes()}
    write_json(LAST_EVAL_FILE, result)
    return result


@router.get("/evaluate")
def last_evaluation() -> dict:
    return read_json(LAST_EVAL_FILE, {})
