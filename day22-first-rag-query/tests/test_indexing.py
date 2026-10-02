"""Day 21: the indexing pipeline - loaders, chunkers, store, check, and the tab's API.

    uv run python -m unittest discover -s tests -v

No model is needed. The embedder here is a bag of hashed words: crude, but
deterministic, instant, and good enough that a query sharing rare words with
one chunk ranks that chunk first - which is all the store, the cache and the
retrieval check need to be exercised end to end. Ollama itself is covered by
the one thing this file cannot fake: running the app.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.chunkers import FixedChunker, StructuralChunker, make_chunker  # noqa: E402
from rag.documents import count_words  # noqa: E402
from rag.embedders import Embedder  # noqa: E402
from rag.evaluation import Expected, Question, evaluate, matches  # noqa: E402
from rag.index_store import IndexStore  # noqa: E402
from rag.loaders import load_text  # noqa: E402
from rag.pipeline import Corpus, build_index, chunk_stats  # noqa: E402


class HashEmbedder(Embedder):
    name = "test:hash"

    def __init__(self):
        self.calls = 0

    def _embed(self, texts):
        self.calls += len(texts)
        rows = []
        for text in texts:
            row = [0.0] * 256
            for word in re.findall(r"\w+", text.lower()):
                row[int(hashlib.md5(word.encode()).hexdigest(), 16) % 256] += 1.0
            rows.append(row)
        return rows


def words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}." if i % 12 == 11 else f"{tag}{i}" for i in range(n))


MARKDOWN = f"""# The Project

Intro paragraph under the title.

## Setup

{words(30, "setup")}

```bash
# install deps - a comment, not a heading
uv sync
```

### Tokens

Short.

## Routing

{words(120, "alpha")}

{words(120, "beta")}

{words(120, "gamma")}

## Zebra

The zebra section mentions quokka and narwhal and nothing else here.
"""

PYTHON = '''"""Module docstring about widgets."""

import os

CONSTANT = 1


def helper(x):
    """Help."""
    return x


class Widget:
    """A widget."""

    size = 3

    def spin(self):
        return 1

    async def stop(self):
        return 2
'''


class LoaderTest(unittest.TestCase):
    def test_markdown_paths_title_and_fences(self):
        doc = load_text("docs/guide.md", MARKDOWN)
        labels = [s.label for s in doc.sections]
        self.assertEqual(doc.title, "The Project")
        self.assertEqual(labels, ["(intro)", "Setup", "Setup › Tokens", "Routing", "Zebra"])
        # The `# install deps` inside the fence did not start a section.
        setup = doc.sections[1]
        self.assertIn("# install deps", doc.section_text(setup))
        # Sections are ranges into the file as read, in order, not overlapping.
        for a, b in zip(doc.sections, doc.sections[1:]):
            self.assertLessEqual(a.end, b.start)

    def test_markdown_with_several_h1_keeps_them_in_the_path(self):
        doc = load_text("x.md", "# A\n\ntext\n\n## A1\n\nmore\n\n# B\n\nlast\n")
        self.assertEqual([s.label for s in doc.sections], ["A", "A › A1", "B"])
        self.assertEqual(doc.title, "x.md")

    def test_python_outline(self):
        doc = load_text("pkg/widgets.py", PYTHON)
        labels = [s.label for s in doc.sections]
        self.assertEqual(labels[0], "module docstring")
        self.assertIn("module level", labels)
        self.assertIn("def helper", labels)
        self.assertIn("class Widget", labels)
        self.assertIn("class Widget › def spin", labels)
        self.assertIn("class Widget › def stop", labels)
        spin = next(s for s in doc.sections if s.label == "class Widget › def spin")
        self.assertIn("return 1", doc.section_text(spin))

    def test_python_that_does_not_parse_falls_back_to_one_section(self):
        doc = load_text("broken.py", "def (:\n  oops\n")
        self.assertEqual(doc.format, "python")
        self.assertEqual(len(doc.sections), 1)

    def test_plain_text_is_one_section(self):
        doc = load_text("notes.txt", "one\n\ntwo\n")
        self.assertEqual([s.kind for s in doc.sections], ["file"])

    def test_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            load_text("image.png", "...")


class ChunkerTest(unittest.TestCase):
    def setUp(self):
        self.doc = load_text("guide.md", MARKDOWN)

    def test_fixed_windows_and_overlap(self):
        chunks = FixedChunker(size=50, overlap=10).chunk(self.doc)
        self.assertTrue(all(c.n_words <= 50 for c in chunks))
        self.assertTrue(all(c.n_words == 50 for c in chunks[:-1]))
        # Consecutive chunks share exactly `overlap` words.
        for a, b in zip(chunks, chunks[1:]):
            shared = self.doc.text[b.start:a.end]
            self.assertEqual(count_words(shared), 10)
        # Every word of the file is in some chunk.
        self.assertEqual(chunks[0].start, self.doc.text.index("#"))
        self.assertEqual(chunks[-1].end, len(self.doc.text.rstrip()))

    def test_fixed_metadata(self):
        chunk = FixedChunker(size=50, overlap=0).chunk(self.doc)[3]
        meta = chunk.metadata()
        for key in ("chunk_id", "source", "title", "section", "position", "start", "end"):
            self.assertIn(key, meta)
        self.assertEqual(meta["source"], "guide.md")
        self.assertEqual(meta["chunk_id"], "fixed:guide.md#0003")

    def test_overlap_must_be_smaller_than_the_chunk(self):
        with self.assertRaises(ValueError):
            FixedChunker(size=50, overlap=50)
        with self.assertRaises(ValueError):
            StructuralChunker(max_words=100, overlap=100)

    def test_structural_follows_sections(self):
        chunks = StructuralChunker(max_words=150, min_words=20, overlap=0).chunk(self.doc)
        by_section = {}
        for c in chunks:
            by_section.setdefault(c.section, []).append(c)
        # Routing (360 words) is split on its paragraphs, nothing else is.
        self.assertEqual(len(by_section["Routing"]), 3)
        self.assertTrue(all(c.n_words <= 150 for c in chunks))
        # Tiny "Tokens" was merged into its parent "Setup" (and so was the
        # five-word intro before it), and the chunk is labelled by the bigger part.
        self.assertNotIn("Setup › Tokens", by_section)
        self.assertEqual(by_section["Setup"][0].merged, 3)
        # Short "Zebra" was not glued onto the last slice of the long "Routing".
        self.assertEqual(len(by_section["Zebra"]), 1)
        self.assertNotIn("zebra", by_section["Routing"][-1].text.lower())
        # The fenced block stayed in one chunk.
        self.assertTrue(all(c.text.count("```") % 2 == 0 for c in chunks))

    def test_structural_overlap_repeats_the_end_of_the_previous_piece(self):
        chunks = [c for c in StructuralChunker(max_words=150, min_words=20, overlap=15).chunk(self.doc)
                  if c.section == "Routing"]
        for a, b in zip(chunks, chunks[1:]):
            self.assertLess(b.start, a.end)
            self.assertEqual(count_words(self.doc.text[b.start:a.end]), 15)
            self.assertLessEqual(b.n_words, 150)

    def test_context_prefix_goes_to_the_embedding_only(self):
        chunk = StructuralChunker(with_context=True).chunk(self.doc)[-1]
        self.assertTrue(chunk.embed_text.startswith("guide.md › Zebra\n\n"))
        self.assertNotIn("guide.md", chunk.text)
        plain = StructuralChunker(with_context=False).chunk(self.doc)[-1]
        self.assertEqual(plain.embed_text, plain.text)

    def test_make_chunker_validates_and_casts(self):
        chunker = make_chunker("fixed", {"size": "80", "overlap": 5, "bogus": 1})
        self.assertEqual(chunker.params(), {"size": 80, "overlap": 5, "with_context": False})
        with self.assertRaises(ValueError):
            make_chunker("semantic")

    def test_stats_tell_the_strategies_apart(self):
        fixed = chunk_stats(FixedChunker(size=40, overlap=10).chunk(self.doc), [self.doc])
        structural = chunk_stats(StructuralChunker(max_words=150, min_words=20).chunk(self.doc), [self.doc])
        self.assertGreater(fixed["mid_sentence_share"], structural["mid_sentence_share"])
        self.assertGreater(fixed["cross_section_share"], structural["cross_section_share"])
        self.assertGreater(fixed["redundancy"], 1.0)


class StoreAndCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IndexStore(Path(self.tmp.name) / "index.sqlite")
        self.embedder = HashEmbedder()
        docs = [load_text("guide.md", MARKDOWN), load_text("widgets.py", PYTHON)]
        self.corpus = Corpus(docs)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, strategy="structural", **params):
        return build_index(strategy, self.corpus, make_chunker(strategy, params), self.embedder, self.store)

    def test_build_search_and_cache(self):
        record = self.build(max_words=150, min_words=20)
        self.assertEqual(record["embedder"], "test:hash")
        self.assertEqual(record["stats"]["from_cache"], 0)
        hits = self.store.search("structural", self.embedder.embed_query("quokka narwhal"), k=3)
        self.assertEqual(hits[0].chunk["section"], "Zebra")
        self.assertEqual(hits[0].rank, 1)
        # A rebuild with the same settings costs no model calls.
        calls = self.embedder.calls
        again = self.build(max_words=150, min_words=20)
        self.assertEqual(again["stats"]["embedded_now"], 0)
        self.assertEqual(self.embedder.calls, calls)
        # Clearing the cache makes the next build embed everything again.
        self.store.clear_cache()
        self.assertEqual(self.build(max_words=150, min_words=20)["stats"]["from_cache"], 0)

    def test_source_filter_and_reset(self):
        self.build()
        self.build("fixed", size=40, overlap=5)
        q = self.embedder.embed_query("widget spin")
        self.assertTrue(all(h.chunk["source"] == "widgets.py"
                            for h in self.store.search("fixed", q, k=5, source="widgets.py")))
        self.store.delete_index("fixed")
        self.assertEqual([i["name"] for i in self.store.indexes()], ["structural"])
        self.store.delete_index(None)
        self.assertEqual(self.store.indexes(), [])
        # The cache survives a reset.
        self.assertTrue(self.store.cache_stats())

    def test_section_range_covers_the_subtree(self):
        self.build()
        whole = self.store.section_range("guide.md", "Setup")
        child = self.store.section_range("guide.md", "Setup › Tokens")
        self.assertLessEqual(whole[0], child[0])
        self.assertGreaterEqual(whole[1], child[1])
        self.assertIsNone(self.store.section_range("guide.md", "Nope"))
        self.assertIsNotNone(self.store.section_range("guide.md", ""))

    def test_match_rule(self):
        chunk = {"source": "a.md", "start": 100, "end": 200}
        self.assertTrue(matches(chunk, "a.md", 90, 180))      # 80% of the chunk inside
        self.assertTrue(matches(chunk, "a.md", 190, 205))     # covers most of a tiny section
        self.assertFalse(matches(chunk, "a.md", 195, 400))    # brushes a long section
        self.assertFalse(matches(chunk, "b.md", 100, 200))

    def test_evaluate(self):
        self.build(max_words=150, min_words=20)
        self.build("fixed", size=40, overlap=5)
        questions = [
            Question("zebra", "quokka narwhal", [Expected("guide.md", "Zebra")]),
            Question("spin", "widget spin return", [Expected("widgets.py", "class Widget")]),
            Question("gone", "anything", [Expected("guide.md", "Removed section")]),
        ]
        result = evaluate(self.store, self.embedder, questions, ["structural", "fixed", "missing"])
        self.assertEqual(result["invalid"], 1)
        self.assertIn("missing", result["skipped"])
        rows = {r["id"]: r for r in result["questions"]}
        self.assertEqual(rows["zebra"]["results"]["structural"]["rank"], 1)
        self.assertFalse(rows["gone"]["valid"])
        for name in ("structural", "fixed"):
            s = result["summary"][name]
            self.assertEqual(s["questions"], 2)
            self.assertEqual(s["hit@5"], 1.0)


class ApiTest(unittest.TestCase):
    """The tab's endpoints, on a bare app - server.py would want a DeepSeek key."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["INDEX_DIR"] = cls.tmp.name
        import importlib

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import indexing_api
        cls.api = importlib.reload(indexing_api)
        cls.api.embedder = HashEmbedder()
        app = FastAPI()
        app.include_router(cls.api.router)
        cls.client = TestClient(app)
        # A small corpus of our own: one upload, and patterns that match nothing else.
        cls.client.put("/api/index/corpus", json={"patterns": ["no-such-file.md"]})
        r = cls.client.post("/api/index/uploads", json={"name": "guide.md", "content": MARKDOWN})
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("INDEX_DIR", None)
        cls.tmp.cleanup()

    def wait_for_job(self):
        for _ in range(100):
            job = self.client.get("/api/index/job").json()
            if not job.get("running"):
                return job
            time.sleep(0.05)
        self.fail("build did not finish")

    def test_whole_flow(self):
        c = self.client
        ov = c.get("/api/index").json()
        self.assertEqual([d["source"] for d in ov["corpus"]["documents"]], ["uploads/guide.md"])
        self.assertEqual(ov["embedder"]["status"]["ok"], True)

        preview = c.post("/api/index/preview", json={"strategy": "fixed", "params": {"size": 40, "overlap": 20},
                                                     "source": "uploads/guide.md"}).json()
        more = c.post("/api/index/preview", json={"strategy": "fixed", "params": {"size": 40, "overlap": 0}}).json()
        self.assertGreater(preview["stats"]["chunks"], more["stats"]["chunks"])
        self.assertEqual(len(preview["chunks"]), preview["shown_of"])
        self.assertEqual(c.post("/api/index/preview", json={"strategy": "fixed",
                                                            "params": {"size": 40, "overlap": 40}}).status_code, 400)

        r = c.post("/api/index/build", json={"items": [{"strategy": "fixed", "params": {"size": 40, "overlap": 5}},
                                                       {"strategy": "structural", "params": {"max_words": 150}}]})
        self.assertEqual(r.status_code, 200, r.text)
        job = self.wait_for_job()
        self.assertEqual(job["built"], ["fixed", "structural"], job)

        names = [i["name"] for i in c.get("/api/index").json()["indexes"]]
        self.assertEqual(names, ["fixed", "structural"])
        hits = c.post("/api/index/search", json={"query": "quokka narwhal", "k": 2}).json()["results"]
        self.assertEqual(hits["structural"]["hits"][0]["section"], "Zebra")

        chunks = c.get("/api/index/indexes/structural/chunks").json()
        self.assertGreater(chunks["total"], 0)
        self.assertIn("chunk_id", chunks["chunks"][0])

        sections = c.get("/api/index/sections", params={"source": "uploads/guide.md"}).json()["sections"]
        self.assertIn("Zebra", [s["label"] for s in sections])

        saved = c.put("/api/index/questions", json={"questions": [
            {"question": "quokka narwhal", "expected": [{"source": "uploads/guide.md", "section": "Zebra"}]},
            {"question": "   "},
        ]}).json()["questions"]
        self.assertEqual(len(saved), 1)
        result = c.post("/api/index/evaluate", json={"depth": 5}).json()
        self.assertEqual(result["summary"]["structural"]["hit@1"], 1.0)
        self.assertEqual(c.get("/api/index").json()["last_eval"]["summary"]["structural"]["hit@1"], 1.0)

        c.delete("/api/index/indexes/fixed")
        self.assertEqual([i["name"] for i in c.get("/api/index").json()["indexes"]], ["structural"])
        c.delete("/api/index/indexes")
        self.assertEqual(c.get("/api/index").json()["indexes"], [])

    def test_patterns_and_uploads_stay_inside(self):
        self.assertEqual(self.client.put("/api/index/corpus", json={"patterns": ["../secrets/*"]}).status_code, 400)
        self.assertEqual(self.client.put("/api/index/corpus", json={"patterns": ["/etc/*"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/index/uploads",
                                          json={"name": "run.sh", "content": "x"}).status_code, 400)
        r = self.client.post("/api/index/uploads", json={"name": "../../evil.md", "content": "# x\n"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue((Path(self.tmp.name) / "uploads" / "evil.md").exists())
        self.client.delete("/api/index/uploads/evil.md")
        self.client.put("/api/index/corpus", json={"patterns": ["no-such-file.md"]})


if __name__ == "__main__":
    unittest.main()
