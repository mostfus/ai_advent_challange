"""Entry point: the web app over the agent.

    uv run server.py

then open http://127.0.0.1:8000. Everything is built by `web.app.create_app()`
(and, under it, `core.runtime.AgentRuntime.build()`): the DeepSeek key from
`.env`, the local model from `local_llm.json`, the document corpus from
`corpus.json`, the stores under `data/`. `uvicorn server:app` serves the same
app.
"""

from __future__ import annotations

import sys

from llm.client import LLMClientError
from web.app import create_app

try:
    app = create_app()
except (LLMClientError, ValueError) as exc:
    # No key in `.env`, or a config file that does not parse: say which, and stop.
    print(f"Error: {exc}")
    sys.exit(1)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
