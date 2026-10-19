# Configuration and data

## Config files (in the app folder, edited by hand)

### `.env`

| Key | What |
|---|---|
| `DEEPSEEK_API_KEY` | Required. The key for DeepSeek's chat-completions API |
| `LOCAL_LLM_AUTOSTART` | `0` keeps llama-server from starting with the app |
| `PROJECT_MCP_AUTOSTART` | `0` keeps the project's MCP servers from starting |
| `INDEX_DIR`, `CHAT_STORE_DIR`, `CHAT_TASKS_DIR`, `CHAT_MEMORY_DIR`, `CHAT_PERSONALITY_DIR`, `CHAT_INVARIANTS_DIR`, `CHAT_MCP_DIR`, `CHAT_PROJECT_DIR` | Move one store out of `data/` |

### `local_llm.json` - the local model (`llm/local.py`)

```json
{
  "enabled": true,                       // the model joins the model list
  "model_id": "local-qwen3-8b",          // the id a chat selects; requests with it go to llama-server
  "autostart": true,                     // the app starts llama-server itself
  "binary": "llama-server",
  "host": "127.0.0.1", "port": 8081,
  "hf_repo": "Qwen/Qwen3-8B-GGUF:Q8_0",  // or "model_path" for a local .gguf
  "context_size": 16384, "parallel": 2, "gpu_layers": 99, "flash_attn": "auto",
  "sampling": {"top_p": 0.8, "top_k": 20, "min_p": 0.0, "repeat_penalty": 1.05},
  "startup_timeout_seconds": 900, "request_timeout_seconds": 600
}
```

### `corpus.json` - which files of the project are indexed (`rag/corpus.py`)

```json
{
  "include": ["**/*.md", "**/*.swift", "**/*.py", "..."],   // glob patterns, relative to the project folder
  "exclude": [".build", "DerivedData", "Pods", "..."],      // a bare name: anywhere; a path with /: from the root
  "max_file_kb": 256                                       // larger files are skipped
}
```

The folder itself is the open project, not a field here. In a git repository only files `git ls-files` lists are read (tracked, or new and not ignored), so the project's `.gitignore` applies first.

### `review.json` - the pull-request reviewer (`review/config.py`)

```json
{
  "model": "deepseek-v4-flash",      // the action's `model` input overrides it
  "reasoning_effort": "medium",      // how long DeepSeek thinks: off | minimal | low | medium | high | xhigh | ultra | max; null = its own
  "temperature": 0.2,
  "language": "English",             // the action's `language` input overrides it
  "detect_copies": true,             // git diff -C -C: a copied file is reviewed as its differences
  "whole_file_lines": 400,           // a changed file up to this long goes up whole
  "context_lines": 8,                // a longer one: its changes and this much around each
  "max_file_lines": 900,             // at most this many lines of one file
  "batch_chars": 60000,              // one request's diff; a bigger PR is split by files
  "max_batches": 6, "parallel": 3,   // requests per review, and at the same time
  "excerpts_per_file": 4, "excerpts_max": 8, "excerpt_chars": 14000,   // RAG
  "max_inline_comments": 20,         // the rest are in the summary only
  "skip": ["*.lock", "package-lock.json", "*.pbxproj", "..."],
  "models": {"local-qwen3-8b": {"batch_chars": 16000, "reasoning_effort": "off", "...": "..."}}
}
```

The index a review builds lives outside the app, in `~/.cache/ai-review/<owner>__<repo>/index.sqlite` (the action's `cache-dir`), with its embedding cache: a second review of a repository embeds only the chunks that changed.

### `project_mcp.json` - the project's MCP servers (`core/project.py`)

```json
{
  "max_result_chars": 8000,               // every tool result is cut to this
  "servers": [
    {
      "id": "git", "label": "Git", "purpose": "...",
      "command": ["uvx", "mcp-server-git", "--repository", "{git_root}"],
      "needs_git": true,                  // skipped when the project is not in a repository
      "tools": ["git_status", "git_branch", "git_log", "git_diff_unstaged", "git_diff_staged", "git_diff", "git_show"],
      "fixed": {"repo_path": "{git_root}"}
    }
  ]
}
```

- `{project}` and `{git_root}` are replaced with the open project's folder and its repository root.
- `tools` - the only tools offered to the model and the only ones that can be called.
- `fixed` - arguments always set and hidden from the model.
- `defaults` - arguments filled in when the model leaves them out; `deny` - file-name patterns refused in path arguments. Both are for a server that takes paths; the shipped git server needs neither.

### `code_agent.json` - agentic code (`core/coder.py`, `tools/files.py`)

```json
{
  "max_tool_rounds": 30,                 // requests in one agentic turn's tool loop (a plain chat turn: 10)
  "max_tool_calls": 80,                  // tool calls in one turn, across its rounds (a plain chat turn: 24)
  "instructions": ["AGENTS.md", "CLAUDE.md"],   // the first one the project has goes into the system prompt
  "max_instructions_chars": 8000,
  "files": {
    "max_result_chars": 12000,           // one tool result
    "read_lines": 400, "max_read_lines": 2000,   // read_file's default window, and its largest
    "max_file_bytes": 1000000,           // larger files are not read or searched
    "search_results": 100,               // matches shown by one search_text
    "max_listed": 400,                   // files shown by one list_files
    "exclude": [".git", "node_modules", ".venv", "DerivedData", "Pods", "..."],  // skipped by listing and search, never written
    "deny": [".env", ".env.*", "*.pem", "*.key", "..."]   // never read, searched or written (*.example and the like are allowed)
  },
  "scenarios": [                          // the one-click starts above the box; {blank} is selected to type over
    {"id": "usages", "label": "Find usages", "prompt": "Find every place in the project where {name} is used. ..."}
  ]
}
```

In a git repository the file tools list what `git ls-files` lists (tracked, and new but not ignored), and refuse to write a file git ignores. Paths outside the project folder, and symlinks out of it, are refused.

## data/ (local state, gitignored)

| Path | Store | What |
|---|---|---|
| `data/conversations/<id>.json` | `ConversationStore` | One chat: `messages` (role, content, settings, usage, tools, rag, and on an agentic answer `changes`: `path`, `status`, `added`, `removed`, `diff`), `summary`, `facts`, `task_state`, `proposals`, `settings` |
| `data/tasks/<id>.json` | `TaskStore` | A task: title, phase, checklist, history, working memory, note, suggestion |
| `data/memory/long_term.json` | `LongTermStore` | Long-term memory items: key, value, pinned |
| `data/personality/profiles.json` | `PersonalityStore` | Personality profiles and which one is active |
| `data/invariants/rules.json` | `InvariantStore` | Rules, each on or off, and whether the set is enabled |
| `data/mcp/servers.json` | `McpStore` | The panel's MCP servers: id, label, url, enabled, last catalogue |
| `data/project/project.json` | `ProjectStore` | The open project: `{"path": "...", "opened_at": "..."}` |
| `data/index/index.sqlite` | `IndexStore` | The index: tables `indexes`, `chunks` (text, metadata, embedding), `embedding_cache` |
| `data/index/uploads/` | `IndexService` | Files uploaded in the Project tab, always part of the corpus |

### `index.sqlite`

```sql
CREATE TABLE indexes (name TEXT PRIMARY KEY, strategy TEXT, params TEXT, embedder TEXT, dim INTEGER,
                      corpus_sha TEXT, built_at REAL, stats TEXT, timings TEXT);
CREATE TABLE chunks (index_name TEXT, chunk_id TEXT, source TEXT, title TEXT, section TEXT, title_path TEXT,
                     position INTEGER, start INTEGER, "end" INTEGER, n_words INTEGER,
                     sections_spanned INTEGER, merged INTEGER, text TEXT, embed_text TEXT, embedding BLOB,
                     PRIMARY KEY (index_name, chunk_id));
CREATE TABLE embedding_cache (embedder TEXT, text_sha TEXT, dim INTEGER, vector BLOB,
                              PRIMARY KEY (embedder, text_sha));   -- an unchanged chunk is not embedded again
```
