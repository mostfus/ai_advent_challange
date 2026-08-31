# DeepSeek CLI Chat

A simple command-line interface for sending prompts to the DeepSeek LLM API and reading the responses.

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
   cd day1
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
uv run code.py
```

## Usage

On startup you'll see a welcome message with the current model and the list of commands. Type any text and press Enter to send it to the model.

| Command    | Description                                        |
|------------|-----------------------------------------------------|
| `/models`  | List available models and switch the active one     |
| `/exit`    | Quit the CLI                                        |

Each message is sent independently — the CLI does not keep conversation history between turns.

### Available models

- `deepseek-v4-flash` (default)
- `deepseek-v4-pro`
- `deepseek-v4-flash-vision-exp`
