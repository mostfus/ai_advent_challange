# DeepSeek Chat

A small **web GUI** for sending prompts to the DeepSeek LLM API and reading the responses, with a **compare mode** that runs several chats side by side so you can see what different models and parameters do to the same prompt. The API logic lives in `chat.py`, the HTTP layer in `server.py`, the interface in `static/index.html`.

> The `/command`-driven CLI that shipped through day 3 is gone — the browser UI covers every option it had, so there is only one front end to keep in sync now.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A DeepSeek API key ([get one here](https://platform.deepseek.com))

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
   cd day4-completion-temperature
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
   DEEPSEEK_API_KEY=your_api_key_here
   ```
   The `.env` file is git-ignored, so your key never gets committed.

## Running

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000** in your browser.

Two tabs at the top switch between the modes: **Single chat** and **Compare models**. The last tab you used is remembered across reloads.

### Single chat

A settings panel on the left (model, system prompt, stop sequences, response format, reasoning effort, temperature), the chat itself centered in the middle. The message box is a real `<textarea>`: paste or type text of any length, including text with line breaks — it's never cut off. Press **Enter** to send, **Shift+Enter** for a line break.

### Compare models

**New in day 4.** Several chats side by side, up to **5**, for answering the only question that matters when tuning parameters: what actually changes when I change this?

- **+ Add chat** appends a column; the counter next to it shows how many of the 5 slots are used, and the button greys out at the cap. Columns split the available width evenly (`flex: 1 1 0`), so two chats are half the screen each and five are a fifth each. Below ~260px per column the row scrolls sideways instead of squeezing them into slivers.
- **Every column is a full chat of its own**: its own model, system prompt, stop sequences, response format, reasoning effort and temperature; its own message box; its own transcript. Nothing is shared.
- The column header shows the model, and under it a one-line recap of that column's parameters (`deepseek-v4-pro · T 0.0 · reasoning: max · text`) — that line is the thing you scan when comparing, so it stays visible with the settings folded away.
- **⚙ Settings** folds that column's parameter form in and out. A newly added chat opens with its settings already visible, since giving it different parameters is the first thing you'd do. The form is the compact variant: the same six controls, but without the explanatory hints and the temperature preset chips — repeated across five narrow columns they were noise, and they pushed the temperature slider below the fold. Temperature here is just the slider and its value; the explanations live in the single-chat view.
- **Debug** opens a request/response log *for that column only*, so you can read the exact JSON each model was sent and returned, with its own token usage and latency. The JSON wraps to the column's width — a long answer never runs off to the side.
- **✕** removes the column.
- The box at the bottom sends **one prompt to every column at once**. Each column still fires its own independent request — same message, different parameters, all in flight at the same time — so a slow model never holds up the others. The per-column message boxes keep working for following up with just one of them.

Requests carry their settings with them and the server keeps no state, which is what makes the columns independent. Measured: five columns sending at once complete in ~840 ms of wall time against ~3.8 s of summed request time, i.e. genuinely in parallel, each with its own parameters.

### Debug panel

In single-chat mode, tick **"Show debug panel"** at the bottom of the settings panel to open a third column on the right that logs, for every message, the exact JSON sent to the DeepSeek API and the exact JSON it returned — model, token usage (including reasoning tokens, when any were spent), latency and finish reason as a one-line summary, then the full request/response bodies underneath (syntax-highlighted, collapsible, with a Copy button each, and the token count for that half of the exchange shown right next to its label). The bodies are wrapped to the panel's width rather than scrolled sideways: a response is mostly one very long line — the answer string — which as unwrapped text ran far off to the right. Indentation is preserved, and words with nothing to break on (ids, base64) are broken anyway rather than allowed to widen the panel. The panel logs every message for the current page session regardless of whether it's shown or hidden, so toggling it back on doesn't lose earlier entries; "Clear log" empties it without touching the conversation. Only the on/off toggle itself is remembered across reloads (via the browser's `localStorage`) — the log is cleared on refresh.

In compare mode the same log lives inside each column, behind that column's own **Debug** button.

Each message is sent independently — no conversation history is kept between turns (the transcript is just a visual log; each request to the model still only contains the one message you just sent). That is worth knowing when comparing: every column answers your prompt from scratch, so the columns differ only by their parameters, not by what they remember.

## Tests

A headless check of the interface lives in `tests/ui-test.js`: it loads `static/index.html` into [jsdom](https://github.com/jsdom/jsdom) and drives it the way a user would — switching tabs, adding and closing compare columns, folding settings and debug panels, moving sliders, sending messages — then asserts what came out, including the exact settings each column put on the wire.

```bash
npm install   # once, pulls jsdom
npm test
```

It starts its own server on port 8765 (override with `TEST_PORT`) and shuts it down afterwards, so it never collides with a dev server on :8000. **It cannot spend tokens:** the API key handed to that process is a dummy, and since `python-dotenv` does not override an already-set variable, a real key in `.env` is ignored. DeepSeek answers every call with an auth error — which is all these checks need, since they cover the plumbing (which parameters each column sends, where the answer and the debug entry land), not model output.

What it does *not* cover: how anything actually looks. jsdom has no layout engine, so widths, wrapping and overflow are only checked as computed style values, never as rendered pixels.

To add a check, call `check("what it should do", <condition>, <detail>)` — a failure sets the exit code.

## Available models

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`
- `deepseek-v4-flash-vision-exp`

All three models share a 1M token context window and a maximum output of 384K tokens (per the [DeepSeek pricing docs](https://api-docs.deepseek.com/quick_start/pricing)).

## Available system prompts

- `Default` — "You are a helpful assistant." (default)
- `Step-by-step reasoning` — asks the model to break the problem into numbered steps, reason through them, then give a final answer

Pick **"Custom…"** in the system prompt dropdown to type a prompt of your own instead.

## Notes on parameters

- **`stop`** — optional; up to 16 sequences. Leave empty to clear.
- **`response_format`** — only `text` (default) and `json_object` are supported by the DeepSeek API. When using `json_object`, you must also ask for JSON explicitly in your message — otherwise the model may generate whitespace until it hits the token limit (see the [DeepSeek API docs](https://api-docs.deepseek.com/api/create-chat-completion)).
- **Reasoning effort** — DeepSeek's models think before answering by default, even with nothing set: every response carries a `reasoning_content` field and spends billed `reasoning_tokens`, confirmed by inspecting live responses. `off`, `low`, `high`, `max` map directly to the API's `reasoning_effort` parameter, except `off`, which sends `reasoning_effort: "none"` — the one value that disables thinking entirely. Default here is `low`.
- **`temperature`** — **new in day 4.** A slider from `0.0` to `2.0` in `0.1` steps, defaulting to `1.0` (the API's own default). It controls how the model picks each next token: low values make it pick the most likely one almost every time — focused, repeatable, near-deterministic answers — while high values flatten the odds, letting less likely tokens through, which reads as more varied and creative but also less reliable. The value is always sent, even at the default, so the debug panel shows exactly what produced the answer. Under the slider (in single-chat mode) are shortcut chips with DeepSeek's [recommended values per use case](https://api-docs.deepseek.com/quick_start/parameter_settings):

  | Use case                | Temperature |
  |-------------------------|-------------|
  | Coding / math           | 0.0         |
  | Data analysis           | 1.0         |
  | Conversation            | 1.3         |
  | Creative writing        | 1.5         |

  The quickest way to see what it does: open **Compare models**, set one column to `0.0` and another to `1.8`, and send the same prompt to both from the shared box at the bottom.

**Note on response length:** there is no reliable API parameter to force an exact response length — `max_tokens` is only a hard cutoff that truncates mid-sentence if hit, not a length target. To control how long the answer is, ask for it explicitly in your message (e.g. "in 2-3 sentences", "under 50 words").
