"""Day 22: the RAG tab's backend - one question, asked with and without the index.

    question -> retrieve the k nearest chunks -> join them onto the question -> the LLM

The same agent answers both ways: same model, same system prompt, same
settings, no memory, no tools, no personality - so the excerpts are the only
thing that differs between the two requests, and the comparison measures them
and nothing else. (The chat's own RAG switch is in `server.py`; it uses
`retrieval_for` and `reranked` below, and everything else the chat has stays on.)

Day 23 makes it three ways, out of one search:

    plain     the question alone
    rag       day 22: the `candidates` nearest chunks, cut at k by cosine
    rerank    the same candidates, read by a judge model, cut at a threshold, at most k

The two RAG modes share the embedding and the search, so they differ only in
how the list was cut - which is the thing being compared.

    GET    /api/rag                      indexes, embedder, models, questions, the last run, the sweep
    GET    /api/rag/sweep                the sweep again, for other candidates / k - no request made
    PUT    /api/rag/questions            the control set
    POST   /api/rag/ask                  a free question, three ways, side by side - not saved
    POST   /api/rag/check/{id}           one control question, three ways, scored - saved
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

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import agent as agent_module
import indexing_api
from agent import Agent, AgentConfig
from llm_client import LLMClientError
from rag import augment, control, rerank
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


def judge(model: str) -> rerank.LLMReranker:
    """Day 23: the reranker over the app's DeepSeek client."""
    if client is None:
        raise HTTPException(503, "No LLM client configured")

    def complete(body: dict) -> dict:
        try:
            return client.complete(body).response
        except LLMClientError as exc:
            raise rerank.RerankError(f"The reranker's request failed: {exc}") from exc
    return rerank.LLMReranker(complete, model)


def reranked(question: str, candidates: augment.Retrieval, model: str, threshold: float, k: int) -> augment.Retrieval:
    """The second stage, its failure an HTTP error: an unread judgement is not a filter."""
    try:
        judgement = judge(model).judge(question, candidates.hits)
    except rerank.RerankError as exc:
        raise HTTPException(502, str(exc)) from exc
    return rerank.keep(candidates, judgement, threshold, k)


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


def with_report(result: dict, retrieval: augment.Retrieval) -> dict:
    result["rag"] = retrieval.report(result["answer"] if not result["error"] else None)
    return result


def filtered_answer(question: str, candidates: augment.Retrieval, s: "RunSettings") -> dict:
    """Judge, cut, answer - in a row, since each needs the one before."""
    try:
        kept = reranked(question, candidates, s.model, s.threshold, s.k)
    except HTTPException as exc:
        return {"error": exc.detail, "answer": None}
    return with_report(one_answer(bare_agent(s.model, augment.render_excerpts(kept)), question), kept)


def answer_all(question: str, s: "RunSettings") -> dict:
    """The three modes at the same time - the slowest sets the pace, not the sum."""
    candidates = None
    retrieval_error = None
    try:
        candidates = retrieval_for(question, s.index, max(s.candidates, s.k))
    except HTTPException as exc:
        retrieval_error = exc.detail
    with ThreadPoolExecutor(max_workers=3) as pool:
        plain = pool.submit(one_answer, bare_agent(s.model), question)
        if candidates is None:
            failed = {"error": retrieval_error, "answer": None}
            return {"plain": plain.result(), "rag": dict(failed), "rerank": dict(failed)}
        top = candidates.top(s.k)
        rag = pool.submit(one_answer, bare_agent(s.model, augment.render_excerpts(top)), question)
        filtered = pool.submit(filtered_answer, question, candidates, s)
        return {"plain": plain.result(), "rag": with_report(rag.result(), top), "rerank": filtered.result()}


# ------------------------------------------------------------------ requests

class RunSettings(BaseModel):
    index: str = augment.DEFAULT_INDEX
    k: int = Field(default=augment.DEFAULT_K, ge=1, le=augment.MAX_K)                    # sent, at most
    candidates: int = Field(default=augment.DEFAULT_CANDIDATES, ge=1, le=augment.MAX_CANDIDATES)  # before the cut
    threshold: int = Field(default=rerank.DEFAULT_THRESHOLD, ge=0, le=rerank.MAX_SCORE)
    model: str = agent_module.DEFAULT_MODEL


class AskRequest(RunSettings):
    question: str


class QuestionsRequest(BaseModel):
    questions: list[dict] = Field(default_factory=list, max_length=100)


def valid_model(model: str) -> str:
    if model not in agent_module.MODELS:
        raise HTTPException(400, f"Unknown model: {model}")
    return model


def last_rows(qs: list[control.ControlQuestion]) -> list[dict | None]:
    last = read_json(RESULTS_FILE, {})
    return [last.get("rows", {}).get(q.id) for q in qs]


def overview() -> dict:
    current = indexing_api.embedder.name
    qs = questions()
    rows = last_rows(qs)
    return {
        "embedder": {**indexing_api.embedder.describe(), "status": indexing_api.embedder.status()},
        "indexes": [{"name": i["name"], "strategy": i["strategy"], "chunks": i["stats"].get("chunks", 0),
                     "embedder": i["embedder"], "usable": i["embedder"] == current}
                    for i in indexing_api.store.indexes()],
        "defaults": {"index": augment.DEFAULT_INDEX, "k": augment.DEFAULT_K, "model": agent_module.DEFAULT_MODEL,
                     "max_k": augment.MAX_K, "candidates": augment.DEFAULT_CANDIDATES,
                     "max_candidates": augment.MAX_CANDIDATES, "threshold": rerank.DEFAULT_THRESHOLD,
                     "max_score": rerank.MAX_SCORE},
        "models": agent_module.MODELS,
        "questions": [q.as_dict() for q in qs],
        "results": {q.id: r for q, r in zip(qs, rows) if r},
        "summary": control.summarise([r for r in rows if r]),
        "sweep": control.sweep([r for r in rows if r]),
    }


# ----------------------------------------------------------------- endpoints

@router.get("")
def get_overview() -> dict:
    return overview()


@router.get("/sweep")
def get_sweep(candidates: int | None = Query(default=None, ge=1, le=augment.MAX_CANDIDATES),
              k: int | None = Query(default=None, ge=1, le=augment.MAX_K)) -> dict | None:
    return control.sweep([r for r in last_rows(questions()) if r], candidates, k)


@router.put("/questions")
def put_questions(req: QuestionsRequest) -> dict:
    control.save_questions(QUESTIONS_FILE, control.clean_questions(req.questions))
    return overview()


@router.post("/ask")
def ask(req: AskRequest) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Type a question")
    valid_model(req.model)
    return {"question": question, **answer_all(question, req)}


@router.post("/check/{qid}")
def check(qid: str, req: RunSettings) -> dict:
    q = next((q for q in questions() if q.id == qid), None)
    if q is None:
        raise HTTPException(404, f"No control question {qid!r}")
    valid_model(req.model)
    answers = answer_all(q.question, req)
    row = {"id": q.id, "question": q.question, "answerable": q.answerable, "at": time.time(),
           "settings": req.model_dump()}
    for mode in control.MODES:
        result = answers[mode]
        result.pop("request", None)        # the prompt is kept; the full body is the debug panel's
        if not result.get("error"):
            result["keywords"] = control.score_keywords(result["answer"], q.keywords)
        row[mode] = result
    for mode in ("rag", "rerank"):
        if not row[mode].get("error") and q.answerable:
            row[mode]["sources"] = control.score_sources(indexing_api.store, q.sources, row[mode]["rag"])
    if not row["rerank"].get("error"):
        control.mark_expected(indexing_api.store, q.sources if q.answerable else [], row["rerank"]["rag"]["rerank"])
    with results_lock:
        last = read_json(RESULTS_FILE, {})
        last.setdefault("rows", {})[q.id] = row
        write_json(RESULTS_FILE, last)
    ov = overview()
    return {"row": row, "summary": ov["summary"], "sweep": ov["sweep"]}


@router.delete("/results")
def clear_results() -> dict:
    with results_lock:
        write_json(RESULTS_FILE, {"rows": {}})
    return overview()
