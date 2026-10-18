"""Day 32: an AI reviewer for pull requests - the diff, the project's docs and code, a review.

    git          the change: merge base, a diff with whole files and copies found
    diff         unified diffs read into files, hunks and new-file line numbers
    context      the changed files fitted to a budget, in batches (one request each)
    knowledge    RAG: the project at head indexed (`rag`), searched for each changed file
    prompt       the reviewer's brief, and the request it is shown
    findings     the reply read and checked: real files, lines the model was shown
    pipeline     Reviewer - all of the above, from two commits to a ReviewResult
    report       the result as Markdown: bugs / architecture / recommendations, and how
    github       the REST calls: PR files, comments, reviews
    publish      the result on the pull request: comments on the code, one summary
    services     Ollama and the local model, started for a review and stopped after it
    config       review.json

`python -m review` is the entry point the GitHub Action (`action.yml`) runs.
Nothing here imports the web app, and nothing in the repository under review
is executed: it is read with `git` and from the files of the checkout.
"""
