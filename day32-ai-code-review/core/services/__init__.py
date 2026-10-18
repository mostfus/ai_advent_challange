"""The operations the web routes need, one service per domain - all reached through the runtime.

    conversations   ConversationService   list, open, clear, undo a strategy
    memory          MemoryService         tasks and their state machine, long-term, proposals
    personality     PersonalityService    profiles, which one is on
    invariants      InvariantService      the rules, on and off, the free marker scan
    mcp             McpService            connected servers, their catalogues, the tool provider

Each is built by `core.runtime.AgentRuntime` with the runtime in hand, works
on its stores, returns the dict the page redraws from, and refuses with a
`core.errors.ServiceError` - never with anything that knows about HTTP.
"""
