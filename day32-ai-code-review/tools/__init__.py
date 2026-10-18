"""What the agent can *do*: one `Tool` abstraction, and a registry of providers.

    base           Tool (name, description, JSON input schema, sync/async call),
                   ToolResult, the ToolProvider protocol
    registry       ToolRegistry - the agent lists tools and calls them through it
    mcp_client     MCP over Streamable HTTP: list a server's tools, call one
    mcp_provider   McpProvider - the connected servers' catalogues as Tools,
                   routed by name (`server__tool`), with one turn's memory

The agent (`core.agent`) is handed a `ToolRegistry` and never learns what is
behind a name. `core.runtime.AgentRuntime.tool_registry()` builds one per turn
from the providers - today, the MCP servers switched on in the panel.

**The slot for local tools.** A provider is anything with `tools()` returning
`Tool`s (and, optionally, `guide()` and `unrouted(name)` - see `tools.base`).
Tools that work on a checked-out repository - read a file, search the code,
look at `git diff` - are meant to arrive as one more provider, e.g. a
`tools/local.py` with `LocalProvider(root)`, added to the registry next to
the MCP one in `AgentRuntime.tool_registry()`. Nothing else has to change: the
agent loop, the receipts and the debug panel already go through the registry.
"""

from tools.base import Tool, ToolProvider, ToolResult
from tools.registry import ToolRegistry

__all__ = ["Tool", "ToolProvider", "ToolRegistry", "ToolResult"]
