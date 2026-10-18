"""What every store shares: the error, the clock, the id rule, where `data/` is.

Each store below keeps its own lock and its own write-to-temp-then-`os.replace`
on purpose - what they have in common is a technique, not a type (see
`storage.memory.TaskStore`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

#: The day folder's `data/` - local state, git-ignored. Every store defaults
#: to a directory under it and can be pointed elsewhere by an environment
#: variable (the tests aim them all at temp directories).
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

#: Conversation ids end up as file names, so they are restricted rather than
#: escaped: no dots, no slashes, nothing that can climb out of the directory.
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StoreError(RuntimeError):
    """A conversation id that cannot be used, or a record that cannot be read."""


def now_iso() -> str:
    """UTC, to the millisecond.

    Milliseconds rather than seconds because `all()` sorts by this field and
    the chat menu is rebuilt in that order: two chats created one after the
    other land in the same second, and listing them backwards would be a
    visible bug.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def validate_id(conversation_id: str) -> str:
    if not CONVERSATION_ID_PATTERN.match(conversation_id or ""):
        raise StoreError(
            "A conversation id may only contain letters, digits, '-' and '_' "
            "(1-64 characters)."
        )
    return conversation_id
