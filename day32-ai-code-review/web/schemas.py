"""The request bodies, as FastAPI validates them - the wire format of every route.

Only the web layer knows these. A route takes one, and hands its fields to
the core as plain arguments; the chat's settings are the core's own
dataclass (`core.settings.ChatSettings`), which FastAPI validates directly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.settings import ChatSettings
from storage import SINGLE_CONVERSATION_ID

# --------------------------------------------------------------------- chat


class ChatRequest(BaseModel):
    message: str
    # Which conversation this message belongs to. Defaulted rather than
    # required so a bare {"message": "..."} - curl, a script - still works and
    # lands in the single chat.
    conversation_id: str = SINGLE_CONVERSATION_ID
    settings: ChatSettings = Field(default_factory=ChatSettings)


class ConversationMeta(BaseModel):
    """What a chat is, before anything has been said in it."""

    settings: ChatSettings = Field(default_factory=ChatSettings)


# ------------------------------------------------------ conversations


class ForgetStateItem(BaseModel):
    section: str
    item: str = ""


# ------------------------------------------------------------- memory


class TaskRequest(BaseModel):
    """A new piece of work. The title is what the selector shows and what the
    prompt names, so it is the only field there is."""

    title: str = ""


class MoveRequest(BaseModel):
    """Where a task should go next, and optionally why.

    `confirm` is a person saying "yes, this stage is done" - it ticks the
    current stage's outstanding required steps and asks the machine again. It
    answers the checklist rule and nothing else: an edge that does not exist
    still does not, and a paused task still does not move.
    """

    to: str = ""
    why: str = ""
    confirm: bool = False


class StepRequest(BaseModel):
    """One checklist item of the stage a task is in."""

    done: bool = True


class PauseRequest(BaseModel):
    """Stop the machine where it stands, or let it go again."""

    paused: bool = True
    note: str = ""


class NoteRequest(BaseModel):
    note: str = ""


class AttachRequest(BaseModel):
    """Which task a chat belongs to. `null` detaches it."""

    task_id: str | None = None


class ItemRequest(BaseModel):
    """One thing to file, in the words it was said in.

    `key` is optional and normally absent. A person types a note, not a
    key - `memory.slug` derives one so that filing the same thing twice
    corrects it instead of leaving two lines that disagree in the prompt.
    The extractor and the proposer still send their own dotted keys, which
    is what the field is for.

    `source` says which conversation the note came out of. The browser sends
    it rather than the server inferring it, and a memory that cannot say
    where it came from is one nobody will ever dare prune (see
    `storage.MemoryItem`).
    """

    value: str
    key: str = ""
    source: dict = Field(default_factory=dict)
    pinned: bool = False


class ProposalDecision(BaseModel):
    """What to do with one of the agent's suggestions. `layer` is here so
    that accepting can also *move* it - the agent proposed long-term, you
    decide it belongs to the task, and that correction is the point."""

    layer: str | None = None


# -------------------------------------------------------- personality


class ProfileRequest(BaseModel):
    """A profile as the form sends it: a name, and the four fields.

    `fields` is a free dict on the wire and is not one by the time it reaches
    disk - `personality.clean_fields` drops every key that is not one of the
    four. Validating by discarding rather than by rejecting because the four
    are this app's idea rather than the caller's, and a fifth key arriving
    from an older page should cost that key, not the edit.
    """

    name: str = ""
    fields: dict = Field(default_factory=dict)


class ActivateRequest(BaseModel):
    """Which profile is in force. `null` is switching personality off, which
    is a state rather than the absence of one - see `Personality.current`."""

    profile_id: str | None = None


# --------------------------------------------------------- invariants


class RuleRequest(BaseModel):
    """One invariant, on the way in.

    Everything is defaulted so a minimal `curl` body still parses, and nothing
    here is trusted: `invariants.clean_rule` decides what a rule is, and both
    the create and the update handler go through it. `markers` is typed loose
    because the form sends a comma-separated line and the API takes a list -
    `clean_markers` accepts either, so the page does not have to grow a tag
    widget to talk to its own server.
    """

    kind: str = ""
    text: str = ""
    because: str = ""
    markers: object = None
    enabled: bool = True


class ToggleRequest(BaseModel):
    """On or off - for one rule, or for the whole set."""

    enabled: bool = True


class ScanRequest(BaseModel):
    """Some text to run the markers over, and nothing else."""

    text: str = ""


# ---------------------------------------------------------------- mcp


class McpRequest(BaseModel):
    """A server somebody is connecting: a name for it and where it lives."""

    label: str = Field("", max_length=60)
    url: str = Field("", max_length=400)


# -------------------------------------------------------------- project


class OpenProjectRequest(BaseModel):
    path: str


# -------------------------------------------------------------- index


class UploadRequest(BaseModel):
    name: str
    content: str


class ChunkingRequest(BaseModel):
    params: dict = Field(default_factory=dict)
    source: str | None = None
    limit: int = 200


class BuildRequest(BaseModel):
    params: dict = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=20)
    source: str | None = None
