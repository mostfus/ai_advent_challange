"""Loaders: a file in, a `Document` out - one loader per format.

The only place in the package that knows what a markdown heading or a Python
`def` looks like. Each loader finds the structure its format actually has and
writes it down as sections; everything after this module works on sections
and never learns which format they came from.

    Markdown   ATX headings (`#` .. `######`), fenced code skipped over
    Python     module docstring, top-level `def`/`class`, methods one level in
    Text       no structure to find: one section, the whole file

Adding a format is a class with `load(source, text) -> Document` and a line
in `LOADERS`. A file whose format has no loader is skipped and reported, not
guessed at - and a Python file that does not parse falls back to plain text
rather than failing the whole corpus.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Protocol

from .documents import Document, Section

# `# Title`, `## Title ##` - the closing hashes are optional in CommonMark.
ATX_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
# Inline markup that has no business in a title path: `code`, **bold**, [link](url).
INLINE_MARKUP = re.compile(r"`([^`]*)`|\*\*([^*]*)\*\*|\*([^*]*)\*|\[([^\]]*)\]\([^)]*\)")


class Loader(Protocol):
    format: str

    def load(self, source: str, text: str) -> Document: ...


def clean_title(title: str) -> str:
    title = INLINE_MARKUP.sub(lambda m: next(g for g in m.groups() if g is not None), title)
    return " ".join(title.split())


def line_offsets(text: str) -> list[int]:
    """Where each line starts - offset of line i is `offsets[i]`, plus one past the end."""
    offsets = [0]
    for match in re.finditer("\n", text):
        offsets.append(match.end())
    if offsets[-1] != len(text):
        offsets.append(len(text))
    return offsets


def non_blank(text: str, start: int, end: int) -> bool:
    return bool(text[start:end].strip())


class MarkdownLoader:
    """Sections from ATX headings; the path is the chain of headings above.

    A document with exactly one `#` heading treats it as the document's title
    rather than as a level of the outline - otherwise every path in a README
    would start with the same forty-word sentence. With several, `#` is just
    the top level. A `#` inside a fenced code block is a shell comment, not a
    heading, and the fence tracking is there so that `# install deps` in a
    bash snippet does not start a section.
    """

    format = "markdown"

    def load(self, source: str, text: str) -> Document:
        headings: list[tuple[int, int, str]] = []   # (offset, level, title)
        fence: str | None = None
        offset = 0
        for line in text.splitlines(keepends=True):
            stripped = line.rstrip("\r\n")
            opener = FENCE.match(stripped)
            if fence is None and opener:
                fence = opener.group(1)[0] * 3
            elif fence is not None:
                if stripped.strip().startswith(fence):
                    fence = None
            else:
                heading = ATX_HEADING.match(stripped)
                if heading:
                    headings.append((offset, len(heading.group(1)), clean_title(heading.group(2))))
            offset += len(line)

        h1 = [h for h in headings if h[1] == 1]
        title = h1[0][2] if len(h1) == 1 else Path(source).name
        title_is_h1 = len(h1) == 1

        sections: list[Section] = []
        first = headings[0][0] if headings else len(text)
        if non_blank(text, 0, first):
            sections.append(Section((), 0, first, kind="intro"))

        stack: list[tuple[int, str]] = []
        for i, (start, level, heading) in enumerate(headings):
            end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
            if title_is_h1 and level == 1:
                # The title line itself: the text under it, up to the first
                # real heading, is the document's intro.
                if non_blank(text, start, end):
                    sections.append(Section((), start, end, kind="intro"))
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            sections.append(Section(tuple(h for _, h in stack), start, end))

        return Document(source=source, title=title, format=self.format, text=text, sections=sections)


class PlainTextLoader:
    """No structure to find, so none is invented: the file is one section.

    The structural chunker still has something to go on inside it - blank
    lines between paragraphs - and that is the honest limit of "structure"
    for a format that has none.
    """

    format = "text"

    def load(self, source: str, text: str) -> Document:
        sections = [Section((), 0, len(text), kind="file")] if text.strip() else []
        return Document(source=source, title=Path(source).name, format=self.format,
                        text=text, sections=sections)


class PythonLoader:
    """Sections from the syntax tree: docstring, `def`, `class`, methods.

    A module's outline is its top-level definitions, and a class's is its
    methods - one level of nesting, which is where the structure a reader
    would navigate by stops. What sits between definitions (imports,
    constants, a comment block) becomes a "module level" section of its own,
    so no line of the file falls outside a section.
    """

    format = "python"

    def load(self, source: str, text: str) -> Document:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            document = PlainTextLoader().load(source, text)
            document.format = self.format
            return document
        sections = PythonOutline(text).sections(tree)
        return Document(source=source, title=Path(source).name, format=self.format, text=text, sections=sections)


class PythonOutline:
    """One file's walk - state lives here, so a shared loader stays thread-safe."""

    DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def __init__(self, text: str):
        self.text = text
        self.offsets = line_offsets(text)

    def sections(self, tree: ast.Module) -> list[Section]:
        text = self.text
        sections: list[Section] = []
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc = body.pop(0)
            sections.append(Section(("module docstring",), self.line_start(doc), self.line_end(doc), kind="docstring"))
        start = sections[-1].end if sections else 0
        self.walk(body, (), start, len(text), sections)
        return sections

    def line_start(self, node: ast.AST) -> int:
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return self.offsets[first - 1]

    def line_end(self, node: ast.AST) -> int:
        return self.offsets[min(node.end_lineno, len(self.offsets) - 1)]

    @staticmethod
    def name(node: ast.AST) -> str:
        return f"class {node.name}" if isinstance(node, ast.ClassDef) else f"def {node.name}"

    def gap(self, path: tuple[str, ...], start: int, end: int, sections: list[Section]) -> None:
        if start < end and non_blank(self.text, start, end):
            sections.append(Section(path + ("module level",) if not path else path, start, end, kind="code"))

    def walk(self, nodes: list[ast.stmt], path: tuple[str, ...], start: int, end: int,
             sections: list[Section]) -> None:
        cursor = start
        for node in nodes:
            if not isinstance(node, self.DEFINITIONS):
                continue
            node_start, node_end = self.line_start(node), self.line_end(node)
            self.gap(path, cursor, node_start, sections)
            node_path = path + (self.name(node),)
            methods = [n for n in node.body if isinstance(n, self.DEFINITIONS)] if not path else []
            if isinstance(node, ast.ClassDef) and methods:
                # The class line, its docstring and attributes, up to the first method.
                head_end = self.line_start(methods[0])
                sections.append(Section(node_path, node_start, head_end, kind="class"))
                self.walk(methods, node_path, head_end, node_end, sections)
            else:
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                sections.append(Section(node_path, node_start, node_end, kind=kind))
            cursor = node_end
        self.gap(path, cursor, end, sections)


LOADERS: dict[str, Loader] = {
    ".md": MarkdownLoader(),
    ".markdown": MarkdownLoader(),
    ".txt": PlainTextLoader(),
    ".py": PythonLoader(),
}


def loader_for(name: str) -> Loader | None:
    return LOADERS.get(Path(name).suffix.lower())


def load_text(source: str, text: str) -> Document:
    loader = loader_for(source)
    if loader is None:
        raise ValueError(f"no loader for {Path(source).suffix or 'files without an extension'}")
    return loader.load(source, text)


def load_file(path: Path, root: Path) -> Document:
    """Read one file; its path relative to `root` becomes its `source`."""
    source = path.resolve().relative_to(root.resolve()).as_posix()
    return load_text(source, path.read_text(encoding="utf-8", errors="replace"))
