"""Day 21: the indexing pipeline - loaders, the chunker, the store, and the tab's API.

    uv run python -m unittest discover -s tests -v

No model is needed. The embedder here is a bag of hashed words: crude, but
deterministic, instant, and good enough that a query sharing rare words with
one chunk ranks that chunk first - which is all the store, the cache and the
search need to be exercised end to end. Ollama itself is covered by the one
thing this file cannot fake: running the app.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
import unittest
from pathlib import Path

from core.errors import Conflict
from rag.augment import INDEX
from rag.chunkers import StructuralChunker, make_chunker
from rag.documents import count_words
from rag.embedders import Embedder
from rag.index_store import IndexStore
from rag.loaders import load_text
from rag.pipeline import Corpus, build_index, chunk_stats


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

    def test_metadata(self):
        chunk = StructuralChunker(max_words=150, min_words=20).chunk(self.doc)[1]
        meta = chunk.metadata()
        for key in ("chunk_id", "source", "title", "section", "position", "start", "end"):
            self.assertIn(key, meta)
        self.assertEqual(meta["source"], "guide.md")
        # The id an index built before there was only one chunker already uses.
        self.assertEqual(meta["chunk_id"], "structural:guide.md#0001")

    def test_overlap_must_be_smaller_than_the_chunk(self):
        with self.assertRaises(ValueError):
            StructuralChunker(max_words=100, overlap=100)
        with self.assertRaises(ValueError):
            StructuralChunker(max_words=5)

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
        chunker = make_chunker({"max_words": "80", "overlap": 5, "with_context": 0, "bogus": 1})
        self.assertEqual(chunker.params(), {"max_words": 80, "min_words": 60, "overlap": 5, "with_context": False})
        with self.assertRaises(ValueError):
            make_chunker({"min_words": 500})

    def test_stats_count_what_the_overlap_costs(self):
        plain = chunk_stats(StructuralChunker(max_words=150, min_words=20, overlap=0).chunk(self.doc), [self.doc])
        overlapped = chunk_stats(StructuralChunker(max_words=150, min_words=20, overlap=15).chunk(self.doc),
                                 [self.doc])
        self.assertEqual(plain["cross_section_share"], 0)
        self.assertEqual(plain["split_code_blocks"], 0)
        self.assertGreater(overlapped["redundancy"], plain["redundancy"])
        self.assertEqual(overlapped["embedded_words"] - plain["embedded_words"], 2 * 15)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IndexStore(Path(self.tmp.name) / "index.sqlite")
        self.embedder = HashEmbedder()
        docs = [load_text("guide.md", MARKDOWN), load_text("widgets.py", PYTHON)]
        self.corpus = Corpus(docs)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, name=INDEX, **params):
        return build_index(name, self.corpus, make_chunker(params), self.embedder, self.store)

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
        self.build("other", max_words=60, min_words=10)
        q = self.embedder.embed_query("widget spin")
        self.assertTrue(all(h.chunk["source"] == "widgets.py"
                            for h in self.store.search(INDEX, q, k=5, source="widgets.py")))
        self.store.delete_index("other")
        self.assertEqual([i["name"] for i in self.store.indexes()], [INDEX])
        self.store.delete_index(None)
        self.assertEqual(self.store.indexes(), [])
        # The cache survives a reset.
        self.assertTrue(self.store.cache_stats())


class CorpusConfigTest(unittest.TestCase):
    """`corpus.json`: include globs, `.gitignore`-like excludes, a size cap - over the project's folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for rel in ("README.md", "app.py", "pkg/mod.py", "pkg/deep/more.py", "tests/test_x.py",
                    "pkg/__pycache__/mod.py", ".venv/lib/site.py", "data/notes.md", "docs/guide.md"):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {rel}\n\nSome words about {rel}.\n" if rel.endswith(".md")
                            else f'"""{rel}."""\n\n\ndef f():\n    return 1\n')

    def tearDown(self):
        self.tmp.cleanup()

    def sources(self, config) -> list[str]:
        return [d.source for d in config.load_corpus().documents]

    def test_include_and_exclude(self):
        from rag.corpus import CorpusConfig
        config = CorpusConfig(root=str(self.root), include=["README.md", "**/*.py"],
                              exclude=[".venv", "tests", "__pycache__", "data"])
        self.assertEqual(self.sources(config), ["README.md", "app.py", "pkg/deep/more.py", "pkg/mod.py"])
        top = CorpusConfig(root=str(self.root), include=["*.py", "docs/*.md"], exclude=[])
        self.assertEqual(self.sources(top), ["app.py", "docs/guide.md"])
        path_rule = CorpusConfig(root=str(self.root), include=["**/*.py"], exclude=["pkg/deep", ".venv"])
        self.assertNotIn("pkg/deep/more.py", self.sources(path_rule))
        self.assertIn("pkg/mod.py", self.sources(path_rule))

    def test_the_file_and_the_defaults(self):
        import json as json_module

        from rag.corpus import DEFAULT_INCLUDE, CorpusConfig
        file = self.root / "corpus.json"
        file.write_text(json_module.dumps({"include": ["*.py"], "max_file_kb": 1}))
        config = CorpusConfig.load(file).at(self.root / "pkg")
        self.assertEqual(config.root_path, (self.root / "pkg").resolve())
        self.assertEqual(self.sources(config), ["mod.py"])
        self.assertEqual(config.describe()["config"], "corpus.json")
        self.assertEqual(CorpusConfig.load(self.root / "missing.json").include, DEFAULT_INCLUDE)
        (self.root / "pkg" / "big.py").write_text("x = 1\n" * 400)
        skipped = config.load_corpus().skipped
        self.assertIn({"source": "big.py", "reason": "larger than 1 KB"}, skipped)
        file.write_text(json_module.dumps({"patterns": ["*.md"]}))
        with self.assertRaisesRegex(ValueError, "patterns"):
            CorpusConfig.load(file)
        # Day 31: the folder is the open project's, not the file's.
        file.write_text(json_module.dumps({"root": "pkg"}))
        with self.assertRaisesRegex(ValueError, "Project tab"):
            CorpusConfig.load(file)

    def test_in_a_git_repository_the_files_are_the_ones_git_knows(self):
        import subprocess

        from rag.corpus import CorpusConfig
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("pkg/deep/\n")
        (self.root / ".env").write_text("SECRET=1\n")
        config = CorpusConfig(root=self.root, include=["**/*.py"], exclude=[".venv", "__pycache__"])
        sources = self.sources(config)
        self.assertIn("pkg/mod.py", sources)
        self.assertIn("tests/test_x.py", sources)
        self.assertNotIn("pkg/deep/more.py", sources)      # ignored by git
        self.assertNotIn(".venv/lib/site.py", sources)     # excluded by the config

    def test_the_shipped_config_indexes_this_project(self):
        from rag.corpus import CorpusConfig
        sources = self.sources(CorpusConfig.load())
        for source in ("README.md", "core/turn.py", "rag/loaders.py", "docs/architecture.md"):
            self.assertIn(source, sources)
        self.assertFalse([s for s in sources if s.startswith(("data/", ".venv/", "tests/fixtures/"))])


class ApiTest(unittest.TestCase):
    """The tab's endpoints, on the app built over a runtime with a fake model - no key, no Ollama."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        from fastapi.testclient import TestClient

        from core.runtime import AgentRuntime
        from rag.corpus import CorpusConfig
        from storage import ProjectStore
        from web.app import create_app

        # A small corpus of our own: one upload, and a corpus.json whose
        # root holds nothing the patterns match.
        root = Path(cls.tmp.name) / "repo"
        root.mkdir()
        # Day 31: the corpus is read from the open project's folder.
        ProjectStore(Path(cls.tmp.name) / "project").save(str(root))
        cls.rt = AgentRuntime.build(llm=object(), data_dir=cls.tmp.name, embedder=HashEmbedder(),
                                    corpus=CorpusConfig(include=["no-such-file.md"], exclude=[]))
        cls.api = cls.rt.index
        cls.client = TestClient(create_app(cls.rt))
        r = cls.client.post("/api/index/uploads", json={"name": "guide.md", "content": MARKDOWN})
        assert r.status_code == 200, r.text

    @classmethod
    def tearDownClass(cls):
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
        self.assertEqual(ov["chunker"]["defaults"], StructuralChunker().params())
        self.assertNotIn("strategies", ov)
        # Before a build the chat's search is refused, not answered from nothing.
        with self.assertRaisesRegex(Conflict, "no 'structural' index"):
            self.api.retrieve("quokka narwhal", 3)

        preview = c.post("/api/index/preview", json={"params": {"max_words": 60, "min_words": 10, "overlap": 20},
                                                     "source": "uploads/guide.md"}).json()
        more = c.post("/api/index/preview", json={"params": {"max_words": 300, "min_words": 10}}).json()
        self.assertGreater(preview["stats"]["chunks"], more["stats"]["chunks"])
        self.assertEqual(len(preview["chunks"]), preview["shown_of"])
        self.assertEqual(c.post("/api/index/preview", json={"params": {"max_words": 40, "overlap": 40}}).status_code,
                         400)

        r = c.post("/api/index/build", json={"params": {"max_words": 150, "min_words": 20}})
        self.assertEqual(r.status_code, 200, r.text)
        job = self.wait_for_job()
        self.assertEqual(job["stage"], "done", job)

        names = [i["name"] for i in c.get("/api/index").json()["indexes"]]
        self.assertEqual(names, [INDEX])
        found = c.post("/api/index/search", json={"query": "quokka narwhal", "k": 2}).json()
        self.assertEqual((found["index"], found["hits"][0]["section"]), (INDEX, "Zebra"))
        self.assertEqual(self.api.retrieve("quokka narwhal", 3).hits[0]["section"], "Zebra")

        chunks = c.get(f"/api/index/indexes/{INDEX}/chunks").json()
        self.assertGreater(chunks["total"], 0)
        self.assertIn("chunk_id", chunks["chunks"][0])

        c.delete("/api/index/indexes")
        self.assertEqual(c.get("/api/index").json()["indexes"], [])

    def test_a_leftover_index_in_the_file_is_never_read(self):
        # A file built when there was a second chunker still holds its rows.
        loaded = self.api.corpus()
        build_index("fixed", loaded, make_chunker({"max_words": 60, "min_words": 10}), self.api.embedder, self.api.store)
        build_index(INDEX, loaded, make_chunker({"max_words": 150, "min_words": 20}), self.api.embedder,
                    self.api.store)
        c = self.client
        self.assertEqual([i["name"] for i in c.get("/api/index").json()["indexes"]], [INDEX])
        found = c.post("/api/index/search", json={"query": "quokka narwhal", "k": 20}).json()
        self.assertTrue(all(h["chunk_id"].startswith(INDEX + ":") for h in found["hits"]))
        self.assertEqual(self.api.retrieve("quokka narwhal", 3).index, INDEX)
        # Reset drops it with everything else.
        c.delete("/api/index/indexes")
        self.assertEqual(self.api.store.indexes(), [])

    def test_the_deleted_endpoints_are_gone(self):
        for method, path in (("get", "/api/index/questions"), ("put", "/api/index/questions"),
                             ("get", "/api/index/evaluate"), ("post", "/api/index/evaluate"),
                             ("get", "/api/index/sections?source=uploads/guide.md")):
            self.assertIn(getattr(self.client, method)(path).status_code, (404, 405), path)

    def test_the_corpus_is_shown_not_edited_and_uploads_stay_inside(self):
        corpus = self.client.get("/api/index").json()["corpus"]
        self.assertEqual((corpus["include"], corpus["exclude"]), (["no-such-file.md"], []))
        self.assertTrue(corpus["root_path"].endswith("repo"))
        self.assertIn(self.client.put("/api/index/corpus", json={"patterns": ["*"]}).status_code, (404, 405))
        self.assertEqual(self.client.post("/api/index/uploads",
                                          json={"name": "run.sh", "content": "x"}).status_code, 400)
        r = self.client.post("/api/index/uploads", json={"name": "../../evil.md", "content": "# x\n"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue((Path(self.tmp.name) / "index" / "uploads" / "evil.md").exists())
        self.client.delete("/api/index/uploads/evil.md")


if __name__ == "__main__":
    unittest.main()
