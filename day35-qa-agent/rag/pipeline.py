"""The pipeline, run end to end - and a record of each stage as it happened.

    load_corpus(patterns, root, exclude) files -> Documents (+ what was skipped and why);
                                         in a git repository, the files git knows
    chunk_corpus(documents, chunker)     Documents -> Chunks
    chunk_stats(chunks, documents)       what the chunker did to the corpus, in numbers
    build_index(...)                     all of it, embedded and written to the store

`build_index` reports progress through a callback rather than printing, so the
web tab can draw a bar without this module knowing there is one. Each stage is
timed separately, because "indexing is slow" is useless and "embedding took
41 s of 43" is not.
"""

from __future__ import annotations

import os
import re
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .chunkers import Chunk, Chunker
from .documents import Document
from .embedders import Embedder
from .index_store import IndexStore, corpus_sha, text_sha
from .loaders import load_file, loader_for

Progress = Callable[[str, int, int], None]    # (stage, done, total)

# A chunk "ends cleanly" if its last character closes a sentence, a list item
# or a code block - or if what follows it in the document is a blank line.
SENTENCE_END = re.compile(r"[.!?:;)\]`\"'»”>|*_]\s*$")
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})", re.MULTILINE)
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


@dataclass
class Corpus:
    documents: list[Document]
    skipped: list[dict] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def sha(self) -> str:
        return corpus_sha(self.documents)


def glob_regex(pattern: str) -> re.Pattern:
    """A glob over relative POSIX paths, as a regex: `*` and `?` stay inside one
    folder, `**/` is any number of folders (none included) - `Path.glob`'s rules."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def excluded(rel: str, exclude: list[str]) -> bool:
    """Whether a relative path is left out, by `.gitignore`-like rules.

    A pattern without a `/` names a file or folder anywhere (`__pycache__`,
    `*.pyc`); one with a `/` is a path from the root (`data`, `docs/old/**`),
    and a folder excludes everything under it.
    """
    parts = rel.split("/")
    for pattern in exclude:
        pattern = pattern.strip().rstrip("/")
        if not pattern or pattern.startswith("#"):
            continue
        if "/" not in pattern:
            rx = glob_regex(pattern)
            if any(rx.match(part) for part in parts):
                return True
        elif glob_regex(pattern).match(rel) or glob_regex(pattern + "/**").match(rel):
            return True
    return False


def git_files(root: Path) -> list[str] | None:
    """Every file git knows under `root` - tracked, or new and not ignored - relative to it.

    None when `root` is in no repository, or there is no git: the caller walks the folder.
    """
    try:
        done = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                               "--exclude-standard"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return [rel for rel in done.stdout.decode("utf-8", "replace").split("\0") if rel]


def resolve_patterns(patterns: list[str], root: Path,
                     exclude: list[str] | tuple = ()) -> tuple[list[Path], list[dict]]:
    """Glob patterns relative to `root`, minus `exclude` -> files inside it, deduplicated, in order.

    In a git repository the candidates are `git_files`; elsewhere, one walk of
    the tree, with excluded folders pruned on the way down - so a `**/*.py`
    over a project with a virtualenv in it does not read the virtualenv first
    and throw it away after.
    """
    root = root.resolve()
    exclude = list(exclude)
    includes = [p.strip() for p in patterns if p.strip() and not p.strip().startswith("#")]
    regexes = [glob_regex(p) for p in includes]
    hits: list[list[str]] = [[] for _ in includes]
    skipped: list[dict] = []
    if not root.is_dir():
        return [], [{"source": str(root), "reason": "the corpus root is not a folder"}]

    def consider(rel: str) -> None:
        for index, rx in enumerate(regexes):
            if rx.match(rel):
                hits[index].append(rel)

    listed = git_files(root)
    if listed is not None:
        # Day 31: in a repository, the files git knows - its `.gitignore`
        # has already left out build output, dependencies and secrets.
        for rel in sorted(set(listed)):
            if any(part in SKIP_DIRS for part in rel.split("/")[:-1]) or excluded(rel, exclude):
                continue
            if (root / rel).is_file():
                consider(rel)
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath).relative_to(root).as_posix()
            prefix = "" if here == "." else here + "/"
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in SKIP_DIRS and not excluded(prefix + d, exclude))
            for name in filenames:
                if not excluded(prefix + name, exclude):
                    consider(prefix + name)
    found: dict[Path, None] = {}
    for pattern, matched in zip(includes, hits):
        if not matched:
            skipped.append({"source": pattern, "reason": "pattern matched nothing"})
        for rel in sorted(matched):
            found[root / rel] = None
    return list(found), skipped


def load_corpus(patterns: list[str], root: Path, exclude: list[str] | tuple = (),
                max_bytes: int | None = None) -> Corpus:
    started = time.monotonic()
    paths, skipped = resolve_patterns(patterns, root, exclude)
    documents: list[Document] = []
    for path in paths:
        source = path.relative_to(root.resolve()).as_posix()
        if loader_for(path.name) is None:
            skipped.append({"source": source, "reason": f"no loader for {path.suffix or 'no extension'}"})
            continue
        if max_bytes is not None and path.stat().st_size > max_bytes:
            skipped.append({"source": source, "reason": f"larger than {max_bytes // 1024} KB"})
            continue
        try:
            document = load_file(path, root)
        except (OSError, ValueError) as exc:
            skipped.append({"source": source, "reason": str(exc)})
            continue
        if not document.sections:
            skipped.append({"source": source, "reason": "empty"})
            continue
        documents.append(document)
    return Corpus(documents, skipped, time.monotonic() - started)


def chunk_corpus(documents: list[Document], chunker: Chunker) -> list[Chunk]:
    return [chunk for document in documents for chunk in chunker.chunk(document)]


def ends_cleanly(chunk: Chunk, document: Document) -> bool:
    if chunk.end >= len(document.text.rstrip()):
        return True
    if SENTENCE_END.search(chunk.text):
        return True
    return document.text[chunk.end:chunk.end + 3].startswith(("\n\n", "\r\n\r\n", "\n \n"))


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def chunk_stats(chunks: list[Chunk], documents: list[Document]) -> dict:
    """What the chunker did to the corpus, before anybody asks it a question."""
    by_source = {d.source: d for d in documents}
    words = [c.n_words for c in chunks]
    corpus_words = sum(d.n_words for d in documents)
    cut = sum(1 for c in chunks if not ends_cleanly(c, by_source[c.source]))
    split_code = sum(1 for c in chunks if len(FENCE.findall(c.text)) % 2 == 1)
    # Crossing a boundary nobody chose: a merge of three small sections spans
    # three on purpose and is not counted; a piece that runs from one heading
    # into the next is.
    spanning = sum(1 for c in chunks if c.sections_spanned > c.merged)
    n = len(chunks) or 1
    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "words_min": min(words) if words else 0,
        "words_median": int(statistics.median(words)) if words else 0,
        "words_p95": percentile(words, 0.95),
        "words_max": max(words) if words else 0,
        "tiny_chunks": sum(1 for w in words if w < 30),
        "mid_sentence_cuts": cut,
        "mid_sentence_share": round(cut / n, 3),
        "split_code_blocks": split_code,
        "cross_section_chunks": spanning,
        "cross_section_share": round(spanning / n, 3),
        "embedded_words": sum(words),
        "corpus_words": corpus_words,
        # > 1 means text embedded more than once - what the overlap costs.
        "redundancy": round(sum(words) / corpus_words, 3) if corpus_words else 0,
    }


def build_index(name: str, corpus: Corpus, chunker: Chunker, embedder: Embedder, store: IndexStore,
                progress: Progress | None = None) -> dict:
    """Chunk, embed (through the cache), store. Returns the index's record."""
    report = progress or (lambda *_: None)
    timings = {"load": round(corpus.seconds, 3)}

    report("chunk", 0, len(corpus.documents))
    started = time.monotonic()
    chunks = chunk_corpus(corpus.documents, chunker)
    timings["chunk"] = round(time.monotonic() - started, 3)
    stats = chunk_stats(chunks, corpus.documents)

    started = time.monotonic()
    texts = [c.embed_text for c in chunks]
    cached = store.cached(embedder.name, texts)
    missing: list[str] = []
    seen: set[str] = set()
    for text in texts:
        sha = text_sha(text)
        if sha not in cached and sha not in seen:
            seen.add(sha)
            missing.append(text)
    report("embed", 0, len(missing))
    fresh = embedder.embed_documents(missing, on_batch=lambda done, total: report("embed", done, total)) \
        if missing else np.zeros((0, 0), dtype=np.float32)
    if missing:
        store.remember(embedder.name, missing, fresh)
        cached.update({text_sha(t): v for t, v in zip(missing, fresh)})
    vectors = np.stack([cached[text_sha(t)] for t in texts]) if texts else np.zeros((0, 1), dtype=np.float32)
    timings["embed"] = round(time.monotonic() - started, 3)
    stats["embedded_now"] = len(missing)
    stats["from_cache"] = len(texts) - len(missing)

    report("store", 0, 1)
    started = time.monotonic()
    store.save_index(name, chunker.strategy, chunker.params(), embedder.name, corpus.sha,
                     chunks, vectors, stats, timings)
    timings["store"] = round(time.monotonic() - started, 3)
    # Written again with the store timing in it - the one number that can
    # only be known after the row it describes has been written.
    store.set_timings(name, timings)
    report("done", 1, 1)
    return store.index(name)
