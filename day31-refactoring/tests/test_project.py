"""Day 31: the developer assistant - the project, its stdio MCP servers, /help, Swift in the index.

    uv run python -m unittest discover -s tests -v

No model, no Ollama, no uvx, no npx. The MCP servers are
`fixtures/fake_mcp_server.py`, started over stdio exactly the way the real
ones are; the model is a script that asks git for the branch, then answers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_indexing import HashEmbedder

from core import help as help_module
from core.errors import Invalid
from core.project import APP_DIR, Project, ProjectServers, ProjectToolsConfig, ServerSpec
from core.runtime import AgentRuntime
from llm.client import LLMResponse
from rag.loaders import load_text, mask_swift
from tools import ToolRegistry
from tools.mcp_client import MCPError
from tools.mcp_provider import Route
from tools.mcp_stdio import StdioServer

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAKE_SERVER = [sys.executable, str(FIXTURES / "fake_mcp_server.py")]
PROJECT_TOOLS = {"git__git_status", "files__list_directory", "files__read_text_file", "files__big"}


def fake_config(max_result_chars: int = 1000) -> ProjectToolsConfig:
    """The shipped config's shape, over the fake server."""
    return ProjectToolsConfig(servers=[
        ServerSpec(id="git", label="Git", command=FAKE_SERVER + ["{project}"], tools=["git_status"],
                   needs_git=True, fixed={"repo_path": "{git_root}"}),
        ServerSpec(id="files", label="Files", command=FAKE_SERVER + ["{project}"],
                   tools=["list_directory", "read_text_file", "big"], deny=[".env", ".env.*", "*.pem"]),
    ], max_result_chars=max_result_chars)


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("# Notes\n\nA SwiftUI app that keeps notes.\n")
    (path / "Sources").mkdir()
    (path / "Sources" / "ContentView.swift").write_text((FIXTURES / "ContentView.swift").read_text())
    return path.resolve()


class StdioServerTest(unittest.TestCase):
    """The transport: a child process, JSON-RPC over its pipes, the session held open."""

    def test_a_session_over_pipes(self):
        server = StdioServer(FAKE_SERVER)
        result = server.start()
        try:
            self.assertEqual(result["serverInfo"]["name"], "fake-server")
            listing = server.list_tools()
            self.assertTrue(listing.ok, listing.error)
            self.assertIn("git_status", [t.name for t in listing.tools])
            done = server.call_tool("list_directory", {"path": "."})
            self.assertTrue(done.ok)
            self.assertIn("README.md", done.text)
            # The session stays open: a second call needs no second handshake.
            self.assertTrue(server.call_tool("read_text_file", {"path": "a.txt"}).ok)
            failed = server.call_tool("no_such_tool", {})
            self.assertTrue(failed.is_error)
            self.assertEqual(failed.transport_error, "")          # the tool answered: an answer
        finally:
            server.stop()
        self.assertFalse(server.alive)
        after = server.call_tool("list_directory", {"path": "."})
        self.assertIn("not running", after.transport_error)

    def test_a_server_that_cannot_start_or_dies_says_so(self):
        with self.assertRaisesRegex(MCPError, "could not start"):
            StdioServer(["no-such-binary-day31"]).start()
        with self.assertRaisesRegex(MCPError, "exited"):
            StdioServer([sys.executable, "-c", "import sys; print('boom', file=sys.stderr)"]).start()


class ProjectServersTest(unittest.TestCase):
    """What the model is offered from the project's servers, and what a call goes through."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = git_repo(Path(cls.tmp.name) / "app")
        cls.servers = ProjectServers(fake_config())
        cls.servers.open(Project.at(cls.repo))
        cls.servers.wait(30)
        cls.registry = ToolRegistry([cls.servers.provider()])

    @classmethod
    def tearDownClass(cls):
        cls.servers.stop()
        cls.tmp.cleanup()

    def test_only_the_allowed_tools_are_offered(self):
        self.assertEqual([s["state"] for s in self.servers.status()], ["ready", "ready"])
        self.assertEqual({t.name for t in self.registry.tools()}, PROJECT_TOOLS)
        refused = self.registry.call("files__write_file", {"path": "x"})
        self.assertTrue(refused.is_error)
        direct = self.servers.call(Route("files__write_file", "files", "Files", "stdio:files", "write_file"),
                                   {"path": "x"})
        self.assertIn("not offered", direct.text)
        self.assertFalse((self.repo / "WRITTEN").exists())

    def test_fixed_arguments_are_hidden_from_the_model_and_always_sent(self):
        schema = next(t for t in self.registry.tools() if t.name == "git__git_status").input_schema
        self.assertNotIn("repo_path", schema["properties"])
        self.assertNotIn("repo_path", schema.get("required", []))
        done = self.registry.call("git__git_status", {"repo_path": "/somewhere/else"})
        self.assertIn(f"repo={self.repo}", done.text)

    def test_secret_files_are_refused_before_the_server_is_asked(self):
        for path in (".env", "config/.env.local", "certs/server.pem"):
            refused = self.registry.call("files__read_text_file", {"path": path})
            self.assertTrue(refused.is_error, path)
            self.assertIn("secrets", refused.text)
        self.assertIn("contents of .env.example",
                      self.registry.call("files__read_text_file", {"path": ".env.example"}).text)

    def test_long_results_are_cut_and_say_so(self):
        done = self.registry.call("files__big", {})
        self.assertLess(len(done.text), 1200)
        self.assertIn("cut: 20000 characters", done.text)

    def test_a_folder_outside_git_gets_no_git_server(self):
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        servers = ProjectServers(fake_config())
        servers.open(Project.at(plain))
        servers.wait(30)
        try:
            self.assertEqual({s["id"]: s["state"] for s in servers.status()}, {"git": "skipped", "files": "ready"})
            self.assertEqual({t.name for t in servers.provider().tools()},
                             {"files__list_directory", "files__read_text_file", "files__big"})
        finally:
            servers.stop()


class ProjectTest(unittest.TestCase):
    def test_a_project_is_an_absolute_folder_and_the_repository_it_is_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = git_repo(Path(tmp) / "app")
            self.assertEqual(Project.at(repo / "Sources").git_root, repo)
            self.assertIsNone(Project.at(tmp).git_root)
            with self.assertRaisesRegex(ValueError, "absolute"):
                Project.at("app")
            with self.assertRaisesRegex(ValueError, "no folder"):
                Project.at(Path(tmp) / "missing")

    def test_the_shipped_servers_are_read_only(self):
        config = ProjectToolsConfig.load()
        servers = {s.id: s for s in config.servers}
        self.assertEqual(set(servers), {"git", "files"})
        self.assertFalse({"git_commit", "git_add", "git_reset", "git_checkout", "git_create_branch"}
                         & set(servers["git"].tools))
        self.assertIn("git_status", servers["git"].tools)
        self.assertEqual(servers["git"].fixed, {"repo_path": "{git_root}"})
        self.assertFalse({"write_file", "edit_file", "move_file", "create_directory"} & set(servers["files"].tools))
        self.assertEqual(servers["files"].denied({"path": ".env"}), ".env")
        self.assertIsNone(servers["files"].denied({"path": ".env.example"}))


class ScriptedModel:
    """The chat asks git for the branch, then answers citing excerpt 1; side requests get `{}`."""

    redacted_headers: dict = {}

    def __init__(self) -> None:
        self.chat_bodies: list[dict] = []

    def complete(self, body: dict) -> LLMResponse:
        messages = body.get("messages") or []
        message = {"role": "assistant", "content": "{}"}
        if messages and messages[0]["content"].startswith("You are a developer assistant"):
            self.chat_bodies.append(json.loads(json.dumps(body)))     # the loop grows the same list
            if not any(m["role"] == "tool" for m in messages):
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "git__git_status", "arguments": "{}"}}]}
            else:
                message = {"role": "assistant", "content": "You are on main [1]."}
        return LLMResponse(request=body, response={"choices": [{"message": message}],
                                                   "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                                             "total_tokens": 7}},
                           url="fake://chat", status_code=200, elapsed_ms=1.0)


class HelpTest(unittest.TestCase):
    """/help: parsed, answered about the open project, with its excerpts and its tools."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.model = ScriptedModel()
        self.rt = AgentRuntime.build(llm=self.model, data_dir=Path(self.tmp.name) / "data",
                                     embedder=HashEmbedder())
        self.rt.project.servers = ProjectServers(fake_config())

    def tearDown(self):
        self.rt.stop()
        self.tmp.cleanup()

    def test_parsing(self):
        self.assertEqual(help_module.parse("/help").asked, help_module.OVERVIEW)
        self.assertEqual(help_module.parse("/HELP  what is X?\n").question, "what is X?")
        for other in ("help me", "/helpful", "what is /help"):
            self.assertIsNone(help_module.parse(other), other)

    def test_a_question_is_answered_from_the_excerpts_and_a_tool_the_model_called(self):
        repo = git_repo(Path(self.tmp.name) / "app")
        report = self.rt.project.open(str(repo))
        self.assertEqual(report["project"]["path"], str(repo))
        for _ in range(200):
            if not self.rt.index.job.snapshot().get("running"):
                break
            time.sleep(0.05)
        self.assertIsNotNone(self.rt.index.built())

        result = self.rt.chat("/help what branch am I on?")

        self.assertEqual(result.answer, "You are on main [1].")
        call = result.reply.tool_calls[0]
        self.assertEqual(call["name"], "git__git_status")
        self.assertIn("On branch main", call["text"])
        first = self.model.chat_bodies[0]
        self.assertIn(f"Folder: {repo}", first["messages"][0]["content"])
        self.assertEqual({t["function"]["name"] for t in first["tools"]}, PROJECT_TOOLS)   # no deepwiki
        question = first["messages"][-1]["content"]
        self.assertIn("Excerpts from the project's documentation and code", question)
        self.assertTrue(question.endswith("Question about the project: what branch am I on?"))
        self.assertEqual(result.sources["cited"], [1])
        self.assertNotIn("response_format", first)                      # prose, not day 24's JSON
        stored = self.rt.stores.conversations.load("single").messages
        self.assertEqual(stored[-2].content, "/help what branch am I on?")

    def test_without_an_index_it_still_answers_from_the_tools(self):
        result = self.rt.chat("/help")
        self.assertEqual(result.answer, "You are on main [1].")
        first = self.model.chat_bodies[0]
        self.assertIn(f"Folder: {APP_DIR}", first["messages"][0]["content"])
        self.assertIn("was not searched", first["messages"][0]["content"])
        self.assertEqual(first["messages"][-1]["content"], help_module.OVERVIEW)
        self.assertIsNone(result.sources)

    def test_opening_a_folder_that_is_not_one_is_refused(self):
        with self.assertRaises(Invalid):
            self.rt.project.open("relative/path")
        self.assertEqual(self.rt.project.current().path, APP_DIR)


class SwiftLoaderTest(unittest.TestCase):
    def test_declarations_become_sections_and_members_go_one_level_in(self):
        document = load_text("ContentView.swift", (FIXTURES / "ContentView.swift").read_text())
        labels = [s.label for s in document.sections]
        for label in ("file level", "struct ContentView", "struct ContentView › var body",
                      "struct ContentView › func addNote", "protocol NoteStoring › func add",
                      "class NoteStore › func add", "extension Note › let empty"):
            self.assertIn(label, labels)
        ends = [(s.start, s.end) for s in document.sections]
        self.assertEqual(ends, sorted(ends))
        self.assertTrue(all(a_end <= b_start for (_, a_end), (b_start, _) in zip(ends, ends[1:])))
        body = next(s for s in document.sections if s.label == "struct ContentView › var body")
        self.assertIn(".navigationTitle(title)", document.section_text(body))

    def test_braces_in_comments_and_strings_are_not_braces(self):
        masked = mask_swift('let a = "{" // }\nlet b = #"}"# /* { /* } */ */\n')
        self.assertNotIn("{", masked)
        self.assertNotIn("}", masked)
        self.assertEqual(masked.count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
