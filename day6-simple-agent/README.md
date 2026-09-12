# LLM Agent

A small **agent** that takes a question, sends it to an LLM over plain HTTP, reads the answer, and shows the whole exchange in a web GUI — including the literal JSON that crossed the wire.

Day 6 is not a new feature on top of day 5; it is a rewrite of how the request is made. Through day 5 the app had a function, `ask_model(client, model, system_prompt, message, stop, response_format, reasoning_effort, temperature)`, called straight from a web handler, and the `openai` SDK underneath it. Now it has an **agent** that owns the conversation, and its **own HTTP client** underneath.

## What "an agent" means here

Not much magic — the point is that it is an *entity*, not a call site.

|  | Day 5 (`ask_model`) | Day 6 (`Agent`) |
|---|---|---|
| Configuration | eight arguments, passed per call | `AgentConfig`, given once at construction |
| Who assembles the request | the caller | the agent |
| Who interprets the reply | the caller | the agent |
| Transcript | nowhere | `agent.history` |
| Knows the provider | yes, DeepSeek was hardcoded per module | no — it is handed a client |

The last row is the load-bearing one. `Agent` is handed an `LLMClient` and never learns whose API is on the other end, so pointing it at a different provider is a new `LLMClient` subclass and a one-line change in `server.py` — nothing in the agent, nothing in the UI. In day 5 the provider was baked into the module that built the request.

The agent also runs perfectly well with no server in sight, which is the simplest proof that it is a separate thing rather than a piece of the web app:

```bash
uv run python -c "
from agent import Agent, AgentConfig
from llm_client import DeepSeekClient

agent = Agent(DeepSeekClient.from_env(), AgentConfig(model='deepseek-v4-flash'))
print(agent.ask('Say hello in five words.').answer)
"
```

## The layers

Each layer knows only about the one below it.

```
static/index.html     the interface: sends text, draws answers
        │  POST /api/chat  {"message": "...", "settings": {...}}
server.py             transport only: HTTP in, HTTP out
        │  Agent(client, config).ask(message)
agent.py          ★   the agent: role, parameters, transcript,
        │              request assembly, reply interpretation
llm_client.py     ★   HTTP: POST /chat/completions, timeouts,
        │              status codes, error messages
   DeepSeek
```

`agent.py` imports no web framework and no HTTP library — only `llm_client`. `server.py` no longer contains a single line about how a DeepSeek request is shaped. Swapping in a third provider means writing one `LLMClient` subclass and touching nothing else.

## Requests go over HTTP, by hand

The `openai` dependency is gone. Every call is now an explicit POST:

```python
httpx.post(
    "https://api.deepseek.com/chat/completions",
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    json={"model": ..., "messages": [...]},
)
```

`LLMClient` holds all of that and `DeepSeekClient` is a five-line subclass naming a host, an environment variable and a label. Most hosted LLMs speak this same OpenAI-compatible protocol, so a second provider is another five-line subclass.

What the SDK used to do through exception classes (`AuthenticationError`, `RateLimitError`, …) is now a mapping from status codes, written out in `LLMClient.describe_failure`: 401/403 names the environment variable to check, 429 says the rate limit was hit, 5xx says to retry, and the provider's own `error.message` is appended when it sent one. Timeouts and connection failures are caught separately.

Two things got better as a side effect:

- **The debug panel stopped lying.** It used to display the kwargs the app *intended* to send, reconstructed after the fact. It now shows the actual request body, the actual response body, the URL, the status code and the request headers.
- **Failures are visible on the same terms as successes.** A 401 shows `POST https://api.deepseek.com/chat/completions → 401`, not just a message.

The `Authorization` header is redacted to `Bearer ***` before it reaches the browser.

## Memory: the seam, deliberately left unused

`Agent` builds its messages as `[system] + self.history + [user]` and appends each completed turn to `self.history`. Keeping one agent alive across turns therefore gives it a real memory — verified:

```
1: My name is Maksim. Reply with just OK.   ->  OK.
2: What is my name?                          ->  Maksim.
```

The server nonetheless **builds a fresh agent per request**, so in this app every question is still answered from scratch. That is on purpose: the compare tabs are only trustworthy if the columns differ by their parameters and nothing else, and a remembered conversation would be another difference. Giving the single-chat tab a persistent agent is a change to `server.py` alone — the agent already supports it.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A DeepSeek API key ([get one here](https://platform.deepseek.com))

The server refuses to start without it, and says so rather than failing on the first message.

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
   cd day6-simple-agent
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
   The `.env` file is git-ignored, so your key never gets committed. So are editor swap files (`*.swp`) — a swap of `.env` would carry its contents.

## Running

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000** in your browser.

Two tabs at the top switch between the modes: **Single chat** and **Compare models**. The last tab you used is remembered across reloads.

### Single chat

Just a chat. There is **no parameter form here** — this tab runs one agent on its default `AgentConfig` (`deepseek-v4-flash`, the default system prompt, `temperature 1.0`, `reasoning_effort: low`, plain text), which is what makes it the plainest demonstration of the day-6 brief: a question in, an agent call, an answer out.

The one setting this tab has — **"Show debug panel"** — sits behind the **⚙ Settings** button in the top bar, which opens a popover under it. Click anywhere else, or press **Escape**, to close it. A 300px sidebar for a single checkbox was not worth the width.

Varying the model or the parameters is what the **Compare models** tab is for, where each column is its own agent with its own configuration.

The message box is a real `<textarea>`: paste or type text of any length, including line breaks. Press **Enter** to send, **Shift+Enter** for a line break.

### Compare models

Several chats side by side, up to **5**, for answering the only question that matters when tuning parameters: what actually changes when I change this?

- **+ Add chat** appends a column; the counter shows how many of the 5 slots are used. Columns split the width evenly, and below ~260px each the row scrolls sideways rather than squeezing them into slivers.
- **Every column is its own agent**: its own model, system prompt, stop sequences, response format, reasoning effort and temperature; its own message box; its own transcript.
- The column header shows the model and a one-line recap of that column's parameters (`deepseek-v4-pro · T 0.0 · reasoning: max · text`) — the line you scan when comparing, so it stays visible with the settings folded away.
- **⚙ Settings** folds that column's parameter form in and out — the only parameter form in the app. A newly added chat opens with its settings visible. The form is deliberately bare: six controls, no explanatory hints, since repeated across five narrow columns they were noise and pushed the temperature slider below the fold.
- **Debug** opens a request/response log *for that column only*.
- **✕** removes the column.
- The box at the bottom sends **one prompt to every column at once**, each as its own parallel request, so a slow model never holds up the others.

Requests carry their settings with them and the server keeps no state, which is what makes the columns independent. Measured on this rewrite: five columns sending at once complete in ~1.6 s of wall time against ~6.7 s of summed request time, i.e. genuinely in parallel.

### Debug panel

In single-chat mode, open **⚙ Settings** in the top bar and tick **"Show debug panel"** to open a third column on the right. For every message it logs:

- a one-line summary — model, temperature, **HTTP status**, token usage (including reasoning tokens when any were spent), latency, finish reason;
- the **HTTP call itself**: `POST https://api.deepseek.com/chat/completions → 200`, green on success, red on failure;
- the exact **request** body, the exact **response** body, and the **request headers** — each collapsible, each with a Copy button and the token count for that half of the exchange.

The bodies are wrapped to the panel's width rather than scrolled sideways: a response is mostly one very long line — the answer string — which as unwrapped text ran far off to the right. Indentation is preserved, and words with nothing to break on (ids, base64) are broken anyway.

The panel logs every message for the current page session whether shown or hidden, so toggling it back on doesn't lose earlier entries; "Clear log" empties it without touching the conversation. Only the on/off toggle is remembered across reloads.

In compare mode the same log lives inside each column, behind that column's own **Debug** button.

## Tests

A headless check of the interface lives in `tests/ui-test.js`: it loads `static/index.html` into [jsdom](https://github.com/jsdom/jsdom) and drives it the way a user would — switching tabs, adding and closing compare columns, folding settings and debug panels, moving sliders, sending messages — then asserts what came out, including the exact settings each column put on the wire, the HTTP line each debug entry logged, and that the API key is redacted before it reaches the browser.

```bash
npm install   # once, pulls jsdom
npm test
```

It starts its own server on port 8765 (override with `TEST_PORT`) and shuts it down afterwards, so it never collides with a dev server on :8000. **It cannot spend tokens:** the API key handed to that process is a dummy, and since `python-dotenv` does not override an already-set variable, a real key in `.env` is ignored. DeepSeek answers every call with a 401 — which is all these checks need, since they cover the plumbing, not model output. Nothing in the suite touches the network.

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

  The quickest way to see what it does: open **Compare models**, set one column to `0.0` and another to `1.8`, and send the same prompt to both from the shared box at the bottom.

**Note on response length:** there is no reliable API parameter to force an exact response length — `max_tokens` is only a hard cutoff that truncates mid-sentence if hit, not a length target. To control how long the answer is, ask for it explicitly in your message (e.g. "in 2-3 sentences", "under 50 words").
