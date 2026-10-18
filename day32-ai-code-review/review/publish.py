"""The review, posted: comments on the code, and one summary comment kept up to date.

    commentable(pr_files)        the lines GitHub accepts a comment on, from its own patches
    place(findings, ...)         which findings go on the code, and why the others do not
    publish(gh, pr, result)      a GitHub review with the line comments, then the summary

**Which lines.** GitHub takes a review comment only on a line inside one of
the hunks *it* shows for the file - not the ones the reviewer's copy-aware
diff shows. So the lines come from the PR's files API (`patch` per file),
and a finding on any other line goes to the summary with a link to it.

**One review per push, nothing twice.** Every line comment carries its
finding's fingerprint (`findings`). Before posting, the fingerprints on the
pull request are read, and a finding that is already there is listed in the
summary with a link to the comment it already has.

**When GitHub refuses the review** (HTTP 422 - one of its lines is not in the
diff after all), the comments are posted one by one, and the ones refused
again go to the summary.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ReviewConfig
from .diff import parse_patch
from .findings import Finding, fingerprints_in, inline_body
from .github import GitHub, GitHubError, PullRequest
from .pipeline import ReviewResult
from .report import SUMMARY_MARKER, summary_markdown


@dataclass
class Published:
    inline: int = 0
    repeated: int = 0
    refused: int = 0
    review_url: str | None = None
    summary_url: str | None = None
    summary: str = ""


def commentable(pr_files: list[dict]) -> dict[str, set[int]]:
    lines: dict[str, set[int]] = {}
    for item in pr_files:
        patch = item.get("patch")
        if patch:
            lines[item["filename"]] = parse_patch(item["filename"], patch).commentable
    return lines


def already_posted(comments: list[dict]) -> dict[str, str]:
    posted: dict[str, str] = {}
    for comment in comments:
        for fp in fingerprints_in([comment.get("body") or ""]):
            posted.setdefault(fp, comment.get("html_url") or "")
    return posted


def place(findings: list[Finding], lines: dict[str, set[int]], posted: dict[str, str],
          max_inline: int) -> list[Finding]:
    """Mark the findings that go on the code; the rest get a note saying why not."""
    chosen: list[Finding] = []
    for f in findings:
        if f.path is None or f.line is None:
            continue
        if f.fingerprint in posted:
            f.url = posted[f.fingerprint] or None
            f.note = "Already commented on an earlier push."
            continue
        if f.path not in lines:
            f.note = "GitHub shows no diff for this file (too large, or binary), so it has no line to comment on."
            continue
        allowed = lines[f.path]
        if f.line not in allowed and (f.end_line is None or f.end_line not in allowed):
            f.note = "The line is outside the part of the file GitHub shows in the diff."
            continue
        if f.line not in allowed:
            f.line, f.end_line = f.end_line, None
        if f.end_line is not None and (f.end_line not in allowed
                                       or any(n not in allowed for n in range(f.line, f.end_line + 1))):
            f.end_line = None
        if len(chosen) >= max_inline:
            f.note = f"Over the limit of {max_inline} comments on the code per review."
            continue
        f.inline = True
        chosen.append(f)
    return chosen


def payload(f: Finding) -> dict:
    comment = {"path": f.path, "side": "RIGHT", "body": inline_body(f)}
    if f.end_line is not None and f.end_line != f.line:
        comment.update(start_line=f.line, start_side="RIGHT", line=f.end_line)
    else:
        comment["line"] = f.line
    return comment


def review_body(result: ReviewResult, n: int) -> str:
    return (f"🤖 **AI review** of `{result.head[:7]}`: {n} comment{'s' if n != 1 else ''} on the code. "
            f"All findings, with the ones that could not be placed on a line, are in the AI review "
            f"summary comment.")


def publish(gh: GitHub, pr: PullRequest, result: ReviewResult, config: ReviewConfig, log=print) -> Published:
    out = Published()
    findings = result.findings
    lines = commentable(gh.pr_files(pr.number))
    posted = already_posted(gh.review_comments(pr.number))
    chosen = place(findings, lines, posted, config.max_inline_comments)
    out.repeated = sum(1 for f in findings if f.fingerprint in posted)
    if chosen:
        try:
            review = gh.create_review(pr.number, pr.head_sha, review_body(result, len(chosen)),
                                      [payload(f) for f in chosen])
            out.review_url = review.get("html_url")
            try:
                urls = already_posted(gh.comments_of_review(pr.number, review["id"]))
            except GitHubError:
                urls = {}
            for f in chosen:
                f.url = urls.get(f.fingerprint) or out.review_url
            out.inline = len(chosen)
        except GitHubError as exc:
            if exc.status != 422:
                raise
            log(f"[review ] GitHub refused the review ({exc}) - posting the comments one by one")
            for f in chosen:
                try:
                    comment = gh.create_review_comment(pr.number, pr.head_sha, payload(f))
                    f.url = comment.get("html_url")
                    out.inline += 1
                except GitHubError as one:
                    f.inline = False
                    f.note = f"GitHub did not accept a comment on this line ({one.status})."
                    out.refused += 1
    out.summary = summary_markdown(
        result, lambda path, line, end: gh.blob_url(pr.head_sha, path, line, end))
    comment = gh.upsert_comment(pr.number, SUMMARY_MARKER, out.summary)
    out.summary_url = (comment or {}).get("html_url")
    log(f"[review ] posted: {out.inline} comment(s) on the code, {out.repeated} already there, "
        f"{out.refused} refused; summary {out.summary_url}")
    return out
