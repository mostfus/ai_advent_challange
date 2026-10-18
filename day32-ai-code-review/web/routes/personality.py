"""Personality profiles (day 12). None of these takes a conversation id: a
profile is one object the whole app runs under, not a property of a chat.

    GET    /api/personality
    POST   /api/personality                 a new profile - not switched on by it
    PUT    /api/personality/{id}
    POST   /api/personality/activate        `null` switches personality off
    DELETE /api/personality/{id}
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import ActivateRequest, ProfileRequest

router = APIRouter(prefix="/api/personality")


@router.get("")
def get_personality(rt: Runtime) -> dict:
    return rt.personality.report()


@router.post("")
def create_profile(req: ProfileRequest, rt: Runtime) -> dict:
    return rt.personality.create(req.name, req.fields)


@router.put("/{profile_id}")
def update_profile(profile_id: str, req: ProfileRequest, rt: Runtime) -> dict:
    return rt.personality.update(profile_id, req.name, req.fields)


@router.post("/activate")
def activate_profile(req: ActivateRequest, rt: Runtime) -> dict:
    return rt.personality.activate(req.profile_id)


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, rt: Runtime) -> dict:
    return rt.personality.delete(profile_id)
