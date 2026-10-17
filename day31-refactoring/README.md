# LLM agent

A developer assistant you point at a project folder: it indexes the project's README, docs and code for RAG, reads its git repository and files through MCP servers, and answers `/help` questions about the project. Underneath is a chat agent over DeepSeek or a local llama.cpp model, with layered memory, rules it may not break and tasks as a state machine. The agent is a headless Python core; the web UI is one front end on top of it.

## Capabilities

- **Developer assistant**: open any local folder in the Project tab; `/help <question>` (or `/help` alone, for an overview) answers about that project from its indexed docs and code and from live git and file tools the model calls itself - the current branch, status, diffs, the log, the file tree, reading a file.
- **Project MCP servers**: the official `mcp-server-git` and `@modelcontextprotocol/server-filesystem` over stdio, read-only - only the tools listed in `project_mcp.json` are offered, secrets like `.env` are refused.
- **RAG over the project**: Markdown, Swift, Python and schema files (YAML, SQL, GraphQL, protobuf), cut by structure, embedded with bge-m3 via Ollama into SQLite; in a git repository `.gitignore` applies. Reranking, grounded answers with citations and quotes, and a task state for RAG chats.
- Chats, each with its own model and settings, three context strategies (sliding window, sticky facts, rolling summary) and a token readout per turn and per conversation.
- Memory in three layers: the conversation, a task's working memory, and long-term memory; the agent proposes what to file, a person accepts it.
- Personality profiles, and invariants - rules every answer is checked against and re-asked once if it breaks one.
- Tasks as a state machine (planning, execution, validation, done) with checklists, pauses and agent-proposed moves.
- MCP tools from any remote server added in the MCP panel, called through one tool registry (`server__tool` routing).
- Local model via llama.cpp (`llama-server`), routed by model id.

Docs: [architecture](docs/architecture.md), [HTTP API](docs/api.md), [config and data](docs/data.md).

## Run

1. `cp .env.example .env` and set `DEEPSEEK_API_KEY`. RAG needs Ollama with `bge-m3`; the project's MCP servers need `uvx` and `npx`.
2. `uv run server.py`, then open http://127.0.0.1:8000.
3. In the Project tab, open a folder (it starts as this app's own) and let the index build; then ask `/help` in the chat.
4. Optional: the local model is in `local_llm.json`, the indexed files in `corpus.json`, the project's MCP servers in `project_mcp.json`.

Tests: `uv run python -m unittest discover -s tests -v` and `npm install && npm test`.
