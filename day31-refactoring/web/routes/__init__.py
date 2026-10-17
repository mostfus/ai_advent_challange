"""One APIRouter per domain - each handler parses a request and calls the runtime.

    config          GET / (the page) and /api/config (what its forms offer)
    chat            POST /api/chat
    conversations   /api/conversations/...: chats, the strategy undos
    memory          /api/tasks, /api/memory, and a chat's task and proposals
    personality     /api/personality
    invariants      /api/invariants
    mcp             /api/mcp
    index           /api/index - the document index
    project         /api/project - the open project folder (day 31)
"""

from web.routes import chat, config, conversations, index, invariants, mcp, memory, personality, project

ROUTERS = [
    config.router,
    chat.router,
    conversations.router,
    memory.router,
    personality.router,
    invariants.router,
    mcp.router,
    index.router,
    project.router,
]
