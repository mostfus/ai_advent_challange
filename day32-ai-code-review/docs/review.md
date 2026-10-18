# AI review of pull requests

A GitHub Action that reviews every pull request: it reads the diff and the changed files, searches the project's documentation and code for what the change touches (RAG), asks the model for **potential bugs, architecture problems and recommendations**, and posts them on the pull request - as comments on the code lines and one summary comment.

```
pull_request (opened, pushed to, reopened)
  -> self-hosted runner on a Mac, label `ai-review`
       actions/checkout (the PR's head, full history)
       action.yml -> uv run python -m review
         git merge-base + git diff -M -C -C -U<whole file>   the change; copies and renames found
         diff.parse -> context.plan                           files -> batches within the budget
         Ollama (bge-m3) + rag -> Knowledge                   the project at head indexed (cache per repo)
         per batch: RAG excerpts + the numbered diff -> DeepSeek (JSON) -> findings checked by code
         GitHub API: PR files -> commentable lines
           -> one review with comments on the code (new findings only)
           -> one summary comment, rewritten on every push
```

## What the model is shown

- **The pull request**: title and description.
- **The change**, file by file, at the head of the PR. A file up to `whole_file_lines` long is shown whole with the change marked in it (`+` added, `-` removed); a longer one as its changes with `context_lines` around each. Every line carries **its number in the new file** - the number a GitHub comment is placed by, so a line the model names is the line the comment goes on.
- **Copies** are found among all files (`git diff -C -C`): a file copied from another and changed is shown as its differences from the source, and an unchanged copy or rename is not reviewed at all. A repository of daily snapshots, like this one, would otherwise send thousands of copied lines.
- **Excerpts from the rest of the project** (RAG): one search per changed file - its path, the functions its hunks are in, its added lines - plus one for the PR's title and description. Chunks of files already in the request are dropped; the rest (the architecture doc, the helper that already exists, the caller) go up closest first, numbered, and a finding cites the ones it relies on.
- Deleted files are listed by name. Binary files and lock or generated files (`review.json` `skip`) are not sent.

A pull request bigger than one request (`batch_chars`) is split by files into up to `max_batches` requests, sent in parallel; each one is told which files the other parts cover.

## What is checked by code

The model answers with JSON (`summary`, `findings[]`: category, severity, file, line, end_line, title, comment, suggestion, sources). Then:

- the file must be one of the request's (a shortened path is matched back when only one file ends with it); a finding about a file not in the change is dropped;
- the line must be one the model was shown, otherwise the finding stays in the summary at file level;
- a reply that is not JSON is asked again once; a reply cut off at the output limit is asked again for fewer, shorter findings;
- on GitHub, a comment can only go on a line inside the hunks *GitHub* shows - those come from the PR files API, and a finding on another line goes to the summary with a link to it;
- every comment carries a fingerprint (category + file + the text of its line): on the next push a finding that is already on the PR is not posted again, the summary links to the existing comment;
- if GitHub refuses the review (HTTP 422), the comments are posted one by one and the refused ones go to the summary.

A request that fails does not fail the others - the summary lists the files that were not reviewed. Only a review where every request failed makes the job red.

## Setting up the runner (a Mac)

The runner is a Mac because RAG needs Ollama with bge-m3, and a GitHub-hosted runner has neither. It can also run the local model.

1. On the Mac: `brew install uv ollama` and `ollama pull bge-m3`. The review starts `ollama serve` itself if it is not running, and stops it after; a running Ollama is used and left running.
2. On GitHub, in the repository: **Settings → Actions → Runners → New self-hosted runner**, macOS, ARM64. Run the commands it shows in a folder of its own (`~/actions-runner/<repo>`), and at `./config.sh` add the label: `--labels ai-review`.
3. Start it: `./run.sh` in a terminal, or `./svc.sh install && ./svc.sh start` to keep it running as a service.
4. **Settings → Secrets and variables → Actions → New repository secret**: `DEEPSEEK_API_KEY`. (Or put `DEEPSEEK_API_KEY=...` in the runner folder's `.env` - every job on that runner gets it - and leave the input empty.)
5. **Settings → Actions → General → Approval for running fork pull request workflows**: *Require approval for all external contributors*. See Security.

On a personal account a runner belongs to one repository: for another repository, register one more runner on the same Mac, in another folder, with the same label. An organization can share one runner between its repositories.

## Adding the review to a repository

Put this in the repository as `.github/workflows/ai-review.yml`:

```yaml
name: AI review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: ai-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    if: github.event.pull_request.head.repo.full_name == github.repository && !github.event.pull_request.draft
    runs-on: [self-hosted, ai-review]
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v5
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: mostfus/ai_advent_challange/day32-ai-code-review@main
        with:
          deepseek-api-key: ${{ secrets.DEEPSEEK_API_KEY }}
          # language: Russian
          # model: deepseek-v4-pro
```

This repository runs the same workflow from `.github/workflows/ai-review.yml`, with `uses: ./day32-ai-code-review` (the reviewer of the PR's own checkout) and `project-path: auto` (the day folder the PR changes is indexed, not the whole repository of snapshots).

### Inputs

| Input | Default | What |
|---|---|---|
| `deepseek-api-key` | `""` | DeepSeek key, from a secret. Empty: `DEEPSEEK_API_KEY` from the runner's environment |
| `github-token` | `github.token` | The token the review is posted with |
| `model` | `review.json`: `deepseek-v4-flash`, reasoning `medium` | A DeepSeek model id, or `local-qwen3-8b` (llama-server on the runner, from `local_llm.json`; no key needed). How long the model thinks is `reasoning_effort` in `review.json` |
| `language` | `review.json` (English) | The language of the comments |
| `project-path` | `.` | The folder RAG indexes; `auto` = the top-level folder most of the change is in |
| `rag` | `true` | `false` reviews without excerpts (no Ollama needed) |
| `cache-dir` | `~/.cache/ai-review` | The index and its embedding cache, one folder per repository |

## Security

- **Nothing from the repository is executed.** The reviewer reads the checkout with `git` and from the files; the model's output is text that is posted, never run.
- **Forks.** A self-hosted runner on a public repository is somebody's computer that a stranger's pull request could run a job on - including a pull request that edits the workflow file itself. The workflow's `if:` skips pull requests from forks, and the reviewer refuses them too; the approval setting in step 5 is what stops a fork's edited workflow from running at all.
- **Whose reviewer runs.** A repository that uses `mostfus/ai_advent_challange/day32-ai-code-review@main` runs that code. This repository runs the reviewer from the PR's own checkout, so a branch can change the reviewer - which only somebody who can push to the repository can do, and they could change the workflow anyway.
- **What leaves the machine**: the diff, the changed files and the excerpts go to the model's provider (DeepSeek, unless the model is the local one). Files a repository keeps out of git never get there: the index reads only what `git ls-files` lists.

## Trying it without GitHub

The same review of a local range, printed - nothing is posted:

```bash
uv run python -m review --repo ~/src/app --base main --head HEAD
```

`--project`, `--model`, `--language`, `--no-rag`, `--out review.md` work the same way.

## Limits

- RAG reads what the app's loaders know (Markdown, Python, Swift, YAML, SQL, GraphQL, protobuf; `corpus.json`). In a project in another language, the docs are searched but the code is not - the changed files themselves are always sent.
- The index is built from the checkout on disk: in a local run, uncommitted files are in it.
- A finding is only as good as the model: the summary says so, and the comments are `COMMENT` reviews, never "request changes".
