# LLM agent

A developer assistant you point at a project folder: switch a chat to **Agentic code** and give it a goal - find every use of an API, update the docs after a change, write a CHANGELOG or an ADR, check the code against the project's rules - and it reads, searches and edits the project's files itself, reads its git history through an MCP server, and shows what it changed as a diff. The same core reviews pull requests on GitHub: a GitHub Action reads the diff, searches the project's docs and code, and comments on the code. Underneath is a chat agent over DeepSeek or a local llama.cpp model, with layered memory, rules it may not break and tasks as a state machine. The agent is a headless Python core; the web UI is one front end on top of it.

## Capabilities

- **AI review of pull requests**: a GitHub Action (`action.yml`) for any repository, on a self-hosted Mac runner. It gets the diff and the changed files (copies and renames found, so only real differences are reviewed), searches the project's documentation and code with RAG for each changed file, and asks the model for potential bugs, architecture problems and recommendations. Every finding is checked against the lines the model was shown; they are posted as comments on the code lines and one summary comment, kept up to date on every push without repeating a comment.
- **Agentic code** - a mini Claude Code over the project open in the Project tab. A switch in the chat's top bar; with it on, a message is a task stated as a goal, and the model decides which files to search, read and change, or asks one question when the goal is ambiguous. Its tools (`tools/files.py`): list the files, read one with line numbers, search every file for a text or a regex, write a file, replace an exact fragment, search the index by meaning; plus git - status, diffs, log - through MCP. Writes go to disk at once; every answer that changed something gets a **Changes** button that opens the diff of each file, and **All changes** shows the whole chat's, with a `.patch` to download and re-apply. The project's `AGENTS.md` (or `CLAUDE.md`) goes into the system prompt. One-click scenarios above the box: find usages, update docs, generate a CHANGELOG, write an ADR, check the rules.
- **Kept to the project**: paths outside the folder and symlinks out of it are refused, `.git` is never touched, secrets like `.env` are never read or written, and in a git repository a file git ignores is not written. Limits, lists and scenarios are in `code_agent.json`.
- **Project MCP server**: the official `mcp-server-git` over stdio, read-only - only the tools listed in `project_mcp.json` are offered.
- **RAG over the project**: Markdown, Swift, Python and schema files (YAML, SQL, GraphQL, protobuf), cut by structure, embedded with bge-m3 via Ollama into SQLite; in a git repository `.gitignore` applies. Reranking, grounded answers with citations and quotes, and a task state for RAG chats.
- Chats, each with its own model and settings, three context strategies (sliding window, sticky facts, rolling summary) and a token readout per turn and per conversation.
- Memory in three layers: the conversation, a task's working memory, and long-term memory; the agent proposes what to file, a person accepts it.
- Personality profiles, and invariants - rules every answer is checked against and re-asked once if it breaks one.
- Tasks as a state machine (planning, execution, validation, done) with checklists, pauses and agent-proposed moves.
- MCP tools from any remote server added in the MCP panel, called through one tool registry (`server__tool` routing).
- Local model via llama.cpp (`llama-server`), routed by model id.

Docs: [architecture](docs/architecture.md), [HTTP API](docs/api.md), [config and data](docs/data.md), [AI review of pull requests](docs/review.md).

## Run

1. `cp .env.example .env` and set `DEEPSEEK_API_KEY`. RAG needs Ollama with `bge-m3`; the git MCP server needs `uvx`.
2. `uv run server.py`, then open http://127.0.0.1:8000.
3. In the Project tab, open a folder (it starts as this app's own) and let the index build.
4. In the chat, switch **Agentic code** on and give it a task - or pick a scenario above the box. Open **Changes** under the answer to see what it wrote.
5. Optional: the local model is in `local_llm.json`, the indexed files in `corpus.json`, the project's MCP servers in `project_mcp.json`, agentic code's limits and scenarios in `code_agent.json`.

Tests: `uv run python -m unittest discover -s tests -v` and `npm install && npm test`.

## AI review of pull requests

1. On the Mac that will run reviews: `brew install uv ollama`, `ollama pull bge-m3`.
2. In the GitHub repository: register a self-hosted runner on that Mac with the label `ai-review` (Settings → Actions → Runners), add the secret `DEEPSEEK_API_KEY`, and require approval for workflows from external contributors.
3. Add `.github/workflows/ai-review.yml` that runs `mostfus/ai_advent_challange/day32-ai-code-review@<full commit SHA>` on `pull_request` (pinned: the code runs on your machine) - the file is in [docs/review.md](docs/review.md), with the inputs (`model`, `language`, `project-path`, ...). This repository has one at the root.
4. Open a pull request: the review appears as comments on the code and an "AI review" summary comment.

Without GitHub, `uv run python -m review --repo <repo> --base main --head HEAD` prints the review of a local range. What is sent and how much is in `review.json`.
