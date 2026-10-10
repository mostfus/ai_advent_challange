"""Day 25: the task state - goal, clarifications, constraints, terms - and the search it feeds.

    uv run python -m unittest discover -s tests -v

No model and no network. The module's own functions are checked on
hand-written patches; the chat is driven end to end over a bare copy of the
app with day 24's fake model, which here also plays the task-state
extractor and answers it from a queue of patches the test sets up.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_grounding  # noqa: E402  - not its classes by name, or they would run twice

import task_state as ts  # noqa: E402
from agent import Agent, AgentConfig, TASK_STATE_PREFIX  # noqa: E402
from llm_client import LLMResponse  # noqa: E402


def patch(**kw) -> ts.StateUpdate:
    return ts.StateUpdate(goal=kw.get("goal", ""), focus=kw.get("focus", ""), add=kw.get("add", {}), remove=kw.get("remove", {}))


class PatchTest(unittest.TestCase):
    def test_a_patch_adds_replaces_and_removes_and_says_what_it_did(self):
        state, changes = ts.apply_patch(None, patch(
            goal="understand the reranker",
            add={"clarified": ["the threshold asked about is the reranker's"],
                 "constraints": ["answer in Russian"], "terms": {"порог": "rag_threshold"}}))
        self.assertEqual(state["goal"], "understand the reranker")
        self.assertEqual([c["op"] for c in changes], ["+", "+", "+", "+"])

        state, changes = ts.apply_patch(state, patch(
            goal="Understand the reranker",                         # the same goal, in other case
            add={"clarified": ["The threshold asked about is the reranker's"],   # already there
                 "terms": {"порог": "rag_threshold, 0-10"}},       # redefined
            remove={"constraints": ["answer in russian"], "clarified": ["never said"]}))
        self.assertEqual(changes, [
            {"section": "constraints", "op": "-", "text": "answer in Russian"},
            {"section": "terms", "op": "~", "text": "порог = rag_threshold, 0-10"},
        ])
        self.assertEqual(state["constraints"], [])
        self.assertEqual(state["terms"], {"порог": "rag_threshold, 0-10"})

        state, changes = ts.apply_patch(state, patch(goal="deploy the agent", remove={"terms": ["ПОРОГ"]}))
        self.assertEqual(changes[0], {"section": "goal", "op": "~", "text": "deploy the agent"})
        self.assertEqual(state["terms"], {})

    def test_caps_drop_the_oldest(self):
        state = None
        for i in range(ts.MAX_ITEMS + 2):
            state, _ = ts.apply_patch(state, patch(add={"clarified": [f"point {i}"]}))
        self.assertEqual(len(state["clarified"]), ts.MAX_ITEMS)
        self.assertEqual(state["clarified"][0], "point 2")

    def test_parse_takes_the_shape_asked_for_and_sections_at_the_top(self):
        def reply(content):
            return {"choices": [{"message": {"content": content}}]}
        phrases, add, remove, error = ts.parse_patch(reply(json.dumps(
            {"goal": "g", "focus": 3, "add": {"clarified": ["c"]}, "remove": {"terms": ["t"]}})))
        self.assertEqual((phrases, add, remove, error),
                         ({"goal": "g", "focus": ""}, {"clarified": ["c"]}, {"terms": ["t"]}, None))
        _, add, _, _ = ts.parse_patch(reply('```json\n{"terms": {"k": "v"}}\n```'))
        self.assertEqual(add, {"terms": {"k": "v"}})
        self.assertEqual(ts.parse_patch(reply("sure, here it is"))[3], "the extractor did not return JSON")

    def test_the_search_gets_the_question_first_then_the_focus_and_no_constraints(self):
        self.assertEqual(ts.search_query("why?", None), "why?")
        state, _ = ts.apply_patch(None, patch(
            goal="the reranker's default threshold", focus="DEFAULT_THRESHOLD = 6",
            add={"clarified": ["the reranker, not chunking"], "constraints": ["answer briefly"],
                 "terms": {"порог": "rag_threshold"}}))
        query = ts.search_query("why that number?", state)
        self.assertEqual(query.split("\n")[0], "why that number?")
        self.assertIn("Conversation context: DEFAULT_THRESHOLD = 6; the reranker's default threshold; "
                      "the reranker, not chunking; порог = rag_threshold", query)
        self.assertNotIn("briefly", query)

    def test_render_and_forget(self):
        state, _ = ts.apply_patch(None, patch(goal="g", focus="f",
                                              add={"constraints": ["c1", "c2"], "terms": {"t": "m"}}))
        self.assertEqual(ts.render(state), "Goal: g\nThe latest question is about: f\n"
                                           "Constraints on the answers:\n- c1\n- c2\nTerms:\n- t: m")
        self.assertEqual(ts.forget(state, "focus", "")["focus"], "")
        self.assertEqual(ts.forget(state, "constraints", "C1")["constraints"], ["c2"])
        self.assertEqual(ts.forget(state, "terms", "t")["terms"], {})
        self.assertEqual(ts.forget(state, "goal", "")["goal"], "")
        self.assertEqual(ts.render(None), "")

    def test_the_block_goes_after_the_facts_and_before_the_question(self):
        agent = Agent(object(), AgentConfig(system_prompt="sys"), facts="k: v", task_state="Goal: g")
        messages = agent.build_messages("q")
        self.assertEqual([m["content"].split("\n")[0] for m in messages][-2], TASK_STATE_PREFIX.split("\n")[0])
        self.assertTrue(messages[-3]["content"].endswith("k: v"))


class StatefulLLM(test_grounding.GroundedLLM):
    """Day 24's model, which also answers the task-state extractor from `patches`."""

    def __init__(self):
        super().__init__()
        self.patches: list[dict] = []
        self.extractions: list[dict] = []

    def complete(self, body: dict) -> LLMResponse:
        if body["messages"][0]["content"] == ts.SYSTEM_PROMPT:
            self.extractions.append(body)
            answer = json.dumps(self.patches.pop(0) if self.patches else {})
            return LLMResponse(request=body, response={
                "choices": [{"message": {"role": "assistant", "content": answer}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            }, url="fake://chat", status_code=200, elapsed_ms=1.0)
        return super().complete(body)


class ChatTaskStateTest(test_grounding.ChatGroundingTest):
    """POST /api/chat with RAG on: the state moves before the search, and the search is asked with it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.llm = StatefulLLM()
        cls.server.llm = cls.llm
        import rag_api
        rag_api.client = cls.llm

    # Day 24's own tests run again here, over the model that also keeps state.

    def judged(self, since: int) -> list[str]:
        return [b["messages"][-1]["content"].split("\n\nCandidates:", 1)[0]
                for b in self.llm.bodies[since:] if self.llm.is_judge(b)]

    def test_a_clarification_is_kept_searched_with_and_sent(self):
        chat = "state-1"
        self.llm.patches = [
            {"goal": "quokka facts", "focus": "the quokka",
             "add": {"constraints": ["answer briefly"], "terms": {"Q": "quokka"}}},
            {"add": {"clarified": ["the narwhal section, not the zebra one"]}},       # a follow-up keeps the focus
        ]
        first = self.ask("Tell me about the quokka", chat)
        changes = first["context"]["sent"]["task_state_changes"]
        self.assertEqual([c["section"] for c in changes], ["goal", "focus", "constraints", "terms"])
        self.assertEqual(first["context"]["task_state"]["goal"], "quokka facts")

        since = len(self.llm.bodies)
        second = self.ask("The narwhal one", chat)
        # The extractor was shown the state and the previous turn, not the transcript.
        sent = self.llm.extractions[-1]["messages"][-1]["content"]
        self.assertIn("Goal: quokka facts", sent)
        self.assertIn("assistant: ", sent)
        # The search and the judge got the question first, then the context.
        query = second["rag"]["query"]
        self.assertEqual(query.split("\n")[0], "The narwhal one")
        self.assertIn("the quokka; quokka facts; the narwhal section, not the zebra one; Q = quokka", query)
        self.assertNotIn("briefly", query)
        self.assertTrue(self.judged(since)[0].startswith("Question: The narwhal one\nConversation context: "))
        # The answer was asked under the block, with the question as typed.
        answer = self.answers(since)[0]
        block = [m["content"] for m in answer["messages"] if m["content"].startswith(TASK_STATE_PREFIX)]
        self.assertEqual(len(block), 1)
        self.assertIn("- the narwhal section, not the zebra one", block[0])
        self.assertIn("- answer briefly", block[0])

        # Stored: the state on the branch, and what each message changed on it.
        record = self.client.get(f"/api/conversations/{chat}").json()
        users = [m for m in record["messages"] if m["role"] == "user"]
        self.assertEqual(len(users[0]["task_state"]), 4)
        self.assertEqual(users[1]["task_state"],
                         [{"section": "clarified", "op": "+", "text": "the narwhal section, not the zebra one"}])
        state = record["context"]["task_state"]
        self.assertEqual((state["count"], state["updates"]), (5, 2))
        self.assertEqual(state["usage_total"]["total_tokens"], 20)

    def test_the_panel_can_take_an_item_out_and_forget_it_all(self):
        chat = "state-2"
        self.llm.patches = [{"goal": "g", "add": {"clarified": ["c1", "c2"]}}]
        self.ask("Tell me about the quokka", chat)
        res = self.client.post(f"/api/conversations/{chat}/task-state/forget",
                               json={"section": "clarified", "item": "c1"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["context"]["task_state"]["clarified"], ["c2"])
        self.assertEqual(self.client.post(f"/api/conversations/{chat}/task-state/forget",
                                          json={"section": "nope"}).status_code, 400)
        res = self.client.delete(f"/api/conversations/{chat}/task-state")
        self.assertIsNone(res.json()["context"]["task_state"])
        since = len(self.llm.extractions)
        self.ask("Tell me about the quokka", chat)
        self.assertIn("(empty)", self.llm.extractions[since]["messages"][-1]["content"])

    def test_a_fork_inherits_the_state_and_the_two_drift_apart(self):
        chat = "state-3"
        self.llm.patches = [{"goal": "g", "add": {"clarified": ["shared"]}}]
        self.ask("Tell me about the quokka", chat)
        res = self.client.post(f"/api/conversations/{chat}/branches", json={})
        self.assertEqual(res.status_code, 200, res.text)
        self.llm.patches = [{"add": {"clarified": ["only in the fork"]}}]
        forked = self.ask("Tell me about the narwhal", chat)
        self.assertEqual(forked["context"]["task_state"]["clarified"], ["shared", "only in the fork"])
        record = self.client.get(f"/api/conversations/{chat}").json()
        main = record["task_state"]["content"]
        self.assertEqual(main["clarified"], ["shared"])

    def test_a_failed_update_sends_the_state_as_it_stood(self):
        chat = "state-4"
        self.llm.patches = [{"goal": "g"}]
        self.ask("Tell me about the quokka", chat)
        broken = self.llm.complete

        def failing(body):
            if body["messages"][0]["content"] == ts.SYSTEM_PROMPT:
                return LLMResponse(request=body, response={"choices": [{"message": {"content": "no"}}]},
                                   url="fake://chat", status_code=200, elapsed_ms=1.0)
            return broken(body)
        self.llm.complete = failing
        try:
            reply = self.ask("Tell me about the narwhal", chat)
        finally:
            del self.llm.complete
        self.assertEqual(reply["context"]["sent"]["task_state_error"], "the extractor did not return JSON")
        self.assertEqual(reply["context"]["task_state"]["goal"], "g")
        self.assertIn("\nConversation context: g", reply["rag"]["query"])

    def test_without_rag_there_is_no_task_state(self):
        since = len(self.llm.extractions)
        res = self.client.post("/api/chat", json={"message": "hello", "conversation_id": "state-5",
                                                  "settings": {"rag": False}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(self.llm.extractions), since)
        self.assertIsNone(res.json()["context"]["task_state"])


if __name__ == "__main__":
    unittest.main()
