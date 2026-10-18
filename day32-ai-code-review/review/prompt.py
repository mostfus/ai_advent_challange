"""The review request: what the reviewer is told, and what it is shown.

    system(language)                  the reviewer's brief and the JSON it answers with
    user_message(title, description, batch, part, others, deleted, problem)
                                      the pull request, the excerpts, the changed files
    CORRECTION                        the one retry, when the reply is not that JSON
"""

from __future__ import annotations

from .context import Batch, label

SYSTEM = """You are a senior engineer reviewing a pull request. You get the change and excerpts from the rest of the project, and you write the review a careful colleague would: few comments, each one worth the author's time.

What to look for:
1. Potential bugs (category "bug"): code that will behave wrongly - logic errors, wrong conditions, off-by-one, null/None and empty cases, errors that are swallowed or not handled, resource leaks, races, broken contracts with callers, security problems (injection, secrets, unchecked input, unsafe paths).
2. Architecture problems (category "architecture"): the change measured against the project as the excerpts describe it - a layer importing what it should not, a responsibility in the wrong module, a helper the project already has written again, coupling, an interface that leaks internals, configuration hard-coded.
3. Recommendations (category "recommendation"): tests worth adding, a simpler way, naming that misleads, an error message nobody can act on, documentation the change makes stale.

Rules:
- Review the change: point at lines marked `+` where you can. The number on the left of each line is its line number in the new file - use exactly that number. For a removed line, point at the nearest line that is still there.
- No comments on formatting, import order or style a linter would catch. No praise, no summaries of what the code does inside findings.
- Every claim must be backed by the diff or an excerpt. If something is only a risk under a condition, say which condition, and lower the severity. Never invent files, functions or behaviour you have not been shown.
- When a finding relies on an excerpt, list its number in "sources".
- Severity: "high" - breaks something or is a security hole; "medium" - wrong in some cases, or a real design problem; "low" - worth fixing, not urgent.
- An empty "findings" list is a valid review of a good change.
- Write the summary, titles and comments in {language}. Keep code, identifiers and paths as they are.

Answer with one JSON object and nothing else:
{{
  "summary": "two to four sentences: what the change does and how risky it is",
  "findings": [
    {{
      "category": "bug" | "architecture" | "recommendation",
      "severity": "high" | "medium" | "low",
      "file": "path exactly as in the diff",
      "line": 42,
      "end_line": 44,
      "title": "one line",
      "comment": "what is wrong, why it matters, what to do",
      "suggestion": "optional: the code you would write instead",
      "sources": [1]
    }}
  ]
}}"""

CORRECTION = ("Your reply was not the JSON object asked for ({problem}). Answer again with exactly one JSON "
              "object with the keys \"summary\" and \"findings\", and nothing else.")

SHORTER = ("\n\nThe last answer to this was cut off at the output limit. Think briefly, keep at most "
           "8 findings - the most important - and keep each comment under 80 words.")

MAX_DESCRIPTION_CHARS = 3000


def system(language: str) -> str:
    return SYSTEM.format(language=language or "English")


def excerpts_block(batch: Batch, problem: str | None) -> str:
    if problem:
        return f"(none - the project was not searched: {problem})"
    if not batch.excerpts:
        return "(none - nothing in the rest of the project was close to this change)"
    return "\n\n".join(f"[{h['rank']}] {label(h)}\n{h['text'].strip()}" for h in batch.excerpts)


def user_message(title: str, description: str, batch: Batch, part: tuple[int, int],
                 others: list[str], deleted: list[str], knowledge_problem: str | None = None) -> str:
    description = (description or "").strip()
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + " ..."
    head = [f"Pull request: {title or '(no title)'}"]
    if description:
        head.append(f"Description:\n{description}")
    n, total = part
    if total > 1:
        head.append(f"This is part {n} of {total} of the review; the other parts cover: "
                    + ", ".join(f"`{p}`" for p in others[:40]) + (" ..." if len(others) > 40 else ""))
    if deleted:
        head.append("Files deleted by the change: " + ", ".join(f"`{p}`" for p in deleted[:40]))
    files = "\n\n".join(f.rendered.text for f in batch.files)
    return (
        "\n\n".join(head)
        + "\n\n--- Excerpts from the rest of the project (documentation and code), found for this change ---\n"
        + excerpts_block(batch, knowledge_problem)
        + "\n--- End of excerpts ---\n\n"
        + "--- The change: each file at the head of the pull request, line numbers of the new file on the "
          "left, `+` added, `-` removed ---\n"
        + files
        + "\n--- End of the change ---\n\nReview it. Reply with the JSON object."
    )
