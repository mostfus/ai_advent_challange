"""The headless agent: everything that answers a message, with no web framework in it.

    runtime       AgentRuntime - LLM + storage + tools + RAG, wired; `chat()` runs a turn
    turn          one chat turn end to end: context, agent, checks, store -> TurnResult
    context       what the next request remembers (window | facts | summary, the layers)
    agent         the agent loop: build the request, ask, run tools through the registry
    settings      ChatSettings - one chat's configuration, and its checks
    reports       the JSON views of a conversation and of a turn
    errors        ServiceError - why the core refused, with an HTTP status for the web layer
    services/     the operations the web routes call, one service per domain

    strategies    window | facts | summary - the names, and the one cut they share
    compaction    the rolling summary (day 9)
    facts         the key-value block (day 10)
    memory        the working and long-term layers: recall, render, the proposer (day 11)
    personality   the profiles' fields and how they render (day 12)
    phases        the task's state machine (day 13)
    invariants    the rules an answer may not break, and the watcher (day 14)
    task_state    the RAG chat's task state (day 25)
    tokens        usage, as the provider reported it (day 8)

Import what you need from the modules (`from core.runtime import AgentRuntime`)
- this file imports nothing, so `storage` can use `core.phases` without
pulling the whole runtime in.
"""
