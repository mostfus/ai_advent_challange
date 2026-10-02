"""Day 22: the RAG tab's backend - one question, asked with and without the index.

    question -> retrieve the k nearest chunks -> join them onto the question -> the LLM

The same agent answers both ways: same model, same system prompt, same
settings, no memory, no tools, no personality - so the excerpts are the only
thing that differs between the two requests, and the comparison measures them
and nothing else. (The chat's own RAG switch is in `server.py`; it uses
`retrieval_for` below, and everything else the chat has stays on.)

    GET    /api/rag                      indexes, embedder, models, questions, the last run
    PUT    /api/rag/questions            the control set
    POST   /api/rag/ask                  a free question, both ways, side by side - not saved
    POST   /api/rag/check/{id}           one control question, both ways, scored - saved
    DELETE /api/rag/results              forget the last run

Each check is its own request, so the tab runs the ten one after another and
draws each row as it lands, instead of holding one request open for the
two-odd minutes twenty answers take.

The index and the embedder are `indexing_api`'s own objects, not copies: a
rebuild in the Index tab is what the next question here retrieves from.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import agent as agent_module
import indexing_api
from agent import Agent, AgentConfig
from rag import augment, control
from rag.embedders import EmbedderError

ROOT = Path(__file__).parent
RAG_DIR = Path(os.getenv("RAG_DIR") or ROOT / "data" / "rag")
QUESTIONS_FILE = RAG_DIR / "questions.json"
RESULTS_FILE = RAG_DIR / "last_run.json"
SEED_QUESTIONS = ROOT / "rag_questions.json"

router = APIRouter(prefix="/api/rag")
# Set by server.py to the app's one DeepSeek client; the tests hand in a fake.
client = None
results_lock = threading.Lock()


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


def questions() -> list[control.ControlQuestion]:
    if not QUESTIONS_FILE.exists() and SEED_QUESTIONS.exists():
        control.save_questions(QUESTIONS_FILE, control.load_questions(SEED_QUESTIONS))
    return control.load_questions(QUESTIONS_FILE)


# ------------------------------------------------------------- retrieval

def retrieval_for(question: str, index: str, k: int) -> augment.Retrieval:
    """The R of RAG, with its failures already turned into HTTP errors."""
    try:
        return augment.retrieve(indexing_api.store, indexing_api.embedder, index, question, k)
    except augment.RetrievalError as exc:
        raise HTTPException(409, str(exc)) from exc
    except EmbedderError as exc:
        raise HTTPException(503, str(exc)) from exc


# ---------------------------------------------------------------- answers

def bare_agent(model: str, knowledge: str | None = None) -> Agent:
    """The chat's defaults with everything the chat adds taken away."""
    if client is None:
        raise HTTPException(503, "No LLM client configured")
    return Agent(client, AgentConfig(
        model=model,
        system_prompt=agent_module.DEFAULT_SYSTEM_PROMPT,
        reasoning_effort=agent_module.DEFAULT_REASONING_EFFORT,
        temperature=agent_module.DEFAULT_TEMPERATURE,
    ), knowledge=knowledge)


def one_answer(agent: Agent, question: str) -> dict:
    reply = agent.ask(question)
    return {"answer": reply.answer, "error": reply.error, "elapsed_ms": round(reply.elapsed_ms or 0),
            "usage": reply.usage, "prompt": reply.request["messages"][-1]["content"],
            "request": reply.request}


def answer_both(question: str, index: str, k: int, model: str) -> dict:
    """Plain and augmented, at the same time - the slower one sets the pace, not the sum."""
    retrieval = None
    retrieval_error = None
    try:
        retrieval = retrieval_for(question, index, k)
    except HTTPException as exc:
        retrieval_error = exc.detail
    with ThreadPoolExecutor(max_workers=2) as pool:
        plain = pool.submit(one_answer, bare_agent(model), question)
        augmented = (pool.submit(one_answer, bare_agent(model, augment.render_excerpts(retrieval)), question)
                     if retrieval else None)
        out = {"plain": plain.result()}
        if augmented is not None:
            rag = augmented.result()
            rag["rag"] = retrieval.report(rag["answer"] if not rag["error"] else None)
            out["rag"] = rag
        else:
            out["rag"] = {"error": retrieval_error, "answer": None}
    return out


# ------------------------------------------------------------------ requests

class RunSettings(BaseModel):
    index: str = augment.DEFAULT_INDEX
    k: int = Field(default=augment.DEFAULT_K, ge=1, le=augment.MAX_K)
    model: str = agent_module.DEFAULT_MODEL


class AskRequest(RunSettings):
    question: str


class QuestionsRequest(BaseModel):
    questions: list[dict] = Field(default_factory=list, max_length=100)


def valid_model(model: str) -> str:
    if model not in agent_module.MODELS:
        raise HTTPException(400, f"Unknown model: {model}")
    return model


def overview() -> dict:
    current = indexing_api.embedder.name
    last = read_json(RESULTS_FILE, {})
    qs = questions()
    rows = [last.get("rows", {}).get(q.id) for q in qs]
    return {
        "embedder": {**indexing_api.embedder.describe(), "status": indexing_api.embedder.status()},
        "indexes": [{"name": i["name"], "strategy": i["strategy"], "chunks": i["stats"].get("chunks", 0),
                     "embedder": i["embedder"], "usable": i["embedder"] == current}
                    for i in indexing_api.store.indexes()],
        "defaults": {"index": augment.DEFAULT_INDEX, "k": augment.DEFAULT_K, "model": agent_module.DEFAULT_MODEL,
                     "max_k": augment.MAX_K},
        "models": agent_module.MODELS,
        "questions": [q.as_dict() for q in qs],
        "results": {q.id: r for q, r in zip(qs, rows) if r},
        "summary": control.summarise([r for r in rows if r]),
    }


# ----------------------------------------------------------------- endpoints

@router.get("")
def get_overview() -> dict:
    return overview()


@router.put("/questions")
def put_questions(req: QuestionsRequest) -> dict:
    control.save_questions(QUESTIONS_FILE, control.clean_questions(req.questions))
    return overview()


@router.post("/ask")
def ask(req: AskRequest) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Type a question")
    return {"question": question, **answer_both(question, req.index, req.k, valid_model(req.model))}


@router.post("/check/{qid}")
def check(qid: str, req: RunSettings) -> dict:
    q = next((q for q in questions() if q.id == qid), None)
    if q is None:
        raise HTTPException(404, f"No control question {qid!r}")
    valid_model(req.model)
    both = answer_both(q.question, req.index, req.k, req.model)
    row = {"id": q.id, "question": q.question, "at": time.time(), "settings": req.model_dump()}
    for mode in ("plain", "rag"):
        result = both[mode]
        result.pop("request", None)        # the prompt is kept; the full body is the debug panel's
        if not result.get("error"):
            result["keywords"] = control.score_keywords(result["answer"], q.keywords)
        row[mode] = result
    if not row["rag"].get("error"):
        row["rag"]["sources"] = control.score_sources(indexing_api.store, q.sources, row["rag"]["rag"])
    with results_lock:
        last = read_json(RESULTS_FILE, {})
        last.setdefault("rows", {})[q.id] = row
        write_json(RESULTS_FILE, last)
    return {"row": row, "summary": overview()["summary"]}


@router.delete("/results")
def clear_results() -> dict:
    with results_lock:
        write_json(RESULTS_FILE, {"rows": {}})
    return overview()
