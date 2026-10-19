"""The repository under review, read through `git` - nothing in it is executed.

    merge_base(repo, base, head)        where the branch left its base: what GitHub diffs against
    ensure_commit(repo, sha)            fetch a commit a shallow checkout does not have
    review_diff(repo, base, head)       the diff the model reads: whole files, renames and copies found
    github_diff(repo, base, head)       the diff GitHub shows: renames only, 3 lines of context -
                                        where a comment can go, when there is no API to ask
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Enough context lines that a changed file comes back whole: the model reads
#: the file, with the change marked in it (`diff.render` folds what it has to).
WHOLE_FILE = 100_000


class GitError(RuntimeError):
    """A git command failed - the message is git's own."""


def run(repo: Path, *args: str, timeout: float = 120) -> str:
    command = ["git", "-C", str(repo), "-c", "core.quotePath=false", *args]
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {' '.join(args[:2])}: {exc}") from exc
    if done.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout.decode("utf-8", "replace")


def has_commit(repo: Path, sha: str) -> bool:
    try:
        run(repo, "cat-file", "-e", f"{sha}^{{commit}}")
        return True
    except GitError:
        return False


def ensure_commit(repo: Path, sha: str) -> None:
    """A checkout made with `fetch-depth: 1` has no base commit: fetch it, with history."""
    if has_commit(repo, sha):
        return
    run(repo, "fetch", "--no-tags", "origin", sha, timeout=600)


def merge_base(repo: Path, base: str, head: str) -> str:
    try:
        return run(repo, "merge-base", base, head).strip()
    except GitError:
        # A shallow history may not reach the fork point: deepen it once. A
        # complete one has no excuse, and git's own error says why.
        if run(repo, "rev-parse", "--is-shallow-repository").strip() != "true":
            raise
        run(repo, "fetch", "--no-tags", "--unshallow", "origin", timeout=600)
        return run(repo, "merge-base", base, head).strip()


def resolve(repo: Path, ref: str) -> str:
    return run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def review_diff(repo: Path, base: str, head: str, copies: bool = True) -> str:
    """base..head with every changed file whole, and copies found among all files (`-C -C`)."""
    args = ["diff", "--no-color", "--no-ext-diff", f"-U{WHOLE_FILE}", "-M"]
    if copies:
        args += ["-C", "-C"]
    return run(repo, *args, base, head, timeout=600)


def github_diff(repo: Path, base: str, head: str) -> str:
    return run(repo, "diff", "--no-color", "--no-ext-diff", "-U3", "-M", base, head, timeout=600)


def subject(repo: Path, sha: str) -> str:
    try:
        return run(repo, "log", "-1", "--format=%s", sha).strip()
    except GitError:
        return ""
