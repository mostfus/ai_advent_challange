"""Day 23: the second stage - a model reads the candidates and keeps the ones about the question.

    retrieve(..., k=20)                  day 22's cosine search, asked for more than will be sent
    LLMReranker.judge(question, hits)    one request: every candidate scored 0-10 for this question
    keep(candidates, judgement, t, k)    the ones scored >= t, best first, at most k

Day 22 sent the k nearest chunks whatever they were, and "nearest" had two
faults. The scores were all alike - 0.579, 0.564, 0.564, 0.559 for the first
control question, with the right chunk second and an unrelated function
first - so the order inside the top few was close to arbitrary. And there is
always a nearest chunk: a question the documents cannot answer still went up
with five excerpts, about whatever shared its vocabulary.

**Why a model, and not a threshold on the cosine.** The cosine compares two
vectors that were made apart - the question's and the chunk's - and a
Russian question against English prose lands every chunk in the same narrow
band, the one that answers and the one that only shares words with it alike.
A model that reads the question and the chunk *together* can tell those
apart; that is what a cross-encoder is, and an LLM asked for a number is one
with a prompt for a head.

**One request for all the candidates, not one per candidate.** Twenty
requests would carry the instructions and the question twenty times and
wait on twenty round trips. A judge that sees the candidates side by side
also grades them against each other, which is what a ranking is. The price
is one prompt the size of twenty chunks, and it is reported with every
answer rather than folded into it.

**The scale is anchored** (`SCALE`): a number means the same thing from one
question to the next, so one threshold can serve all of them, and the
anchors say where "on the topic" ends and "answers it" begins - which is the
line a threshold is for.

**A judgement that cannot be read is a failure, not a fallback.** If the
reply is not the JSON asked for, the mode fails with that said, rather than
quietly sending the unfiltered top k: an answer shown as filtered has to
have been. A candidate the reply skipped is scored None and is not kept.

The model call itself is not in here. `LLMReranker` is handed a function
that takes a chat-completions body and returns the response JSON, so the
package still imports nothing from the app around it; `rag_api` hands it
one over the app's DeepSeek client.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from .augment import MAX_K, Retrieval, label, preview

DEFAULT_THRESHOLD = 6
MAX_SCORE = 10

SCALE = (
    "10 - answers the question, or one part of it, outright\n"
    " 7 - holds a substantial part of the answer\n"
    " 4 - on the same subject, but does not answer it\n"
    " 1 - shares words with the question and nothing more\n"
    " 0 - unrelated"
)

SYSTEM_PROMPT = (
    "You grade search results for a question-answering system over one software "
    "project's documents: its README and its Python modules. You are given a question "
    "and numbered candidate excerpts. For every candidate, rate how much its own text "
    "helps answer the question, on this scale:\n\n"
    f"{SCALE}\n\n"
    "Use the whole range, 0 to 10. Judge each candidate by what it says, not by what "
    "the project might say elsewhere. A question can have several parts: a candidate "
    "that fully answers one of them deserves a high score. The question may be in a "
    "different language from the excerpts; that does not matter.\n\n"
    'Reply with one JSON object and nothing else: {"scores": {"1": <0-10>, "2": <0-10>, ...}}, '
    "with an entry for every candidate."
)

# A reply is a score per candidate, ~8 tokens each - the cap is a guard
# against a reply that starts explaining itself, not a budget.
MAX_TOKENS = 600


class RerankError(RuntimeError):
    """The judge could not be asked, or its reply could not be read."""


@dataclass
class Judgement:
    scores: list[float | None]      # one per candidate, in the candidates' order
    model: str
    elapsed_ms: int
    usage: dict | None
    reply: str                      # the judge's words, for the page


class LLMReranker:
    """A chat model as a cross-encoder: the question and the candidates in, a score each out."""

    name = "llm"

    def __init__(self, complete: Callable[[dict], dict], model: str):
        self.complete = complete     # chat-completions body -> response JSON; raises RerankError
        self.model = model

    def request(self, question: str, hits: list[dict]) -> dict:
        candidates = "\n\n".join(f"[{i}] {label(h)}\n{h['text'].strip()}" for i, h in enumerate(hits, 1))
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Question: {question}\n\nCandidates:\n\n{candidates}"},
            ],
            # A grade, not a composition: the same candidates should get the
            # same numbers on the next run, or a threshold means nothing.
            "temperature": 0,
            "response_format": {"type": "json_object"},
            # Thinking is billed as output, and reading twenty excerpts against
            # one question is what the prompt already is.
            "reasoning_effort": "none",
            "max_tokens": MAX_TOKENS,
        }

    def judge(self, question: str, hits: list[dict]) -> Judgement:
        if not hits:
            return Judgement([], self.model, 0, None, "")
        started = time.monotonic()
        response = self.complete(self.request(question, hits))
        elapsed_ms = round((time.monotonic() - started) * 1000)
        message = ((response.get("choices") or [{}])[0] or {}).get("message") or {}
        reply = message.get("content") or ""
        raw = response.get("usage") or {}
        usage = {k: raw.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")} if raw else None
        return Judgement(parse_scores(reply, len(hits)), self.model, elapsed_ms, usage, reply)


def number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), MAX_SCORE))
    if isinstance(value, str) and re.fullmatch(r"\s*\d+(\.\d+)?\s*", value):
        return max(0.0, min(float(value), MAX_SCORE))
    return None


def loads(text: str):
    """The reply as JSON - as it is, or the outermost object or list in it (a fenced block)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    inner = re.search(r"[\[{].*[\]}]", text, re.S)
    if inner:
        try:
            return json.loads(inner.group(0))
        except ValueError:
            pass
    raise RerankError(f"The reranker's reply is not JSON: {text[:160]!r}")


def parse_scores(reply: str, n: int) -> list[float | None]:
    """The judge's reply -> a score per candidate, None where it gave none.

    The prompt asks for `{"scores": {"1": 8, ...}}`; a bare `{"1": 8}`, a list in
    candidate order and a list of `{"n": 1, "score": 8}` are read too, since a
    model that ignores the shape usually still means the same thing. A reply
    that yields no score at all raises `RerankError`.
    """
    text = (reply or "").strip()
    data = loads(text)
    if isinstance(data, dict) and "scores" in data:
        data = data["scores"]
    scores: list[float | None] = [None] * n
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).strip().isdigit() and 1 <= int(key) <= n:
                scores[int(key) - 1] = number(value)
    elif isinstance(data, list):
        for i, item in enumerate(data[:n]):
            if isinstance(item, dict):
                at = item.get("n", item.get("id", i + 1))
                if isinstance(at, (int, str)) and str(at).strip().isdigit() and 1 <= int(at) <= n:
                    scores[int(at) - 1] = number(item.get("score"))
            else:
                scores[i] = number(item)
    if all(s is None for s in scores):
        raise RerankError(f"The reranker's reply has no scores in it: {text[:160]!r}")
    return scores


def keep(candidates: Retrieval, judgement: Judgement, threshold: float, k: int) -> Retrieval:
    """The candidates scored >= threshold, best first, at most k - renumbered [1]..[n].

    Ties go to the better cosine rank. Every candidate stays in the report,
    with its score and where it ended up, so a cut is something the page can
    show and the threshold sweep can replay.
    """
    k = max(1, min(int(k), MAX_K))
    scored = list(zip(candidates.hits, judgement.scores))
    passed = sorted(((h, s) for h, s in scored if s is not None and s >= threshold),
                    key=lambda pair: (-pair[1], pair[0]["rank"]))[:k]
    kept_as = {h["rank"]: n for n, (h, _) in enumerate(passed, 1)}
    hits = [{**h, "rank": n, "cos_rank": h["rank"], "llm": s} for n, (h, s) in enumerate(passed, 1)]
    report = {
        "method": LLMReranker.name, "model": judgement.model,
        "threshold": threshold, "k": k, "candidates": len(candidates.hits), "kept": len(hits),
        "elapsed_ms": judgement.elapsed_ms, "usage": judgement.usage, "reply": judgement.reply,
        "scored": [{"n": h["rank"], "chunk_id": h["chunk_id"], "source": h["source"], "section": h["section"],
                    "score": h["score"], "llm": s, "kept_as": kept_as.get(h["rank"]), "n_words": h["n_words"],
                    "start": h["start"], "end": h["end"], "preview": preview(h)}
                   for h, s in scored],
    }
    return Retrieval(candidates.query, candidates.index, k, hits, candidates.embed_ms, candidates.search_ms,
                     rerank=report)
