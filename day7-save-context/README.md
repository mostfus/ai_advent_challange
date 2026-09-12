# LLM Agent that remembers

A small **agent** that takes a question, sends it to an LLM over plain HTTP, shows the whole exchange in a web GUI — and, since day 7, **still knows what you talked about after you close it, reload it, or restart the server.**

Day 6 built the agent and deliberately left one seam unused: `Agent` already replayed `self.history` into every request, but the server built a fresh agent per call, so that list was always empty and every question was answered from scratch. Day 7 fills the seam. Nothing about the agent's design had to change — it had to be given somewhere to put a transcript, and somewhere to get one back from.

```
1st run:   "My name is Maksim. Reply with just OK."   ->  OK
           ^C  (the process dies)
2nd run:   "What is my name?"                         ->  Maksim
```

That transcript is real, captured against the code in this folder; the second answer came out of a process that had never seen the first question.

## Where should the context live?

That was the actual design question of the day, and it has four plausible answers.

| Where | Survives a reload | Survives a restart | Who owns the truth |
|---|---|---|---|
| In the `Agent` object (keep it alive per chat) | yes | **no** | the process |
| In the browser (`localStorage`) | yes | yes | **the client** |
| **JSON files on the server** ← chosen | yes | yes | the server |
| SQLite / Postgres on the server | yes | yes | the server |

**Not in the agent.** The obvious first idea — keep one long-lived `Agent` per chat in a dict — gives you memory for exactly as long as the process lives, which is the one thing the brief rules out. Persistence is not a lifetime problem; it is a *writing-down* problem. So agents stay short-lived: still one per request, as in day 6. They just inherit a transcript now.

**Not in the browser.** This one is tempting, because `localStorage` is free and the browser is already drawing the transcript. But the transcript has two jobs: it is *drawn on screen*, and it is *replayed to the model*. Chat-completions APIs are stateless — every request carries the entire conversation — and in this app the agent is what assembles that request. Keeping the history client-side would mean the browser uploading the whole conversation with every message and the agent taking dictation from it: the client would own the context, the server would forget everything the moment the tab closed, and opening the same chat on a phone would show an empty screen. The division that survives scrutiny is **the browser owns what is on screen; the server owns what was said.**

**Files, not a database — for now.** One JSON file per conversation, written whole, replaced atomically. It is the smallest thing that answers the requirement and it stays legible: `cat data/conversations/single.json` is the entire debugging story. The costs are honest ones — every write rewrites the whole file, and concurrent writers need a lock (there is one; the compare view fires five requests at once). SQLite is the next step if this ever grows past one person on one machine, and the only file that would change is `store.py`: `load`, `save`, `delete`, `all` is the whole interface the rest of the app uses.

What stays in `localStorage`: whether the debug panel is open, and which tab you were on. Those are preferences of *this screen*, not parts of the conversation — which is exactly the line being drawn.

## The layers

One new file. Each layer still knows only about the one below it.

```
static/index.html     the interface: draws what the server remembers
        │  GET  /api/conversations          (on load: rebuild the page)
        │  POST /api/chat                   {"message", "conversation_id", "settings"}
        │  DELETE /api/conversations/{id}/messages   (clear context)
server.py             transport + the one place where a saved transcript
        │              and an agent are introduced to each other
        ├──────────────────────────────┐
agent.py          ★   the agent:       │  store.py      ★ NEW
        │              role, params,   │    conversations as JSON files,
        │              request, reply  │    atomic writes, one lock
llm_client.py         HTTP: POST /chat/completions
   DeepSeek
```

`agent.py` imports no storage layer, `store.py` imports no agent, and neither imports a web framework. The wiring is three lines in `server.py`:

```python
saved = store.load(conversation_id)
agent = Agent(client, config, history=saved.history() if saved else None)
store.append_turn(conversation_id, message, reply.answer)
```

That is the whole feature. Everything else in this README is a consequence of one of those three lines.

### What changed in `agent.py`

Almost nothing, which is the point:

```python
Agent(client, config, history=[...])   # an agent born remembering
```

`build_messages` already produced `[system] + history + [user]`; it now trims the middle to the last `DEFAULT_CONTEXT_MESSAGES` (40) messages. The agent still has no idea where the history came from — it is handed a list, exactly as it is handed a client.

## What a stored conversation looks like

`data/conversations/single.json`:

```json
{
  "id": "single",
  "view": "single",
  "settings": { "model": "deepseek-v4-flash", "temperature": 1.0, "...": "..." },
  "messages": [
    { "role": "user",      "content": "My name is Maksim...", "ts": "2026-09-12T12:14:07.881+00:00" },
    { "role": "assistant", "content": "OK",                   "ts": "2026-09-12T12:14:09.204+00:00" }
  ],
  "created_at": "2026-09-12T12:14:07.881+00:00",
  "updated_at": "2026-09-12T12:14:09.204+00:00"
}
```

The single chat always uses the id `single`, so it is the same conversation on every reload and in every browser. Each compare column gets its own id when it is added and keeps it until it is closed.

**Settings are stored with the conversation**, not just the messages. A compare column with nothing said in it is still a column, and the parameters you just dialled into it are worth keeping — so adding a column, or moving one of its controls, saves it immediately rather than waiting for a message.

### Kept, but not replayed

Two things are stored and then deliberately left out of the request:

- **Failed turns.** When a call comes back 401, or times out, the error is written down with `"error": true` and redrawn after a reload — it is part of what you saw. But `Conversation.history()` skips error turns *and* the question that got them, so the model is never shown a hanging question, and never taught to apologise for an HTTP status.
- **Old turns.** Everything is kept on disk; only the last 40 messages (20 exchanges) are replayed. A conversation grows without limit while a request is paid for by the token every single time, so the cap is what stops a months-old chat from quietly costing more per message than a new one. DeepSeek's 1M-token window is not the binding constraint — the bill is.

The debug panel is **not** persisted. It is a log of HTTP calls this page made, not a record of the conversation, and it says so by emptying on reload.

## Clearing the context

**⚙ Settings → Clear context**, in the single chat's top bar. It shows how much is saved (`8 messages saved on the server.`), and clearing takes two clicks — the first arms the button, the second fires it. A `confirm()` dialog would be the usual answer, but it moves the question out of the panel it was asked in, and this one is asked inside a popover that a stray click closes.

In the compare view every column has its own **Clear context** button, at the foot of its own settings form — each column is its own conversation, so each is cleared on its own. It sits outside the parameter fields on purpose: the form is six parameters, and a destructive button is not a seventh.

Three different ways of forgetting, kept distinct:

| Action | Messages | The chat itself | Its settings |
|---|---|---|---|
| **Clear context** | gone | kept | kept |
| **✕** on a compare column | gone | gone | gone |
| Deleting `data/conversations/` | gone | gone | gone |

Closing a column really does delete its conversation — otherwise it would reappear on the next reload, which is not what "close" means anywhere else.

## What happens when the page loads

One request: `GET /api/conversations`. Whatever comes back is what gets drawn — the single chat's transcript, and one compare column per saved column, in the order they were created, each with its own settings form filled in from the store. Nothing about *which* chats exist lives in the browser, so clearing site data costs you nothing, and the same chats come back in a different browser.

Restored messages are drawn above a divider — `2 messages restored` — because without it a reloaded page looks like a conversation you are already in the middle of, with nothing to say where "now" begins.

The compare view used to seed itself with two empty columns the first time it was opened. It still does, unless there are saved columns: whatever you left behind is what you get back, including none.

### Memory in the compare view

Day 6 argued that compare columns should *not* remember, since "the columns are only trustworthy if they differ by their parameters and nothing else". That argument survives one turn and no more: ask five columns the same question and each one answers differently, so from the second turn onward each column is carrying a different conversation — because each is carrying *its own* answer. That is what a chat is, and it is visible rather than hidden: the summary line still shows the parameters, and **Clear context** on each column is one click away when you want a clean comparison again.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A DeepSeek API key ([get one here](https://platform.deepseek.com))

The server refuses to start without the key, and says so rather than failing on the first message.

### Installing uv

```bash
# macOS (Homebrew)
brew install uv

# or the official installer (any OS)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Setup

1. Go to this folder:
   ```bash
   cd day7-save-context
   ```
2. Install the pinned dependencies (creates a local `.venv`):
   ```bash
   uv sync
   ```
3. Create your `.env` file from the example and add your API key:
   ```bash
   cp .env.example .env
   ```
   Then open `.env` and replace the placeholder:
   ```
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   ```
   The `.env` file is git-ignored, so your key never gets committed. So are editor swap files (`*.swp`) — a swap of `.env` would carry its contents — and `data/`, which is where your conversations end up.

## Running

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000**. Send a message, stop the server with `Ctrl-C`, start it again, reload the page: the conversation is still there, and the agent still knows what is in it.

Conversations are written to `data/conversations/` next to `server.py`. Point `CHAT_STORE_DIR` somewhere else to keep them elsewhere:

```bash
CHAT_STORE_DIR=~/chats uv run server.py
```

Two tabs at the top switch between the modes: **Single chat** and **Compare models**. The last tab you used is remembered across reloads.

### Single chat

Just a chat — one agent on its default `AgentConfig` (`deepseek-v4-flash`, the default system prompt, `temperature 1.0`, `reasoning_effort: low`, plain text), which is what makes it the plainest demonstration of the brief. It now also has a memory, and the ⚙ **Settings** popover in the top bar is where you can see how much of one and throw it away. The other setting there is **"Show debug panel"**. Click anywhere else, or press **Escape**, to close the popover.

The message box is a real `<textarea>`: paste or type text of any length, including line breaks. Press **Enter** to send, **Shift+Enter** for a line break.

### Compare models

Several chats side by side, up to **5**, for answering the only question that matters when tuning parameters: what actually changes when I change this?

- **+ Add chat** appends a column; the counter shows how many of the 5 slots are used. Columns split the width evenly, and below ~260px each the row scrolls sideways rather than squeezing them into slivers.
- **Every column is its own agent**: its own model, system prompt, stop sequences, response format, reasoning effort and temperature; its own message box; its own transcript; and since day 7, its own saved conversation.
- The column header shows the model and a one-line recap of that column's parameters (`deepseek-v4-pro · T 0.0 · reasoning: max · text`) — the line you scan when comparing, so it stays visible with the settings folded away.
- **⚙ Settings** folds that column's parameter form in and out. A newly added chat opens with its settings visible. Its **Clear context** button is at the bottom of that form.
- **Debug** opens a request/response log *for that column only*.
- **✕** removes the column, and deletes its saved conversation.
- The box at the bottom sends **one prompt to every column at once**, each as its own parallel request, so a slow model never holds up the others.

Requests still carry their settings with them; what the server now keeps is the transcript, which is what makes a column the same column tomorrow. The columns still run genuinely in parallel — the handler is a plain `def`, so FastAPI runs it in its threadpool, and the store holds its lock only for the moment it writes a file.

### Debug panel

In single-chat mode, open **⚙ Settings** in the top bar and tick **"Show debug panel"** to open a third column on the right. For every message it logs:

- a one-line summary — model, temperature, **HTTP status**, token usage (including reasoning tokens when any were spent), latency, finish reason;
- the **HTTP call itself**: `POST https://api.deepseek.com/chat/completions → 200`, green on success, red on failure;
- the exact **request** body, the exact **response** body, and the **request headers** — each collapsible, each with a Copy button and the token count for that half of the exchange.

Since day 7 the request body is where you can watch the memory work: `messages` carries the replayed conversation ahead of your new question, which is also the honest picture of what it costs — the prompt token count grows with the conversation.

The `Authorization` header is redacted to `Bearer ***` before it reaches the browser.

The bodies are wrapped to the panel's width rather than scrolled sideways. The panel logs every message for the current page session whether shown or hidden, so toggling it back on doesn't lose earlier entries; "Clear log" empties it without touching the conversation — and, unlike **Clear context**, without touching anything on the server.

## Tests

A headless check of the interface lives in `tests/ui-test.js`: it loads `static/index.html` into [jsdom](https://github.com/jsdom/jsdom) and drives it the way a user would — switching tabs, adding and closing compare columns, folding settings and debug panels, moving sliders, sending messages — then asserts what came out.

Day 7 adds a second act: the page is loaded **again** into a fresh jsdom against the same server, which is what a reload (or a restart — the conversations are in files, not in the process) looks like from the browser's side. Everything after that point asks one question: did what was on screen come back? It checks the restored transcripts, the restored columns and their order, the parameters in their settings forms, the two-click clear button, and that a closed column and a cleared chat stay gone on the next load — against both the DOM and the JSON files on disk.

```bash
npm install   # once, pulls jsdom
npm test
```

It starts its own server on port 8765 (override with `TEST_PORT`) and shuts it down afterwards, so it never collides with a dev server on :8000. It points that server's `CHAT_STORE_DIR` at a fresh temp directory, so it never reads or writes the conversations you have been having in the real app. **It cannot spend tokens:** the API key handed to that process is a dummy, and since `python-dotenv` does not override an already-set variable, a real key in `.env` is ignored. DeepSeek answers every call with a 401 — which is all these checks need, since they cover the plumbing, not model output. (It also makes the error-turn behaviour easy to assert: every stored answer in the suite is a failed one.) Nothing in the suite touches the network.

What it does *not* cover: how anything looks. jsdom has no layout engine, so widths, wrapping and overflow are only checked as computed style values, never as rendered pixels.

To add a check, call `check("what it should do", <condition>, <detail>)` — a failure sets the exit code.

## Available models

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`
- `deepseek-v4-flash-vision-exp`

All three share a 1M token context window and a maximum output of 384K tokens (per the [DeepSeek pricing docs](https://api-docs.deepseek.com/quick_start/pricing)).

## Available system prompts

- `Default` — "You are a helpful assistant." (default)
- `Step-by-step reasoning` — asks the model to break the problem into numbered steps, reason through them, then give a final answer

Pick **"Custom…"** in the system prompt dropdown to type a prompt of your own.

## Notes on parameters

These are the fields of `AgentConfig`. Every one except `model` is optional, and an unset field is simply left out of the request body.

- **`stop`** — optional; up to 16 sequences. Leave empty to clear.
- **`response_format`** — only `text` (default) and `json_object` are supported by the DeepSeek API. When using `json_object`, you must also ask for JSON explicitly in your message — otherwise the model may generate whitespace until it hits the token limit (see the [DeepSeek API docs](https://api-docs.deepseek.com/api/create-chat-completion)).
- **Reasoning effort** — DeepSeek's models think before answering by default, even with nothing set: every response carries a `reasoning_content` field and spends billed `reasoning_tokens`. `off`, `low`, `high`, `max` map to the API's `reasoning_effort`, except `off`, which sends `reasoning_effort: "none"` — the one value that disables thinking entirely. Default here is `low`.
- **`temperature`** — a slider from `0.0` to `2.0` in `0.1` steps, defaulting to `1.0` (the API's own default). Low values make the model pick the most likely next token almost every time — focused, repeatable answers — while high values flatten the odds, which reads as more varied but less reliable. Always sent, even at the default, so the debug panel shows exactly what produced the answer. DeepSeek's [recommended values per use case](https://api-docs.deepseek.com/quick_start/parameter_settings), for reference when setting the slider:

  | Use case                | Temperature |
  |-------------------------|-------------|
  | Coding / math           | 0.0         |
  | Data analysis           | 1.0         |
  | Conversation            | 1.3         |
  | Creative writing        | 1.5         |

**Note on response length:** there is no reliable API parameter to force an exact response length — `max_tokens` is only a hard cutoff that truncates mid-sentence if hit, not a length target. To control how long the answer is, ask for it explicitly in your message (e.g. "in 2-3 sentences", "under 50 words").

## API

Everything the frontend uses, in case you want to drive it from `curl`.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/config` | models, prompts, limits, defaults |
| `GET` | `/api/conversations` | every saved chat, oldest first |
| `GET` | `/api/conversations/{id}` | one saved chat |
| `PUT` | `/api/conversations/{id}` | create it / update its settings, messages untouched |
| `DELETE` | `/api/conversations/{id}` | forget it entirely |
| `DELETE` | `/api/conversations/{id}/messages` | clear the context, keep the chat |
| `POST` | `/api/chat` | ask a question in a conversation |

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"message": "What is my name?", "conversation_id": "single"}' | jq .answer
```

`conversation_id` defaults to `single`, so a bare `{"message": "..."}` lands in the single chat — the same conversation the browser is looking at. Ids become file names, so they are restricted rather than escaped: letters, digits, `-` and `_`, up to 64 characters. Anything else is a 400.
