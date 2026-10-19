"""Loaders: a file in, a `Document` out - one loader per format.

The only place in the package that knows what a markdown heading or a Python
`def` looks like. Each loader finds the structure its format actually has and
writes it down as sections; everything after this module works on sections
and never learns which format they came from.

    Markdown   ATX headings (`#` .. `######`), fenced code skipped over
    Python     module docstring, top-level `def`/`class`, methods one level in
    Swift      top-level declarations, members one level in (day 31)
    Text       no structure to find: one section, the whole file - also YAML,
               SQL, GraphQL and protobuf schemas, by their own format name

Adding a format is a class with `load(source, text) -> Document` and a line
in `LOADERS`. A file whose format has no loader is skipped and reported, not
guessed at - and a Python file that does not parse falls back to plain text
rather than failing the whole corpus.
"""

from __future__ import annotations

import ast
import bisect
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

    def __init__(self, format: str = "text") -> None:
        self.format = format

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


# -------------------------------------------------------------------- Swift

# What a Swift declaration line starts with: attributes, modifiers, the keyword
# and the name - matched at a line's start (`match` with a position). Read off the masked text, so a keyword in a comment or a
# string is never one. `class` is both a modifier (`class func`) and a
# keyword, and the regex backtracks to the right reading.
SWIFT_DECLARATION = re.compile(
    r"[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?[ \t]+)*"
    r"(?:(?:public|private|fileprivate|internal|open|package|final|static|class|override|mutating|"
    r"nonmutating|convenience|required|lazy|weak|unowned|indirect|nonisolated|distributed|dynamic|"
    r"optional|consuming|borrowing)(?:\([\w]+\))?[ \t]+)*"
    r"(class|struct|enum|protocol|extension|actor|func|init|deinit|var|let|typealias|subscript|macro)"
    r"(?![\w])[?!]?[ \t]*([^\s:{(<=,]*)"
)
SWIFT_TYPES = {"class", "struct", "enum", "protocol", "extension", "actor"}
# Lines that belong to the declaration under them: doc comments and attributes.
SWIFT_LEAD = ("///", "//", "/*", "*", "@")
# What a line may start with and still be the declaration above it, carried on.
SWIFT_CONTINUES = ("where", "->", "throws", "rethrows", "async", ":", ",", ".", "{", "&&", "||")
RAW_STRING = re.compile(r'(#*)("""|")')
NON_SPACE = re.compile(r"\S")


def mask_swift(text: str) -> str:
    """The text with comments and string literals blanked - same length, newlines kept.

    What is left is code, so a brace counted in it is a real one. Nested
    `/* */` comments and raw strings (`#"..."#`) are handled; a string
    interpolation holding a string of its own is not, and costs at worst one
    misplaced section boundary.
    """
    out = list(text)
    n = len(text)

    def blank(a: int, b: int) -> None:
        for j in range(a, min(b, n)):
            if out[j] != "\n":
                out[j] = " "

    i = 0
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif text[i] in "\"#" and (literal := RAW_STRING.match(text, i)):
            hashes, quote = literal.group(1), literal.group(2)
            if text[i] == "#" and not hashes:
                i += 1
                continue
            close, j = quote + hashes, literal.end()
            while j < n:
                if not hashes and text[j] == "\\":
                    j += 2
                    continue
                if text.startswith(close, j):
                    j += len(close)
                    break
                if quote == '"' and text[j] == "\n":
                    break
                j += 1
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


class SwiftLoader:
    """Sections from Swift declarations; the path is the type, then the member.

    The same outline `PythonLoader` writes: a file's top-level declarations,
    and a type's members one level in - a SwiftUI view is its `struct`, then
    its `var body` and helpers. There is no Swift parser to lean on, so it is
    done the way a reader does it: comments and strings masked out, braces
    counted, and a declaration is a line at the right depth that starts with a
    declaration keyword. Doc comments and attributes above a declaration
    belong to it. Code between declarations (imports, `#Preview`, top-level
    statements) is a "file level" section, so no line falls outside a section.
    """

    format = "swift"

    def load(self, source: str, text: str) -> Document:
        sections = SwiftOutline(text).sections()
        if not sections and text.strip():
            sections = [Section((), 0, len(text), kind="file")]
        return Document(source=source, title=Path(source).name, format=self.format, text=text, sections=sections)


class SwiftOutline:
    """One file's walk - state lives here, so a shared loader stays thread-safe."""

    def __init__(self, text: str):
        self.text = text
        self.masked = mask_swift(text)
        self.offsets = line_offsets(text)
        self.lines = len(self.offsets) - 1
        self.depths: list[int] = []
        depth = 0
        for i in range(self.lines):
            self.depths.append(depth)
            line = self.masked[self.offsets[i]:self.offsets[i + 1]]
            depth += line.count("{") - line.count("}")

    def line_of(self, offset: int) -> int:
        return max(0, bisect.bisect_right(self.offsets, offset) - 1)

    def declarations(self, first: int, last: int, depth: int) -> list[tuple[int, str, str]]:
        """`(line, keyword, name)` of every declaration at `depth` on lines `[first, last)`."""
        found = []
        for i in range(first, min(last, self.lines)):
            if self.depths[i] != depth:
                continue
            match = SWIFT_DECLARATION.match(self.masked, self.offsets[i], self.offsets[i + 1])
            if match:
                found.append((i, match.group(1), match.group(2)))
        return found

    def lead(self, line: int, floor: int) -> int:
        """Where a declaration starts: its first line, or the doc comments and attributes above it."""
        start = line
        while start - 1 >= 0 and self.offsets[start - 1] >= floor:
            above = self.text[self.offsets[start - 1]:self.offsets[start]].strip()
            if not above or not above.startswith(SWIFT_LEAD):
                break
            start -= 1
        return self.offsets[start]

    def end(self, line: int, depth: int) -> int:
        """Where a declaration that starts on `line` ends: after the brace closing its
        body, or at the end of its statement when it has none."""
        masked, n = self.masked, len(self.masked)
        level, parens, opened = depth, 0, False
        i = self.offsets[line]
        while i < n:
            c = masked[i]
            if c == "{":
                level, opened = level + 1, True
            elif c == "}":
                level -= 1
                if level <= depth:
                    newline = masked.find("\n", i)
                    return n if newline < 0 else newline + 1
            elif c in "([":
                parens += 1
            elif c in ")]":
                parens -= 1
            elif c == "\n" and not opened and parens <= 0:
                following = NON_SPACE.search(masked, i + 1)
                if following is None or not masked.startswith(SWIFT_CONTINUES, following.start()):
                    return i + 1
            i += 1
        return n

    def gap(self, path: tuple[str, ...], start: int, end: int, sections: list[Section]) -> None:
        if start < end and non_blank(self.text, start, end):
            sections.append(Section(path if path else ("file level",), start, end, kind="code"))

    def sections(self) -> list[Section]:
        sections: list[Section] = []
        cursor = 0
        for line, keyword, name in self.declarations(0, self.lines, 0):
            if self.offsets[line] < cursor:
                continue                       # inside the declaration before it
            start = self.lead(line, cursor)
            end = self.end(line, 0)
            self.gap((), cursor, start, sections)
            path = (f"{keyword} {name}".strip(),)
            members = []
            if keyword in SWIFT_TYPES:
                members = self.declarations(line + 1, self.line_of(end - 1) + 1, 1)
            if members:
                head_end = self.lead(members[0][0], start)
                sections.append(Section(path, start, head_end, kind="type"))
                inner = head_end
                for m_line, m_keyword, m_name in members:
                    if self.offsets[m_line] < inner:
                        continue
                    m_start = self.lead(m_line, inner)
                    m_end = min(self.end(m_line, 1), end)
                    self.gap(path, inner, m_start, sections)
                    sections.append(Section(path + (f"{m_keyword} {m_name}".strip(),), m_start, m_end,
                                            kind=m_keyword))
                    inner = m_end
                self.gap(path, inner, end, sections)
            else:
                sections.append(Section(path, start, end, kind="type" if keyword in SWIFT_TYPES else keyword))
            cursor = end
        self.gap((), cursor, len(self.text), sections)
        return sections


LOADERS: dict[str, Loader] = {
    ".md": MarkdownLoader(),
    ".markdown": MarkdownLoader(),
    ".txt": PlainTextLoader(),
    ".py": PythonLoader(),
    ".swift": SwiftLoader(),
    ".yaml": PlainTextLoader("yaml"),
    ".yml": PlainTextLoader("yaml"),
    ".graphql": PlainTextLoader("graphql"),
    ".gql": PlainTextLoader("graphql"),
    ".proto": PlainTextLoader("protobuf"),
    ".sql": PlainTextLoader("sql"),
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
