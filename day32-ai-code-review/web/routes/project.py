"""Day 31: the Project tab's first card - `core.services.project.ProjectService`, over HTTP.

    GET    /api/project     the open project, its MCP servers, its index
    PUT    /api/project     open another folder: {"path": "/abs/path"} - the index
                            switches to it and starts building, the servers restart
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import OpenProjectRequest

router = APIRouter(prefix="/api/project")


@router.get("")
def get_project(rt: Runtime) -> dict:
    return rt.project.report()


@router.put("")
def open_project(req: OpenProjectRequest, rt: Runtime) -> dict:
    return rt.project.open(req.path)
