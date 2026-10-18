"""Unified diffs, read: files, hunks, and the lines a comment can sit on.

    parse(text)                  `git diff` output -> [FileDiff]
    parse_patch(path, patch)     one file's `patch` from GitHub's PR files API -> FileDiff
    FileDiff.commentable         new-file line numbers inside the hunks: where GitHub
                                 accepts a review comment (added and context lines)
    render(file, ...)            the file as the model reads it - the new file's line
                                 numbers on the left, `+` and `-` marking the change

**Line numbers are the new file's.** A review comment on GitHub is placed by
`path` + `line` on the RIGHT side, which is the line's number in the head
version of the file. The model is shown exactly those numbers, so the line it
names is the line the comment goes on - nothing is counted twice.

**Copies.** The reviewer asks git for copies as well as renames (`-C -C`): a
repository that keeps snapshots - or a file split off from another - would
otherwise show a copied file as two hundred added lines, and the model would
review code that was already reviewed where it came from. A copy comes back
as `status == "copied"` with only its differences from the source marked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")

ADDED, REMOVED, CONTEXT = "+", "-", " "


@dataclass
class Line:
    kind: str                 # "+" added, "-" removed, " " context
    text: str
    old: int | None           # its number in the base file (None for an added line)
    new: int | None           # its number in the head file (None for a removed line)


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str               # what git printed after the second @@ - the enclosing function
    lines: list[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str                 # the head path (the base path, for a deleted file)
    old_path: str | None = None
    status: str = "modified"  # added | modified | deleted | renamed | copied
    similarity: int | None = None
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for line in h.lines if line.kind == ADDED)

    @property
    def removed(self) -> int:
        return sum(1 for h in self.hunks for line in h.lines if line.kind == REMOVED)

    @property
    def changed(self) -> bool:
        return self.added > 0 or self.removed > 0

    @property
    def commentable(self) -> set[int]:
        """Head-file lines inside a hunk - the ones GitHub lets a comment sit on."""
        return {line.new for h in self.hunks for line in h.lines if line.new is not None}

    @property
    def added_lines(self) -> set[int]:
        return {line.new for h in self.hunks for line in h.lines if line.kind == ADDED}

    def head_lines(self) -> dict[int, str]:
        """The head file's lines the diff shows, by number."""
        return {line.new: line.text for h in self.hunks for line in h.lines if line.new is not None}

    def headers(self) -> list[str]:
        seen: list[str] = []
        for h in self.hunks:
            if h.header and h.header not in seen:
                seen.append(h.header)
        return seen

    def describe(self) -> str:
        if self.status in ("renamed", "copied") and self.old_path:
            how = "copied from" if self.status == "copied" else "renamed from"
            return f"{how} `{self.old_path}`" + (f", {self.similarity}% similar" if self.similarity else "")
        return self.status


# ------------------------------------------------------------------ parsing

def unquote(path: str) -> str:
    """Git's C-style quoting of an unusual path, undone (`"a\\tb"` -> a<TAB>b)."""
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    raw = path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    return raw.encode("latin-1", "replace").decode("utf-8", "replace")


def strip_prefix(path: str) -> str | None:
    path = unquote(path.rstrip("\t").rstrip())
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def paths_from_header(rest: str) -> tuple[str, str]:
    """`a/<p> b/<p>` from a `diff --git` line - unambiguous only when both paths are the
    same, which is every case that has no `---`/`+++`/`rename to` line to go by."""
    rest = rest.strip()
    if rest.startswith('"'):
        end = rest.index('"', 1)
        while rest[end - 1] == "\\":
            end = rest.index('"', end + 1)
        first, second = rest[: end + 1], rest[end + 1:].strip()
        return strip_prefix(first) or "", strip_prefix(second) or ""
    if (len(rest) - 5) % 2 == 0:
        n = (len(rest) - 5) // 2
        if rest[:2] == "a/" and rest[2 + n: 5 + n] == " b/" and rest[2:2 + n] == rest[5 + n:]:
            return rest[2:2 + n], rest[5 + n:]
    first, _, second = rest.partition(" b/")
    return first[2:] if first.startswith("a/") else first, second


def read_hunks(lines: list[str], start: int, file: FileDiff) -> int:
    """Hunks from `lines[start]` on, into `file`; returns where the next file begins."""
    i = start
    while i < len(lines) and not lines[i].startswith("diff --git "):
        match = HUNK.match(lines[i])
        if match is None:
            i += 1
            continue
        old_start, old_count = int(match.group(1)), int(match.group(2) or 1)
        new_start, new_count = int(match.group(3)), int(match.group(4) or 1)
        hunk = Hunk(old_start, old_count, new_start, new_count, match.group(5).strip())
        old_no, new_no = old_start, new_start
        old_left, new_left = old_count, new_count
        i += 1
        while i < len(lines) and (old_left > 0 or new_left > 0):
            raw = lines[i]
            if raw.startswith("\\"):              # "\ No newline at end of file"
                i += 1
                continue
            kind, text = (raw[:1] or CONTEXT), raw[1:]
            if kind == ADDED:
                hunk.lines.append(Line(ADDED, text, None, new_no))
                new_no += 1
                new_left -= 1
            elif kind == REMOVED:
                hunk.lines.append(Line(REMOVED, text, old_no, None))
                old_no += 1
                old_left -= 1
            elif kind == CONTEXT:
                hunk.lines.append(Line(CONTEXT, text, old_no, new_no))
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
            else:
                break
            i += 1
        while i < len(lines) and lines[i].startswith("\\"):
            i += 1
        file.hunks.append(hunk)
    return i


def parse(text: str) -> list[FileDiff]:
    """`git diff` output -> one `FileDiff` per file, in the order git printed them."""
    lines = text.splitlines()
    files: list[FileDiff] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            i += 1
            continue
        old, new = paths_from_header(lines[i][len("diff --git "):])
        file = FileDiff(path=new or old, old_path=old if old != new else None)
        i += 1
        minus: str | None = None
        plus: str | None = None
        saw_minus = saw_plus = False
        while i < len(lines) and not lines[i].startswith(("diff --git ", "@@ ")):
            line = lines[i]
            if line.startswith("new file mode"):
                file.status = "added"
            elif line.startswith("deleted file mode"):
                file.status = "deleted"
            elif line.startswith("similarity index "):
                file.similarity = int(line.split()[-1].rstrip("%") or 0)
            elif line.startswith("rename from "):
                file.status, file.old_path = "renamed", unquote(line[len("rename from "):])
            elif line.startswith("rename to "):
                file.path = unquote(line[len("rename to "):])
            elif line.startswith("copy from "):
                file.status, file.old_path = "copied", unquote(line[len("copy from "):])
            elif line.startswith("copy to "):
                file.path = unquote(line[len("copy to "):])
            elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
                file.binary = True
            elif line.startswith("--- "):
                minus, saw_minus = strip_prefix(line[4:]), True
            elif line.startswith("+++ "):
                plus, saw_plus = strip_prefix(line[4:]), True
            i += 1
        if saw_plus and plus is not None:
            file.path = plus
        elif saw_plus and plus is None and minus is not None:
            file.path = minus                     # deleted: the base path is the only one
        if saw_minus and minus is not None and file.status in ("renamed", "copied"):
            file.old_path = minus
        if file.status == "modified" and file.old_path and file.old_path != file.path:
            file.status = "renamed"
        i = read_hunks(lines, i, file)
        files.append(file)
    return files


def parse_patch(path: str, patch: str, status: str = "modified") -> FileDiff:
    """One file's `patch` from GitHub (hunks only, no headers)."""
    file = FileDiff(path=path, status=status)
    read_hunks((patch or "").splitlines(), 0, file)
    return file


# ------------------------------------------------------------------ rendering

@dataclass
class Rendered:
    text: str
    shown: set[int]           # head-file line numbers the model was shown
    truncated: int = 0        # lines left out at the end because of max_lines


MAX_LINE_CHARS = 400


def render(file: FileDiff, context: int | None = None, max_lines: int | None = None) -> Rendered:
    """The diff as the model reads it.

        ### path - modified, +3 -1
         41 |     def load(self):
         42 | +       data = read()
            | -       data = None
         43 |         return data

    `context` - unchanged lines kept around each change; None keeps them all
    (the diff came with the whole file, so the model reads the file). Lines
    further from a change are folded into one `... N unchanged lines ...`.
    `max_lines` - a cap on what is printed, shared out over every part of the
    change (`spread`): each part loses lines, none disappears.
    A line longer than `MAX_LINE_CHARS` (minified code, data) is cut.
    """
    rows: list[tuple[str, Line | None, str]] = []
    for hunk in file.hunks:
        keep = keep_mask(hunk.lines, context)
        if rows:
            rows.append(("gap", None, ""))
        if hunk.header and context is not None:
            rows.append(("header", None, hunk.header))
        folded = 0
        for line, kept in zip(hunk.lines, keep):
            if not kept:
                folded += 1
                continue
            if folded:
                rows.append(("fold", None, f"... {folded} unchanged lines ..."))
                folded = 0
            rows.append(("line", line, ""))
        if folded:
            rows.append(("fold", None, f"... {folded} unchanged lines ..."))

    rows, truncated = spread(rows, max_lines)
    width = max([len(str(line.new)) for kind, line, _ in rows if line and line.new] + [3])
    out = [f"### {file.path} - {file.describe()}, +{file.added} -{file.removed}"]
    shown: set[int] = set()
    for kind, line, text in rows:
        if kind == "line":
            number = str(line.new) if line.new is not None else ""
            mark = {"+": "+ ", "-": "- ", " ": "  "}[line.kind]
            text = line.text if len(line.text) <= MAX_LINE_CHARS else line.text[:MAX_LINE_CHARS] + " ..."
            out.append(f"{number:>{width}} | {mark}{text}")
            if line.new is not None:
                shown.add(line.new)
        elif kind == "header":
            out.append(f"{'':>{width}} @@ {text}")
        elif kind == "fold":
            out.append(f"{'':>{width}} | {text}")
        elif kind == "gap":
            out.append(f"{'':>{width}} |")
    return Rendered("\n".join(out), shown, truncated)


#: However tight the cap, each part of the change keeps at least this many lines.
MIN_PART_LINES = 4


def spread(rows: list[tuple], max_lines: int | None) -> tuple[list[tuple], int]:
    """Cut the printed lines to `max_lines`, every part of the change keeping its share.

    A part is a run of lines between two folds or hunks - one change and its
    context. Cutting from the end would hide the last changes entirely; here
    each part keeps lines in proportion to its length (at least
    `MIN_PART_LINES`) - the changed lines first, then the context nearest
    them - and says where it left lines out.
    """
    total = sum(1 for kind, _, _ in rows if kind == "line")
    if max_lines is None or total <= max_lines:
        return rows, 0
    parts: list[list[tuple]] = [[]]
    for row in rows:
        if row[0] == "line":
            parts[-1].append(row)
        else:
            parts.append([row])
            parts.append([])
    out: list[tuple] = []
    hidden = 0
    for part in parts:
        if not part or part[0][0] != "line":
            out += part
            continue
        keep = max(MIN_PART_LINES, max_lines * len(part) // total)
        if len(part) <= keep:
            out += part
            continue
        # The lines closest to a change stay; the context furthest from one goes.
        changed = [i for i, row in enumerate(part) if row[1].kind != CONTEXT] or [0]
        distance = [min(abs(i - c) for c in changed) for i in range(len(part))]
        kept = set(sorted(range(len(part)), key=lambda i: (distance[i], i))[:keep])
        hidden += len(part) - keep
        gap = 0
        for i, row in enumerate(part):
            if i in kept:
                if gap:
                    out.append(("fold", None, f"... {gap} lines not shown (the file is too long) ..."))
                    gap = 0
                out.append(row)
            else:
                gap += 1
        if gap:
            out.append(("fold", None, f"... {gap} lines not shown (the file is too long) ..."))
    return out, hidden


def keep_mask(lines: list[Line], context: int | None) -> list[bool]:
    """Which lines of a hunk to print: every one, or the changes and `context` around them."""
    if context is None:
        return [True] * len(lines)
    changed = [i for i, line in enumerate(lines) if line.kind != CONTEXT]
    keep = [False] * len(lines)
    for i in changed:
        for j in range(max(0, i - context), min(len(lines), i + context + 1)):
            keep[j] = True
    return keep
