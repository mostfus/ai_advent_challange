"""Chunkers: a `Document` in, a list of `Chunk`s out - the two strategies.

**fixed** walks the document's text in windows of `size` words, each one
starting `size - overlap` words after the last. It never looks at a section:
a window ends wherever the count runs out, in the middle of a sentence, a
code block or a heading. That is the point of having it - it is the baseline
anything smarter has to beat.

**structural** walks the sections. A section that fits in `max_words` is one
chunk. One that does not is split on blank lines (never inside a ``` fence)
and the paragraphs packed back up to `max_words`, with `overlap` words
carried over from one piece into the next; a single paragraph longer than
that is cut into windows as a last resort. Sections shorter than
`min_words` are merged into a neighbour under the same top-level heading, so
a two-line heading does not become a chunk that means nothing on its own.

Both produce chunks with the same metadata, and both lay the chunk back over
the document's sections to fill it in - so a fixed chunk also knows which
section it starts in and how many it runs across, which is one of the
numbers the comparison is about.

`overlap` means the same in both: how many words at the start of a chunk
repeat the end of the one before. It is what keeps a sentence cut at a
boundary readable from at least one side, and it is paid for in duplicate
text - the redundancy figure in the stats is exactly that price.

`with_context` prepends "file › section" to the text that is *embedded*
(not to the text that is stored or shown): a chunk from "Day 17 › The loop"
that says "the model calls it twice" is findable by "day 17" only if
something in the vector says so.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Protocol

from .documents import PATH_SEPARATOR, WORD, Document, Section, section_label

PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
FENCE_LINE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})", re.MULTILINE)


@dataclass
class Chunk:
    """One piece of a document, and everything known about where it came from."""

    chunk_id: str
    strategy: str
    source: str
    title: str
    section: str                 # label of the section the chunk starts in
    title_path: tuple[str, ...]
    position: int                # 0.. within the document
    start: int                   # character range in Document.text
    end: int
    text: str
    embed_text: str              # what the embedder is given
    n_words: int
    sections_spanned: int = 1
    merged: int = 1              # how many sections a structural merge joined

    def metadata(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "strategy": self.strategy,
            "source": self.source,
            "title": self.title,
            "section": self.section,
            "position": self.position,
            "start": self.start,
            "end": self.end,
            "n_words": self.n_words,
            "sections_spanned": self.sections_spanned,
            "merged": self.merged,
        }


class Chunker(Protocol):
    strategy: str

    def params(self) -> dict: ...

    def chunk(self, document: Document) -> list[Chunk]: ...


def word_spans(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    end = len(text) if end is None else end
    return [(m.start() + start, m.end() + start) for m in WORD.finditer(text[start:end])]


def common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not paths:
        return ()
    prefix = paths[0]
    for path in paths[1:]:
        n = 0
        while n < min(len(prefix), len(path)) and prefix[n] == path[n]:
            n += 1
        prefix = prefix[:n]
    return prefix


def make_chunk(document: Document, strategy: str, position: int, start: int, end: int,
               with_context: bool, title_path: tuple[str, ...] | None = None, merged: int = 1) -> Chunk:
    text = document.text[start:end]
    touched = document.sections_between(start, end)
    if title_path is None:
        at = document.section_at(start)
        title_path = at.title_path if at else (touched[0].title_path if touched else ())
    label = section_label(title_path)
    embed_text = text
    if with_context:
        # The file name, not the title: a README's title is a sixty-word
        # sentence, and the same sixty words in front of every chunk pull all
        # of their vectors towards one another.
        context = PATH_SEPARATOR.join([document.source] + list(title_path))
        embed_text = f"{context}\n\n{text}"
    return Chunk(
        chunk_id=f"{strategy}:{document.source}#{position:04d}",
        strategy=strategy,
        source=document.source,
        title=document.title,
        section=label,
        title_path=tuple(title_path),
        position=position,
        start=start,
        end=end,
        text=text,
        embed_text=embed_text,
        n_words=sum(1 for _ in WORD.finditer(text)),
        sections_spanned=max(1, len(touched)),
        merged=merged,
    )


def windows(n: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Word-index windows `[i, j)` over `n` words: `size` long, `overlap` shared."""
    if n == 0:
        return []
    step = max(1, size - overlap)
    out, i = [], 0
    while True:
        j = min(i + size, n)
        out.append((i, j))
        if j >= n:
            return out
        i += step


def validate(size: int, overlap: int, what: str = "size") -> None:
    if size < 10:
        raise ValueError(f"{what} must be at least 10 words")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must be between 0 and {what} - 1 ({size - 1})")


@dataclass
class FixedChunker:
    size: int = 200
    overlap: int = 40
    with_context: bool = False
    strategy: str = field(default="fixed", init=False)

    def __post_init__(self):
        validate(self.size, self.overlap)

    def params(self) -> dict:
        return {"size": self.size, "overlap": self.overlap, "with_context": self.with_context}

    def chunk(self, document: Document) -> list[Chunk]:
        spans = word_spans(document.text)
        return [
            make_chunk(document, self.strategy, position, spans[i][0], spans[j - 1][1], self.with_context)
            for position, (i, j) in enumerate(windows(len(spans), self.size, self.overlap))
        ]


@dataclass
class Piece:
    """A structural chunk before it is a chunk: a range and the path it belongs to."""

    start: int
    end: int
    path: tuple[str, ...]
    words: int
    whole: bool          # a whole section (mergeable) rather than a slice of one
    merged: int = 1


def paragraphs(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Blank-line separated blocks of `text[start:end]`, a fenced block kept whole."""
    body = text[start:end]
    fences = [m.start() for m in FENCE_LINE.finditer(body)]
    inside = [(fences[k], fences[k + 1]) for k in range(0, len(fences) - 1, 2)]
    cuts = [m for m in PARAGRAPH_BREAK.finditer(body)
            if not any(a < m.start() < b for a, b in inside)]
    blocks, cursor = [], 0
    for cut in cuts:
        if body[cursor:cut.start()].strip():
            blocks.append((start + cursor, start + cut.start()))
        cursor = cut.end()
    if body[cursor:].strip():
        blocks.append((start + cursor, end))
    return blocks


@dataclass
class StructuralChunker:
    max_words: int = 300
    min_words: int = 60
    overlap: int = 40
    with_context: bool = True
    strategy: str = field(default="structural", init=False)

    def __post_init__(self):
        validate(self.max_words, self.overlap, "max_words")
        if not 0 <= self.min_words < self.max_words:
            raise ValueError("min_words must be between 0 and max_words - 1")

    def params(self) -> dict:
        return {"max_words": self.max_words, "min_words": self.min_words,
                "overlap": self.overlap, "with_context": self.with_context}

    def chunk(self, document: Document) -> list[Chunk]:
        pieces: list[Piece] = []
        for section in document.sections:
            pieces.extend(self.split(document.text, section))
        pieces = self.merge(pieces)
        return [
            make_chunk(document, self.strategy, position, p.start, p.end, self.with_context,
                       title_path=p.path, merged=p.merged)
            for position, p in enumerate(pieces)
        ]

    def split(self, text: str, section: Section) -> list[Piece]:
        spans = word_spans(text, section.start, section.end)
        if not spans:
            return []
        if len(spans) <= self.max_words:
            return [Piece(spans[0][0], spans[-1][1], section.title_path, len(spans), whole=True)]

        # Each paragraph as a range of word indexes, so packing and overlap
        # can both be done in words.
        starts = [s for s, _ in spans]
        ends = [e for _, e in spans]
        ranges: list[tuple[int, int]] = []
        for a, b in paragraphs(text, section.start, section.end):
            i, j = bisect_left(starts, a), bisect_right(ends, b)
            if i < j:
                ranges.append((i, j))

        # A paragraph longer than a chunk is cut into windows; the rest pack.
        blocks: list[tuple[int, int]] = []
        for i, j in ranges:
            if j - i > self.max_words:
                blocks.extend((i + a, i + b) for a, b in windows(j - i, self.max_words, self.overlap))
            else:
                blocks.append((i, j))

        pieces: list[tuple[int, int]] = []
        current: tuple[int, int] | None = None
        for i, j in blocks:
            if current is None:
                current = (i, j)
                continue
            if j - current[0] <= self.max_words:
                current = (current[0], j)
            else:
                pieces.append(current)
                # Carry `overlap` words of the previous piece over - fewer if
                # the new block would otherwise push the piece past the limit.
                current = (min(i, max(i - self.overlap, j - self.max_words, current[0] + 1)), j)
        if current is not None:
            pieces.append(current)
        return [Piece(spans[i][0], spans[j - 1][1], section.title_path, j - i, whole=False)
                for i, j in pieces]

    @staticmethod
    def related(a: Piece, b: Piece) -> bool:
        """Under the same top-level heading - or two whole sibling sections.

        The second case is a run of small top-level `def`s, or a document's
        intro and its first heading. It needs both sides whole: otherwise a
        short "## Zebra" would be glued onto the last slice of the long
        section before it and carry that section's name.
        """
        if a.path[:1] and a.path[:1] == b.path[:1]:
            return True
        return a.whole and b.whole and a.path[:-1] == b.path[:-1]

    def merge(self, pieces: list[Piece]) -> list[Piece]:
        # At least one side must be a whole section: two slices of one long
        # section overlap, and are already the size they were cut to.
        out: list[Piece] = []
        for piece in pieces:
            last = out[-1] if out else None
            if (last is not None and (last.whole or piece.whole)
                    and self.related(last, piece)
                    and (last.words < self.min_words or piece.words < self.min_words)
                    and last.words + piece.words <= self.max_words):
                # Labelled by what the two share - or, for siblings that share
                # nothing (`def a` + `def b`), by the bigger one; `merged` says
                # how many sections the chunk holds.
                path = common_prefix([last.path, piece.path]) or \
                    (last.path if last.words >= piece.words else piece.path)
                out[-1] = Piece(last.start, piece.end, path,
                                last.words + piece.words, whole=last.whole and piece.whole,
                                merged=last.merged + piece.merged)
            else:
                out.append(piece)
        return out


STRATEGIES = {"fixed": FixedChunker, "structural": StructuralChunker}

DEFAULT_PARAMS = {
    "fixed": FixedChunker().params(),
    "structural": StructuralChunker().params(),
}


def make_chunker(strategy: str, params: dict | None = None) -> Chunker:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; known: {', '.join(STRATEGIES)}")
    merged = {**DEFAULT_PARAMS[strategy], **(params or {})}
    kinds = {k: type(v) for k, v in DEFAULT_PARAMS[strategy].items()}
    clean = {}
    for key, value in merged.items():
        if key not in kinds:
            continue
        clean[key] = bool(value) if kinds[key] is bool else int(value)
    return STRATEGIES[strategy](**clean)
