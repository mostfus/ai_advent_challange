"""Conversations: the day-7 endpoints and the strategy undos.

    GET    /api/conversations                         every saved chat - what the page is rebuilt from
    GET    /api/conversations/{id}
    PUT    /api/conversations/{id}                    register a chat, or update its settings
    DELETE /api/conversations/{id}                    forget it (deleted from the chat menu)
    DELETE /api/conversations/{id}/messages           "Clear context": empty it, keep it
    DELETE /api/conversations/{id}/summary            day 9's undo
    DELETE /api/conversations/{id}/facts              day 10's undo
    DELETE /api/conversations/{id}/task-state         day 25: forget the RAG chat's task state
    POST   /api/conversations/{id}/task-state/forget  ... or one item of it
    GET    /api/conversations/{id}/changes            day 34: what agentic code changed, turn by turn
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import ConversationMeta, ForgetStateItem

router = APIRouter(prefix="/api/conversations")


@router.get("")
def list_conversations(rt: Runtime) -> dict:
    return rt.conversations.list_all()


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.get(conversation_id)


@router.get("/{conversation_id}/changes")
def get_changes(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.changes(conversation_id)


@router.put("/{conversation_id}")
def put_conversation(conversation_id: str, meta: ConversationMeta, rt: Runtime) -> dict:
    return rt.conversations.put(conversation_id, meta.settings)


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.delete(conversation_id)


@router.delete("/{conversation_id}/messages")
def clear_conversation(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.clear(conversation_id)


@router.delete("/{conversation_id}/summary")
def clear_summary(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.clear_summary(conversation_id)


@router.delete("/{conversation_id}/facts")
def clear_facts(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.clear_facts(conversation_id)


@router.delete("/{conversation_id}/task-state")
def clear_task_state(conversation_id: str, rt: Runtime) -> dict:
    return rt.conversations.clear_task_state(conversation_id)


@router.post("/{conversation_id}/task-state/forget")
def forget_task_state_item(conversation_id: str, req: ForgetStateItem, rt: Runtime) -> dict:
    return rt.conversations.forget_task_state_item(conversation_id, req.section, req.item)
