# HTTP API

Every route is a thin handler in `web/routes/` that calls the runtime (`core/`). Errors come back as `{"detail": "..."}` with the status of the `ServiceError` the core raised: 400 invalid, 404 not found, 409 conflict, 413 too large, 502 upstream failed, 503 unavailable.

## Page and config - `web/routes/config.py`

| Method | Path | What it does |
|---|---|---|
| GET | `/` | The page (`static/index.html`) |
| GET | `/api/config` | What the forms offer: models, system prompts, response formats, limits, defaults |

## Chat - `web/routes/chat.py`

| Method | Path | What it does |
|---|---|---|
| POST | `/api/chat` | One message, answered and stored. Body: `{message, conversation_id?, settings?}` |

A message starting with `/help` is answered about the open project, with the project's excerpts and its git and file MCP tools (`core/help.py`). The reply has `answer`, `tools` (every tool call: server, tool, arguments, result), `rag` (the excerpts sent and which ones the answer cites), `usage`, `context` and the panels' state.

## Conversations - `web/routes/conversations.py`

| Method | Path | What it does |
|---|---|---|
| GET | `/api/conversations` | Every chat, newest first |
| GET | `/api/conversations/{id}` | One chat: messages, settings, summary, facts, task state |
| PUT | `/api/conversations/{id}` | Create or update a chat's settings |
| DELETE | `/api/conversations/{id}` | Delete a chat |
| DELETE | `/api/conversations/{id}/messages` | Clear the transcript |
| DELETE | `/api/conversations/{id}/summary` | Forget the rolling summary |
| DELETE | `/api/conversations/{id}/facts` | Forget the sticky facts |
| DELETE | `/api/conversations/{id}/task-state` | Forget the RAG chat's task state |
| POST | `/api/conversations/{id}/task-state/forget` | Remove one item from the task state |

## Memory and tasks - `web/routes/memory.py`

| Method | Path | What it does |
|---|---|---|
| GET, POST | `/api/tasks` | List tasks; create one |
| POST | `/api/tasks/{id}/phase` | Move a task to another stage (planning, execution, validation, done) |
| POST | `/api/tasks/{id}/steps/{key}` | Tick or untick a checklist step |
| POST | `/api/tasks/{id}/pause` | Pause or resume |
| DELETE | `/api/tasks/{id}/suggestion` | Dismiss the agent's suggested move |
| POST | `/api/tasks/{id}/note` | Set the task's note |
| DELETE | `/api/tasks/{id}` | Delete a task |
| POST, DELETE | `/api/tasks/{id}/memory[/{key}]` | File or forget a working-memory item |
| GET, POST | `/api/memory` | Long-term memory: list, file an item |
| DELETE | `/api/memory/{key}` | Forget a long-term item |
| POST | `/api/conversations/{id}/task` | Attach a chat to a task |
| POST, DELETE | `/api/conversations/{id}/proposals/{index}` | Accept or reject a memory proposal |

## Personality - `web/routes/personality.py`

| Method | Path | What it does |
|---|---|---|
| GET, POST | `/api/personality` | Profiles; create one |
| PUT, DELETE | `/api/personality/{id}` | Edit or delete a profile |
| POST | `/api/personality/activate` | Switch the active profile (or none) |

## Invariants - `web/routes/invariants.py`

| Method | Path | What it does |
|---|---|---|
| GET, POST | `/api/invariants` | Rules; add one |
| PUT, DELETE | `/api/invariants/{id}` | Edit or delete a rule |
| POST | `/api/invariants/{id}/toggle` | Switch one rule on or off |
| POST | `/api/invariants/enabled` | Switch the whole set on or off |
| POST | `/api/invariants/scan` | Check a text against the rules |

## MCP servers (the panel) - `web/routes/mcp.py`

Remote servers over Streamable HTTP, offered in every chat turn while switched on.

| Method | Path | What it does |
|---|---|---|
| GET, POST | `/api/mcp` | Connected servers and their tool catalogues; connect one (`{label, url}`) |
| POST | `/api/mcp/refresh` | Re-read every enabled server's catalogue |
| POST | `/api/mcp/{id}/refresh` | Re-read one |
| POST | `/api/mcp/{id}/toggle` | Switch a server on or off |
| DELETE | `/api/mcp/{id}` | Disconnect |

## Project - `web/routes/project.py`

| Method | Path | What it does |
|---|---|---|
| GET | `/api/project` | The open project (`name`, `path`, `git_root`), its MCP servers (`state`: starting, ready, failed, skipped; the tools offered) and its index |
| PUT | `/api/project` | Open another folder: `{"path": "/absolute/path"}`. The index switches to it and starts building; the MCP servers restart. `note` says if the build could not start (for example, Ollama is not running) |

## Index - `web/routes/index.py`

| Method | Path | What it does |
|---|---|---|
| GET | `/api/index` | Embedder, corpus (files, skipped files, `corpus.json` in effect), the index, the cache, the build job |
| POST | `/api/index/uploads` | Add a file to the index's uploads: `{name, content}` |
| DELETE | `/api/index/uploads/{name}` | Remove an upload |
| POST | `/api/index/preview` | Chunk without embedding: `{params, source?, limit?}` -> stats and one file's chunks |
| POST | `/api/index/build` | Chunk, embed and store in the background: `{params}` |
| GET | `/api/index/job` | How far the build has got |
| DELETE | `/api/index/indexes[/{name}]` | Drop the index |
| DELETE | `/api/index/cache` | Forget cached vectors |
| GET | `/api/index/indexes/{name}/chunks` | Browse stored chunks: `?source=&offset=&limit=` |
| POST | `/api/index/search` | One query against the index: `{query, k}` |
