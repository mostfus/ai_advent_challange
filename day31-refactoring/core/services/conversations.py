"""Conversations: list, open, register, clear, undo a strategy.

Days 7 to 10 and 25 - every endpoint about a chat that is not sending it a
message. None of them touches a model; each returns what the page redraws
from (`core.reports`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import task_state as task_state_module
from core.errors import Invalid, NotFound
from core.reports import context_report, usage_report, with_usage
from core.settings import ChatSettings, as_dict, validate_settings
from storage import StoreError, validate_id

if TYPE_CHECKING:
    from core.runtime import AgentRuntime


def valid_id(conversation_id: str) -> str:
    """A conversation id that is safe to turn into a file name."""
    try:
        return validate_id(conversation_id)
    except StoreError as exc:
        raise Invalid(str(exc)) from exc


class ConversationService:
    """The conversation endpoints' operations, over `rt.stores.conversations`."""

    def __init__(self, rt: "AgentRuntime") -> None:
        self.rt = rt

    @property
    def store(self):
        return self.rt.stores.conversations

    def list_all(self) -> dict:
        """Every saved chat, oldest first - what the page is rebuilt from.

        The browser asks for this once on load and puts back what it finds:
        every chat in the menu, in the order they were created. Nothing about
        which chats exist lives in `localStorage`, so the same conversations come
        back in a different browser, and clearing site data loses nothing.
        """
        return {"conversations": [with_usage(self.rt, c) for c in self.store.all()]}

    def get(self, conversation_id: str) -> dict:
        conversation = self.store.load(valid_id(conversation_id))
        if conversation is None:
            raise NotFound(f"No saved conversation: {conversation_id}")
        return with_usage(self.rt, conversation)

    def put(self, conversation_id: str, settings: ChatSettings) -> dict:
        """Create a chat, or update its settings, without touching its messages.

        This is how a new chat registers itself the moment it is opened, so an
        empty chat - and the settings someone just picked for it - still exists
        after a restart.
        """
        settings = validate_settings(settings)
        conversation = self.store.upsert(valid_id(conversation_id), as_dict(settings))
        return with_usage(self.rt, conversation)

    def delete(self, conversation_id: str) -> dict:
        """Forget a chat completely - its ✕ in the chat menu."""
        return {"deleted": self.store.delete(valid_id(conversation_id))}

    def clear(self, conversation_id: str) -> dict:
        """Empty a chat but keep it. What the "Clear context" button calls.

        Deliberately not the same as deleting the conversation: the chat, and the
        settings it runs under, are still there - it simply has nothing left to
        remember, so the next message starts a conversation rather than continuing
        one.
        """
        conversation = self.store.clear(valid_id(conversation_id))
        if conversation is None:
            # Nothing stored under that id is the state the caller asked for.
            return {
                "cleared": True,
                "messages": [],
                "usage": usage_report(None),
                "context": context_report(None),
            }
        return {
            "cleared": True,
            "messages": [],
            # Day 9: emptied of its summary as well, which the strip has to be
            # told about - it was the one thing a cleared chat could still know.
            "context": context_report(conversation),
            # Emptied along with the messages: the usage was theirs, not the
            # chat's. What was spent is spent, but it was spent on a conversation
            # that no longer exists here.
            "usage": usage_report(conversation),
        }

    def clear_summary(self, conversation_id: str) -> dict:
        """Throw away the compression, keep every message - day 9's own undo.

        Deliberately a different button from "Clear context": that one forgets the
        conversation, this one forgets only what the app made of it. The next
        message that overflows the window summarises the transcript again from the
        beginning, which is what you want after changing the window, or after a
        summary that came out wrong and has been quietly shaping every answer
        since.
        """
        conversation = self.store.clear_summary(valid_id(conversation_id))
        if conversation is None:
            return {"cleared": True, "context": context_report(None), "usage": usage_report(None)}
        return {
            "cleared": True,
            "context": context_report(conversation),
            # The compressions it paid for go with it: what is reported has to
            # describe the summary that exists, and there is no longer one.
            "usage": usage_report(conversation),
        }

    def clear_facts(self, conversation_id: str) -> dict:
        """Throw away the key-value memory, keep every message - day 10's undo.

        The same button as the one above, for the other strategy, and needed a
        little more sharply. A summary is rewritten from the transcript on the
        next overflow, so a bad one has a limited life; a fact is carried forward
        untouched for as long as it stands, is never re-derived from what was
        actually said, and shapes every answer from here on. This is the way out.
        """
        conversation = self.store.clear_facts(valid_id(conversation_id))
        if conversation is None:
            return {"cleared": True, "context": context_report(None), "usage": usage_report(None)}
        return {
            "cleared": True,
            "context": context_report(conversation),
            "usage": usage_report(conversation),
        }

    def clear_task_state(self, conversation_id: str) -> dict:
        """Day 25: forget the chat's task state - it starts again from the next message."""
        conversation = self.store.clear_task_state(valid_id(conversation_id))
        return {"cleared": True, "context": context_report(conversation)}

    def forget_task_state_item(self, conversation_id: str, section: str, item: str = "") -> dict:
        """Day 25: take one item out of the task state - the panel's ×.

        The model writes the state; a person can always take something out of
        it. A wrong clarification is sent with every request and joined onto
        every search until it goes, so this is the way out of one, as "Forget
        facts" is for day 10's block.
        """
        if section not in task_state_module.SECTIONS:
            raise Invalid(f"No such section: {section}")
        conversation = self.store.load(valid_id(conversation_id))
        if conversation is None or conversation.task_state is None:
            raise NotFound("This chat has no task state")
        state = conversation.task_state
        state.content = task_state_module.forget(state.content, section, item)
        conversation = self.store.save_task_state(conversation.id, state) or conversation
        return {"context": context_report(conversation)}
