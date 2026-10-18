"""The GitHub REST API, as much of it as a reviewer needs - plain HTTP with httpx.

    PullRequest.from_event_file(path)      the PR an Actions run was started for (GITHUB_EVENT_PATH)
    GitHub(token, repo)
        pr_files(n)                        the changed files with GitHub's own patches -
                                           which lines a comment can go on
        review_comments(n)                 comments on the code already there (fingerprints)
        create_review(n, sha, body, comments)   one review: a body and its line comments
        create_review_comment(n, sha, comment)  one line comment, when the review is refused
        upsert_comment(n, marker, body)    the summary: one comment, updated on every push

The token is the run's `GITHUB_TOKEN` (`pull-requests: write`); nothing here
reads or writes anything but the one pull request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

DEFAULT_API = "https://api.github.com"
DEFAULT_SERVER = "https://github.com"


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class PullRequest:
    repo: str                 # owner/name
    number: int
    title: str
    body: str
    base_sha: str
    head_sha: str
    base_ref: str
    head_ref: str
    head_repo: str
    html_url: str
    draft: bool = False

    @classmethod
    def from_event(cls, event: dict) -> "PullRequest":
        pr = event.get("pull_request")
        if not isinstance(pr, dict):
            raise ValueError("the event has no pull_request - run the reviewer on `pull_request` events")
        repo = (event.get("repository") or {}).get("full_name") or pr["base"]["repo"]["full_name"]
        return cls(
            repo=repo, number=int(pr["number"]), title=pr.get("title") or "", body=pr.get("body") or "",
            base_sha=pr["base"]["sha"], head_sha=pr["head"]["sha"],
            base_ref=pr["base"].get("ref", ""), head_ref=pr["head"].get("ref", ""),
            head_repo=((pr["head"].get("repo") or {}).get("full_name") or ""),
            html_url=pr.get("html_url", ""), draft=bool(pr.get("draft")),
        )

    @classmethod
    def from_event_file(cls, path: Path | str) -> "PullRequest":
        return cls.from_event(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def from_fork(self) -> bool:
        return bool(self.head_repo) and self.head_repo != self.repo


class GitHub:
    def __init__(self, token: str, repo: str, api_url: str = DEFAULT_API, server_url: str = DEFAULT_SERVER,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0) -> None:
        self.repo = repo
        self.server_url = server_url.rstrip("/")
        self.http = httpx.Client(
            base_url=api_url.rstrip("/"), timeout=timeout, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ai-review"})

    def request(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub {method} {path}: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json()
                message = detail.get("message", "")
                errors = detail.get("errors")
                if errors:
                    message += f" {json.dumps(errors)[:300]}"
            except ValueError:
                message = response.text[:300]
            raise GitHubError(f"GitHub {method} {path}: HTTP {response.status_code} {message}",
                              response.status_code)
        return response.json() if response.content else None

    def paginate(self, path: str, limit: int = 3000) -> list[dict]:
        items: list[dict] = []
        page = 1
        while len(items) < limit:
            batch = self.request("GET", path, params={"per_page": 100, "page": page})
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    # -- reads -----------------------------------------------------------------

    def pr_files(self, number: int) -> list[dict]:
        return self.paginate(f"/repos/{self.repo}/pulls/{number}/files")

    def review_comments(self, number: int) -> list[dict]:
        return self.paginate(f"/repos/{self.repo}/pulls/{number}/comments")

    def comments_of_review(self, number: int, review_id: int) -> list[dict]:
        return self.paginate(f"/repos/{self.repo}/pulls/{number}/reviews/{review_id}/comments")

    def issue_comments(self, number: int) -> list[dict]:
        return self.paginate(f"/repos/{self.repo}/issues/{number}/comments")

    # -- writes ----------------------------------------------------------------

    def create_review(self, number: int, commit_id: str, body: str, comments: list[dict]) -> dict:
        return self.request("POST", f"/repos/{self.repo}/pulls/{number}/reviews",
                            json={"commit_id": commit_id, "event": "COMMENT", "body": body,
                                  "comments": comments})

    def create_review_comment(self, number: int, commit_id: str, comment: dict) -> dict:
        return self.request("POST", f"/repos/{self.repo}/pulls/{number}/comments",
                            json={"commit_id": commit_id, **comment})

    def login(self) -> str | None:
        """Who the token is - a personal token can say; the run's GITHUB_TOKEN cannot (403)."""
        try:
            return (self.request("GET", "/user") or {}).get("login")
        except GitHubError:
            return None

    def upsert_comment(self, number: int, marker: str, body: str) -> dict:
        """The one comment carrying `marker`, rewritten - or a new one, the first time.

        Only a comment this token wrote: its login when it has one, else any bot
        (`github-actions[bot]` is the run's token) - a person quoting the marker
        does not get their comment overwritten.
        """
        me = self.login()
        for comment in self.issue_comments(number):
            author = comment.get("user") or {}
            mine = author.get("login") == me if me else author.get("type") == "Bot"
            if marker in (comment.get("body") or "") and mine:
                return self.request("PATCH", f"/repos/{self.repo}/issues/comments/{comment['id']}",
                                    json={"body": body})
        return self.request("POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body})

    # -- links -----------------------------------------------------------------

    def blob_url(self, sha: str, path: str, line: int | None = None, end_line: int | None = None) -> str:
        url = f"{self.server_url}/{self.repo}/blob/{sha}/{quote_path(path)}"
        if line:
            url += f"#L{line}" + (f"-L{end_line}" if end_line and end_line != line else "")
        return url

    def close(self) -> None:
        self.http.close()


def quote_path(path: str) -> str:
    return quote(path, safe="/")
