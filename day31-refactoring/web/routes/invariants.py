"""Invariants (day 14): the rules an answer may not break.

    GET    /api/invariants
    POST   /api/invariants                  a new rule - in force from the start
    PUT    /api/invariants/{id}
    POST   /api/invariants/{id}/toggle
    POST   /api/invariants/enabled          the whole set on or off
    DELETE /api/invariants/{id}
    POST   /api/invariants/scan             what the free marker scan sees in some text
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import RuleRequest, ScanRequest, ToggleRequest

router = APIRouter(prefix="/api/invariants")


@router.get("")
def get_invariants(rt: Runtime) -> dict:
    """Every rule, and the block that will ride on the next request."""
    return rt.invariants.report()


@router.post("")
def create_rule(req: RuleRequest, rt: Runtime) -> dict:
    return rt.invariants.create(req.model_dump())


@router.put("/{rule_id}")
def update_rule(rule_id: str, req: RuleRequest, rt: Runtime) -> dict:
    return rt.invariants.update(rule_id, req.model_dump())


@router.post("/{rule_id}/toggle")
def toggle_rule(rule_id: str, req: ToggleRequest, rt: Runtime) -> dict:
    return rt.invariants.toggle(rule_id, req.enabled)


@router.post("/enabled")
def set_invariants_enabled(req: ToggleRequest, rt: Runtime) -> dict:
    return rt.invariants.set_enabled(req.enabled)


@router.delete("/{rule_id}")
def delete_rule(rule_id: str, rt: Runtime) -> dict:
    return rt.invariants.delete(rule_id)


@router.post("/scan")
def scan_text(req: ScanRequest, rt: Runtime) -> dict:
    return rt.invariants.scan(req.text)
