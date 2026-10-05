"""The one shape every format is read into: a text and the sections in it.

A loader's whole job is to turn a file into this, and a chunker's whole job
starts from it - so this is the seam that keeps "which format" and "how to
split" apart. Markdown, plain text and Python look nothing alike on disk and
exactly alike here:

    Document(source="README.md", title="...", text="<the whole file>",
             sections=[Section(("Day 20", "Routing"), start=4210, end=5873), ...])

Two decisions are worth stating.

**A section is a range, not a copy.** `start`/`end` are character offsets
into `Document.text`, which is the file exactly as read. So the fixed
chunker can ignore sections and walk the text, the structural one can walk
the sections, and afterwards both kinds of chunk can be laid over the same
sections to ask "which part of the document is this from" - which is what
the metadata and the retrieval check are built on.

**A title path, not a title.** `("Day 20", "Routing", "Why one server")` is
where a section sits, not just what it is called. Two sections called
"Checking it" exist in the README a dozen times over; the path is what tells
them apart, and a prefix of it (`("Day 20",)`) names a whole subtree - which
is how a question can say "the answer is somewhere in day 20".
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

#: How a title path is written for a person: the same separator the UI and
#: the questions file use, so a label can be copied from one to the other.
PATH_SEPARATOR = " › "

#: The label of text that sits before the first heading of a document.
INTRO_LABEL = "(intro)"

WORD = re.compile(r"\S+")


def section_label(title_path: tuple[str, ...] | list[str]) -> str:
    return PATH_SEPARATOR.join(title_path) if title_path else INTRO_LABEL


def parse_label(label: str) -> tuple[str, ...]:
    label = (label or "").strip()
    if not label or label == INTRO_LABEL:
        return ()
    return tuple(part.strip() for part in label.split(PATH_SEPARATOR.strip()) if part.strip())


def count_words(text: str) -> int:
    """The unit every size in this package is measured in.

    Words - runs of non-space - rather than model tokens, on purpose: the
    count has to be the same whichever embedder is plugged in, it has to be
    computable without one, and it has to be something a person can check by
    eye. For bge-m3 a word is roughly 1.3-1.5 tokens of English.
    """
    return sum(1 for _ in WORD.finditer(text))


@dataclass(frozen=True)
class Section:
    """One stretch of a document, and where in the document's outline it is."""

    title_path: tuple[str, ...]
    start: int
    end: int
    # What produced it - "heading", "intro", "file", "def", "class" - shown in
    # the chunk browser, never used to decide anything.
    kind: str = "heading"

    @property
    def label(self) -> str:
        return section_label(self.title_path)

    def within(self, prefix: tuple[str, ...]) -> bool:
        """Is this section `prefix` itself or somewhere underneath it?"""
        return self.title_path[: len(prefix)] == prefix


@dataclass
class Document:
    """A file, read: its text, what it is called and how it is divided."""

    source: str          # path relative to the corpus root - the document's id
    title: str
    format: str          # "markdown", "text", "python", ...
    text: str
    sections: list[Section] = field(default_factory=list)

    @property
    def sha(self) -> str:
        return hashlib.sha1(self.text.encode("utf-8")).hexdigest()

    @property
    def n_words(self) -> int:
        return count_words(self.text)

    def section_text(self, section: Section) -> str:
        return self.text[section.start:section.end]

    def section_at(self, offset: int) -> Section | None:
        """The section a character offset falls into, if any."""
        for section in self.sections:
            if section.start <= offset < section.end:
                return section
        return None

    def sections_between(self, start: int, end: int) -> list[Section]:
        """Every section a range `[start, end)` touches."""
        return [s for s in self.sections if s.start < end and start < s.end]
