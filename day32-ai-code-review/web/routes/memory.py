"""The memory layers: tasks and their state machine (days 11 and 13), the long-term layer, proposals.

    GET    /api/tasks                               every task, and the long-term layer
    POST   /api/tasks
    POST   /api/tasks/{id}/phase                    move a stage, if the machine allows it (409 if not)
    POST   /api/tasks/{id}/steps/{key}              tick or untick a checklist step
    POST   /api/tasks/{id}/pause
    DELETE /api/tasks/{id}/suggestion               "not yet" to the agent's offer
    POST   /api/tasks/{id}/note
    DELETE /api/tasks/{id}
    POST   /api/tasks/{id}/memory                   file into a task's working layer
    DELETE /api/tasks/{id}/memory/{key}
    GET    /api/memory                              the long-term layer (same shape as /api/tasks)
    POST   /api/memory
    DELETE /api/memory/{key}
    POST   /api/conversations/{id}/task             join a chat to a task, or take it out
    POST   /api/conversations/{id}/proposals/{n}    accept one of the agent's suggestions
    DELETE /api/conversations/{id}/proposals/{n}    ... or say no to it
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import (
    AttachRequest,
    ItemRequest,
    MoveRequest,
    NoteRequest,
    PauseRequest,
    ProposalDecision,
    StepRequest,
    TaskRequest,
)

router = APIRouter()


@router.get("/api/tasks")
def list_tasks(rt: Runtime) -> dict:
    return rt.memory.tasks_report()


@router.post("/api/tasks")
def create_task(req: TaskRequest, rt: Runtime) -> dict:
    return rt.memory.create_task(req.title)


@router.post("/api/tasks/{task_id}/phase")
def move_task(task_id: str, req: MoveRequest, rt: Runtime) -> dict:
    return rt.memory.move_task(task_id, req.to, why=req.why, confirm=req.confirm)


@router.post("/api/tasks/{task_id}/steps/{key}")
def set_task_step(task_id: str, key: str, req: StepRequest, rt: Runtime) -> dict:
    return rt.memory.set_task_step(task_id, key, req.done)


@router.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str, req: PauseRequest, rt: Runtime) -> dict:
    return rt.memory.pause_task(task_id, req.paused, req.note)


@router.delete("/api/tasks/{task_id}/suggestion")
def dismiss_suggestion(task_id: str, rt: Runtime) -> dict:
    return rt.memory.dismiss_suggestion(task_id)


@router.post("/api/tasks/{task_id}/note")
def set_task_note(task_id: str, req: NoteRequest, rt: Runtime) -> dict:
    return rt.memory.set_task_note(task_id, req.note)


@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, rt: Runtime) -> dict:
    return rt.memory.delete_task(task_id)


@router.post("/api/tasks/{task_id}/memory")
def add_task_item(task_id: str, req: ItemRequest, rt: Runtime) -> dict:
    return rt.memory.add_task_item(task_id, req.value, req.key, req.source, req.pinned)


@router.delete("/api/tasks/{task_id}/memory/{key}")
def delete_task_item(task_id: str, key: str, rt: Runtime) -> dict:
    return rt.memory.delete_task_item(task_id, key)


@router.get("/api/memory")
def get_long_term(rt: Runtime) -> dict:
    return rt.memory.tasks_report()


@router.post("/api/memory")
def add_long_term(req: ItemRequest, rt: Runtime) -> dict:
    return rt.memory.add_long_term(req.value, req.key, req.source, req.pinned)


@router.delete("/api/memory/{key}")
def delete_long_term(key: str, rt: Runtime) -> dict:
    return rt.memory.delete_long_term(key)


@router.post("/api/conversations/{conversation_id}/task")
def attach_task(conversation_id: str, req: AttachRequest, rt: Runtime) -> dict:
    return rt.memory.attach_task(conversation_id, req.task_id)


@router.post("/api/conversations/{conversation_id}/proposals/{index}")
def decide_proposal(conversation_id: str, index: int, req: ProposalDecision, rt: Runtime) -> dict:
    return rt.memory.decide_proposal(conversation_id, index, req.layer)


@router.delete("/api/conversations/{conversation_id}/proposals/{index}")
def dismiss_proposal(conversation_id: str, index: int, rt: Runtime) -> dict:
    return rt.memory.dismiss_proposal(conversation_id, index)
