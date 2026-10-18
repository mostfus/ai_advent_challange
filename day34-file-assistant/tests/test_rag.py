"""Days 22-23: the RAG request - retrieval, the second stage, the augmented question.

    uv run python -m unittest discover -s tests -v

No model and no network. The embedder is day 21's bag of hashed words; the
LLM is a fake that answers from whatever excerpts it was given - it quotes
the first one and cites it - and says it does not know when it was given
none. Asked as the judge (day 23), it scores a candidate 9 when it shares a
word with the question and 1 otherwise. That is enough to exercise the path:
what goes into the request, what is cut, what is cited, what is scored, and
what is stored.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from test_indexing import MARKDOWN, HashEmbedder

from core.agent import KNOWLEDGE_TEMPLATE, Agent, AgentConfig
from llm.client import LLMResponse
from rag import augment, rerank
from rag.chunkers import make_chunker
from rag.index_store import IndexStore
from rag.loaders import load_text
from rag.pipeline import Corpus, build_index


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
    build_index(augment.INDEX, Corpus([load_text("guide.md", MARKDOWN)]),
                make_chunker({"max_words": 150, "min_words": 20}), embedder, store)
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
        # Fetching goes up to the candidates' bound - what is sent is held to MAX_K elsewhere.
        many = augment.retrieve(self.store, self.embedder, "structural", "x", k=99)
        self.assertEqual(many.k, augment.MAX_CANDIDATES)
        self.assertEqual([h["rank"] for h in many.hits[:2]], [1, 2])
        with self.assertRaises(augment.RetrievalError):
            augment.retrieve(self.store, self.embedder, "no-such-index", "x")

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
        from storage import Message
        rag = {"index": "structural", "k": 5, "cited": [1], "hits": [{"n": 1, "source": "a.md"}]}
        back = Message.from_dict(Message(role="assistant", content="x [1]", rag=rag).to_dict())
        self.assertEqual(back.rag, rag)
        self.assertNotIn("rag", Message(role="assistant", content="x").to_dict())


if __name__ == "__main__":
    unittest.main()
