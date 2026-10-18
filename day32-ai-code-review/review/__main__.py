"""`python -m review` - what the GitHub Action runs.

In a GitHub Actions job started by a `pull_request` event (`GITHUB_EVENT_PATH`
is set), the pull request comes from the event, and the review is posted to
it: comments on the code and one summary comment. The repository is the
job's checkout, at the head of the pull request.

    python -m review --repo "$GITHUB_WORKSPACE" [--project auto] [--model ...]

Given `--base` and `--head` instead, it reviews that range of a local
repository and prints the summary - nothing is sent to GitHub. That is how
the reviewer is tried before a runner is set up.

    python -m review --repo ~/src/app --base main --head HEAD
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

from llm import local as local_llm
from llm.client import DeepSeekClient, LLMClientError
from rag.embedders import EmbedderError, OllamaEmbedder, make_embedder

from . import git
from .config import CONFIG_PATH, ReviewConfig
from .github import DEFAULT_API, DEFAULT_SERVER, GitHub, GitHubError, PullRequest
from .knowledge import Knowledge, KnowledgeError
from .pipeline import Reviewer
from .publish import publish
from .report import summary_markdown
from .services import LocalModel, Ollama, ServiceError

DEFAULT_CACHE = Path.home() / ".cache" / "ai-review"


def log(line: str) -> None:
    print(line, flush=True)


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m review", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="the repository's checkout (default: here)")
    parser.add_argument("--base", help="local run: the base ref (nothing is posted)")
    parser.add_argument("--head", help="local run: the head ref (default HEAD)")
    parser.add_argument("--project", default=".",
                        help="the folder RAG indexes, relative to the repo; `auto` = the top-level "
                             "folder most of the change is in (a repository of several projects)")
    parser.add_argument("--model", default="", help="overrides review.json")
    parser.add_argument("--language", default="", help="overrides review.json")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="where the RAG index is kept")
    parser.add_argument("--no-rag", action="store_true", help="review without the project's excerpts")
    parser.add_argument("--dry-run", action="store_true", help="in Actions too: review, post nothing")
    parser.add_argument("--out", help="also write the summary Markdown to this file")
    return parser.parse_args(argv)


def auto_project(paths: list[str]) -> str:
    """The top-level folder most changed files are in; "." when that is the root or a dot-folder."""
    if not paths:
        return "."
    counts = Counter(p.split("/", 1)[0] if "/" in p else "." for p in paths)
    top = counts.most_common(1)[0][0]
    return "." if top.startswith(".") else top


def cache_folder(base: Path, repo: Path) -> Path:
    """One index per repository: `owner__name` in Actions, `<folder>-<hash of its path>` locally."""
    if os.getenv("GITHUB_REPOSITORY"):
        return base / os.environ["GITHUB_REPOSITORY"].replace("/", "__")
    return base / f"{repo.name}-{hashlib.sha1(str(repo).encode()).hexdigest()[:8]}"


def build_llm(config: ReviewConfig):
    local = local_llm.load_config()
    if local.enabled and config.model == local.model_id:
        return local_llm.LlamaCppClient(local), LocalModel(local, log=log)
    return DeepSeekClient.from_env(timeout=300), None


def write_step_summary(markdown: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    repo = Path(args.repo).resolve()
    config = ReviewConfig.load(args.config).for_model(args.model or None)
    if args.language:
        config = replace(config, language=args.language)

    event = os.getenv("GITHUB_EVENT_PATH")
    pr: PullRequest | None = None
    if args.base:
        base, head = git.resolve(repo, args.base), git.resolve(repo, args.head or "HEAD")
        title, description = git.subject(repo, head), ""
    elif event:
        pr = PullRequest.from_event_file(event)
        if pr.from_fork:
            log(f"::notice::AI review skipped: the pull request comes from a fork ({pr.head_repo}). "
                f"A self-hosted runner does not review code from outside the repository.")
            return 0
        for sha in (pr.base_sha, pr.head_sha):
            git.ensure_commit(repo, sha)
        base, head, title, description = pr.base_sha, pr.head_sha, pr.title, pr.body
    else:
        log("Give --base (and --head) for a local run, or run in a GitHub Actions pull_request job.")
        return 2

    project = args.project
    if project == "auto":
        changed = git.run(repo, "diff", "--name-only", git.merge_base(repo, base, head), head).split("\n")
        project = auto_project([p for p in changed if p])
    root = (repo / project).resolve()
    if not root.is_dir() or (root != repo and repo not in root.parents):
        log(f"[review ] project folder `{project}` is not in the checkout - indexing the whole repository")
        root, project = repo, "."
    prefix = "" if root == repo else root.relative_to(repo).as_posix() + "/"
    log(f"[review ] {repo.name}: {base[:7]}..{head[:7]}, model {config.model}, project `{project}`")

    started: list = []
    try:
        llm, local_model = build_llm(config)
        if local_model is not None:
            started.append(local_model.start())
        knowledge, problem = None, None
        if args.no_rag:
            problem = "switched off for this run"
        else:
            try:
                embedder = make_embedder()
                if isinstance(embedder, OllamaEmbedder):
                    started.append(Ollama(embedder, log=log).start())
                knowledge = Knowledge.build(root, prefix, cache_folder(Path(args.cache_dir).expanduser(), repo),
                                            embedder, log=log)
            except (KnowledgeError, ServiceError, EmbedderError, OSError) as exc:
                problem = str(exc)
                log(f"::warning::AI review runs without RAG: {problem}")
        result = Reviewer(llm, config, knowledge, problem, log=log).review(repo, base, head, title, description)
    except (LLMClientError, ServiceError, git.GitError) as exc:
        log(f"::error::AI review failed: {exc}")
        return 1
    finally:
        for service in reversed(started):
            service.stop()

    if pr is not None and not args.dry_run:
        token = os.getenv("GITHUB_TOKEN") or ""
        if not token:
            log("::error::GITHUB_TOKEN is not set - the action passes it as the `github-token` input")
            return 1
        gh = GitHub(token, pr.repo, os.getenv("GITHUB_API_URL") or DEFAULT_API,
                    os.getenv("GITHUB_SERVER_URL") or DEFAULT_SERVER)
        try:
            markdown = publish(gh, pr, result, config, log=log).summary
        except GitHubError as exc:
            log(f"::error::Could not post the review: {exc}")
            markdown = summary_markdown(result)
            write_step_summary(markdown)
            return 1
        finally:
            gh.close()
    else:
        markdown = summary_markdown(result)
        print("\n" + markdown)
        inline = [f for f in result.findings if f.path and f.line]
        log(f"[review ] {len(inline)} finding(s) point at a line (they would be comments on the code)")
    write_step_summary(markdown)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    if not result.ok:
        log("::error::AI review failed: " + "; ".join(sorted({r.error for r in result.failed})))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
