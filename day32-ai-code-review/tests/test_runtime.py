"""The agent without the web app: the packages, the runtime, one turn - and the app built over it.

    uv run python -m unittest discover -s tests -v

No model, no Ollama, no network. The core packages are imported in a fresh
interpreter to show no web framework comes with them; a runtime is built over
a fake model and a temp directory and asked something, from plain Python; a
conversation stored while chats could branch is read back flat; and the web
app is built over the same kind of runtime and asked for every page it draws.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_indexing import HashEmbedder

from core.agent import DEFAULT_SYSTEM_PROMPT
from core.errors import Invalid
from core.runtime import AgentRuntime
from core.settings import ChatSettings
from llm.client import LLMResponse
from storage import ConversationStore
from tools import Tool, ToolRegistry

PROJECT_DIR = Path(__file__).resolve().parent.parent
HEADLESS = ("core", "llm", "tools", "rag", "storage", "review")
BRANCHED = Path(__file__).resolve().parent / "fixtures" / "branched_conversation.json"


class FakeModel:
    """Answers the chat with a fixed line - or, when given tools, asks for one first -
    and every side request (proposals, checks) with an empty JSON object."""

    redacted_headers: dict = {}

    def __init__(self, answer: str = "Hello back.") -> None:
        self.answer = answer
        self.bodies: list[dict] = []

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(body)
        messages = body.get("messages") or []
        is_chat = messages and messages[0]["content"] == DEFAULT_SYSTEM_PROMPT
        message = {"role": "assistant", "content": self.answer if is_chat else "{}"}
        called = any(m["role"] == "tool" for m in messages)
        if is_chat and body.get("tools") and not called:
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": body["tools"][0]["function"]["name"], "arguments": '{"path": "README.md"}'}}]}
        return LLMResponse(request=body, response={
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        }, url="fake://chat", status_code=200, elapsed_ms=1.0)


def runtime(tmp: str, llm=None) -> AgentRuntime:
    return AgentRuntime.build(llm=llm or FakeModel(), data_dir=tmp, embedder=HashEmbedder())


class HeadlessImportTest(unittest.TestCase):
    def test_the_core_packages_bring_no_web_framework(self):
        script = (
            "import importlib, json, pkgutil, sys\n"
            f"for name in {HEADLESS!r}:\n"
            "    package = importlib.import_module(name)\n"
            "    for info in pkgutil.walk_packages(package.__path__, name + '.'):\n"
            "        importlib.import_module(info.name)\n"
            "print(json.dumps(sorted(m for m in sys.modules\n"
            "                        if m.split('.')[0] in ('fastapi', 'starlette', 'uvicorn', 'web'))))\n"
        )
        done = subprocess.run([sys.executable, "-c", script], cwd=PROJECT_DIR, capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), [])


class RuntimeTurnTest(unittest.TestCase):
    """A turn from plain Python: no app, no HTTP - what a CI script will do."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_turn_is_answered_and_stored(self):
        model = FakeModel()
        rt = runtime(self.tmp.name, model)
        result = rt.chat("Hello there", conversation_id="ci-run",
                         settings=ChatSettings(memory_proposals=False, invariant_check=False))
        self.assertEqual(result.answer, "Hello back.")
        self.assertIsNone(result.reply.error)
        self.assertEqual(model.bodies[0]["messages"][-1]["content"], "Hello there")
        stored = rt.stores.conversations.load("ci-run")
        self.assertEqual(stored.history(), [{"role": "user", "content": "Hello there"},
                                            {"role": "assistant", "content": "Hello back."}])
        self.assertEqual(stored.usages()[0]["total_tokens"], 13)
        # The second turn replays the first.
        rt.chat("And again", conversation_id="ci-run",
                settings=ChatSettings(memory_proposals=False, invariant_check=False))
        self.assertEqual([m["content"] for m in model.bodies[-1]["messages"][1:]],
                         ["Hello there", "Hello back.", "And again"])

    def test_the_stores_are_seeded_under_the_data_dir(self):
        rt = runtime(self.tmp.name)
        self.assertTrue((Path(self.tmp.name) / "personality" / "profiles.json").exists())
        self.assertEqual([s.id for s in rt.stores.mcp_servers.load()], ["deepwiki"])
        self.assertEqual(rt.index.dir, Path(self.tmp.name) / "index")

    def test_a_refusal_is_a_service_error(self):
        rt = runtime(self.tmp.name)
        with self.assertRaises(Invalid):
            rt.chat("   ")
        with self.assertRaisesRegex(Invalid, "Unknown model"):
            rt.chat("hi", settings=ChatSettings(model="no-such-model"))

    def test_a_local_provider_plugs_into_the_turn(self):
        calls = []

        def read(arguments: dict) -> str:
            calls.append(arguments)
            return "# The README"

        class Local:
            def tools(self):
                return [Tool("read_file", "Read a file.", {"type": "object", "properties": {"path": {"type": "string"}}},
                             read, source="local")]

        rt = runtime(self.tmp.name)
        rt.tool_registry = lambda: ToolRegistry([rt.mcp.provider(), Local()])
        result = rt.chat("What is in the README?", conversation_id="tools",
                         settings=ChatSettings(memory_proposals=False, invariant_check=False))
        self.assertEqual(calls, [{"path": "README.md"}])
        self.assertEqual(result.reply.tool_calls[0]["name"], "read_file")
        self.assertEqual(rt.stores.conversations.load("tools").messages[-1].tools[0]["text"], "# The README")


class BranchedConversationTest(unittest.TestCase):
    """A file written while chats could branch: read as the branch that was active, saved back flat."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "conversations"
        self.dir.mkdir()
        self.file = self.dir / "legacy.json"
        shutil.copy(BRANCHED, self.file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_it_reads_as_its_active_branch(self):
        conversation = ConversationStore(self.dir).load("legacy")
        # main up to the fork, branch-2 up to the next one, then branch-3's own.
        self.assertEqual([m.content for m in conversation.messages],
                         ["Plan a weekend trip", "Rome or Lisbon?", "Lisbon", "Lisbon it is.",
                          "And the hotel?", "Near the river."])
        self.assertEqual(conversation.facts.items, {"city": "Lisbon", "hotel": "by the river"})
        self.assertEqual(conversation.facts.updates, 2)
        self.assertEqual(conversation.summary.text, "The trip is to Lisbon.")
        self.assertEqual(conversation.task_state.content["goal"], "a trip to Lisbon")
        self.assertEqual(conversation.task_state.usage_total, {"total_tokens": 30})
        # The usage rides on the messages - the active line's only, not the abandoned ones'.
        self.assertEqual([u["total_tokens"] for u in conversation.usages()], [15, 36, 54])
        self.assertEqual(len(conversation.history()), 6)
        for key in ("branches", "active_branch", "checkpoints", "view"):
            self.assertNotIn(key, conversation.to_dict())

    def test_a_record_on_main_keeps_main_and_drops_the_forks(self):
        data = json.loads(BRANCHED.read_text(encoding="utf-8"))
        del data["active_branch"]
        self.file.write_text(json.dumps(data), encoding="utf-8")
        conversation = ConversationStore(self.dir).load("legacy")
        self.assertEqual([m.content for m in conversation.messages][-1], "Rome it is.")
        self.assertEqual(conversation.facts.items, {"city": "Rome"})
        self.assertEqual(conversation.task_state.content["goal"], "a trip to Rome")

    def test_the_page_opens_it_and_the_next_save_writes_it_flat(self):
        from fastapi.testclient import TestClient

        from web.app import create_app
        model = FakeModel()
        rt = runtime(self.tmp.name, model)
        client = TestClient(create_app(rt))
        body = client.get("/api/conversations/legacy")
        self.assertEqual(body.status_code, 200, body.text)
        body = body.json()
        self.assertEqual(len(body["messages"]), 6)
        self.assertNotIn("branching", body)
        self.assertEqual(body["usage"]["total"]["total_tokens"], 15 + 36 + 54)
        self.assertEqual(body["context"]["facts"]["items"]["hotel"], "by the river")
        self.assertEqual(body["context"]["task_state"]["goal"], "a trip to Lisbon")
        # Reading writes nothing.
        self.assertIn("branches", json.loads(self.file.read_text(encoding="utf-8")))

        # The page sends the stored settings back, `rag_index` and all.
        res = client.post("/api/chat", json={"message": "Thanks", "conversation_id": "legacy",
                                             "settings": {**body["settings"], "context_strategy": "window",
                                                          "memory_proposals": False, "invariant_check": False}})
        self.assertEqual(res.status_code, 200, res.text)
        replayed = [m["content"] for m in model.bodies[-1]["messages"][1:]]
        self.assertEqual(replayed[-3:], ["And the hotel?", "Near the river.", "Thanks"])
        self.assertNotIn("Rome it is.", replayed)

        on_disk = json.loads(self.file.read_text(encoding="utf-8"))
        for key in ("branches", "active_branch", "checkpoints", "view"):
            self.assertNotIn(key, on_disk)
        self.assertNotIn("rag_index", on_disk["settings"])
        self.assertEqual(len(on_disk["messages"]), 8)
        self.assertEqual(on_disk["facts"]["items"], {"city": "Lisbon", "hotel": "by the river"})
        self.assertEqual(on_disk["summary"]["text"], "The trip is to Lisbon.")


class WebAppTest(unittest.TestCase):
    """The app factory over a runtime: every page the UI draws, in-process."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from web.app import create_app
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rt = runtime(cls.tmp.name)
        cls.client = TestClient(create_app(cls.rt))      # not entered: no lifespan, no llama-server

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_page_and_every_domain_answers(self):
        c = self.client
        self.assertIn("<html", c.get("/").text.lower())
        for path in ("/api/config", "/api/conversations", "/api/tasks", "/api/memory", "/api/personality",
                     "/api/invariants", "/api/mcp", "/api/index", "/api/index/job"):
            self.assertEqual(c.get(path).status_code, 200, path)
        config = c.get("/api/config").json()
        self.assertEqual(config["defaults"]["model"], ChatSettings().model)
        self.assertFalse([k for k in config if k.startswith("digests")])
        self.assertNotIn("rag_index", config["defaults"])
        self.assertNotIn("max_compare_chats", config)
        self.assertEqual(c.get("/api/index").json()["corpus"]["config"], "corpus.json")

    def test_a_chat_round_trip_and_the_refusals(self):
        c = self.client
        body = c.post("/api/chat", json={"message": "hi", "conversation_id": "web-1",
                                         "settings": {"memory_proposals": False}}).json()
        self.assertEqual(body["answer"], "Hello back.")
        self.assertNotIn("flow", body)
        self.assertEqual(len(c.get("/api/conversations/web-1").json()["messages"]), 2)
        missing = c.get("/api/conversations/no-such-chat")
        self.assertEqual((missing.status_code, missing.json()["detail"]), (404, "No saved conversation: no-such-chat"))
        self.assertEqual(c.post("/api/chat", json={"message": " "}).status_code, 400)
        self.assertEqual(c.post("/api/chat", json={"message": "x", "settings": {"temperature": "hot"}}).status_code, 422)

    def test_the_deleted_features_are_gone(self):
        c = self.client
        for path in ("/api/events", "/api/scenarios", "/api/rag", "/api/rag/sweep", "/api/index/questions",
                     "/api/index/evaluate", "/static/rag.js", "/static/rag.css"):
            self.assertEqual(c.get(path).status_code, 404, path)
        c.post("/api/chat", json={"message": "hi", "conversation_id": "web-2", "settings": {"memory_proposals": False}})
        for method, path in (("post", "/api/rag/ask"), ("post", "/api/index/evaluate"),
                             ("post", "/api/conversations/web-2/checkpoints"),
                             ("post", "/api/conversations/web-2/branches"),
                             ("get", "/api/conversations/web-2/branches"),
                             ("post", "/api/conversations/web-2/branches/main/activate")):
            self.assertIn(getattr(c, method)(path).status_code, (404, 405), path)
        record = c.get("/api/conversations/web-2").json()
        self.assertFalse({"view", "branching", "branches", "active_branch"} & set(record))


if __name__ == "__main__":
    unittest.main()
