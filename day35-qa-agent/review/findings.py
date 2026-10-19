"""The model's reply, read and checked: findings that point at real lines.

    parse_reply(text)              the JSON -> (summary, raw findings) or an error to retry on
    read_findings(raw, batch)      each finding checked against what the model was shown
    dedupe / ordered               one finding per place, the worst first
    inline_body(finding)           the text of a comment on the code

**Checked by code, not trusted.** A finding names a file and a line. The file
must be one of the request's; a path the model shortened (`review/diff.py`
for `app/review/diff.py`) is matched back when exactly one file ends with it.
The line must be one the model was shown - a number outside them is a guess,
and the finding is kept for the summary at file level rather than pinned to
a line it may not be about. An excerpt number that was not sent is dropped.

**Fingerprints.** Each finding carries `category + path + the text of its
line and of the lines on either side`, hashed, as a hidden marker in its
comment - the neighbours tell two `return None` in one file apart. On the next push the
reviewer reads the markers already on the pull request and does not comment
on the same line for the same reason twice - the wording will differ from
run to run, the line it is about will not.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .context import Batch, label

CATEGORIES = ("bug", "architecture", "recommendation")
CATEGORY_TITLES = {"bug": "Potential bugs", "architecture": "Architecture problems",
                   "recommendation": "Recommendations"}
CATEGORY_LABELS = {"bug": "Potential bug", "architecture": "Architecture", "recommendation": "Recommendation"}
CATEGORY_ICONS = {"bug": "🐞", "architecture": "🏛️", "recommendation": "💡"}
SEVERITIES = ("high", "medium", "low")
SEVERITY_ICONS = {"high": "🔴", "medium": "🟠", "low": "🟡"}

ALIASES = {
    "bug": "bug", "bugs": "bug", "potential_bug": "bug", "potential bug": "bug", "error": "bug",
    "correctness": "bug", "security": "bug", "defect": "bug",
    "architecture": "architecture", "architectural": "architecture", "design": "architecture",
    "architecture_problem": "architecture", "architectural problem": "architecture",
    "recommendation": "recommendation", "recommendations": "recommendation", "suggestion": "recommendation",
    "improvement": "recommendation", "style": "recommendation", "test": "recommendation",
    "tests": "recommendation", "maintainability": "recommendation", "docs": "recommendation",
}

FINGERPRINT = re.compile(r"<!-- ai-review:fp=([0-9a-f]{12}) -->")
FENCE_LANG = {".py": "python", ".swift": "swift", ".js": "javascript", ".ts": "typescript",
              ".tsx": "tsx", ".jsx": "jsx", ".kt": "kotlin", ".java": "java", ".go": "go",
              ".rs": "rust", ".rb": "ruby", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
              ".json": "json", ".md": "markdown", ".sql": "sql", ".m": "objectivec", ".c": "c",
              ".h": "c", ".cpp": "cpp", ".cs": "csharp", ".php": "php", ".html": "html", ".css": "css"}


@dataclass
class Finding:
    category: str
    severity: str
    path: str | None
    line: int | None
    end_line: int | None
    title: str
    comment: str
    suggestion: str = ""
    sources: list[str] = field(default_factory=list)    # excerpt labels: "docs/x.md › Layers"
    line_text: str = ""        # the line and its neighbours: what the fingerprint is anchored to
    # Filled when it is published (`publish`):
    inline: bool = False
    url: str | None = None
    note: str = ""

    @property
    def fingerprint(self) -> str:
        anchor = " ".join(self.line_text.split()) if self.line is not None else self.title.lower()
        raw = f"{self.category}|{self.path or ''}|{anchor}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @property
    def where(self) -> str:
        if not self.path:
            return ""
        if self.line is None:
            return self.path
        if self.end_line and self.end_line != self.line:
            return f"{self.path}:{self.line}-{self.end_line}"
        return f"{self.path}:{self.line}"


# ------------------------------------------------------------------ the reply

def loads(text: str):
    text = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except ValueError:
        pass
    inner = re.search(r"\{.*\}", text, re.S)
    if inner:
        try:
            return json.loads(inner.group(0))
        except ValueError:
            pass
    return None


def parse_reply(text: str) -> tuple[str, list[dict], str | None]:
    """(summary, findings as the model wrote them, the problem - None when it is fine)."""
    data = loads(text)
    if not isinstance(data, dict):
        return "", [], "it could not be read as JSON"
    findings = data.get("findings", data.get("comments", data.get("issues")))
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        return "", [], "\"findings\" is not a list"
    return str(data.get("summary") or "").strip(), [f for f in findings if isinstance(f, dict)], None


def as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.match(r"\s*L?(\d+)", value)
        return int(match.group(1)) if match else None
    return None


def category_of(raw) -> str:
    key = str(raw or "").strip().lower().replace("-", "_")
    return ALIASES.get(key, ALIASES.get(key.replace("_", " "), "recommendation"))


def severity_of(raw) -> str:
    key = str(raw or "").strip().lower()
    if key in SEVERITIES:
        return key
    if key in ("critical", "blocker", "major", "error"):
        return "high"
    if key in ("minor", "info", "nit", "trivial"):
        return "low"
    return "medium"


def match_path(raw: str, paths: list[str]) -> str | None:
    raw = re.sub(r":L?\d+(?:-L?\d+)?$", "", (raw or "").strip().strip("`"))
    while raw.startswith("./"):
        raw = raw[2:]
    if raw.startswith(("a/", "b/")) and raw[2:] in paths:
        raw = raw[2:]
    if raw in paths:
        return raw
    if not raw:
        return None
    ending = [p for p in paths if p.endswith("/" + raw)]
    return ending[0] if len(ending) == 1 else None


def read_findings(raw: list[dict], batch: Batch) -> tuple[list[Finding], list[str]]:
    """Findings checked against the batch; the second list says what was dropped and why."""
    views = {f.path: f for f in batch.files}
    paths = list(views)
    sources = {h["rank"]: label(h) for h in batch.excerpts}
    found: list[Finding] = []
    dropped: list[str] = []
    for item in raw:
        title = " ".join(str(item.get("title") or "").split())
        comment = str(item.get("comment") or item.get("body") or item.get("description") or "").strip()
        if not title and not comment:
            dropped.append("a finding with no title and no comment")
            continue
        if not title:
            title = comment.split("\n", 1)[0][:120]
        raw_path = str(item.get("file") or item.get("path") or "")
        path = match_path(raw_path, paths)
        if raw_path and path is None:
            dropped.append(f"`{raw_path}` is not a file of this change: {title}")
            continue
        line = as_int(item.get("line", item.get("start_line")))
        end_line = as_int(item.get("end_line"))
        line_text = ""
        if path is not None and line is not None:
            view = views[path]
            if line not in view.rendered.shown:
                line, end_line = None, None
            else:
                head = view.file.head_lines()
                line_text = "\n".join(head.get(n, "") for n in (line - 1, line, line + 1))
                if end_line is not None and (end_line <= line or end_line not in view.rendered.shown
                                             or end_line - line > 50):
                    end_line = None
        else:
            line, end_line = None, None
        cited = []
        raw_sources = item.get("sources")
        if not isinstance(raw_sources, list):
            raw_sources = [raw_sources] if raw_sources is not None else []
        for n in raw_sources:
            n = as_int(n)
            if n in sources and sources[n] not in cited:
                cited.append(sources[n])
        found.append(Finding(
            category=category_of(item.get("category") or item.get("type")),
            severity=severity_of(item.get("severity")),
            path=path, line=line, end_line=end_line, title=title, comment=comment,
            suggestion=str(item.get("suggestion") or "").strip("\n"),
            sources=cited, line_text=line_text,
        ))
    return found, dropped


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One finding per (category, file, line) - the most severe of them."""
    best: dict[tuple, Finding] = {}
    for f in ordered(findings):
        key = (f.category, f.path, f.line) if f.line is not None else (f.category, f.path, f.title.lower())
        best.setdefault(key, f)
    return ordered(list(best.values()))


def ordered(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (CATEGORIES.index(f.category), SEVERITIES.index(f.severity),
                                           f.path or "", f.line or 0))


# ------------------------------------------------------------------ comments

def fence(code: str, path: str | None) -> str:
    longest = max([len(run) for run in re.findall(r"`{3,}", code)] + [2])
    ticks = "`" * (longest + 1)
    lang = FENCE_LANG.get(("." + path.rsplit(".", 1)[-1].lower()) if path and "." in path else "", "")
    return f"{ticks}{lang}\n{code}\n{ticks}"


def marker(fingerprint: str) -> str:
    return f"<!-- ai-review:fp={fingerprint} -->"


def inline_body(f: Finding) -> str:
    parts = [f"**{CATEGORY_ICONS[f.category]} {CATEGORY_LABELS[f.category]} · "
             f"{SEVERITY_ICONS[f.severity]} {f.severity}** — {f.title}"]
    if f.comment and f.comment != f.title:
        parts.append(f.comment)
    if f.suggestion:
        parts.append("Possible fix:\n" + fence(f.suggestion, f.path))
    if f.sources:
        parts.append("<sub>Project context: " + " · ".join(f"`{s}`" for s in f.sources) + "</sub>")
    parts.append(marker(f.fingerprint))
    return "\n\n".join(parts)


def fingerprints_in(bodies: list[str]) -> set[str]:
    return {fp for body in bodies for fp in FINGERPRINT.findall(body or "")}
