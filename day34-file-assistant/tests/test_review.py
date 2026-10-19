"""Day 32: the pull-request reviewer - the diff, the batches, RAG, the findings, GitHub.

    uv run python -m unittest discover -s tests -p 'test_review.py' -v

A real git repository is made in a temp folder for every test that needs one:
the diff, the copies, the line numbers are git's own, not a fixture's. The
model is a fake that answers with whatever JSON the test gives it, and
records what it was sent; GitHub is an `httpx.MockTransport` that keeps
every request. RAG runs on the hashed-words embedder of `test_indexing`.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import httpx

from test_indexing import HashEmbedder

from llm.client import LLMClientError, LLMResponse
from review import __main__ as entry
from review import git
from review.config import ReviewConfig
from review.context import plan
from review.diff import parse, parse_patch, render
from review.findings import Finding, fingerprints_in, inline_body, read_findings
from review.github import GitHub, GitHubError, PullRequest
from review.knowledge import Knowledge, KnowledgeError, query_for
from review.pipeline import Reviewer
from review.publish import place, publish
from review.report import SUMMARY_MARKER, summary_markdown

QUIET = lambda *_: None  # noqa: E731
NO_RAG = "not used in this test"

LOADER = '''"""Load settings from a file."""

import json


def load(path):
    with open(path) as handle:
        return json.load(handle)


def save(path, data):
    with open(path, "w") as handle:
        json.dump(data, handle)
'''

SHARED = "\n".join(f"def helper_{i}(x):\n    return x + {i}\n" for i in range(30))

ARCHITECTURE = """# Architecture

## Layers

The storage layer writes JSON atomically through `storage.common.write_atomic`.
Nothing outside storage opens files for writing.

## Errors

Every error raised to the user is a ServiceError with a readable message.
"""


#: The test repositories ignore this machine's git config (signing, hooks, default branch).
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=GIT_ENV).stdout


def make_repo(root: Path) -> tuple[Path, str, str]:
    """A base commit and a head commit on a branch: a modified file, a new file, a copy with
    one changed line, an unchanged copy, a rename, a deletion, a lock file and a binary."""
    repo = root / "repo"
    (repo / "app" / "docs").mkdir(parents=True)
    sh(repo, "init", "-q", "-b", "main")
    sh(repo, "config", "user.email", "test@example.com")
    sh(repo, "config", "user.name", "Test")
    (repo / "app" / "loader.py").write_text(LOADER)
    (repo / "app" / "shared.py").write_text(SHARED)
    (repo / "app" / "docs" / "architecture.md").write_text(ARCHITECTURE)
    (repo / "app" / "old_name.py").write_text("def kept():\n    return 1\n")
    (repo / "app" / "gone.py").write_text("def gone():\n    return 0\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "base")
    base = sh(repo, "rev-parse", "HEAD").strip()

    sh(repo, "checkout", "-q", "-b", "feature")
    (repo / "app" / "loader.py").write_text(LOADER.replace(
        "        return json.load(handle)",
        "        data = json.load(handle)\n        return data or None"))
    (repo / "app" / "writer.py").write_text(
        'def write(path, text):\n    f = open(path, "w")\n    f.write(text)\n')
    copied = SHARED.replace("return x + 7", "return x - 7")
    (repo / "snapshot").mkdir()
    (repo / "snapshot" / "shared.py").write_text(copied)
    (repo / "snapshot" / "same.py").write_text(SHARED)
    sh(repo, "mv", "app/old_name.py", "app/new_name.py")
    sh(repo, "rm", "-q", "app/gone.py")
    (repo / "app" / "uv.lock").write_text("lock = 1\n")
    (repo / "app" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00binary\x00")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "Feature: write files")
    head = sh(repo, "rev-parse", "HEAD").strip()
    return repo, base, head


class FakeLLM:
    """Answers each request with the next reply (a dict is sent as JSON); records the bodies."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies: list[dict] = []

    def complete(self, body):
        self.bodies.append(body)
        if not self.replies:
            raise AssertionError(f"unexpected request {len(self.bodies)}")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return LLMResponse(request=body, response={
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        }, url="fake://chat", status_code=200, elapsed_ms=1.0)


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo, self.base, self.head = make_repo(self.root)
        self.files = {f.path: f for f in parse(git.review_diff(self.repo, self.base, self.head))}

    def tearDown(self):
        self.tmp.cleanup()


class DiffTest(RepoTest):
    def test_statuses_come_from_git(self):
        f = self.files
        self.assertEqual(f["app/loader.py"].status, "modified")
        self.assertEqual(f["app/writer.py"].status, "added")
        self.assertEqual(f["app/new_name.py"].status, "renamed")
        self.assertEqual(f["app/new_name.py"].old_path, "app/old_name.py")
        self.assertEqual(f["app/gone.py"].status, "deleted")
        self.assertTrue(f["app/logo.png"].binary)

    def test_a_copy_is_its_differences_from_the_source(self):
        copy = self.files["snapshot/shared.py"]
        self.assertEqual(copy.status, "copied")
        self.assertEqual(copy.old_path, "app/shared.py")
        self.assertEqual((copy.added, copy.removed), (1, 1))
        unchanged = self.files["snapshot/same.py"]
        self.assertEqual(unchanged.status, "copied")
        self.assertFalse(unchanged.changed)

    def test_line_numbers_are_the_head_files(self):
        loader = self.files["app/loader.py"]
        head_text = (self.repo / "app" / "loader.py").read_text().splitlines()
        for number, text in loader.head_lines().items():
            self.assertEqual(head_text[number - 1], text)
        self.assertEqual({head_text[n - 1].strip() for n in loader.added_lines},
                         {"data = json.load(handle)", "return data or None"})

    def test_render_numbers_lines_and_folds_far_context(self):
        loader = self.files["app/loader.py"]
        whole = render(loader)
        self.assertIn(" 8 | +         data = json.load(handle)", whole.text)
        self.assertIn("   | -         return json.load(handle)", whole.text)
        self.assertEqual(whole.shown, set(loader.head_lines()))
        folded = render(loader, context=1)
        self.assertIn("unchanged lines", folded.text)
        self.assertNotIn(1, folded.shown)
        capped = render(loader, max_lines=3)
        self.assertLessEqual(len(capped.shown), 4)          # one part keeps MIN_PART_LINES rows
        self.assertIn("lines not shown (the file is too long)", capped.text)
        self.assertTrue(loader.added_lines <= capped.shown)

    def test_a_cap_keeps_every_part_of_the_change_in_view(self):
        sh(self.repo, "checkout", "-q", self.head)
        lines = [f"line_{i} = {i}" for i in range(300)]
        (self.repo / "long.py").write_text("\n".join(lines) + "\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "long")
        lines[10], lines[150], lines[290] = "first = 'changed'", "middle = 'changed'", "last = 'changed'"
        (self.repo / "long.py").write_text("\n".join(lines) + "\n")
        sh(self.repo, "commit", "-qam", "three changes")
        [long] = parse(git.review_diff(self.repo, "HEAD~1", "HEAD"))
        rendered = render(long, context=20, max_lines=30)
        for text in ("first = 'changed'", "middle = 'changed'", "last = 'changed'"):
            self.assertIn(text, rendered.text)
        self.assertLessEqual(len(rendered.shown), 36)
        self.assertGreater(rendered.truncated, 0)

    def test_a_github_patch_gives_the_commentable_lines(self):
        patch = "@@ -1,3 +1,4 @@ def f():\n a\n-b\n+c\n+d\n e\n\\ No newline at end of file"
        self.assertEqual(parse_patch("x.py", patch).commentable, {1, 2, 3, 4})

    def test_paths_with_spaces_are_read(self):
        sh(self.repo, "checkout", "-q", self.head)
        (self.repo / "My Folder").mkdir()
        (self.repo / "My Folder" / "a b.py").write_text("x = 1\n")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "spaces")
        files = parse(git.review_diff(self.repo, self.head, "HEAD"))
        self.assertEqual([f.path for f in files], ["My Folder/a b.py"])


class PlanTest(RepoTest):
    def test_what_is_reviewed_and_what_is_not(self):
        p = plan(list(self.files.values()), ReviewConfig())
        reviewed = {f.path for f in p.files}
        self.assertEqual(reviewed, {"app/loader.py", "app/writer.py", "snapshot/shared.py"})
        self.assertEqual({f.path for f in p.unchanged}, {"snapshot/same.py", "app/new_name.py"})
        self.assertEqual([f.path for f in p.deleted], ["app/gone.py"])
        self.assertEqual({s["path"] for s in p.skipped}, {"app/uv.lock", "app/logo.png"})

    def test_a_big_change_is_split_into_batches(self):
        p = plan(list(self.files.values()), ReviewConfig(batch_chars=600))
        self.assertEqual([b.paths for b in p.batches],
                         [["app/loader.py", "app/writer.py"], ["snapshot/shared.py"]])
        self.assertTrue(all(b.chars <= 600 for b in p.batches))
        # The copy alone is over the budget whole: it went up as its changes.
        self.assertIn("unchanged lines", p.batches[1].files[0].rendered.text)
        capped = plan(list(self.files.values()), ReviewConfig(batch_chars=600, max_batches=1))
        self.assertEqual(len(capped.batches), 1)
        self.assertIn({"path": "snapshot/shared.py", "reason": "over the review's size limit of 1 request"},
                      capped.skipped)

    def test_a_long_file_goes_up_as_its_changes(self):
        p = plan([self.files["snapshot/shared.py"]], ReviewConfig(whole_file_lines=10, context_lines=2))
        text = p.files[0].rendered.text
        self.assertIn("copied from `app/shared.py`", text)
        self.assertIn("unchanged lines", text)
        self.assertIn("return x - 7", text)


def batch_for(files, paths, config=None):
    p = plan([files[path] for path in paths], config or ReviewConfig())
    return p.batches[0]


class FindingsTest(RepoTest):
    def test_findings_are_checked_against_what_was_shown(self):
        batch = batch_for(self.files, ["app/loader.py", "app/writer.py"])
        batch.excerpts = [{"rank": 1, "path": "app/docs/architecture.md", "section": "Layers", "text": "..."}]
        raw = [
            {"category": "bug", "severity": "high", "file": "app/writer.py", "line": 2,
             "title": "File is never closed", "comment": "Use `with`.", "sources": [1, 7]},
            {"category": "design", "severity": "major", "file": "writer.py", "line": 999,
             "title": "Opens files outside storage", "comment": "..."},
            {"category": "bug", "file": "app/nowhere.py", "line": 1, "title": "Invented", "comment": "x"},
            {"category": "nit", "file": "./app/loader.py:9", "line": "L9", "end_line": 3,
             "title": "Return value", "comment": "..."},
        ]
        found, dropped = read_findings(raw, batch)
        self.assertEqual(len(found), 3)
        first, second, third = found
        self.assertEqual((first.path, first.line, first.sources), ("app/writer.py", 2, ["app/docs/architecture.md › Layers"]))
        self.assertIn('    f = open(path, "w")', first.line_text.splitlines())
        # A shortened path is matched back; a line the model was not shown is dropped to file level.
        self.assertEqual((second.category, second.severity, second.path, second.line),
                         ("architecture", "high", "app/writer.py", None))
        self.assertEqual((third.category, third.path, third.line, third.end_line),
                         ("recommendation", "app/loader.py", 9, None))
        self.assertEqual(len(dropped), 1)
        self.assertIn("app/nowhere.py", dropped[0])

    def test_the_fingerprint_follows_the_line_not_the_wording(self):
        a = Finding("bug", "high", "a.py", 3, None, "One wording", "x", line_text="  f = open(p)  ")
        b = Finding("bug", "low", "a.py", 7, None, "Another wording", "y", line_text="f = open(p)")
        c = Finding("architecture", "high", "a.py", 3, None, "One wording", "x", line_text="f = open(p)")
        self.assertEqual(a.fingerprint, b.fingerprint)
        self.assertNotEqual(a.fingerprint, c.fingerprint)
        self.assertEqual(fingerprints_in([inline_body(a)]), {a.fingerprint})
        # The same line text with other neighbours is another place.
        d = Finding("bug", "high", "a.py", 3, None, "t", "x", line_text="x = 1\nreturn None\n}")
        e = Finding("bug", "high", "a.py", 9, None, "t", "x", line_text="y = 2\nreturn None\n}")
        self.assertNotEqual(d.fingerprint, e.fingerprint)

    def test_a_finding_anchors_to_its_line_and_neighbours_and_takes_any_sources(self):
        batch = batch_for(self.files, ["app/writer.py"])
        [f], _ = read_findings([{"category": "bug", "file": "app/writer.py", "line": 2, "title": "t",
                                 "comment": "c", "sources": 3}], batch)
        self.assertEqual(f.line_text, 'def write(path, text):\n    f = open(path, "w")\n    f.write(text)')
        self.assertEqual(f.sources, [])

    def test_a_suggestion_with_backticks_gets_a_longer_fence(self):
        f = Finding("recommendation", "low", "a.md", 1, None, "t", "c", suggestion="```\ncode\n```")
        self.assertIn("````markdown\n```\ncode\n```\n````", inline_body(f))


class KnowledgeTest(RepoTest):
    def test_excerpts_come_from_the_rest_of_the_project(self):
        cache = self.root / "cache"
        knowledge = Knowledge.build(self.repo / "app", "app/", cache, HashEmbedder(), log=QUIET)
        self.assertGreater(knowledge.describe()["chunks"], 0)
        batch = batch_for(self.files, ["app/loader.py", "app/writer.py"])
        hits = knowledge.for_batch(batch, per_file=3, max_hits=4, max_chars=10_000,
                                   extra_query="storage writes files atomically")
        paths = {h["path"] for h in hits}
        self.assertIn("app/docs/architecture.md", paths)
        self.assertNotIn("app/loader.py", paths)          # in the request already
        self.assertNotIn("app/writer.py", paths)
        self.assertEqual([h["rank"] for h in hits], list(range(1, len(hits) + 1)))
        self.assertLessEqual(len(hits), 4)
        self.assertIn("app/writer.py", query_for(self.files["app/writer.py"]))
        # A second build embeds nothing that did not change.
        again = Knowledge.build(self.repo / "app", "app/", cache, HashEmbedder(), log=QUIET)
        self.assertEqual(again.describe()["embedded_now"], 0)


class ReviewerTest(RepoTest):
    def test_a_review_end_to_end(self):
        knowledge = Knowledge.build(self.repo / "app", "app/", self.root / "cache", HashEmbedder(), log=QUIET)
        llm = FakeLLM("not json at all", {
            "summary": "Adds a writer.",
            "findings": [{"category": "bug", "severity": "high", "file": "app/writer.py", "line": 2,
                          "end_line": 3, "title": "File handle leaks", "comment": "Use `with open`.",
                          "suggestion": "with open(path, 'w') as f:\n    f.write(text)", "sources": [1]}],
        })
        result = Reviewer(llm, ReviewConfig(), knowledge, log=QUIET).review(
            self.repo, "main", "feature", "Feature: write files", "Adds writer.py")
        self.assertTrue(result.ok)
        self.assertEqual(result.requests, 2)                 # one retry for the bad JSON
        self.assertEqual(result.merge_base, self.base)
        sent = llm.bodies[0]
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertEqual(sent["reasoning_effort"], "medium")
        user = sent["messages"][1]["content"]
        self.assertIn("Pull request: Feature: write files", user)
        self.assertIn("app/docs/architecture.md", user)      # an excerpt
        self.assertIn('2 | +     f = open(path, "w")', user)  # a numbered line
        self.assertIn("Files deleted by the change: `app/gone.py`", user)
        self.assertNotIn("uv.lock", user)
        [finding] = result.findings
        self.assertEqual((finding.path, finding.line, finding.end_line), ("app/writer.py", 2, 3))
        self.assertTrue(finding.sources)
        markdown = summary_markdown(result)
        self.assertIn(SUMMARY_MARKER, markdown)
        self.assertIn("1 potential bug", markdown)
        self.assertIn("File handle leaks", markdown)
        self.assertIn("snapshot/shared.py ← app/shared.py", markdown)
        self.assertIn("`app/logo.png` (binary)", markdown)

    def test_batches_go_up_in_parallel_each_knowing_the_others(self):
        llm = FakeLLM({"summary": "one", "findings": []}, {"summary": "two", "findings": []})
        config = ReviewConfig(batch_chars=600, parallel=2)
        result = Reviewer(llm, config, None, knowledge_problem=NO_RAG, log=QUIET).review(self.repo, self.base, self.head)
        self.assertEqual(result.requests, 2)
        self.assertEqual(sorted(result.summaries), ["one", "two"])
        users = sorted(b["messages"][1]["content"] for b in llm.bodies)
        part1 = next(u for u in users if "part 1 of 2" in u)
        part2 = next(u for u in users if "part 2 of 2" in u)
        self.assertIn("the other parts cover: `snapshot/shared.py`", part1)
        self.assertIn("the other parts cover: `app/loader.py`, `app/writer.py`", part2)

    def test_a_search_that_fails_half_way_keeps_the_excerpts_found(self):
        class HalfKnowledge:
            calls = 0

            def for_batch(self, batch, *args, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    raise KnowledgeError("Ollama stopped answering")
                return [{"rank": 1, "path": "app/docs/architecture.md", "section": "Layers", "text": "LAYERS"}]

            def describe(self):
                return {"root": "app", "embedder": "x", "documents": 1, "chunks": 1, "embedded_now": 0,
                        "seconds": 0}

        llm = FakeLLM({"summary": "", "findings": []}, {"summary": "", "findings": []})
        result = Reviewer(llm, ReviewConfig(batch_chars=600, parallel=1), HalfKnowledge(), log=QUIET).review(
            self.repo, self.base, self.head)
        first, second = (b["messages"][1]["content"] for b in llm.bodies)
        self.assertIn("[1] app/docs/architecture.md › Layers\nLAYERS", first)
        self.assertIn("(none - the project was not searched: the search failed: Ollama stopped answering)", second)
        self.assertEqual(result.knowledge_problem, "the search failed: Ollama stopped answering")
        self.assertIn("some parts without excerpts - the search failed", summary_markdown(result))

    def test_a_reply_cut_off_is_asked_again_for_less(self):
        class CutLLM(FakeLLM):
            def complete(self, body):
                response = super().complete(body)
                if len(self.bodies) == 1:
                    response.response["choices"][0].update(finish_reason="length",
                                                           message={"content": '{"summary": "half'})
                return response

        llm = CutLLM({}, {"summary": "short", "findings": []})
        result = Reviewer(llm, ReviewConfig(), None, knowledge_problem=NO_RAG, log=QUIET).review(self.repo, self.base, self.head)
        self.assertTrue(result.ok)
        retry = llm.bodies[1]["messages"]
        self.assertEqual(len(retry), 2)                     # the half reply is not sent back
        self.assertIn("cut off at the output limit", retry[1]["content"])

    def test_an_unexpected_reply_fails_its_batch_only(self):
        llm = FakeLLM(RuntimeError("bad shape"), {"summary": "the other part", "findings": []})
        result = Reviewer(llm, ReviewConfig(batch_chars=600, parallel=1), None, knowledge_problem=NO_RAG,
                          log=QUIET).review(self.repo, self.base, self.head)
        self.assertEqual([r.error for r in result.failed], ["RuntimeError: bad shape"])
        self.assertEqual(result.summaries, ["the other part"])
        self.assertTrue(result.ok)

    def test_a_long_summary_is_compacted_to_fit_a_github_comment(self):
        many = [{"category": "bug", "severity": "low", "file": "app/writer.py", "title": f"Finding {i}",
                 "comment": "x" * 2000, "suggestion": "y" * 2000} for i in range(40)]
        result = Reviewer(FakeLLM({"summary": "s", "findings": many}), ReviewConfig(), None, knowledge_problem=NO_RAG,
                          log=QUIET).review(self.repo, self.base, self.head)
        markdown = summary_markdown(result)
        self.assertLess(len(markdown), 65_536)
        self.assertIn("Finding 39", markdown)
        self.assertNotIn("Possible fix", markdown)

    def test_a_failed_request_is_reported_not_raised(self):
        llm = FakeLLM(LLMClientError("DeepSeek rate limit hit (HTTP 429)."))
        result = Reviewer(llm, ReviewConfig(), None, knowledge_problem="switched off", log=QUIET).review(
            self.repo, self.base, self.head)
        self.assertFalse(result.ok)
        markdown = summary_markdown(result)
        self.assertIn("The review failed: DeepSeek rate limit hit", markdown)
        self.assertIn("not used - switched off", markdown)
        self.assertIn("(none - the project was not searched: switched off)",
                      llm.bodies[0]["messages"][1]["content"])


class FakeGitHub:
    """The REST API in memory: PR files, review comments, issue comments."""

    def __init__(self, files, review_comments=(), issue_comments=(), refuse_review=False, refuse_lines=(),
                 fail_comments_with=None):
        self.files = files
        self.review_comments = list(review_comments)
        self.issue_comments = list(issue_comments)
        self.refuse_review = refuse_review
        self.refuse_lines = set(refuse_lines)
        self.fail_comments_with = fail_comments_with
        self.requests: list[tuple[str, str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, body))
        page = int(request.url.params.get("page", 1))
        if request.method == "GET":
            if path.endswith("/files"):
                return httpx.Response(200, json=self.files if page == 1 else [])
            if path.endswith("/pulls/7/comments"):
                return httpx.Response(200, json=self.review_comments if page == 1 else [])
            if "/reviews/" in path:
                return httpx.Response(200, json=[c for c in self.review_comments if c.get("review_id") == 99]
                                      if page == 1 else [])
            if path.endswith("/issues/7/comments"):
                return httpx.Response(200, json=self.issue_comments if page == 1 else [])
        if request.method == "POST" and path.endswith("/reviews"):
            if self.refuse_review:
                return httpx.Response(422, json={"message": "Unprocessable Entity",
                                                 "errors": ["Line could not be resolved"]})
            for c in body["comments"]:
                self.review_comments.append({**c, "review_id": 99, "html_url": f"https://c/{c['line']}"})
            return httpx.Response(200, json={"id": 99, "html_url": "https://review/99"})
        if request.method == "POST" and path.endswith("/pulls/7/comments"):
            if self.fail_comments_with:
                return httpx.Response(self.fail_comments_with, json={"message": "rate limit exceeded"})
            if body["line"] in self.refuse_lines:
                return httpx.Response(422, json={"message": "line must be part of the diff"})
            return httpx.Response(201, json={"html_url": f"https://c/{body['line']}"})
        if request.method == "POST" and path.endswith("/issues/7/comments"):
            return httpx.Response(201, json={"id": 1, "html_url": "https://summary/new"})
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": 5, "html_url": "https://summary/5"})
        return httpx.Response(404, json={"message": f"no route {request.method} {path}"})


PR = PullRequest(repo="me/repo", number=7, title="t", body="", base_sha="b" * 40, head_sha="h" * 40,
                 base_ref="main", head_ref="feature", head_repo="me/repo", html_url="")


def a_result(repo, base, head, findings):
    llm = FakeLLM({"summary": "ok", "findings": findings})
    return Reviewer(llm, ReviewConfig(), None, knowledge_problem=NO_RAG, log=QUIET).review(repo, base, head)


class PublishTest(RepoTest):
    FINDINGS = [
        {"category": "bug", "severity": "high", "file": "app/writer.py", "line": 2, "title": "Leak", "comment": "c"},
        {"category": "architecture", "severity": "medium", "file": "app/writer.py", "line": 3,
         "title": "Writes outside storage", "comment": "c"},
        {"category": "recommendation", "severity": "low", "file": "app/loader.py", "line": 1,
         "title": "Docstring", "comment": "far from the change"},
    ]

    def github_files(self):
        # GitHub's own patches: 3 lines of context, no copies.
        return [{"filename": f.path, "patch": patch_text(f)}
                for f in parse(git.github_diff(self.repo, self.base, self.head)) if f.hunks]

    def gh(self, fake):
        return GitHub("token", "me/repo", transport=httpx.MockTransport(fake.handler))

    def test_comments_on_the_code_and_one_summary(self):
        result = a_result(self.repo, self.base, self.head, self.FINDINGS)
        fake = FakeGitHub(self.github_files())
        out = publish(self.gh(fake), PR, result, ReviewConfig(), log=QUIET)
        [review] = [b for m, p, b in fake.requests if m == "POST" and p.endswith("/reviews")]
        self.assertEqual(review["commit_id"], "h" * 40)
        self.assertEqual(review["event"], "COMMENT")
        self.assertEqual([(c["path"], c["line"], c["side"]) for c in review["comments"]],
                         [("app/writer.py", 2, "RIGHT"), ("app/writer.py", 3, "RIGHT")])
        self.assertEqual(out.inline, 2)
        # Line 1 of loader.py is not in GitHub's hunk: the summary has it, with a link.
        self.assertIn("/blob/" + "h" * 40 + "/app/loader.py#L1", out.summary)
        self.assertIn("outside the part of the file GitHub shows", out.summary)
        self.assertIn("[`app/writer.py:2`](https://c/2) · on the code", out.summary)
        [(method, path, body)] = [r for r in fake.requests if r[1].endswith("/issues/7/comments") and r[0] == "POST"]
        self.assertTrue(body["body"].startswith(SUMMARY_MARKER))

    def test_the_next_push_does_not_repeat_a_comment_and_updates_the_summary(self):
        result = a_result(self.repo, self.base, self.head, self.FINDINGS[:2])
        first = [f for f in result.findings if f.line == 2][0]
        fake = FakeGitHub(self.github_files(),
                          review_comments=[{"body": inline_body(first), "html_url": "https://old/1"}],
                          issue_comments=[{"id": 5, "body": SUMMARY_MARKER + " old", "user": {"type": "Bot"}}])
        out = publish(self.gh(fake), PR, result, ReviewConfig(), log=QUIET)
        [review] = [b for m, p, b in fake.requests if m == "POST" and p.endswith("/reviews")]
        self.assertEqual([c["line"] for c in review["comments"]], [3])
        self.assertEqual(out.repeated, 1)
        self.assertIn("(https://old/1)", out.summary)
        self.assertTrue(any(m == "PATCH" and p.endswith("/issues/comments/5") for m, p, _ in fake.requests))

    def test_a_refused_review_falls_back_to_single_comments(self):
        result = a_result(self.repo, self.base, self.head, self.FINDINGS[:2])
        fake = FakeGitHub(self.github_files(), refuse_review=True, refuse_lines={3})
        out = publish(self.gh(fake), PR, result, ReviewConfig(), log=QUIET)
        self.assertEqual((out.inline, out.refused), (1, 1))
        self.assertIn("GitHub did not accept a comment on this line (422)", out.summary)

    def test_a_file_github_shows_no_diff_for_is_summary_only(self):
        f = Finding("bug", "high", "big.py", 2, None, "t", "c", line_text="x")
        self.assertEqual(place([f], {}, {}, max_inline=5), [])
        self.assertIn("GitHub shows no diff for this file", f.note)

    def test_the_summary_written_with_a_personal_token_is_found_by_its_login(self):
        result = a_result(self.repo, self.base, self.head, [])
        fake = FakeGitHub(self.github_files(), issue_comments=[
            {"id": 4, "body": SUMMARY_MARKER + " quoted", "user": {"login": "someone", "type": "User"}},
            {"id": 5, "body": SUMMARY_MARKER + " old", "user": {"login": "me", "type": "User"}}])
        handler = fake.handler
        fake.handler = lambda r: (httpx.Response(200, json={"login": "me"}) if r.url.path == "/user"
                                  else handler(r))
        publish(self.gh(fake), PR, result, ReviewConfig(), log=QUIET)
        self.assertEqual([p for m, p, _ in fake.requests if m == "PATCH"], ["/repos/me/repo/issues/comments/5"])

    def test_a_github_failure_in_the_fallback_fails_the_job_but_posts_the_summary(self):
        result = a_result(self.repo, self.base, self.head, self.FINDINGS[:2])
        fake = FakeGitHub(self.github_files(), refuse_review=True, fail_comments_with=429)
        with self.assertRaisesRegex(GitHubError, "HTTP 429"):
            publish(self.gh(fake), PR, result, ReviewConfig(), log=QUIET)
        single = [r for r in fake.requests if r[0] == "POST" and r[1].endswith("/pulls/7/comments")]
        self.assertEqual(len(single), 1)                   # the second is not tried
        [(_, _, summary)] = [r for r in fake.requests if r[1].endswith("/issues/7/comments") and r[0] == "POST"]
        self.assertIn("GitHub failed (429)", summary["body"])
        self.assertNotIn("did not accept a comment on this line", summary["body"])

    def test_place_respects_the_limit_and_ranges(self):
        f1 = Finding("bug", "high", "a.py", 2, 4, "t", "c", line_text="x")
        f2 = Finding("bug", "low", "a.py", 9, None, "t2", "c", line_text="y")
        chosen = place([f1, f2], {"a.py": {2, 3, 9}}, {}, max_inline=1)
        self.assertEqual(chosen, [f1])
        self.assertIsNone(f1.end_line)                   # line 4 is not in the hunk
        self.assertIn("limit of 1", f2.note)


def patch_text(f) -> str:
    out = []
    for h in f.hunks:
        out.append(f"@@ -{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@ {h.header}".rstrip())
        out += [line.kind + line.text for line in h.lines]
    return "\n".join(out)


class ConfigTest(unittest.TestCase):
    def test_model_overrides(self):
        config = ReviewConfig.from_dict({"models": {"local": {"batch_chars": 100}}})
        self.assertEqual(config.for_model("local").batch_chars, 100)
        self.assertEqual(config.for_model("other").batch_chars, ReviewConfig().batch_chars)
        with self.assertRaisesRegex(ValueError, "models.local: unknown field.*model"):
            ReviewConfig.from_dict({"models": {"local": {"model": "x"}}})
        with self.assertRaisesRegex(ValueError, "unknown field"):
            ReviewConfig.from_dict({"modle": "x"})

    def test_reasoning_effort_is_checked_on_load(self):
        self.assertEqual(ReviewConfig().reasoning_effort, "medium")
        self.assertIsNone(ReviewConfig.from_dict({"reasoning_effort": None}).reasoning_effort)
        with self.assertRaisesRegex(ValueError, "reasoning_effort 'meduim' is not one of"):
            ReviewConfig.from_dict({"reasoning_effort": "meduim"})
        with self.assertRaisesRegex(ValueError, "models.local: reasoning_effort"):
            ReviewConfig.from_dict({"models": {"local": {"reasoning_effort": "maximum"}}})
        body = Reviewer(None, ReviewConfig(reasoning_effort="off")).body([])
        self.assertEqual(body["reasoning_effort"], "none")

    def test_a_repository_config_is_laid_over_the_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, mine = Path(tmp) / "review.json", Path(tmp) / "ai-review.json"
            base.write_text(json.dumps({"batch_chars": 100, "language": "English",
                                        "models": {"local": {"batch_chars": 10, "parallel": 1}}}))
            mine.write_text(json.dumps({"language": "Russian", "skip": ["*.gen.py"],
                                        "models": {"local": {"parallel": 2}}}))
            config = ReviewConfig.load(base, mine)
            self.assertEqual((config.batch_chars, config.language, config.skip), (100, "Russian", ["*.gen.py"]))
            self.assertEqual(config.models["local"], {"batch_chars": 10, "parallel": 2})
            with self.assertRaisesRegex(ValueError, "does not exist"):
                ReviewConfig.load(base, Path(tmp) / "missing.json")
            mine.write_text(json.dumps({"batch_chrs": 1}))
            with self.assertRaisesRegex(ValueError, "review.json \\+ ai-review.json: unknown field"):
                ReviewConfig.load(base, mine)

    def test_the_shipped_review_json_loads(self):
        config = ReviewConfig.load()
        self.assertEqual((config.model, config.reasoning_effort), ("deepseek-v4-flash", "medium"))
        self.assertEqual(config.for_model("local-qwen3-8b").reasoning_effort, "off")
        self.assertTrue(config.skipped("app/uv.lock"))
        self.assertFalse(config.skipped("app/main.py"))


class EntryTest(RepoTest):
    def test_auto_project_is_the_folder_most_of_the_change_is_in(self):
        self.assertEqual(entry.auto_project(["day32/a.py", "day32/b.py", ".github/w.yml"]), "day32")
        self.assertEqual(entry.auto_project([".github/w.yml"]), ".")
        self.assertEqual(entry.auto_project(["README.md", "setup.py", "src/a.py"]), ".")

    def test_a_local_run_prints_the_review_and_posts_nothing(self):
        llm = FakeLLM({"summary": "Looks fine.", "findings": []})
        out = io.StringIO()
        with mock.patch.object(entry, "build_llm", return_value=(llm, None)), \
             mock.patch.dict("os.environ", {"GITHUB_EVENT_PATH": "", "GITHUB_STEP_SUMMARY": ""}), \
             redirect_stdout(out):
            code = entry.main(["--repo", str(self.repo), "--base", "main", "--head", "feature",
                               "--no-rag", "--out", str(self.root / "review.md")])
        self.assertEqual(code, 0)
        self.assertIn("No problems found", out.getvalue())
        self.assertIn("Looks fine.", (self.root / "review.md").read_text())

    def test_a_pull_request_from_a_fork_is_not_reviewed(self):
        event = {"pull_request": {"number": 1, "title": "x", "body": None, "html_url": "",
                                  "base": {"sha": self.base, "ref": "main", "repo": {"full_name": "me/repo"}},
                                  "head": {"sha": self.head, "ref": "f", "repo": {"full_name": "stranger/repo"}}},
                 "repository": {"full_name": "me/repo"}}
        path = self.root / "event.json"
        path.write_text(json.dumps(event))
        out = io.StringIO()
        with mock.patch.object(entry, "build_llm") as build, \
             mock.patch.dict("os.environ", {"GITHUB_EVENT_PATH": str(path)}), redirect_stdout(out):
            self.assertEqual(entry.main(["--repo", str(self.repo)]), 0)
        build.assert_not_called()
        self.assertIn("comes from a fork", out.getvalue())


if __name__ == "__main__":
    unittest.main()
