"""MCP servers (days 16 and 17): connect one, ask what it can do, switch it off.

    GET    /api/mcp                     what is connected, off the cache - nothing is contacted
    POST   /api/mcp                     connect a server, and ask it once, immediately
    POST   /api/mcp/refresh             ask every server switched on, side by side
    POST   /api/mcp/{id}/refresh
    POST   /api/mcp/{id}/toggle
    DELETE /api/mcp/{id}
"""

from __future__ import annotations

from fastapi import APIRouter

from web.deps import Runtime
from web.schemas import McpRequest, ToggleRequest

router = APIRouter(prefix="/api/mcp")


@router.get("")
def get_mcp(rt: Runtime) -> dict:
    return rt.mcp.report()


@router.post("")
def add_mcp(req: McpRequest, rt: Runtime) -> dict:
    return rt.mcp.add(req.label, req.url)


@router.post("/refresh")
def refresh_all_mcp(rt: Runtime) -> dict:
    return rt.mcp.refresh_all()


@router.post("/{server_id}/refresh")
def refresh_mcp(server_id: str, rt: Runtime) -> dict:
    return rt.mcp.refresh(server_id)


@router.post("/{server_id}/toggle")
def toggle_mcp(server_id: str, req: ToggleRequest, rt: Runtime) -> dict:
    return rt.mcp.toggle(server_id, req.enabled)


@router.delete("/{server_id}")
def delete_mcp(server_id: str, rt: Runtime) -> dict:
    return rt.mcp.delete(server_id)
