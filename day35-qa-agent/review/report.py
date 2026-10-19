"""The review as Markdown: the summary comment on the pull request, and the job summary.

    summary_markdown(result, link)    potential bugs / architecture / recommendations,
                                      then how the review was made, folded

`link(path, line, end_line)` makes a file reference a link to the head
commit; without one (a local run) references stay plain `path:line`.
"""

from __future__ import annotations

from typing import Callable

from .context import label
from .findings import CATEGORIES, CATEGORY_ICONS, CATEGORY_TITLES, SEVERITY_ICONS, Finding, fence
from .pipeline import ReviewResult

SUMMARY_MARKER = "<!-- ai-review:summary -->"
MAX_LISTED = 12
#: GitHub refuses a comment over 65,536 characters; past this the summary is compacted.
MAX_SUMMARY_CHARS = 60_000
COMPACT_COMMENT_CHARS = 300

Link = Callable[[str, int | None, int | None], str]


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def reference(f: Finding, link: Link | None) -> str:
    if not f.path:
        return ""
    if f.url:
        return f"[`{f.where}`]({f.url})"
    if link is not None:
        return f"[`{f.where}`]({link(f.path, f.line, f.end_line)})"
    return f"`{f.where}`"


def indented(text: str, by: str = "  ") -> str:
    return "\n".join(by + line if line.strip() else "" for line in text.splitlines())


def finding_item(f: Finding, link: Link | None, compact: bool = False) -> str:
    where = reference(f, link)
    head = f"- {SEVERITY_ICONS[f.severity]} **{f.title}**" + (f" — {where}" if where else "")
    if f.inline:
        return head + " · on the code"
    parts = [head]
    if f.comment and f.comment != f.title:
        comment = f.comment
        if compact and len(comment) > COMPACT_COMMENT_CHARS:
            comment = comment[:COMPACT_COMMENT_CHARS].rstrip() + " ..."
        parts.append(indented(comment))
    if f.suggestion and not compact:
        parts.append(indented("<details><summary>Possible fix</summary>\n\n" + fence(f.suggestion, f.path)
                              + "\n\n</details>"))
    if f.sources:
        parts.append(indented("<sub>Project context: " + " · ".join(f"`{s}`" for s in f.sources) + "</sub>"))
    if f.note:
        parts.append(indented(f"<sub>{f.note}</sub>"))
    return "\n".join(parts)


def listed(paths: list[str]) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:MAX_LISTED])
    return shown + (f" and {len(paths) - MAX_LISTED} more" if len(paths) > MAX_LISTED else "")


def summary_markdown(result: ReviewResult, link: Link | None = None) -> str:
    """The summary - compacted (no fixes, comments cut) when it would be too long for GitHub."""
    text = render_summary(result, link)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    text = render_summary(result, link, compact=True)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS].rsplit("\n", 1)[0] + "\n\n... the rest did not fit in one comment.\n"


def render_summary(result: ReviewResult, link: Link | None = None, compact: bool = False) -> str:
    findings = result.findings
    counts = {c: sum(1 for f in findings if f.category == c) for c in CATEGORIES}
    reviewed = result.plan.files
    out = [SUMMARY_MARKER, "## 🤖 AI review", ""]
    out.append(f"**{plural(counts['bug'], 'potential bug')} · {plural(counts['architecture'], 'architecture problem')}"
               f" · {plural(counts['recommendation'], 'recommendation')}** — commit `{result.head[:7]}`, "
               f"{plural(len(reviewed), 'file')} reviewed")
    out.append("")
    if not result.ok:
        out += ["> [!WARNING]", "> The review failed: " + "; ".join(sorted({r.error for r in result.failed})), ""]
    elif result.failed:
        failed = [p for r in result.failed for p in r.batch.paths]
        out += ["> [!WARNING]", f"> Part of the review failed ({result.failed[0].error}); not reviewed: "
                + listed(failed), ""]
    summaries = result.summaries
    if len(summaries) == 1:
        out += [summaries[0], ""]
    elif summaries:
        out += [f"- {s}" for s in summaries] + [""]
    if not result.plan.batches:
        out += ["Nothing to review: no changed text file in this pull request.", ""]
    elif not findings and result.ok:
        out += ["No problems found in the reviewed changes.", ""]
    for category in CATEGORIES:
        items = [f for f in findings if f.category == category]
        if not items:
            continue
        out += [f"### {CATEGORY_ICONS[category]} {CATEGORY_TITLES[category]} ({len(items)})", ""]
        out += [finding_item(f, link, compact) for f in items]
        out.append("")
    out += details(result)
    out += ["", "<sub>Written by an AI reviewer: check a finding before acting on it.</sub>"]
    return "\n".join(out).rstrip() + "\n"


def details(result: ReviewResult) -> list[str]:
    usage = result.usage
    plan = result.plan
    reviewed = plan.files
    added = sum(f.file.added for f in reviewed)
    removed = sum(f.file.removed for f in reviewed)
    lines = ["<details><summary>How this review was made</summary>", ""]
    lines.append(f"- **Model**: `{result.model}` · {plural(result.requests, 'request')} · "
                 f"{usage['prompt_tokens']:,} tokens in, {usage['completion_tokens']:,} out"
                 + (f" ({usage['reasoning_tokens']:,} thinking)" if usage["reasoning_tokens"] else "")
                 + f" · {result.seconds:.0f} s")
    lines.append(f"- **Diff**: `{result.merge_base[:7]}..{result.head[:7]}`, {plural(len(result.files), 'file')} "
                 f"changed; reviewed {len(reviewed)} (+{added} −{removed}) in "
                 f"{plural(len(plan.batches), 'request')}")
    partly = [f"{f.path} (first {len(f.rendered.shown)} lines)" for f in plan.files if f.rendered.truncated]
    if partly:
        lines.append("- **Partly reviewed** (too long for one request): " + listed(partly))
    copies = [f for f in plan.files if f.file.status == "copied"]
    if copies:
        lines.append(f"- **Copies**: {plural(len(copies), 'file')} reviewed as differences from the file "
                     f"they were copied from: " + listed([f"{f.path} ← {f.file.old_path}" for f in copies]))
    if plan.unchanged:
        lines.append(f"- **Unchanged copies and renames** (nothing to review): "
                     + listed([f.path for f in plan.unchanged]))
    if plan.deleted:
        lines.append("- **Deleted**: " + listed([f.path for f in plan.deleted]))
    if plan.skipped:
        skipped = [f"`{s['path']}` ({s['reason']})" for s in plan.skipped]
        lines.append("- **Skipped**: " + ", ".join(skipped[:MAX_LISTED])
                     + (f" and {len(skipped) - MAX_LISTED} more" if len(skipped) > MAX_LISTED else ""))
    if result.knowledge:
        k = result.knowledge
        used = []
        for run in result.runs:
            for hit in run.batch.excerpts:
                name = label(hit)
                if name not in used:
                    used.append(name)
        lines.append(f"- **RAG**: `{k['root']}` indexed with `{k['embedder']}` - {k['documents']} files, "
                     f"{k['chunks']} chunks ({k['embedded_now']} embedded now, {k['seconds']} s); "
                     f"{plural(len(used), 'excerpt')} sent" + (": " + listed(used) if used else ""))
    if result.knowledge_problem:
        lines.append(f"- **RAG**: {'some parts without excerpts' if result.knowledge else 'not used'} - "
                     f"{result.knowledge_problem}")
    dropped = [d for run in result.runs for d in run.dropped]
    if dropped:
        lines.append(f"- **Dropped**: {plural(len(dropped), 'finding')} that did not point at the change")
    lines += ["", "</details>"]
    return lines
