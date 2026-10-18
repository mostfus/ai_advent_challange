# AGENTS.md

Instructions for anyone - a person or an agent - changing this project. Agentic code puts this file into its system prompt when this folder is the open project, and "Check the rules" checks the project against the Rules below.

## Layout

- `core/` - the headless agent: `AgentRuntime`, one chat turn (`turn.py`), the agent and its tool loop, memory, rules, tasks, agentic code (`coder.py`).
- `llm/` - chat-completions clients (DeepSeek, llama-server) and routing by model id.
- `tools/` - `Tool`, `ToolRegistry`, the MCP clients, the project's file tools (`files.py`).
- `rag/` - loaders, chunker, embedder, the SQLite index, retrieval, rerank, grounding.
- `storage/` - the JSON stores under `data/`.
- `review/` - the pull-request reviewer the GitHub Action runs.
- `web/` - the FastAPI routes; `static/` - the page.
- `docs/` - architecture, HTTP API, config and data, review.

## Rules

1. Nothing under `core/`, `llm/`, `tools/`, `rag/`, `storage/` or `review/` imports FastAPI or Starlette - only `web/` does.
2. Every Python module starts with a docstring that says what it is for.
3. A setting a person may change lives in a JSON file in the folder root (`corpus.json`, `local_llm.json`, `project_mcp.json`, `code_agent.json`, `review.json`), read by a `load()` that raises `ValueError` naming the file for a bad value.
4. The model reaches tools only through `tools.registry.ToolRegistry`: `core/agent.py` and `core/turn.py` never call an MCP client directly.
5. Secrets never reach a model: `.env` and key files are on the deny list of `code_agent.json`, and no tool reads them.
6. Code, comments, docs and commit messages are in English.
7. Every package (`core`, `llm`, `tools`, `rag`, `storage`, `review`, `web`) has a test module in `tests/` that exercises it.
8. Every HTTP route is listed in `docs/api.md`, and every config file in `docs/data.md`.

## Working here

- Tests: `uv run python -m unittest discover -s tests -v`, and `npm install && npm test` for the page.
- `data/` is local state and `uv.lock` is generated: neither is edited by hand.
- A new tool goes into a provider in `tools/` and is offered through the registry, not wired into the agent.
