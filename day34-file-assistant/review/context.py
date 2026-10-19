"""What one review request is made of: the changed files, cut to a budget, in batches.

    select(files, config)       which files are reviewed, and why the others are not
    view(file, config)          one file as the model reads it (`diff.render`), fitted to the budget
    plan(files, config)         the reviewed files in batches of at most `batch_chars` each

A small file goes up whole, with its change marked in it - the model reads the
code around the change, not three lines of it. A long one goes up as its
changes with `context_lines` around each. A pull request bigger than one
request is split by files, in the order git lists them (so a folder's files
stay together); each batch is one request, with its own RAG excerpts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ReviewConfig
from .diff import FileDiff, Rendered, render


@dataclass
class FileView:
    file: FileDiff
    rendered: Rendered

    @property
    def path(self) -> str:
        return self.file.path

    @property
    def chars(self) -> int:
        return len(self.rendered.text)


def label(hit: dict) -> str:
    """An excerpt's name in a prompt and in the summary: `path › section`."""
    return f"{hit['path']} › {hit['section']}" if hit.get("section") else hit["path"]


@dataclass
class Batch:
    files: list[FileView] = field(default_factory=list)
    #: RAG hits for this batch, ranks renumbered 1..n (`knowledge.Knowledge.for_batch`).
    excerpts: list[dict] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(f.chars for f in self.files)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]


@dataclass
class Plan:
    batches: list[Batch]
    skipped: list[dict]          # {"path", "reason"} - shown in the summary
    unchanged: list[FileDiff]    # renames and copies with nothing changed
    deleted: list[FileDiff]

    @property
    def files(self) -> list[FileView]:
        return [f for b in self.batches for f in b.files]


def select(files: list[FileDiff], config: ReviewConfig) -> tuple[list[FileDiff], list[dict], list[FileDiff], list[FileDiff]]:
    reviewed, skipped, unchanged, deleted = [], [], [], []
    for file in files:
        if file.binary:
            skipped.append({"path": file.path, "reason": "binary"})
        elif config.skipped(file.path):
            skipped.append({"path": file.path, "reason": "generated or a lock file - review.json `skip`"})
        elif file.status == "deleted":
            deleted.append(file)
        elif not file.changed:
            unchanged.append(file)
        else:
            reviewed.append(file)
    return reviewed, skipped, unchanged, deleted


def view(file: FileDiff, config: ReviewConfig) -> FileView:
    length = sum(len(h.lines) for h in file.hunks)
    context = None if length <= config.whole_file_lines else config.context_lines
    rendered = render(file, context, config.max_file_lines)
    max_lines = config.max_file_lines
    # Still over one request's budget: fewer lines, until it fits.
    while len(rendered.text) > config.batch_chars and max_lines > 20:
        max_lines //= 2
        rendered = render(file, min(context or config.context_lines, 3), max_lines)
    return FileView(file, rendered)


def plan(files: list[FileDiff], config: ReviewConfig) -> Plan:
    reviewed, skipped, unchanged, deleted = select(files, config)
    batches: list[Batch] = []
    for file in reviewed:
        fv = view(file, config)
        if batches and batches[-1].chars + fv.chars <= config.batch_chars:
            batches[-1].files.append(fv)
            continue
        if len(batches) >= config.max_batches:
            limit = f"{config.max_batches} request" + ("s" if config.max_batches != 1 else "")
            skipped.append({"path": file.path, "reason": f"over the review's size limit of {limit}"})
            continue
        batches.append(Batch([fv]))
    return Plan(batches, skipped, unchanged, deleted)
