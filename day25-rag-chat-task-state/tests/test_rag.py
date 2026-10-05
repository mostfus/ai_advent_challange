"""Days 22-23: the RAG request - retrieval, the second stage, the augmented question, the control check.

    uv run python -m unittest discover -s tests -v

No model and no network. The embedder is day 21's bag of hashed words; the
LLM is a fake that answers from whatever excerpts it was given - it quotes
the first one and cites it - and says it does not know when it was given
none. Asked as the judge (day 23), it scores a candidate 9 when it shares a
word with the question and 1 otherwise. That is enough to exercise the whole
path three ways: what goes into the request, what is cut, what is cited,
what is scored, and what is stored.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_indexing import MARKDOWN, HashEmbedder  # noqa: E402

from agent import KNOWLEDGE_TEMPLATE, Agent, AgentConfig  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from rag import augment, control, rerank  # noqa: E402
from rag.chunkers import make_chunker  # noqa: E402
from rag.evaluation import Expected  # noqa: E402
from rag.index_store import IndexStore  # noqa: E402
from rag.loaders import load_text  # noqa: E402
from rag.pipeline import Corpus, build_index  # noqa: E402


class FakeLLM:
    """Answers from the excerpts if there are any - and cites the first - or admits it cannot.

    As the judge, scores a candidate 9 when it shares a word of four letters
    or more with the question, and 1 when it does not.
    """

    redacted_headers: dict = {}

    def __init__(self):
        self.bodies: list[dict] = []
        self.judge_reply: str | None = None     # set to break the judge on purpose

    @staticmethod
    def judge(prompt: str) -> str:
        question = prompt.split("\n", 1)[0].removeprefix("Question: ").lower()
        words = {w for w in re.findall(r"\w+", question) if len(w) >= 4}
        parts = re.split(r"\n\n(?=\[\d+\] )", prompt.split("Candidates:\n\n", 1)[1])
        scores = {re.match(r"\[(\d+)\]", part).group(1): 9 if any(w in part.lower() for w in words) else 1
                  for part in parts}
        return json.dumps({"scores": scores})

    def is_judge(self, body: dict) -> bool:
        return body["messages"][0]["content"] == rerank.SYSTEM_PROMPT

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(body)
        last = body["messages"][-1]["content"]
        found = re.search(r"\[1\] [^\n]*\n(.*?)\n(?:\n\[2\]|--- End)", last, re.S)
        if self.is_judge(body):
            answer = self.judge_reply if self.judge_reply is not None else self.judge(last)
        else:
            answer = f"From the documents: {' '.join(found.group(1).split())} [1] [9]" if found else "I do not know this project."
        return LLMResponse(request=body, response={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": len(last.split()), "completion_tokens": len(answer.split()), "total_tokens": 1},
        }, url="fake://chat", status_code=200, elapsed_ms=5.0)


def built_store(tmp: str) -> tuple[IndexStore, HashEmbedder]:
    store = IndexStore(Path(tmp) / "index.sqlite")
    embedder = HashEmbedder()
    build_index("structural", Corpus([load_text("guide.md", MARKDOWN)]),
                make_chunker("structural", {"max_words": 150, "min_words": 20}), embedder, store)
    return store, embedder


class AugmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.embedder = built_store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieve_render_and_cite(self):
        r = augment.retrieve(self.store, self.embedder, "structural", "quokka narwhal", k=3)
        self.assertEqual(len(r.hits), 3)
        self.assertEqual(r.hits[0]["section"], "Zebra")
        text = augment.render_excerpts(r)
        self.assertTrue(text.startswith("[1] guide.md › Zebra\n"))
        self.assertIn("\n\n[2] guide.md › ", text)
        # [9] was never sent, so it is not a source; [1, 3] and [2;3] are both citations.
        self.assertEqual(augment.cited("see [3] and [1, 3], also [9] and [2;3]", r), [3, 1, 2])
        report = r.report("only [2]")
        self.assertEqual(report["cited"], [2])
        self.assertEqual([h["cited"] for h in report["hits"]], [False, True, False])
        self.assertEqual(report["words"], sum(h["n_words"] for h in report["hits"]))

    def test_k_is_clamped_and_missing_index_is_refused(self):
        # Fetching goes up to the candidates' bound; sending is held to MAX_K.
        many = augment.retrieve(self.store, self.embedder, "structural", "x", k=99)
        self.assertEqual(many.k, augment.MAX_CANDIDATES)
        self.assertEqual(many.top(99).k, augment.MAX_K)
        self.assertEqual([h["rank"] for h in many.top(2).hits], [1, 2])
        with self.assertRaises(augment.RetrievalError):
            augment.retrieve(self.store, self.embedder, "fixed", "x")

    def test_other_embedder_is_refused(self):
        class Other(HashEmbedder):
            name = "test:other"
        with self.assertRaises(augment.RetrievalError) as caught:
            augment.retrieve(self.store, Other(), "structural", "x")
        self.assertIn("rebuild", str(caught.exception))


class RerankTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.embedder = built_store(self.tmp.name)
        self.candidates = augment.retrieve(self.store, self.embedder, "structural", "quokka narwhal", k=4)

    def tearDown(self):
        self.tmp.cleanup()

    def judgement(self, scores):
        return rerank.Judgement(scores, "m", 12, {"total_tokens": 99}, "{}")

    def test_parse_scores_in_every_shape_a_model_sends(self):
        self.assertEqual(rerank.parse_scores('{"scores": {"1": 8, "2": "3", "3": 12, "9": 5}}', 3), [8, 3, 10])
        self.assertEqual(rerank.parse_scores('{"1": 5}', 2), [5, None])
        self.assertEqual(rerank.parse_scores('{"scores": [7, 2]}', 2), [7, 2])
        self.assertEqual(rerank.parse_scores('[{"n": 2, "score": 9}]', 2), [None, 9])
        self.assertEqual(rerank.parse_scores('```json\n{"scores": {"1": 4}}\n```', 1), [4])
        self.assertEqual(rerank.parse_scores('{"scores": {"1": true, "2": 6}}', 2), [None, 6])
        for broken in ("not json", '{"scores": {}}', '{"verdict": "fine"}'):
            with self.assertRaises(rerank.RerankError):
                rerank.parse_scores(broken, 2)

    def test_keep_cuts_at_the_threshold_orders_by_score_and_renumbers(self):
        n = len(self.candidates.hits)
        self.assertGreaterEqual(n, 4)
        kept = rerank.keep(self.candidates, self.judgement([3, 8, 8, 9]), threshold=6, k=2)
        # 9 first, then the better cosine rank of the two 8s; the third one passed but k is 2.
        self.assertEqual([(h["rank"], h["cos_rank"], h["llm"]) for h in kept.hits], [(1, 4, 9), (2, 2, 8)])
        self.assertTrue(augment.render_excerpts(kept).startswith("[1] "))
        report = kept.report("it is [2]")
        self.assertEqual(report["cited"], [2])
        self.assertEqual((report["hits"][1]["cos_rank"], report["hits"][1]["llm"]), (2, 8))
        rr = report["rerank"]
        self.assertEqual((rr["candidates"], rr["kept"], rr["threshold"], rr["k"]), (4, 2, 6, 2))
        self.assertEqual([c["kept_as"] for c in rr["scored"]], [None, 2, None, 1])
        self.assertEqual([c["llm"] for c in rr["scored"]], [3, 8, 8, 9])

    def test_nothing_kept_is_said_rather_than_sent_bare(self):
        kept = rerank.keep(self.candidates, self.judgement([1, 2, None, 0]), threshold=6, k=5)
        self.assertEqual(kept.hits, [])
        self.assertEqual(augment.render_excerpts(kept), augment.NO_EXCERPTS)
        agent = Agent(FakeLLM(), AgentConfig(model="m"), knowledge=augment.render_excerpts(kept))
        self.assertIn(augment.NO_EXCERPTS, agent.build_messages("q")[-1]["content"])
        self.assertEqual(kept.report()["rerank"]["kept"], 0)

    def test_the_judge_request(self):
        sent = []

        def complete(body):
            sent.append(body)
            return {"choices": [{"message": {"content": '{"scores": {"1": 9, "2": 1, "3": 1, "4": 1}}'}}],
                    "usage": {"prompt_tokens": 400, "completion_tokens": 20, "total_tokens": 420}}

        judgement = rerank.LLMReranker(complete, "deepseek-v4-flash").judge("quokka?", self.candidates.hits)
        self.assertEqual(judgement.scores, [9, 1, 1, 1])
        self.assertEqual(judgement.usage, {"prompt_tokens": 400, "completion_tokens": 20, "total_tokens": 420})
        body = sent[0]
        self.assertEqual((body["temperature"], body["reasoning_effort"]), (0, "none"))
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"][0]["content"], rerank.SYSTEM_PROMPT)
        user = body["messages"][1]["content"]
        self.assertTrue(user.startswith("Question: quokka?\n\nCandidates:\n\n[1] guide.md › Zebra\n"))
        self.assertIn("\n\n[4] ", user)
        # Nothing to judge, nothing asked.
        self.assertEqual(rerank.LLMReranker(complete, "m").judge("q", []).scores, [])
        self.assertEqual(len(sent), 1)


class AgentKnowledgeTest(unittest.TestCase):
    def test_excerpts_are_joined_onto_the_question_and_not_remembered(self):
        llm = FakeLLM()
        agent = Agent(llm, AgentConfig(model="m", system_prompt="sys"), knowledge="[1] a.md › X\nthe answer is 42")
        reply = agent.ask("what is it?")
        sent = llm.bodies[0]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertEqual(sent[-1]["content"], KNOWLEDGE_TEMPLATE.format(
            excerpts="[1] a.md › X\nthe answer is 42", question="what is it?"))
        self.assertIn("[1]", reply.answer)
        # The transcript keeps the question as typed, not the excerpts.
        self.assertEqual(agent.history[-2], {"role": "user", "content": "what is it?"})

    def test_without_knowledge_the_question_goes_as_it_is(self):
        llm = FakeLLM()
        Agent(llm, AgentConfig(model="m")).ask("what is it?")
        self.assertEqual(llm.bodies[0]["messages"][-1]["content"], "what is it?")


class StoredSourcesTest(unittest.TestCase):
    def test_the_sources_ride_on_the_answer(self):
        from store import Message
        rag = {"index": "structural", "k": 5, "cited": [1], "hits": [{"n": 1, "source": "a.md"}]}
        back = Message.from_dict(Message(role="assistant", content="x [1]", rag=rag).to_dict())
        self.assertEqual(back.rag, rag)
        self.assertNotIn("rag", Message(role="assistant", content="x").to_dict())


class ControlTest(unittest.TestCase):
    def test_keywords_fold_and_verdicts(self):
        lines = control.keyword_lines(["bge-m3", "многоязыч | multilingual", ["1024"], "  ", "__"])
        self.assertEqual(lines, [["bge-m3"], ["многоязыч", "multilingual"], ["1024"], ["__"]])
        full = control.score_keywords("Модель BGE m3 — многоязычная, 1024 измерения, weather__forecast", lines)
        self.assertEqual((full["verdict"], full["hit"], full["of"]), ("full", 4, 4))
        self.assertEqual(full["lines"][0]["found"], "bge-m3")
        partial = control.score_keywords("It is a Multilingual model with 1024 dims", lines)
        self.assertEqual(partial["verdict"], "partial")
        self.assertEqual(control.score_keywords("no idea", lines)["verdict"], "miss")
        self.assertIsNone(control.score_keywords("anything", [])["verdict"])
        # ё is е; dashes and spaces are one.
        self.assertEqual(control.score_keywords("Ещё MCP-сервер", control.keyword_lines(["еще", "mcp сервер"]))["hit"], 2)

    def test_clean_questions(self):
        qs = control.clean_questions([
            {"question": "  Q one?  ", "keywords": ["a | b"], "sources": [{"source": "x.md", "section": "S"}, {"source": " "}]},
            {"question": ""},
            {"id": "q-one", "question": "Q two"},
        ])
        self.assertEqual(len(qs), 2)
        self.assertTrue(qs[0].answerable)
        self.assertFalse(control.clean_questions([{"question": "q", "answerable": False}])[0].answerable)
        self.assertEqual(qs[0].keywords, [["a", "b"]])
        self.assertEqual(qs[0].sources, [Expected("x.md", "S")])
        self.assertNotEqual(qs[0].id, qs[1].id)

    def test_sweep_replays_the_stored_scores(self):
        def row(answerable, scored, checked=True):
            return {"answerable": answerable, "rerank": {"rag": {"rerank": {
                "threshold": 6, "k": 5, "expected_checked": checked, "scored": scored}}}}
        rows = [
            row(True, [{"n": 1, "llm": 3, "expected": False}, {"n": 2, "llm": 9, "expected": True},
                       {"n": 3, "llm": None, "expected": False}]),
            row(False, [{"n": 1, "llm": 2}, {"n": 2, "llm": 0}], checked=False),
        ]
        w = control.sweep(rows)
        self.assertEqual(w["used"], [6])
        at = {x["threshold"]: x for x in w["thresholds"]}
        self.assertEqual(len(at), 11)
        self.assertEqual((at[0]["sent"], at[0]["found"], at[0]["precision"], at[0]["emptied"]), (2, 1, 0.5, 0))
        self.assertEqual((at[6]["sent"], at[6]["found"], at[6]["precision"], at[6]["emptied"]), (1, 1, 1.0, 1))
        self.assertEqual((at[10]["sent"], at[10]["found"], at[10]["precision"], at[10]["emptied"]), (0, 0, None, 1))
        # The day-22 cut, for reference: the first k by cosine, whatever they are.
        self.assertEqual((w["cosine"]["sent"], w["cosine"]["found"], w["cosine"]["emptied"]), (3, 1, 0))
        self.assertEqual((w["candidates"], w["k"], w["max_candidates"], w["as_run"]), (3, 5, 3, True))
        # Fewer candidates: the 9 at cosine rank 2 is never looked at.
        one = control.sweep(rows, candidates=1)
        self.assertEqual((one["thresholds"][0]["sent"], one["thresholds"][0]["found"], one["as_run"]), (1, 0, False))
        # More than were judged is as many as were judged; k caps what is sent.
        capped = control.sweep(rows, candidates=99, k=1)
        self.assertEqual((capped["candidates"], capped["k"]), (3, 1))
        self.assertEqual((capped["thresholds"][0]["sent"], capped["thresholds"][0]["precision"]), (1, 1.0))
        self.assertIsNone(control.sweep([{"plain": {}}]))

    def test_sources_by_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, embedder = built_store(tmp)
            r = augment.retrieve(store, embedder, "structural", "quokka narwhal", k=3)
            ok = control.score_sources(store, [Expected("guide.md", "Zebra")], r.report("it is [1]"))
            self.assertEqual((ok["retrieved"], ok["first_rank"], ok["cited"]), (True, 1, True))
            self.assertEqual((ok["sent"], ok["precision"]), (3, 0.333))
            uncited = control.score_sources(store, [Expected("guide.md", "Zebra")], r.report("it is [2]"))
            self.assertEqual((uncited["retrieved"], uncited["cited"], uncited["cited_any"]), (True, False, True))
            gone = control.score_sources(store, [Expected("guide.md", "No such heading")], r.report(""))
            self.assertFalse(gone["checked"])
            self.assertFalse(control.score_sources(store, [], r.report(""))["checked"])


class ApiTest(unittest.TestCase):
    """The tab's endpoints on a bare app, over a small index and the fake LLM."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["INDEX_DIR"] = str(Path(cls.tmp.name) / "index")
        os.environ["RAG_DIR"] = str(Path(cls.tmp.name) / "rag")
        import importlib

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import indexing_api
        import rag_api
        cls.ix = importlib.reload(indexing_api)
        cls.api = importlib.reload(rag_api)
        cls.ix.embedder = HashEmbedder()
        cls.llm = FakeLLM()
        cls.api.client = cls.llm
        app = FastAPI()
        app.include_router(cls.ix.router)
        app.include_router(cls.api.router)
        cls.client = TestClient(app)
        cls.client.put("/api/index/corpus", json={"patterns": ["no-such-file.md"]})
        cls.client.post("/api/index/uploads", json={"name": "guide.md", "content": MARKDOWN})

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("INDEX_DIR", None)
        os.environ.pop("RAG_DIR", None)
        cls.tmp.cleanup()

    def build(self):
        loaded = self.ix.corpus()
        build_index("structural", loaded, make_chunker("structural", {"max_words": 150, "min_words": 20}),
                    self.ix.embedder, self.ix.store)

    def test_a_seeded_with_thirteen(self):
        ov = self.client.get("/api/rag").json()
        self.assertEqual(len(ov["questions"]), 13)
        answerable = [q for q in ov["questions"] if q["answerable"]]
        offtopic = [q for q in ov["questions"] if not q["answerable"]]
        self.assertEqual((len(answerable), len(offtopic)), (10, 3))
        self.assertTrue(all(q["keywords"] and q["sources"] for q in answerable))
        self.assertTrue(all(len(q["keywords"]) == 1 and not q["sources"] for q in offtopic))
        self.assertEqual((ov["defaults"]["candidates"], ov["defaults"]["threshold"]),
                         (augment.DEFAULT_CANDIDATES, rerank.DEFAULT_THRESHOLD))

    def test_without_an_index_the_plain_mode_still_answers(self):
        self.ix.store.delete_index(None)
        res = self.client.post("/api/rag/ask", json={"question": "quokka narwhal"}).json()
        self.assertEqual(res["plain"]["answer"], "I do not know this project.")
        self.assertIn("no 'structural' index", res["rag"]["error"])
        self.assertIn("no 'structural' index", res["rerank"]["error"])

    def answer_bodies(self, since: int) -> list[dict]:
        return [b for b in self.llm.bodies[since:] if not self.llm.is_judge(b)]

    def test_check_three_ways(self):
        self.build()
        c = self.client
        c.put("/api/rag/questions", json={"questions": [
            {"id": "zebra", "question": "quokka narwhal", "expected": "the Zebra section",
             "keywords": ["quokka", "from the documents"], "sources": [{"source": "uploads/guide.md", "section": "Zebra"}]},
            {"id": "nowhere", "question": "xyzzy plugh", "keywords": ["do not know"], "answerable": False},
        ]})
        self.assertEqual(c.post("/api/rag/check/nope", json={}).status_code, 404)
        self.assertEqual(c.post("/api/rag/check/zebra", json={"model": "gpt-9"}).status_code, 400)
        self.assertEqual(c.post("/api/rag/check/zebra", json={"threshold": 11}).status_code, 422)

        before = len(self.llm.bodies)
        res = c.post("/api/rag/check/zebra", json={"index": "structural", "k": 3, "candidates": 5, "threshold": 6}).json()
        row = res["row"]
        self.assertEqual(row["plain"]["keywords"]["verdict"], "miss")
        # Day 22's cut: the first three, whatever they are.
        self.assertEqual(row["rag"]["keywords"]["verdict"], "full")
        self.assertEqual(len(row["rag"]["rag"]["hits"]), 3)
        self.assertEqual(row["rag"]["rag"]["cited"], [1])          # [9] was not sent, so it is dropped
        self.assertEqual(row["rag"]["sources"]["first_rank"], 1)
        self.assertEqual(row["rag"]["sources"]["precision"], 0.333)
        # The judge's cut: only the chunk about the question survives.
        rr = row["rerank"]["rag"]["rerank"]
        self.assertEqual((rr["candidates"], rr["kept"], rr["threshold"], rr["k"]), (5, 1, 6, 3))
        self.assertEqual([h["section"] for h in row["rerank"]["rag"]["hits"]], ["Zebra"])
        self.assertEqual(row["rerank"]["keywords"]["verdict"], "full")
        self.assertEqual((row["rerank"]["sources"]["first_rank"], row["rerank"]["sources"]["precision"]), (1, 1.0))
        self.assertTrue(rr["expected_checked"])
        self.assertEqual([c["expected"] for c in rr["scored"]], [c["kept_as"] is not None for c in rr["scored"]])
        self.assertIn("--- Excerpts from the project's documents", row["rerank"]["prompt"])
        self.assertNotIn("request", row["rerank"])

        # One search, one judge, three answers - and the three answer requests
        # differ in their last message only.
        judges = [b for b in self.llm.bodies[before:] if self.llm.is_judge(b)]
        self.assertEqual(len(judges), 1)
        self.assertEqual(judges[0]["messages"][1]["content"].count("\n\n["), 5)
        bodies = self.answer_bodies(before)
        self.assertEqual(len(bodies), 3)
        for body in bodies[1:]:
            self.assertEqual({k: v for k, v in body.items() if k != "messages"},
                             {k: v for k, v in bodies[0].items() if k != "messages"})
            self.assertEqual(body["messages"][:-1], bodies[0]["messages"][:-1])

        # A question the documents do not answer: top k sends three anyway,
        # the judge sends none and says so.
        off = c.post("/api/rag/check/nowhere", json={"k": 3, "candidates": 5, "threshold": 6}).json()["row"]
        self.assertFalse(off["answerable"])
        self.assertEqual(len(off["rag"]["rag"]["hits"]), 3)
        self.assertEqual(off["rerank"]["rag"]["hits"], [])
        self.assertIn(augment.NO_EXCERPTS, off["rerank"]["prompt"])
        self.assertEqual(off["rerank"]["keywords"]["verdict"], "full")
        self.assertNotIn("sources", off["rerank"])
        self.assertFalse(off["rerank"]["rag"]["rerank"]["expected_checked"])

        summary = c.get("/api/rag").json()["summary"]
        plain, top, filtered = summary["plain"], summary["rag"], summary["rerank"]
        self.assertEqual((plain["miss"], top["full"], filtered["full"]), (1, 1, 1))
        self.assertEqual((top["retrieved"], filtered["retrieved"]), (1, 1))
        self.assertEqual((top["excerpts"], filtered["excerpts"], filtered["candidates"]), (3, 1, 5))
        self.assertEqual((top["precision"], filtered["precision"]), (0.333, 1.0))
        self.assertEqual((plain["refused"], top["refused"], filtered["refused"]), (1, 0, 1))
        self.assertEqual((top["emptied"], filtered["emptied"]), (0, 1))
        self.assertIsNone(top["rerank_tokens"])
        self.assertGreater(filtered["rerank_tokens"], 0)
        self.assertGreater(top["prompt_tokens"], plain["prompt_tokens"])

        sweep = c.get("/api/rag").json()["sweep"]
        self.assertEqual(sweep["used"], [6])
        at6 = sweep["thresholds"][6]
        self.assertEqual((at6["sent"], at6["found"], at6["emptied"], at6["offtopic"]), (1, 1, 1, 1))
        self.assertEqual((sweep["cosine"]["sent"], sweep["cosine"]["emptied"]), (3, 0))
        replay = c.get("/api/rag/sweep", params={"candidates": 1, "k": 1}).json()
        self.assertEqual((replay["candidates"], replay["k"], replay["as_run"]), (1, 1, False))
        self.assertEqual(c.get("/api/rag/sweep", params={"k": 99}).status_code, 422)

        # Kept for the next page load, and forgotten on request.
        self.assertIn("zebra", c.get("/api/rag").json()["results"])
        self.assertEqual(c.delete("/api/rag/results").json()["results"], {})

    def test_an_unreadable_judgement_fails_the_reranked_mode_only(self):
        self.build()
        self.llm.judge_reply = "I think the first one is good"
        try:
            res = self.client.post("/api/rag/ask", json={"question": "quokka narwhal", "candidates": 5}).json()
        finally:
            self.llm.judge_reply = None
        self.assertIn("not JSON", res["rerank"]["error"])
        self.assertIsNone(res["rerank"]["answer"])
        self.assertIn("[1]", res["rag"]["answer"])
        self.assertEqual(res["plain"]["answer"], "I do not know this project.")


if __name__ == "__main__":
    unittest.main()
