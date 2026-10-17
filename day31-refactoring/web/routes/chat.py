"""POST /api/chat - one message, answered and stored (`core.turn.run_turn`)."""

from __future__ import annotations

from fastapi import APIRouter

from core.reports import turn_report
from web.deps import Runtime
from web.schemas import ChatRequest

router = APIRouter()


@router.post("/api/chat")
def post_chat(req: ChatRequest, rt: Runtime) -> dict:
    result = rt.chat(req.message, conversation_id=req.conversation_id, settings=req.settings)
    return turn_report(rt, result)
