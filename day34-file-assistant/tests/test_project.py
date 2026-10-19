"""Day 31: the developer assistant - the project, its stdio MCP servers, Swift in the index.

    uv run python -m unittest discover -s tests -v

No model, no Ollama, no uvx, no npx. The MCP servers are
`fixtures/fake_mcp_server.py`, started over stdio exactly the way the real
ones are. Day 34's agentic code, which these servers serve now that /help is
gone, is `test_files.py`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_indexing import HashEmbedder

from core.errors import Invalid
from core.project import APP_DIR, Project, ProjectServers, ProjectToolsConfig, ServerSpec
from core.runtime import AgentRuntime
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

    def test_the_shipped_git_server_is_read_only(self):
        # Day 34: git is the one shipped server - the files are `tools.files`.
        config = ProjectToolsConfig.load()
        servers = {s.id: s for s in config.servers}
        self.assertEqual(set(servers), {"git"})
        self.assertFalse({"git_commit", "git_add", "git_reset", "git_checkout", "git_create_branch"}
                         & set(servers["git"].tools))
        self.assertIn("git_status", servers["git"].tools)
        self.assertEqual(servers["git"].fixed, {"repo_path": "{git_root}"})


class OpeningTest(unittest.TestCase):
    def test_opening_a_folder_that_is_not_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = AgentRuntime.build(llm=object(), data_dir=Path(tmp) / "data", embedder=HashEmbedder())
            rt.project.servers = ProjectServers(fake_config())
            try:
                with self.assertRaises(Invalid):
                    rt.project.open("relative/path")
                self.assertEqual(rt.project.current().path, APP_DIR)
            finally:
                rt.stop()


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
