"""Day 31: `/help` - questions about the open project, from its docs, its code and git.

    /help                 an overview: what the project is, how it is laid out, where git stands
    /help <question>      the question

A /help turn is a chat turn with three things swapped in (`core.turn.run_turn`):

  * the system prompt says what the assistant is for and which project it is in;
  * the question goes up with excerpts from the project's index - README,
    docs and code (`rag`) - retrieved for it, whether or not the chat has RAG
    switched on, and without the rerank: the tools are how an answer checks
    what the excerpts did not cover;
  * the tools on offer are the project's MCP servers - git and the files,
    read-only (`core.project`) - and the model decides when to call them.
    Nothing is called by code: the branch is in an answer because the model
    asked git for it.

An index that is missing (never built, or Ollama is down) does not stop the
turn: it runs on the tools alone, and the system prompt says the
documentation was not searched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.project import Project

COMMAND = "/help"
HELP = re.compile(r"^/help(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)

#: What a bare `/help` asks.
OVERVIEW = ("Give me an overview of this project: what it is for, how the code and documentation "
            "are organised (the main folders and files), which git branch is checked out and whether "
            "there are uncommitted changes, and what I can ask you about it.")

#: How many excerpts go up with the question.
K = 5

SYSTEM = """You are a developer assistant for one software project. You answer questions about it.

Project: {name}
Folder: {path}
Git repository: {git}

You have two sources:
1. Excerpts from the project's documentation and code (README, docs, source files), found by a search for the question. They come with the question, numbered; cite the ones you use as [1], [2].
2. Tools that read the project live: git (branch, status, log, diffs) and the files (list folders, the file tree, read a file, search file names). Paths are relative to the project folder.

How to work:
- The current state - the branch, uncommitted changes, recent commits - comes from the git tools, never from the excerpts.
- To describe the structure, list the folders with the file tools; to explain a file, a type or a function, find it in the excerpts or read it first.
- Never invent files, types or functions: what you have not seen in an excerpt or a tool result, check, or say you do not know.
- Be concise. Put file paths and identifiers in backticks. Answer in the language of the question."""

NO_INDEX = ("\n\nThe documentation index was not searched for this question ({reason}). There are no "
            "excerpts: work from the tools.")

TEMPLATE = (
    "--- Excerpts from the project's documentation and code, found for this question ---\n"
    "{excerpts}\n"
    "--- End of excerpts ---\n\n"
    "Question about the project: {question}"
)


@dataclass
class HelpRequest:
    """A `/help` message, read: what to ask the model."""

    question: str           # as typed after the command; "" for a bare /help

    @property
    def asked(self) -> str:
        return self.question or OVERVIEW


def parse(message: str) -> HelpRequest | None:
    """A `/help` message, or None for any other."""
    match = HELP.match((message or "").strip())
    if match is None:
        return None
    return HelpRequest((match.group(1) or "").strip())


def system_prompt(project: Project, index_problem: str | None = None) -> str:
    if project.git_root is None:
        git = "none - the git tools are not available"
    elif project.git_root == project.path:
        git = str(project.git_root)
    else:
        # A folder inside a bigger repository: git's paths start at the
        # repository root, the file tools' at the project folder.
        inside = project.path.relative_to(project.git_root).as_posix()
        git = (f"{project.git_root} - the project folder is `{inside}/` inside it, so paths in git's "
               f"output start with `{inside}/` and the rest of the repository is not this project")
    text = SYSTEM.format(name=project.name, path=project.path, git=git)
    return text + (NO_INDEX.format(reason=index_problem) if index_problem else "")
