"""Day 22: the first RAG request - retrieval, the augmented question, the control check.

    uv run python -m unittest discover -s tests -v

No model and no network. The embedder is day 21's bag of hashed words; the
LLM is a fake that answers from whatever excerpts it was given - it quotes
the first one and cites it - and says it does not know when it was given
none. That is enough to exercise the whole path both ways: what goes into the
request, what is cited, what is scored, and what is stored.
"""

from __future__ import annotations

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
from rag import augment, control  # noqa: E402
from rag.chunkers import make_chunker  # noqa: E402
from rag.evaluation import Expected  # noqa: E402
from rag.index_store import IndexStore  # noqa: E402
from rag.loaders import load_text  # noqa: E402
from rag.pipeline import Corpus, build_index  # noqa: E402


class FakeLLM:
    """Answers from the excerpts if there are any - and cites the first - or admits it cannot."""

    redacted_headers: dict = {}

    def __init__(self):
        self.bodies: list[dict] = []

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(body)
        last = body["messages"][-1]["content"]
        found = re.search(r"\[1\] [^\n]*\n(.*?)\n(?:\n\[2\]|--- End)", last, re.S)
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
        self.assertEqual(augment.retrieve(self.store, self.embedder, "structural", "x", k=99).k, augment.MAX_K)
        with self.assertRaises(augment.RetrievalError):
            augment.retrieve(self.store, self.embedder, "fixed", "x")

    def test_other_embedder_is_refused(self):
        class Other(HashEmbedder):
            name = "test:other"
        with self.assertRaises(augment.RetrievalError) as caught:
            augment.retrieve(self.store, Other(), "structural", "x")
        self.assertIn("rebuild", str(caught.exception))


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
        self.assertEqual(qs[0].keywords, [["a", "b"]])
        self.assertEqual(qs[0].sources, [Expected("x.md", "S")])
        self.assertNotEqual(qs[0].id, qs[1].id)

    def test_sources_by_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, embedder = built_store(tmp)
            r = augment.retrieve(store, embedder, "structural", "quokka narwhal", k=3)
            ok = control.score_sources(store, [Expected("guide.md", "Zebra")], r.report("it is [1]"))
            self.assertEqual((ok["retrieved"], ok["first_rank"], ok["cited"]), (True, 1, True))
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

    def test_a_seeded_with_ten(self):
        ov = self.client.get("/api/rag").json()
        self.assertEqual(len(ov["questions"]), 10)
        self.assertTrue(all(q["keywords"] and q["sources"] for q in ov["questions"]))

    def test_without_an_index_the_plain_mode_still_answers(self):
        self.ix.store.delete_index(None)
        res = self.client.post("/api/rag/ask", json={"question": "quokka narwhal"}).json()
        self.assertEqual(res["plain"]["answer"], "I do not know this project.")
        self.assertIn("no 'structural' index", res["rag"]["error"])

    def test_check_both_ways(self):
        self.build()
        c = self.client
        c.put("/api/rag/questions", json={"questions": [
            {"id": "zebra", "question": "quokka narwhal", "expected": "the Zebra section",
             "keywords": ["quokka", "from the documents"], "sources": [{"source": "uploads/guide.md", "section": "Zebra"}]},
            {"id": "other", "question": "anything at all", "keywords": ["xyzzy"]},
        ]})
        self.assertEqual(c.post("/api/rag/check/nope", json={}).status_code, 404)
        self.assertEqual(c.post("/api/rag/check/zebra", json={"model": "gpt-9"}).status_code, 400)

        res = c.post("/api/rag/check/zebra", json={"index": "structural", "k": 3}).json()
        row = res["row"]
        self.assertEqual(row["plain"]["keywords"]["verdict"], "miss")
        self.assertEqual(row["rag"]["keywords"]["verdict"], "full")
        self.assertEqual(row["rag"]["rag"]["cited"], [1])          # [9] was not sent, so it is dropped
        self.assertEqual(row["rag"]["sources"]["first_rank"], 1)
        self.assertTrue(row["rag"]["sources"]["cited"])
        self.assertIn("--- Excerpts from the project's documents", row["rag"]["prompt"])
        self.assertEqual(row["plain"]["prompt"], "quokka narwhal")
        self.assertNotIn("request", row["rag"])
        # Same model, same system prompt both ways - the excerpts are the only difference.
        plain_body, rag_body = sorted(self.llm.bodies[-2:], key=lambda b: len(b["messages"][-1]["content"]))
        self.assertEqual({k: v for k, v in plain_body.items() if k != "messages"},
                         {k: v for k, v in rag_body.items() if k != "messages"})
        self.assertEqual(plain_body["messages"][:-1], rag_body["messages"][:-1])

        summary = res["summary"]
        self.assertEqual((summary["plain"]["miss"], summary["rag"]["full"]), (1, 1))
        self.assertEqual((summary["rag"]["retrieved"], summary["rag"]["cited"]), (1, 1))
        self.assertGreater(summary["rag"]["prompt_tokens"], summary["plain"]["prompt_tokens"])

        # Kept for the next page load, and forgotten on request.
        self.assertIn("zebra", c.get("/api/rag").json()["results"])
        self.assertEqual(c.delete("/api/rag/results").json()["results"], {})


if __name__ == "__main__":
    unittest.main()
