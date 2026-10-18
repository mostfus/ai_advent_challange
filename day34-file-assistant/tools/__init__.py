"""What the agent can *do*: one `Tool` abstraction, and a registry of providers.

    base           Tool (name, description, JSON input schema, sync/async call),
                   ToolResult, the ToolProvider protocol
    registry       ToolRegistry - the agent lists tools and calls them through it
    mcp_client     MCP over Streamable HTTP: list a server's tools, call one
    mcp_provider   McpProvider - the connected servers' catalogues as Tools,
                   routed by name (`server__tool`), with one turn's memory
    mcp_stdio      MCP over stdio: a server as a child process (the project's git)
    files          FilesProvider - the open project's files as tools (list, read,
                   grep, write, edit), and ChangeLog - what one turn's writes did

The agent (`core.agent`) is handed a `ToolRegistry` and never learns what is
behind a name. `core.runtime.AgentRuntime.tool_registry()` builds one per turn
from the panel's MCP servers; an agentic-code turn gets the project's instead -
`FilesProvider` beside the git server (`core.services.project.code_session`).

A provider is anything with `tools()` returning `Tool`s (and, optionally,
`guide()` and `unrouted(name)` - see `tools.base`). `FilesProvider` is the
proof that the slot works: tools in this process, offered through the same
registry as MCP ones, with nothing changed in the agent loop, the receipts or
the debug panel.
"""

from tools.base import Tool, ToolProvider, ToolResult
from tools.registry import ToolRegistry

__all__ = ["Tool", "ToolProvider", "ToolRegistry", "ToolResult"]
