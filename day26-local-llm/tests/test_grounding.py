"""Day 24: grounded answers - sources, quotes looked up in their excerpts, and "I don't know".

    uv run python -m unittest discover -s tests -v

No model and no network. The module's own functions are checked on
hand-written replies; the chat is driven end to end over a bare copy of the
app, with day 21's bag-of-words embedder and a fake model that answers in
the JSON the template asks for - quoting the first sentence of excerpt [1],
or a sentence that is in no excerpt when told to, or "I don't know" when it
was given no excerpts.
"""

from __future__ import annotations

import importlib
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
from test_rag import FakeLLM, built_store  # noqa: E402

from agent import Agent, AgentConfig  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from rag import augment, grounding, rerank  # noqa: E402
from rag.chunkers import make_chunker  # noqa: E402
from rag.pipeline import build_index  # noqa: E402


class LocateTest(unittest.TestCase):
    TEXT = ("# Day 22. The excerpts the index returned for this question, and what to do\n"
            "# with them. Not a system block like **every prefix** above: it is `joined` onto\n"
            "# the question itself — so the last message reads “here is”.")

    def found(self, quote):
        return [self.TEXT[s:e] for s, e in grounding.locate(quote, self.TEXT)]

    def test_across_lines_comment_marks_markdown_and_typography(self):
        self.assertEqual(self.found("the excerpts the index returned for this question, and what to do with them."),
                         ["The excerpts the index returned for this question, and what to do\n# with them"])
        self.assertEqual(self.found("Not a system block like every prefix above"),
                         ["Not a system block like **every prefix** above"])
        self.assertEqual(len(self.found('it is joined onto the question itself - so the last message reads "here is"')), 1)

    def test_an_ellipsis_is_every_part_in_order(self):
        self.assertEqual(self.found("The excerpts ... joined onto the question"),
                         ["The excerpts", "joined` onto\n# the question"])
        self.assertEqual(self.found("joined onto ... The excerpts"), [])

    def test_a_quote_that_is_not_there(self):
        self.assertEqual(self.found("the excerpts are translated first"), [])
        self.assertEqual(self.found("  ...  "), [])
        self.assertEqual(grounding.locate("anything", ""), [])


class ParseTest(unittest.TestCase):
    def test_the_shape_asked_for_and_the_shapes_a_model_bends_it_into(self):
        g = grounding.parse(json.dumps({"status": "answered", "answer": "It is 42 [1].",
                                        "sources": ["[1]", 2, {"n": 3}, 2, "x"],
                                        "quotes": [{"n": "1", "quote": "the answer is 42"}, {"n": 2, "text": "b"},
                                                   {"n": 3, "quote": ""}, "loose"],
                                        "clarification": ""}))
        self.assertEqual((g.status, g.answer, g.sources), ("answered", "It is 42 [1].", [1, 2, 3]))
        self.assertEqual(g.quotes, [{"n": 1, "quote": "the answer is 42"}, {"n": 2, "quote": "b"}])
        self.assertIsNone(g.error)
        self.assertEqual(g.text, "It is 42 [1].")

    def test_unknown_carries_its_question_back(self):
        g = grounding.parse('```json\n{"status": "Unknown", "answer": "Не знаю.", "clarification": "Что именно?"}\n```')
        self.assertEqual(g.status, "unknown")
        self.assertEqual(g.text, "Не знаю.\n\nЧто именно?")
        # A question already in the answer is not said twice.
        self.assertEqual(grounding.parse('{"status": "unknown", "answer": "Не знаю. Что именно?", '
                                         '"clarification": "Что именно?"}').text, "Не знаю. Что именно?")

    def test_not_json_is_kept_as_the_answer_and_said(self):
        g = grounding.parse("Plain words [1].")
        self.assertEqual((g.status, g.answer, g.sources, g.quotes), ("answered", "Plain words [1].", [], []))
        self.assertIn("not the JSON", g.error)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.embedder = built_store(self.tmp.name)
        self.r = augment.retrieve(self.store, self.embedder, "structural", "quokka narwhal", k=3)
        self.zebra = "The zebra section mentions quokka and narwhal"

    def tearDown(self):
        self.tmp.cleanup()

    def g(self, **kw):
        return grounding.Grounded(**{"status": "answered", "answer": "Quokka and narwhal [1].", **kw})

    def test_a_grounded_answer_has_no_problems_and_its_sources_resolved(self):
        rep = grounding.check(self.g(sources=[1], quotes=[{"n": 1, "quote": self.zebra}]), self.r)
        self.assertEqual(rep["problems"], [])
        self.assertEqual(rep["status"], "answered")
        self.assertEqual(rep["sources"][0]["chunk_id"], self.r.hits[0]["chunk_id"])
        self.assertEqual((rep["sources"][0]["source"], rep["sources"][0]["section"]), ("guide.md", "Zebra"))
        self.assertTrue(rep["quotes"][0]["found"])
        start, end = rep["quotes"][0]["spans"][0]
        self.assertEqual(self.r.hits[0]["text"][start:end], self.zebra)

    def test_the_sources_are_what_was_named_quoted_or_cited_and_was_sent(self):
        rep = grounding.check(self.g(answer="See [3].", sources=[9], quotes=[{"n": 2, "quote": self.zebra}]), self.r)
        # [9] was never sent; the quote from [2] is not in [2]; [3] is cited inline.
        self.assertEqual([s["n"] for s in rep["sources"]], [3])
        self.assertTrue(any("[9] was never sent" in p for p in rep["problems"]))
        self.assertTrue(any("not in excerpt [2]" in p for p in rep["problems"]))
        self.assertFalse(rep["quotes"][0]["found"])

    def test_answered_without_evidence(self):
        rep = grounding.check(self.g(answer="Quokka.", sources=[], quotes=[]), self.r)
        self.assertTrue(any(p.startswith("No sources") for p in rep["problems"]))
        self.assertTrue(any(p.startswith("No quotes") for p in rep["problems"]))
        rep = grounding.check(self.g(sources=[1, 2], quotes=[{"n": 1, "quote": self.zebra}]), self.r)
        self.assertEqual(rep["problems"], ["These sources have no quote found in them: [2]."])

    def test_unknown_needs_a_question_back_and_nothing_else(self):
        ok = grounding.check(grounding.Grounded("unknown", "Не знаю.", clarification="Что именно?"), self.r)
        self.assertEqual((ok["problems"], ok["sources"], ok["clarification"]), ([], [], "Что именно?"))
        bare = grounding.check(grounding.Grounded("unknown", "Не знаю."), self.r)
        self.assertTrue(any("clarifying question" in p for p in bare["problems"]))

    def test_the_gate_is_codes_decision(self):
        empty = augment.Retrieval("q", "structural", 5, [], rerank={"threshold": 6, "candidates": 3, "scored": [
            {"n": 1, "source": "a.md", "section": "X", "llm": 4.0},
            {"n": 2, "source": "b.py", "section": "", "llm": None},
            {"n": 3, "source": "c.md", "section": "Y", "llm": 1.0}]})
        gate = grounding.gate(empty)
        self.assertEqual((gate["best"], gate["threshold"], [c["source"] for c in gate["nearest"]]),
                         (4.0, 6, ["a.md", "c.md"]))
        self.assertIn("the best scored 4/10, and an answer needs 6/10", grounding.render_gate(gate))
        self.assertIn("- a.md › X (4/10)", grounding.render_gate(gate))
        rep = grounding.check(self.g(sources=[], quotes=[]), empty, gate)
        self.assertEqual(rep["status"], "unknown")
        self.assertTrue(any("must be" in p for p in rep["problems"]))
        # A retrieval that kept something, or was never reranked, has no gate.
        self.assertIsNone(grounding.gate(self.r))
        self.assertIsNone(grounding.gate(augment.Retrieval("q", "structural", 5, [])))
        # Unrelated (0) is never named, and a section is named once.
        flat = augment.Retrieval("q", "structural", 5, [], rerank={"threshold": 6, "candidates": 3, "scored": [
            {"n": 1, "source": "a.md", "section": "X", "llm": 0.0},
            {"n": 2, "source": "b.md", "section": "Y", "llm": 1.0},
            {"n": 3, "source": "b.md", "section": "Y", "llm": 1.0}]})
        self.assertEqual([c["source"] for c in grounding.gate(flat)["nearest"]], ["b.md"])
        none = augment.Retrieval("q", "structural", 5, [], rerank={"threshold": 6, "candidates": 1, "scored": [
            {"n": 1, "source": "a.md", "section": "X", "llm": 0.0}]})
        self.assertEqual(grounding.gate(none)["nearest"], [])
        self.assertIn("None of them was even on the subject", grounding.render_gate(grounding.gate(none)))

    def test_the_correction_survives_format_and_the_report_carries_the_texts(self):
        template = grounding.with_correction(grounding.TEMPLATE, ['This quote is not in excerpt [1]: "{x}"'])
        prompt = template.format(excerpts="[1] a", question="Q?")
        self.assertIn('- This quote is not in excerpt [1]: "{x}"', prompt)
        self.assertTrue(prompt.rstrip().endswith("with these fixed."))
        self.assertIn('{"status": "answered" or "unknown",', grounding.TEMPLATE.format(excerpts="", question=""))
        rep = grounding.check(self.g(sources=[1], quotes=[{"n": 1, "quote": self.zebra}]), self.r)
        out = grounding.report(self.r, "Quokka [1].", rep)
        self.assertEqual(out["hits"][0]["text"], self.r.hits[0]["text"])
        self.assertEqual(out["cited"], [1])
        self.assertIs(out["grounding"], rep)

    def test_the_template_goes_into_the_question(self):
        llm = FakeLLM()
        Agent(llm, AgentConfig(model="m"), knowledge="[1] a.md\nthe answer is 42",
              knowledge_template=grounding.TEMPLATE).ask("what is it?")
        sent = llm.bodies[0]["messages"][-1]["content"]
        self.assertTrue(sent.startswith("--- Excerpts from the project's documents"))
        self.assertIn("[1] a.md\nthe answer is 42", sent)
        self.assertTrue(sent.endswith("Question: what is it?"))


class GroundedLLM(FakeLLM):
    """The chat's model: JSON with a quote from excerpt [1], or "I don't know" when given none.

    `fabricate` makes the next N answers quote a sentence that is in no
    excerpt - the hallucination the server has to catch.
    """

    def __init__(self):
        super().__init__()
        self.fabricate = 0

    def complete(self, body: dict) -> LLMResponse:
        last = body["messages"][-1]["content"] if body.get("messages") else ""
        if self.is_judge(body):
            return super().complete(body)
        self.bodies.append(body)
        if "--- What the project's documents hold" in last:
            answer = json.dumps({"status": "unknown", "answer": "Не знаю: в документах проекта этого нет.",
                                 "sources": [], "quotes": [], "clarification": "О какой части проекта вопрос?"})
        elif "--- Excerpts from the project's documents" in last:
            found = re.search(r"\[1\] [^\n]*\n(.*?)\n(?:\n\[\d+\] |--- End)", last, re.S)
            sentence = re.split(r"(?<=[.!?])\s", " ".join(found.group(1).split()))[0]
            quote = sentence
            if self.fabricate:
                self.fabricate -= 1
                quote = "The zebra section was translated from Swahili."
            answer = json.dumps({"status": "answered", "answer": "**Quokka** and narwhal [1].",
                                 "sources": [1], "quotes": [{"n": 1, "quote": quote}], "clarification": ""})
        else:
            answer = "{}"        # the side requests (facts, proposals) get nothing to file
        return LLMResponse(request=body, response={
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }, url="fake://chat", status_code=200, elapsed_ms=5.0)


class ChatGroundingTest(unittest.TestCase):
    """POST /api/chat with RAG on, over a bare copy of the app in temp directories."""

    ENV = ("CHAT_STORE_DIR", "CHAT_TASKS_DIR", "CHAT_MEMORY_DIR", "CHAT_PERSONALITY_DIR",
           "CHAT_INVARIANTS_DIR", "CHAT_MCP_DIR", "CHAT_DIGESTS_DIR", "INDEX_DIR", "RAG_DIR")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = {k: os.environ.get(k) for k in cls.ENV + ("DEEPSEEK_API_KEY", "MCP_AUTOSTART", "DIGEST_POLL_SECONDS")}
        for k in cls.ENV:
            os.environ[k] = str(Path(cls.tmp.name) / k.lower())
        os.environ.update({"DEEPSEEK_API_KEY": "test-dummy-key", "MCP_AUTOSTART": "0", "DIGEST_POLL_SECONDS": "0"})
        from fastapi.testclient import TestClient

        import indexing_api
        import rag_api
        importlib.reload(indexing_api)
        importlib.reload(rag_api)
        import server
        cls.server = importlib.reload(server)
        cls.llm = GroundedLLM()
        cls.server.llm = cls.llm
        rag_api.client = cls.llm
        indexing_api.embedder = HashEmbedder()
        from rag.pipeline import Corpus
        from rag.loaders import load_text
        build_index("structural", Corpus([load_text("guide.md", MARKDOWN)]),
                    make_chunker("structural", {"max_words": 150, "min_words": 20}),
                    indexing_api.embedder, indexing_api.store)
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def ask(self, message: str, conversation: str) -> dict:
        res = self.client.post("/api/chat", json={
            "message": message, "conversation_id": conversation,
            "settings": {"rag": True, "rag_rerank": True, "rag_candidates": 5, "rag_threshold": 6, "rag_k": 3}})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def answers(self, since: int) -> list[dict]:
        return [b for b in self.llm.bodies[since:] if not self.llm.is_judge(b)
                and "--- " in (b["messages"][-1]["content"] if b.get("messages") else "")]

    def test_an_answer_comes_back_with_its_sources_and_quotes(self):
        since = len(self.llm.bodies)
        body = self.ask("quokka narwhal", "chat-grounded-0001")
        sent = self.answers(since)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["response_format"], {"type": "json_object"})
        # The bubble gets the answer, not the JSON around it - and so does the transcript.
        self.assertEqual(body["answer"], "**Quokka** and narwhal [1].")
        g = body["rag"]["grounding"]
        self.assertEqual((g["status"], g["problems"]), ("answered", []))
        self.assertEqual(g["sources"][0]["source"], "guide.md")
        self.assertTrue(g["quotes"][0]["found"] and g["quotes"][0]["spans"])
        self.assertIn("quokka", body["rag"]["hits"][0]["text"])
        stored = self.client.get("/api/conversations/chat-grounded-0001").json()
        last = stored["messages"][-1]
        self.assertEqual(last["content"], "**Quokka** and narwhal [1].")
        self.assertEqual(last["rag"]["grounding"]["status"], "answered")

    def test_a_quote_that_is_not_in_its_excerpt_is_asked_for_again_once(self):
        self.llm.fabricate = 1
        since = len(self.llm.bodies)
        body = self.ask("quokka narwhal zebra", "chat-grounded-0002")
        sent = self.answers(since)
        self.assertEqual(len(sent), 2)
        self.assertNotIn("was rejected", sent[0]["messages"][-1]["content"])
        self.assertIn("Your previous reply to this question was rejected", sent[1]["messages"][-1]["content"])
        self.assertIn("Swahili", sent[1]["messages"][-1]["content"])
        g = body["rag"]["grounding"]
        self.assertEqual(g["problems"], [])
        self.assertTrue(any("not in excerpt [1]" in p for p in g["retried"]["problems"]))

    def test_never_twice_and_what_is_still_wrong_is_shown(self):
        self.llm.fabricate = 2
        since = len(self.llm.bodies)
        g = self.ask("quokka narwhal", "chat-grounded-0003")["rag"]["grounding"]
        self.assertEqual(len(self.answers(since)), 2)
        self.assertTrue(g["retried"] and any("not in excerpt [1]" in p for p in g["problems"]))
        self.assertFalse(g["quotes"][0]["found"])
        self.llm.fabricate = 0

    def test_below_the_threshold_it_does_not_know_and_asks(self):
        since = len(self.llm.bodies)
        body = self.ask("kubernetes replicas autoscaling", "chat-grounded-0004")
        sent = self.answers(since)
        self.assertEqual(len(sent), 1)
        prompt = sent[0]["messages"][-1]["content"]
        self.assertIn("Do not answer the question below", prompt)
        self.assertIn("an answer needs 6/10", prompt)
        self.assertNotIn("--- Excerpts from", prompt)
        g = body["rag"]["grounding"]
        self.assertEqual((g["status"], g["problems"], g["sources"], g["quotes"]), ("unknown", [], [], []))
        self.assertEqual((g["gate"]["threshold"], g["gate"]["best"]), (6, 1.0))
        self.assertEqual(g["clarification"], "О какой части проекта вопрос?")
        self.assertEqual(body["answer"], "Не знаю: в документах проекта этого нет.\n\nО какой части проекта вопрос?")
        self.assertEqual(body["rag"]["hits"], [])


if __name__ == "__main__":
    unittest.main()
