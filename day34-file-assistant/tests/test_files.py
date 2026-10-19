"""Day 34: agentic code - the file tools, the change log, and a whole turn.

    uv run python -m unittest discover -s tests -v

No model, no Ollama, no uvx. The project is a temporary git repository; the
git server is `fixtures/fake_mcp_server.py`; the model is a script that
searches, reads, edits and creates a file, then answers.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from test_indexing import HashEmbedder
from test_project import FAKE_SERVER

from core import coder
from core.coder import CodeAgentConfig
from core.project import ProjectServers, ProjectToolsConfig, ServerSpec
from core.runtime import AgentRuntime
from core.settings import ChatSettings
from llm.client import LLMResponse
from tools.files import ChangeLog, FilesConfig, FilesProvider, patch, unified_diff
from tools.registry import ToolRegistry


def git(path: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True)
    return done.stdout


def project(path: Path) -> Path:
    """A small git repository: code, docs, a secret, an ignored and an excluded folder."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    files = {
        "README.md": "# Demo\n\nCall `greet()` to say hello.\n",
        "src/app.py": "from src.util import greet\n\nprint(greet('x'))\n",
        "src/util.py": "def greet(name):\n    return 'hi ' + name\n\n\ndef wave():\n    return greet('all')\n",
        ".env": "KEY=secret-greet\n",
        ".env.example": "KEY=\n",
        ".gitignore": "data/\n",
        "data/state.json": '{"greet": 1}\n',
        "node_modules/lib/index.js": "greet()\n",
        "AGENTS.md": "# Rules\n\n1. Every function has a docstring.\n",
    }
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    git(path, "add", "README.md", "src", ".env.example", ".gitignore", "AGENTS.md")
    git(path, "commit", "-q", "-m", "start")
    return path.resolve()


class FilesProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = project(Path(self.tmp.name) / "demo")
        self.files = FilesProvider(self.root, FilesConfig(), git=True)
        self.tools = ToolRegistry([self.files])

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name: str, **arguments):
        return self.tools.call(name, arguments)

    # -- what is a project file ----------------------------------------------

    def test_the_tools_on_offer(self):
        self.assertEqual([t.name for t in self.tools.tools()],
                         ["list_files", "read_file", "search_text", "write_file", "edit_file"])
        docs = FilesProvider(self.root, docs=lambda q, k: "[1] README.md\nhello")
        self.assertIn("search_docs", [t.name for t in docs.tools()])
        self.assertIn("[1] README.md", ToolRegistry([docs]).call("search_docs", {"query": "hello"}).text)

    def test_listing_follows_git_and_skips_dependencies(self):
        listed = self.call("list_files").text
        for name in ("README.md", "src/app.py", "src/util.py", ".env.example", "AGENTS.md"):
            self.assertIn(name, listed)
        self.assertNotIn("data/state.json", listed)                 # git ignores it
        self.assertNotIn("node_modules", listed)                    # excluded
        self.assertIn("2 files under src matching *.py", self.call("list_files", path="src", pattern="*.py").text)
        self.assertIn("1 file under . matching src/a*.py", self.call("list_files", pattern="src/a*.py").text)

    def test_a_folder_outside_git_is_walked(self):
        plain = Path(self.tmp.name) / "plain"
        (plain / "node_modules").mkdir(parents=True)
        (plain / "a.txt").write_text("a\n")
        (plain / "node_modules" / "b.txt").write_text("b\n")
        listed = ToolRegistry([FilesProvider(plain)]).call("list_files", {}).text
        self.assertIn("a.txt", listed)
        self.assertNotIn("b.txt", listed)

    # -- reading ---------------------------------------------------------------

    def test_a_file_is_read_with_line_numbers_and_in_windows(self):
        done = self.call("read_file", path="src/util.py")
        self.assertTrue(done.ok)
        self.assertIn("src/util.py - lines 1-6 of 6:", done.text)
        self.assertIn("1\tdef greet(name):", done.text)
        window = self.call("read_file", path="src/util.py", offset=2, limit=2).text
        self.assertIn("lines 2-3 of 6", window)
        self.assertIn("read on with offset=4", window)
        self.assertNotIn("def greet", window)

    def test_nothing_outside_the_project_or_secret_is_read(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("outside\n")
        os.symlink(outside, self.root / "link.txt")
        for path, why in (("../outside.txt", "outside the project"), (str(outside), "outside the project"),
                          ("link.txt", "outside the project"), (".env", "secrets"),
                          (".git/config", "inside .git"), ("missing.py", "no file"), ("src", "folder")):
            done = self.call("read_file", path=path)
            self.assertTrue(done.is_error, path)
            self.assertIn(why, done.text, path)
        self.assertTrue(self.call("read_file", path=".env.example").ok)

    # -- searching ---------------------------------------------------------------

    def test_search_finds_every_use_and_never_a_secret(self):
        found = self.call("search_text", pattern="greet").text
        self.assertIn("5 matches in 3 files", found)
        self.assertIn("src/util.py\n  1: def greet(name):", found)
        self.assertIn("  6: return greet('all')", found)
        self.assertNotIn(".env", found)
        self.assertNotIn("node_modules", found)
        self.assertNotIn("state.json", found)
        self.assertIn("in 2 files", self.call("search_text", pattern="greet", glob="*.py").text)
        self.assertIn("2 matches in 1 file", self.call("search_text", pattern=r"def \w+\(", regex=True).text)
        self.assertIn("No matches", self.call("search_text", pattern="GREET").text)
        self.assertIn("5 matches", self.call("search_text", pattern="GREET", ignore_case=True).text)
        self.assertTrue(self.call("search_text", pattern="(", regex=True).is_error)

    def test_a_long_search_is_cut_and_names_the_other_files(self):
        small = ToolRegistry([FilesProvider(self.root, FilesConfig(search_results=2), git=True)])
        found = small.call("search_text", {"pattern": "greet"}).text
        self.assertIn("showing the first 2 matches", found)
        self.assertIn("More files with matches: src/util.py", found)        # src/app.py was begun

    # -- writing -----------------------------------------------------------------

    def test_edit_replaces_one_exact_fragment(self):
        done = self.call("edit_file", path="src/util.py", old_text="return 'hi ' + name",
                         new_text="return 'hello ' + name")
        self.assertTrue(done.ok, done.text)
        self.assertIn("Edited src/util.py: 1 replacement, +1 -1", done.text)
        self.assertIn("+    return 'hello ' + name", done.text)
        self.assertIn("return 'hello ' + name", (self.root / "src/util.py").read_text())

    def test_edit_refuses_what_it_cannot_do_exactly(self):
        missing = self.call("edit_file", path="src/util.py", old_text="return 'bye'", new_text="x")
        self.assertIn("was not found", missing.text)
        spaced = self.call("edit_file", path="src/util.py", old_text="def greet(name):\n  return",
                           new_text="x")
        self.assertIn("different whitespace", spaced.text)
        twice = self.call("edit_file", path="README.md", old_text="e", new_text="E")
        self.assertIn("occurs", twice.text)
        same = self.call("edit_file", path="README.md", old_text="Demo", new_text="Demo")
        self.assertIn("the same", same.text)
        self.assertEqual((self.root / "README.md").read_text(), "# Demo\n\nCall `greet()` to say hello.\n")
        every = self.call("edit_file", path="src/util.py", old_text="greet", new_text="hello", replace_all=True)
        self.assertIn("2 replacements", every.text)

    def test_windows_line_endings_survive_an_edit(self):
        (self.root / "win.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
        done = self.call("edit_file", path="win.txt", old_text="one\ntwo", new_text="one\n2")
        self.assertTrue(done.ok, done.text)
        self.assertEqual((self.root / "win.txt").read_bytes(), b"one\r\n2\r\nthree\r\n")

    def test_write_creates_and_rewrites_but_only_project_files(self):
        created = self.call("write_file", path="docs/adr/0001-files.md", content="# ADR 1\n")
        self.assertIn("Created docs/adr/0001-files.md (1 lines)", created.text)
        self.assertTrue((self.root / "docs/adr/0001-files.md").is_file())
        again = self.call("write_file", path="docs/adr/0001-files.md", content="# ADR 1\n")
        self.assertIn("nothing written", again.text)
        rewritten = self.call("write_file", path="README.md", content="# Demo\n")
        self.assertIn("Rewrote README.md", rewritten.text)
        for path, why in (("data/new.json", "ignored by git"), ("node_modules/x.js", "not written to"),
                          (".env", "secrets"), (".git/hooks/x", "inside .git"), ("../x.txt", "outside")):
            done = self.call("write_file", path=path, content="x")
            self.assertTrue(done.is_error, path)
            self.assertIn(why, done.text, path)
        self.assertFalse((self.root / "data/new.json").exists())

    def test_writes_in_one_round_do_not_lose_each_other(self):
        (self.root / "many.txt").write_text("".join(f"line {i}\n" for i in range(20)))
        threads = [threading.Thread(target=self.call, args=("edit_file",),
                                    kwargs={"path": "many.txt", "old_text": f"line {i}\n", "new_text": f"LINE {i}\n"})
                   for i in range(10, 20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        text = (self.root / "many.txt").read_text()
        self.assertEqual(sum(f"LINE {i}\n" in text for i in range(10, 20)), 10)

    # -- what the turn did --------------------------------------------------------

    def test_the_change_log_is_one_diff_per_file_from_before_the_turn(self):
        self.call("edit_file", path="src/util.py", old_text="'hi '", new_text="'hello '")
        self.call("edit_file", path="src/util.py", old_text="def wave():", new_text="def wave_all():")
        self.call("write_file", path="CHANGELOG.md", content="# Changelog\n")
        self.call("edit_file", path="CHANGELOG.md", old_text="# Changelog\n", new_text="# Changelog\n\n- greet\n")
        self.call("edit_file", path="README.md", old_text="hello", new_text="HELLO")
        self.call("edit_file", path="README.md", old_text="HELLO", new_text="hello")      # and back
        changes = self.files.changes.changes()
        self.assertEqual([(c["path"], c["status"]) for c in changes],
                         [("CHANGELOG.md", "added"), ("src/util.py", "modified")])
        util = changes[1]
        self.assertEqual((util["added"], util["removed"]), (2, 2))
        self.assertIn("-    return 'hi ' + name", util["diff"])
        self.assertIn("+def wave_all():", util["diff"])
        self.assertTrue(changes[0]["diff"].startswith("diff --git a/CHANGELOG.md b/CHANGELOG.md\nnew file mode"))
        self.assertEqual(coder.totals(changes), {"files": 2, "added": 5, "removed": 2})

    def test_the_patch_re_applies_the_turn(self):
        self.call("edit_file", path="src/util.py", old_text="'hi '", new_text="'hello '")
        self.call("write_file", path="docs/new.md", content="no newline at the end")
        after = {p: (self.root / p).read_text() for p in ("src/util.py", "docs/new.md")}
        text = patch(self.files.changes.changes())
        git(self.root, "checkout", "--", "src/util.py")
        (self.root / "docs/new.md").unlink()
        (Path(self.tmp.name) / "turn.patch").write_text(text)
        git(self.root, "apply", str(Path(self.tmp.name) / "turn.patch"))
        self.assertEqual({p: (self.root / p).read_text() for p in after}, after)

    def test_a_file_without_a_final_newline_diffs_like_git(self):
        diff = unified_diff("a.txt", "one", "one\ntwo")
        self.assertIn("-one\n\\ No newline at end of file\n+one\n+two\n\\ No newline at end of file", diff)
        log = ChangeLog()
        log.record("a.txt", "x\n", "y\n")
        log.record("a.txt", "y\n", "x\n")
        self.assertEqual(log.changes(), [])


class CodeAgentConfigTest(unittest.TestCase):
    def test_the_shipped_config(self):
        config = CodeAgentConfig.load()
        self.assertGreater(config.max_tool_rounds, 10)
        self.assertIn(".env", config.files.deny)
        self.assertEqual({s.id for s in config.scenarios}, {"usages", "docs", "changelog", "adr", "rules"})

    def test_a_bad_config_says_what_is_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "code_agent.json"
            for raw, message in (({"files": {"deny": ".env"}}, "files.deny"),
                                 ({"max_tool_rounds": 0}, "max_tool_rounds"),
                                 ({"max_tool_calls": "many"}, "code_agent.json: max_tool_calls"),
                                 ({"scenarios": [{"label": "x"}]}, "scenario")):
                path.write_text(json.dumps(raw))
                with self.assertRaisesRegex(ValueError, message):
                    CodeAgentConfig.load(path)
            self.assertEqual(CodeAgentConfig.load(Path(tmp) / "missing.json").max_tool_rounds, 30)


class ScriptedCoder:
    """Searches, reads, edits a file and creates one, then answers; side requests get `{}`."""

    redacted_headers: dict = {}

    STEPS = [
        [("search_text", {"pattern": "greet"})],
        [("read_file", {"path": "src/util.py"}), ("git__git_status", {})],
        [("edit_file", {"path": "src/util.py", "old_text": "def greet(name):\n",
                        "new_text": "def greet(name):\n    \"\"\"Say hello.\"\"\"\n"}),
         ("write_file", {"path": "docs/usage.md", "content": "# Usage\n\n" + "greet() says hello.\n" * 200})],
    ]

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.side = 0

    def complete(self, body: dict) -> LLMResponse:
        messages = body.get("messages") or []
        message = {"role": "assistant", "content": "{}"}
        if messages and messages[0]["content"].startswith("You are a coding agent"):
            self.bodies.append(json.loads(json.dumps(body)))
            done = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
            if done < len(self.STEPS):
                message = {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"call_{done}_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(self.STEPS[done])]}
            else:
                message = {"role": "assistant",
                           "content": "`greet` is used in `src/app.py:3` and `src/util.py:6`. I added a docstring."}
        else:
            self.side += 1
        return LLMResponse(request=body, response={"choices": [{"message": message}],
                                                   "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                                             "total_tokens": 7}},
                           url="fake://chat", status_code=200, elapsed_ms=1.0)


def git_only_config() -> ProjectToolsConfig:
    return ProjectToolsConfig(servers=[
        ServerSpec(id="git", label="Git", command=FAKE_SERVER + ["{project}"], tools=["git_status"],
                   needs_git=True, fixed={"repo_path": "{git_root}"})])


class AgenticTurnTest(unittest.TestCase):
    """A chat with agentic code on: the model works in the project's files, the diff is kept."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = project(Path(self.tmp.name) / "demo")
        self.model = ScriptedCoder()
        self.rt = AgentRuntime.build(llm=self.model, data_dir=Path(self.tmp.name) / "data",
                                     embedder=HashEmbedder())
        self.rt.project.servers = ProjectServers(git_only_config())
        self.rt.project.open(str(self.root))
        for _ in range(200):
            if not self.rt.index.job.snapshot().get("running"):
                break
            time.sleep(0.05)

    def tearDown(self):
        self.rt.stop()
        self.tmp.cleanup()

    def test_the_model_works_in_the_files_and_the_diff_is_stored(self):
        result = self.rt.chat("Find where greet is used and document it",
                              settings=ChatSettings(agentic_code=True))

        self.assertIn("src/app.py:3", result.answer)
        self.assertIn('"""Say hello."""', (self.root / "src/util.py").read_text())
        self.assertTrue((self.root / "docs/usage.md").is_file())

        first = self.model.bodies[0]
        system = first["messages"][0]["content"]
        self.assertIn(f"Folder: {self.root}", system)
        self.assertIn("The project's instructions, from AGENTS.md", system)
        self.assertIn("Every function has a docstring.", system)
        offered = {t["function"]["name"] for t in first["tools"]}
        self.assertEqual(offered, {"list_files", "read_file", "search_text", "write_file", "edit_file",
                                   "search_docs", "git__git_status"})
        self.assertEqual(first["messages"][-1]["content"], "Find where greet is used and document it")

        calls = [c["name"] for c in result.reply.tool_calls]
        self.assertEqual(calls, ["search_text", "read_file", "git__git_status", "edit_file", "write_file"])
        self.assertTrue(all(c["ok"] for c in result.reply.tool_calls), result.reply.tool_calls)
        write = result.reply.tool_calls[-1]
        self.assertLess(len(write["arguments"]["content"]), 500)         # stored short; the file is whole

        self.assertEqual([(c["path"], c["status"]) for c in result.changes],
                         [("docs/usage.md", "added"), ("src/util.py", "modified")])
        self.assertIsNone(result.check)                                   # no invariant retry
        self.assertIsNone(result.advice)
        stored = self.rt.stores.conversations.load("single").messages
        self.assertEqual(stored[-1].changes, result.changes)
        self.assertEqual(len(stored[-1].tools), 5)

        report = self.rt.conversations.changes("single")
        self.assertEqual(report["totals"]["files"], 2)
        self.assertEqual(report["turns"][0]["asked"], "Find where greet is used and document it")
        self.assertIn("+    \"\"\"Say hello.\"\"\"", report["patch"])

    def test_without_agentic_code_the_project_tools_are_not_offered(self):
        result = self.rt.chat("hello")
        self.assertEqual(self.model.bodies, [])
        self.assertIsNone(result.changes)
        self.assertNotIn("write_file", json.dumps(result.reply.request.get("tools") or []))


if __name__ == "__main__":
    unittest.main()
