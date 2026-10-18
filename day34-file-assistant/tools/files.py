"""Day 34: the project's files as tools - read, search, write - for agentic code.

    FilesConfig     the `files` part of `code_agent.json`: what is never read
                    or written, what is skipped, the limits
    FilesProvider   the tools, over one project folder:
                      list_files    the files under a folder, a glob to narrow them
                      read_file     a file with line numbers, or a window of it
                      search_text   a text or a regex in every file (grep), as `path:line`
                      write_file    a new file, or one rewritten whole
                      edit_file     an exact fragment replaced
                      search_docs   the project's index, by meaning (when it is built)
    ChangeLog       what the writes of one turn did: each file before and after,
                    and the unified diff between them

Run in this process rather than through the official filesystem MCP server
for three reasons: that server cannot search inside files, which is half of
"find every place X is used"; it writes with no record of what was there
before, so a turn's changes could not be shown as a diff; and the rules below
need the project's git to tell a project file from a build product.

What keeps the tools to the project:

    paths      relative to the project folder; `..`, an absolute path
               elsewhere and a symlink out of it are refused
    deny       secret file names (`.env`, keys) are neither read, searched nor
               written - what the model reads goes to the model's provider
    .git       never read or written: its config can hold credentials
    exclude    folders that listing and search skip, and that are never
               written: dependencies, virtualenvs, build output
    ignored    in a git repository, a write to a file git ignores is refused -
               the app's own `data/`, build products, local config

Writes go to disk at once (the user's choice on day 34); the change log keeps
the text a file had before the turn's first write to it, so the diff of a turn
is exact even when one file was edited five times.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from tools.base import Tool, ToolResult

#: Names a deny pattern does not catch: templates of secret files are not secrets.
SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")
#: A line longer than this is cut when shown - a minified file is one line.
MAX_LINE_CHARS = 1000
#: How much of a search hit's line is shown.
MAX_HIT_CHARS = 300
#: What one stored diff may hold before it is cut.
MAX_DIFF_CHARS = 200_000
#: Bytes looked at to tell a binary file from a text one.
SNIFF_BYTES = 8192

DEFAULT_EXCLUDE = [".git", "node_modules", ".venv", "venv", "__pycache__", ".build", ".swiftpm",
                   "DerivedData", "Pods", "Carthage", "dist", "build", ".idea", ".gradle"]
DEFAULT_DENY = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.p8", "*.mobileprovision",
                "id_rsa*", "id_ed25519*", ".netrc", "credentials*", "secrets*"]


class FileError(Exception):
    """A call the tools refuse or cannot do - its message goes back to the model."""


# --------------------------------------------------------------------- the config

@dataclass
class FilesConfig:
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    deny: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    max_result_chars: int = 12000
    read_lines: int = 400
    max_read_lines: int = 2000
    max_file_bytes: int = 1_000_000
    search_results: int = 100
    max_listed: int = 400

    @classmethod
    def from_dict(cls, raw: dict | None) -> "FilesConfig":
        """The `files` object of `code_agent.json`; a missing key keeps its default."""
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("files: expected an object")
        config = cls()
        for key in ("exclude", "deny"):
            if key in raw:
                if not isinstance(raw[key], list):
                    raise ValueError(f"files.{key}: expected a list of names")
                setattr(config, key, [str(item) for item in raw[key]])
        for key in ("max_result_chars", "read_lines", "max_read_lines", "max_file_bytes",
                    "search_results", "max_listed"):
            if key in raw:
                try:
                    value = int(raw[key])
                except (TypeError, ValueError):
                    value = 0
                if value < 1:
                    raise ValueError(f"files.{key}: must be a positive number")
                setattr(config, key, value)
        return config


# --------------------------------------------------------------------- the diffs

def diff_lines(text: str) -> list[str]:
    """`text` as difflib wants it - every line ending in a newline, the last one
    marked the way `git diff` marks a file without one."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def unified_diff(path: str, before: str | None, after: str) -> str:
    """One file's change as a git-style unified diff - `git apply` reads it."""
    head = [f"diff --git a/{path} b/{path}\n"]
    if before is None:
        head.append("new file mode 100644\n")
    body = difflib.unified_diff(diff_lines(before or ""), diff_lines(after),
                                fromfile="/dev/null" if before is None else f"a/{path}",
                                tofile=f"b/{path}", n=3)
    return "".join(head + list(body))


def count_lines(diff: str) -> tuple[int, int]:
    """Lines added and removed in a unified diff, its headers not counted."""
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


class ChangeLog:
    """What one turn's writes did to the project: each file's first `before`
    and last `after`. Shared by the write tools, which may run side by side."""

    def __init__(self) -> None:
        self._files: dict[str, list] = {}
        self._lock = threading.Lock()

    def record(self, path: str, before: str | None, after: str) -> None:
        with self._lock:
            if path in self._files:
                self._files[path][1] = after
            else:
                self._files[path] = [before, after]

    def __bool__(self) -> bool:
        return bool(self.changes())

    def changes(self) -> list[dict]:
        """One entry per file that ended the turn different from how it began, by path."""
        with self._lock:
            files = sorted(self._files.items())
        out = []
        for path, (before, after) in files:
            if before == after:
                continue            # edited and edited back
            diff = unified_diff(path, before, after)
            added, removed = count_lines(diff)
            entry = {"path": path, "status": "added" if before is None else "modified",
                     "added": added, "removed": removed, "diff": diff}
            if len(diff) > MAX_DIFF_CHARS:
                entry["diff"] = diff[:MAX_DIFF_CHARS] + "\n... (diff cut: too long to store)\n"
                entry["truncated"] = True
            out.append(entry)
        return out


def patch(changes: list[dict]) -> str:
    """The diffs of a list of changes, joined into one patch."""
    return "".join(change["diff"] if change["diff"].endswith("\n") else change["diff"] + "\n"
                   for change in changes)


# --------------------------------------------------------------------- the tools

def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _cut(line: str, limit: int) -> str:
    return line if len(line) <= limit else line[:limit] + f" ... ({len(line)} characters)"


class FilesProvider:
    """The project folder's files as tools - one provider per agentic turn."""

    source = "files"

    def __init__(self, root: Path | str, config: FilesConfig | None = None, *, git: bool = False,
                 changes: ChangeLog | None = None,
                 docs: Callable[[str, int], str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.config = config or FilesConfig()
        self.git = git
        self.changes = changes if changes is not None else ChangeLog()
        self.docs = docs
        self._write_lock = threading.Lock()

    # -- what the model is offered ------------------------------------------

    def tools(self) -> list[Tool]:
        tools = [
            self._tool("list_files", self.list_files,
                       "List the project's files (in a git repository: the files git tracks or would "
                       "track; dependencies and build folders are skipped). Narrow with `path` (a folder) "
                       "and `pattern`, a glob matched against the file name, or against the whole path "
                       "when it has a slash: `*.swift`, `docs/*.md`.",
                       {"path": {"type": "string", "description": "A folder relative to the project root. Default: the root."},
                        "pattern": {"type": "string", "description": "A glob, e.g. `*.py` or `Sources/*View.swift`."}}),
            self._tool("read_file", self.read_file,
                       "Read a text file, with line numbers (`12<TAB>text`). Long files come in windows: "
                       "pass `offset` (the first line) and `limit` (how many lines). Read a file before "
                       "you edit it.",
                       {"path": {"type": "string", "description": "The file, relative to the project root."},
                        "offset": {"type": "integer", "description": "First line to show, from 1."},
                        "limit": {"type": "integer", "description": f"How many lines (default {self.config.read_lines})."}},
                       ["path"]),
            self._tool("search_text", self.search_text,
                       "Search inside every project file for a text (or a regular expression) and get "
                       "each match as `path:line: text`, grouped by file - like grep. Use it to find where "
                       "a type, function, API or setting is defined and used.",
                       {"pattern": {"type": "string", "description": "The text to find, or a regex when `regex` is true."},
                        "path": {"type": "string", "description": "Only under this folder (or this one file)."},
                        "glob": {"type": "string", "description": "Only files matching this glob, e.g. `*.swift`."},
                        "regex": {"type": "boolean", "description": "Treat `pattern` as a Python regular expression."},
                        "ignore_case": {"type": "boolean", "description": "Match regardless of case."},
                        "context": {"type": "integer", "description": "Lines of context around each match (0-3)."}},
                       ["pattern"]),
            self._tool("write_file", self.write_file,
                       "Create a file, or replace a file's whole content. Folders are created as needed. "
                       "Written to disk at once. For a change inside an existing file use edit_file.",
                       {"path": {"type": "string", "description": "The file, relative to the project root."},
                        "content": {"type": "string", "description": "The complete new content of the file."}},
                       ["path", "content"]),
            self._tool("edit_file", self.edit_file,
                       "Change part of an existing file: `old_text` is replaced by `new_text`. `old_text` "
                       "must be copied exactly from the file (read it first: same indentation, no line "
                       "numbers) and must occur once - add surrounding lines to make it unique, or set "
                       "`replace_all`. Written to disk at once; the result shows the diff.",
                       {"path": {"type": "string", "description": "The file, relative to the project root."},
                        "old_text": {"type": "string", "description": "The exact text to replace."},
                        "new_text": {"type": "string", "description": "What replaces it."},
                        "replace_all": {"type": "boolean", "description": "Replace every occurrence."}},
                       ["path", "old_text", "new_text"]),
        ]
        if self.docs is not None:
            tools.append(self._tool(
                "search_docs", self.search_docs,
                "Search the project's documentation and code index by meaning (not by exact words): "
                "for 'where is X explained' or 'which part handles Y' when you do not know the names. "
                "Returns numbered excerpts with their file and section; open the file to get lines.",
                {"query": {"type": "string", "description": "What you are looking for, in words."},
                 "k": {"type": "integer", "description": "How many excerpts (1-10, default 5)."}},
                ["query"]))
        return tools

    def guide(self) -> str:
        return ""

    def _tool(self, name: str, run: Callable[[dict], str], description: str, properties: dict,
              required: list | None = None) -> Tool:
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        def call(arguments: dict) -> ToolResult:
            try:
                text = run(arguments or {})
            except FileError as err:
                return ToolResult(ok=False, is_error=True, text=f"Error: {err}", tool=name, server_id=self.source)
            except OSError as err:
                return ToolResult(ok=False, is_error=True, text=f"Error: {err.strerror or err}",
                                  tool=name, server_id=self.source)
            return ToolResult(ok=True, text=self._fit(text), tool=name, server_id=self.source)

        return Tool(name=name, description=description, input_schema=schema, call=call, source=self.source)

    def _fit(self, text: str) -> str:
        limit = self.config.max_result_chars
        if len(text) <= limit:
            return text
        return (text[:limit] + f"\n... (cut: {len(text)} characters, the first {limit} shown - "
                "ask for something narrower)")

    # -- paths -----------------------------------------------------------------

    def resolve(self, raw, *, write: bool = False) -> tuple[Path, str]:
        """The file a path names, and its path relative to the root - or `FileError`."""
        text = str(raw if raw is not None else "").strip().replace("\\", "/") or "."
        given = Path(text).expanduser()
        full = (given if given.is_absolute() else self.root / given).resolve()
        try:
            rel = full.relative_to(self.root)
        except ValueError:
            raise FileError(f"{text} is outside the project folder ({self.root})") from None
        rel_text = rel.as_posix() if rel.parts else "."
        if ".git" in rel.parts:
            raise FileError(f"{rel_text} is inside .git, which these tools do not touch")
        name = rel.name
        if name and not name.endswith(SAFE_SUFFIXES) and any(fnmatch.fnmatch(name, p) for p in self.config.deny):
            raise FileError(f"{rel_text} is not readable or writable by this assistant - files like it "
                            "hold secrets (the deny list in code_agent.json)")
        if write:
            if not rel.parts:
                raise FileError("the project folder itself is not a file")
            skipped = next((part for part in rel.parts[:-1] if part in self.config.exclude), None)
            if skipped:
                raise FileError(f"{rel_text} is inside {skipped}/, which is not written to "
                                "(the exclude list in code_agent.json)")
            if self.git and self._ignored(rel_text):
                raise FileError(f"{rel_text} is ignored by git (.gitignore) - not a project file, "
                                "so it is not written")
        return full, rel_text

    def _ignored(self, rel: str) -> bool:
        try:
            done = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", "--", rel],
                                  capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    def _excluded(self, rel: str) -> bool:
        parts = PurePosixPath(rel).parts
        return any(part in self.config.exclude for part in parts[:-1]) or ".git" in parts

    def _denied(self, rel: str) -> bool:
        name = PurePosixPath(rel).name
        return not name.endswith(SAFE_SUFFIXES) and any(fnmatch.fnmatch(name, p) for p in self.config.deny)

    def project_files(self) -> list[str]:
        """Every project file, relative to the root, sorted: `git ls-files` in a
        repository (tracked and untracked-but-not-ignored), a walk otherwise."""
        listed: list[str] | None = None
        if self.git:
            try:
                done = subprocess.run(["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others",
                                       "--exclude-standard"], capture_output=True, timeout=30)
                if done.returncode == 0:
                    listed = sorted({p for p in done.stdout.decode("utf-8", "replace").split("\0") if p})
            except (OSError, subprocess.TimeoutExpired):
                listed = None
        if listed is None:
            listed = []
            for folder, dirs, files in os.walk(self.root):
                dirs[:] = sorted(d for d in dirs if d not in self.config.exclude and d != ".git")
                base = Path(folder).relative_to(self.root)
                listed.extend((base / name).as_posix() for name in sorted(files))
        return [rel for rel in listed
                if not self._excluded(rel) and (self.root / rel).is_file()]

    def _under(self, files: list[str], path) -> tuple[list[str], str]:
        full, rel = self.resolve(path)
        if full.is_file():
            return [rel], rel
        if not full.is_dir():
            raise FileError(f"there is no file or folder {rel}")
        if rel == ".":
            return files, rel
        prefix = rel + "/"
        return [f for f in files if f.startswith(prefix)], rel

    @staticmethod
    def _matches(rel: str, pattern: str) -> bool:
        if not pattern:
            return True
        pattern = pattern.strip()
        if "/" in pattern:
            return fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, pattern.replace("**/", ""))
        return fnmatch.fnmatch(PurePosixPath(rel).name, pattern)

    # -- reading ---------------------------------------------------------------

    def _read_text(self, full: Path, rel: str) -> str:
        if not full.exists():
            raise FileError(f"there is no file {rel}")
        if full.is_dir():
            raise FileError(f"{rel} is a folder - list it with list_files")
        size = full.stat().st_size
        if size > self.config.max_file_bytes:
            raise FileError(f"{rel} is {size} bytes - larger than the {self.config.max_file_bytes} "
                            "these tools read")
        data = full.read_bytes()
        if b"\0" in data[:SNIFF_BYTES]:
            raise FileError(f"{rel} is a binary file")
        return data.decode("utf-8", errors="replace")

    def _read_exact(self, full: Path, rel: str) -> str:
        """A file's text for rewriting - refused when it is not valid UTF-8, which
        a lossy read and a write back would corrupt."""
        self._read_text(full, rel)
        try:
            return full.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise FileError(f"{rel} is not UTF-8 text, so it is not edited") from None

    def list_files(self, args: dict) -> str:
        files, where = self._under(self.project_files(), args.get("path"))
        pattern = str(args.get("pattern") or "")
        chosen = [f for f in files if self._matches(f, pattern)]
        what = f"{len(chosen)} file{'s' if len(chosen) != 1 else ''}"
        head = f"{what} under {where}" + (f" matching {pattern}" if pattern else "") + ":"
        if not chosen:
            return head.rstrip(":") + "."
        shown = chosen[: self.config.max_listed]
        lines = [head, *shown]
        if len(chosen) > len(shown):
            lines.append(f"... and {len(chosen) - len(shown)} more - narrow it with path or pattern")
        return "\n".join(lines)

    def read_file(self, args: dict) -> str:
        full, rel = self.resolve(args.get("path"))
        lines = self._read_text(full, rel).splitlines()
        total = len(lines)
        if total == 0:
            return f"{rel} is empty."
        start = _int(args.get("offset"), 1, 1, max(1, total))
        limit = _int(args.get("limit"), self.config.read_lines, 1, self.config.max_read_lines)
        end = min(total, start + limit - 1)
        out = [f"{rel} - lines {start}-{end} of {total}:"]
        size = len(out[0])
        for number in range(start, end + 1):
            row = f"{number}\t{_cut(lines[number - 1], MAX_LINE_CHARS)}"
            if size + len(row) + 1 > self.config.max_result_chars - 200:
                end = number - 1
                out.append(f"... (stopped at line {end} to fit - read on with offset={end + 1})")
                return "\n".join(out)
            out.append(row)
            size += len(row) + 1
        if end < total:
            out.append(f"... ({total - end} more lines - read on with offset={end + 1})")
        return "\n".join(out)

    def search_text(self, args: dict) -> str:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            raise FileError("pattern is empty")
        flags = re.IGNORECASE if _bool(args.get("ignore_case")) else 0
        try:
            matcher = re.compile(pattern if _bool(args.get("regex")) else re.escape(pattern), flags)
        except re.error as err:
            raise FileError(f"not a valid regular expression: {err}") from None
        context = _int(args.get("context"), 0, 0, 3)
        glob = str(args.get("glob") or "")
        files, where = self._under(self.project_files(), args.get("path"))
        files = [f for f in files if self._matches(f, glob) and not self._denied(f)]
        limit = self.config.search_results
        shown = matches = searched = 0
        hit_files: list[str] = []
        blocks: list[str] = []
        for rel in files:
            full = self.root / rel
            try:
                if full.stat().st_size > self.config.max_file_bytes:
                    continue
                data = full.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:SNIFF_BYTES]:
                continue
            searched += 1
            lines = data.decode("utf-8", errors="replace").splitlines()
            found = [i for i, line in enumerate(lines) if matcher.search(line)]
            if not found:
                continue
            matches += len(found)
            hit_files.append(rel)
            if shown >= limit:
                continue
            rows = [rel]
            last = -1
            for i in found:
                if shown >= limit:
                    break
                for j in range(max(0, i - context, last + 1), min(len(lines), i + context + 1)):
                    mark = ":" if j == i else "-"
                    rows.append(f"  {j + 1}{mark} {_cut(lines[j].strip() if not context else lines[j], MAX_HIT_CHARS)}")
                    last = j
                shown += 1
            blocks.append("\n".join(rows))
        scope = f"under {where}" + (f", files matching {glob}" if glob else "")
        if not matches:
            return f"No matches for {pattern!r} in {searched} files {scope}."
        head = (f"{matches} match{'es' if matches != 1 else ''} in {len(hit_files)} "
                f"file{'s' if len(hit_files) != 1 else ''} for {pattern!r} ({searched} files searched {scope}):")
        tail = []
        if shown < matches:
            rest = [f for f in hit_files if not any(b.startswith(f + "\n") for b in blocks)]
            tail.append(f"... showing the first {shown} matches - narrow it with path or glob."
                        + (f" More files with matches: {', '.join(rest[:30])}" if rest else ""))
        return "\n".join([head, *blocks, *tail])

    def search_docs(self, args: dict) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            raise FileError("query is empty")
        try:
            return self.docs(query, _int(args.get("k"), 5, 1, 10))
        except Exception as err:  # noqa: BLE001 - an index that cannot be searched is an answer
            detail = getattr(err, "detail", None) or str(err)
            raise FileError(f"the index could not be searched ({detail}) - use search_text") from None

    # -- writing ---------------------------------------------------------------

    def write_file(self, args: dict) -> str:
        if "content" not in args or not isinstance(args.get("content"), str):
            raise FileError("content is missing - pass the file's complete text")
        content = args["content"]
        with self._write_lock:
            full, rel = self.resolve(args.get("path"), write=True)
            before = self._read_exact(full, rel) if full.exists() else None
            if before == content:
                return f"{rel} already has exactly this content - nothing written."
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content.encode("utf-8"))
            self.changes.record(rel, before, content)
        lines = len(content.splitlines())
        if before is None:
            return f"Created {rel} ({lines} lines)."
        added, removed = count_lines(unified_diff(rel, before, content))
        return f"Rewrote {rel}: now {lines} lines, +{added} -{removed}."

    def edit_file(self, args: dict) -> str:
        old, new = args.get("old_text"), args.get("new_text")
        if not isinstance(old, str) or not old:
            raise FileError("old_text is empty - to create a file use write_file")
        if not isinstance(new, str):
            raise FileError("new_text is missing")
        if old == new:
            raise FileError("old_text and new_text are the same - nothing to change")
        with self._write_lock:
            full, rel = self.resolve(args.get("path"), write=True)
            if not full.exists():
                raise FileError(f"there is no file {rel} - to create it use write_file")
            before = self._read_exact(full, rel)
            if "\r\n" in before and "\r\n" not in old:
                old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
            count = before.count(old)
            if count == 0:
                hint = (" It is there with different whitespace or indentation - copy it exactly."
                        if " ".join(old.split()) in " ".join(before.split()) else "")
                raise FileError(f"old_text was not found in {rel}.{hint} Read the file again and copy "
                                "the fragment exactly, without line numbers.")
            if count > 1 and not _bool(args.get("replace_all")):
                raise FileError(f"old_text occurs {count} times in {rel} - add surrounding lines to make "
                                "it unique, or set replace_all to change every one")
            after = before.replace(old, new) if _bool(args.get("replace_all")) else before.replace(old, new, 1)
            full.write_bytes(after.encode("utf-8"))
            self.changes.record(rel, before, after)
        diff = unified_diff(rel, before, after)
        added, removed = count_lines(diff)
        body = diff.splitlines()[3:]          # the hunks, without the file headers
        if len(body) > 60:
            body = body[:60] + [f"... ({len(body) - 60} more diff lines)"]
        replaced = count if _bool(args.get("replace_all")) else 1
        return (f"Edited {rel}: {replaced} replacement{'s' if replaced != 1 else ''}, +{added} -{removed}.\n"
                + "\n".join(body))
