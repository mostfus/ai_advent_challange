# DeepSeek Chat

A small tool for sending prompts to the DeepSeek LLM API and reading the responses — a **web GUI** (recommended) and the original **CLI**, sharing the same underlying logic in `code.py`.

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
   cd day3
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

### Web GUI (recommended)

```bash
uv run server.py
```

Then open **http://127.0.0.1:8000** in your browser.

A settings panel on the left (model, system prompt, stop sequences, response format — the same options as the CLI's commands, just as dropdowns/inputs instead of `/commands`), the chat itself centered in the middle. The message box is a real `<textarea>`: paste or type text of any length, including text with line breaks — it's never cut off. Press **Enter** to send, **Shift+Enter** for a line break.

Tick **"Show debug panel"** at the bottom of the settings panel to open a third column on the right that logs, for every message, the exact JSON sent to the DeepSeek API and the exact JSON it returned — model, token usage, latency and finish reason as a one-line summary, then the full request/response bodies underneath (syntax-highlighted, collapsible, with a Copy button each). The panel logs every message for the current page session regardless of whether it's shown or hidden, so toggling it back on doesn't lose earlier entries; "Clear log" empties it without touching the conversation. Only the on/off toggle itself is remembered across reloads (via the browser's `localStorage`) — the log is cleared on refresh.

Planned next: temperature and other sampling parameters.

### CLI

```bash
uv run code.py
```

On startup you'll see a welcome message with the current model, the current system prompt, and the list of commands. Type any text and press Enter to send it to the model.

Messages can span multiple lines and be of any length — this also covers pasting text that contains line breaks, which previously got cut off after the first line. Keep typing/pasting, then press Enter on an empty line to send the message.

| Command             | Description                                          |
|---------------------|-------------------------------------------------------|
| `/models`           | List available models and switch the active one       |
| `/system`           | List system prompts and switch the active one          |
| `/stop`             | Set stop sequence(s) (up to 16, comma-separated)       |
| `/response_format`  | Choose the response format: `text` or `json_object`   |
| `/exit`             | Quit the CLI                                           |

Both interfaces send each message independently — no conversation history is kept between turns (the web GUI's transcript is just a visual log; each request to the model still only contains the one message you just sent).

## Available models

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`
- `deepseek-v4-flash-vision-exp`

All three models share a 1M token context window and a maximum output of 384K tokens (per the [DeepSeek pricing docs](https://api-docs.deepseek.com/quick_start/pricing)).

## Available system prompts

- `Default` — "You are a helpful assistant." (default)
- `Step-by-step reasoning` — asks the model to break the problem into numbered steps, reason through them, then give a final answer

Both interfaces also let you enter a custom system prompt of your own instead of picking a preset (`/system` then `0` in the CLI; "Custom…" in the web GUI's system prompt dropdown).

## Notes on parameters

- **`stop`** — optional; up to 16 sequences. Leave empty to clear.
- **`response_format`** — only `text` (default) and `json_object` are supported by the DeepSeek API. When using `json_object`, you must also ask for JSON explicitly in your message — otherwise the model may generate whitespace until it hits the token limit (see the [DeepSeek API docs](https://api-docs.deepseek.com/api/create-chat-completion)).

**Note on response length:** there is no reliable API parameter to force an exact response length — `max_tokens` is only a hard cutoff that truncates mid-sentence if hit, not a length target. To control how long the answer is, ask for it explicitly in your message (e.g. "in 2-3 sentences", "under 50 words").
