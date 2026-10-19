# Architecture

The app is a developer assistant: a chat agent with memory, rules and tools, a RAG index over a project folder, and - with agentic code on - tools that read, search and change that project's files and read its git repository. It is a headless Python core with a FastAPI web UI on top - and a pull-request reviewer (`review/`) that a GitHub Action runs on the same `llm/` and `rag/`.

## Layers

```
web/        FastAPI app, one router per domain, static/ is the page      (HTTP in, JSON out)
  │
review/     the pull-request reviewer: python -m review, run by action.yml    (a PR in, comments out)
  │
core/       AgentRuntime - the composition root - and everything a turn does
  ├── llm/      chat-completions clients: DeepSeek, local llama-server, routing by model id
  ├── tools/    Tool, ToolRegistry, the MCP clients (Streamable HTTP and stdio), the project's file tools
  ├── rag/      loaders, chunker, embedder, SQLite index, retrieval, rerank, grounding
  └── storage/  JSON files under data/
```

Nothing under `core/`, `llm/`, `tools/`, `rag/`, `storage/` or `review/` imports FastAPI. A test checks it, and a script can do everything the web UI does:

```python
from core.runtime import AgentRuntime

rt = AgentRuntime.build()          # .env, local_llm.json, corpus.json, project_mcp.json, data/
rt.start()                         # local model and the project's MCP servers, in the background
coding = ChatSettings(agentic_code=True)   # from core.settings
result = rt.chat("Find every place ToolRegistry is used", settings=coding)
print(result.answer, [c["path"] for c in result.changes or []])
rt.stop()
```

## Packages

### core/

- `runtime.py` - `AgentRuntime`: builds the LLM client, the stores, the index and the services, and runs a turn (`chat`). `start()` and `stop()` run the local model and the project's MCP servers.
- `turn.py` - one chat turn from start to finish: the context plan, retrieval, the agent, the retries, storing the turn, the memory proposer. Agentic code is switched in here (see below).
- `agent.py` - `Agent`: builds the chat-completions request (system prompt, memory blocks, history, question) and runs the tool loop. It lists and calls tools only through a `ToolRegistry`.
- `coder.py` - agentic code: `CodeAgentConfig` (`code_agent.json`), the coding agent's system prompt, the project's instructions file (`AGENTS.md`), the stored receipts kept short.
- `project.py` - `Project` (a folder and its git repository), `ProjectToolsConfig` (`project_mcp.json`), `ProjectServers` (the running MCP servers and the provider an agentic turn gets).
- `context.py`, `strategies.py`, `compaction.py`, `facts.py` - what of the conversation goes into a request: a sliding window, sticky facts or a rolling summary.
- `memory.py`, `personality.py`, `invariants.py`, `phases.py`, `task_state.py` - long-term and task memory, personality profiles, rules an answer may not break, tasks as a state machine, the RAG chat's task state.
- `services/` - the operations the web routes call: conversations, memory, personality, invariants, mcp, project.
- `settings.py` (`ChatSettings`), `reports.py` (the JSON a turn reports), `errors.py` (`ServiceError` with an HTTP status), `tokens.py` (usage and cost).

### llm/

- `client.py` - `DeepSeekClient`, plain HTTP with httpx.
- `local.py` - `LocalServer` starts llama-server from `local_llm.json`; `LlamaCppClient` talks to it.
- `routing.py` - `RoutingClient` sends a request to the local model or to DeepSeek by its `model` id.

### tools/

- `base.py` - `Tool`, `ToolResult`, the `ToolProvider` protocol.
- `registry.py` - `ToolRegistry`: every provider's tools behind one name, sync and async calls.
- `mcp_client.py` - MCP over Streamable HTTP: the panel's servers (DeepWiki and others).
- `mcp_stdio.py` - MCP over stdio: a server as a child process, the session held open. Used for the project's servers.
- `mcp_provider.py` - MCP servers as one provider: `server__tool` names, routing, the guide, a server that failed is not called again in the same turn.
- `files.py` - the project's files as tools, in this process: `list_files`, `read_file`, `search_text` (grep), `write_file`, `edit_file`, `search_docs` (the index); the rules that keep them to the project; `ChangeLog`, the turn's diffs.

### rag/

- `loaders.py` - a file becomes a `Document` with sections: Markdown headings, Python and Swift declarations, plain text for YAML, SQL, GraphQL and protobuf.
- `chunkers.py` - the structural chunker: one chunk per section, long ones split on paragraphs, short ones merged.
- `embedders.py` - bge-m3 over Ollama.
- `index_store.py` - SQLite: the index, its chunks and vectors, an embedding cache.
- `pipeline.py` - load, chunk, embed, store; in a git repository the files are the ones `git ls-files` lists.
- `corpus.py` - `corpus.json`: which files of the project are indexed.
- `indexing.py` - `IndexService`: the Project tab's operations and the chat's `retrieve`.
- `augment.py`, `rerank.py`, `grounding.py` - excerpts for a question, the LLM reranker, answers with sources and verified quotes.

### review/

The AI reviewer of pull requests - see [review.md](review.md). It uses `llm/` (DeepSeek or the local model), `rag/` (the loaders, the chunker, bge-m3, the SQLite store and its embedding cache) and `core.tokens`, and not the agent: a review is one JSON request per batch, with no tools and no memory.

- `git.py` - the merge base, the diff with whole files and copies (`-M -C -C`), fetching a missing commit.
- `diff.py` - unified diffs read into files, hunks and head-file line numbers; `render` numbers the lines for the model.
- `context.py` - which files are reviewed (binary, lock files, unchanged copies, deleted are not), each fitted to the budget, in batches.
- `knowledge.py` - the project at head indexed; one query per changed file; excerpts of files not in the request.
- `prompt.py` - the reviewer's brief, the JSON it answers with, the request.
- `findings.py` - the reply read and checked: real files, shown lines, cited excerpts; fingerprints; comment text.
- `pipeline.py` - `Reviewer`: batches in parallel, one retry, a `ReviewResult`.
- `report.py` - the summary Markdown. `publish.py` - the review with line comments, the summary comment. `github.py` - the REST calls.
- `services.py` - Ollama and llama-server, started for a review and stopped after it. `config.py` - `review.json`.
- `__main__.py` - the entry: a PR from `GITHUB_EVENT_PATH`, posted; or `--base/--head`, printed.

### storage/

One JSON file per record or per store, written atomically. See [data.md](data.md).

### web/

- `app.py` - `create_app(runtime)`: the routers, the static page, `ServiceError` as `{"detail": ...}`, the runtime started and stopped with the app.
- `routes/` - one router per domain. See [api.md](api.md).
- `schemas.py` - the request bodies.

## A chat turn

```
POST /api/chat {message, conversation_id, settings}
  -> core.turn.run_turn
       context plan        window / facts / summary, memory layers, task stage
       retrieval           RAG on: search the index, rerank, gate ("I do not know")
       Agent.ask           request -> tool calls through the registry -> answer
       retries             grounding (missing evidence), stage, invariants - one each
       store               append the turn, then the memory proposer
  -> core.reports.turn_report -> JSON for the page
```

## Agentic code - the assistant that works in the files

A chat with **Agentic code** switched on (the topbar button; `settings.agentic_code`) treats every message as a task in the open project, given as a goal - "find every place X is used", "update the docs after these changes", "write a CHANGELOG", "check the project against its rules". The model decides which files to look at and what to change; nothing is opened for it. Three things of a chat turn are swapped:

1. **The system prompt** (`core/coder.py`) makes it a coding agent in this project: search and read before concluding, ask one question and stop when the goal is ambiguous, report and do not edit when asked to find or check, answer with `path:line`. The project's instructions file - the first of `AGENTS.md`, `CLAUDE.md` it has - follows it, so the project's own rules are what "check the rules" checks.
2. **The tools** are the project's, not the panel's: the file tools (`tools/files.py`) and the git MCP server, read-only. `search_docs` - the index by meaning - is offered when the project's index is built.
3. **The budget** of the tool loop is `code_agent.json`'s (30 rounds, 80 calls), not a chat turn's 10 and 24.

```
message (a goal)
  -> ProjectService.code_session()      FilesProvider + git provider, a fresh ChangeLog, the prompt
  -> Agent.ask                          search_text -> read_file -> edit_file / write_file -> ... -> answer
  -> ChangeLog.changes()                one unified diff per file: before the turn's first write, after its last
  -> store                              the answer with `tools` (cut short) and `changes`
  -> the page                           "✎ 2 files changed · +12 −3 — view diff" under the answer; the Changes window
```

Writes go to disk at once. Nothing re-asks the model after an agentic answer - the stage advisor and the invariant check, which can throw an answer away and ask again, are skipped, because a second answer would run the writes a second time. Calls the model asks for in one round run side by side, so the write tools hold a lock.

The changes are kept twice: on the answer (the window under it) and, for the whole chat, behind `GET /api/conversations/{id}/changes`, whose `patch` is every diff as one patch - `git apply` in the project folder re-applies a turn's changes to a fresh checkout.

### What keeps the file tools to the project

- Paths are relative to the project folder; `..`, absolute paths elsewhere and symlinks out of it are refused.
- `.git` is never read or written. Folders on `exclude` (dependencies, build output) are skipped by listing and search and never written.
- Names on `deny` (`.env`, keys, certificates) are never read, searched or written - whatever the model reads is sent to the model's provider.
- In a git repository the files are what `git ls-files` lists, and a write to a file git ignores is refused: the app's own `data/`, build products.
- A file that is binary or not valid UTF-8 is not edited; Windows line endings survive an edit.

The project is the folder opened in the Project tab (`PUT /api/project`); with none opened, it is the app's own folder. Opening a project switches the index to it (dropping the old project's chunks), starts a build, and restarts the MCP servers.

### What keeps the git server read-only

`project_mcp.json` lists the tools that are offered. Nothing else is shown to the model or called: no `git_commit`, `git_checkout`, `git_reset`. `repo_path` is fixed to the project's repository and hidden from the model. Every tool result is cut to `max_result_chars`.
