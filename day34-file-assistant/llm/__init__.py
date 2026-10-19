"""The model side: how a finished request body reaches an LLM.

    client    `LLMClient` - one OpenAI-compatible chat-completions POST - and
              `DeepSeekClient`, the remote provider (key from `.env`)
    local     llama.cpp's `llama-server`: `local_llm.json`, the process the app
              may start, and `LlamaCppClient`, which speaks to it
    routing   `RoutingClient` - one `complete(body)`, sent local or remote by
              `body["model"]`

Nothing here knows about agents, prompts, storage or the web app: it is
handed a request body and hands back what came off the wire.
"""

from llm.client import DeepSeekClient, LLMClient, LLMClientError, LLMResponse
from llm.routing import RoutingClient

__all__ = ["DeepSeekClient", "LLMClient", "LLMClientError", "LLMResponse", "RoutingClient"]
