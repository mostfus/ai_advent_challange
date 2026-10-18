"""Day 26: one client for the whole app, routed by the model id of each request.

    body["model"] == the local model id  ->  the llama-server client (`llm.local`)
    anything else                        ->  the remote client (DeepSeek, `llm.client`)

Everything that talks to a model - the agent, the side requests (facts,
summaries, proposals, checks), the reranker - is handed this one object and
calls `complete(body)` on it, exactly as it would on a single provider.
"""

from __future__ import annotations

from llm.client import LLMClient, LLMResponse


class RoutingClient:
    """One `complete(body)` for the whole app, sent where `body["model"]` says.

    Every caller in the app is handed one client and keeps it. This is that
    client, and it reads the model id off each request - so a facts update, a
    reranker or an invariant check for a local chat runs locally too, with no
    call site knowing there are two.
    """

    def __init__(self, remote: LLMClient, local: LLMClient | None = None,
                 local_model_id: str | None = None) -> None:
        self.remote = remote
        self.local = local
        self.local_model_id = local_model_id

    def client_for(self, model: str | None) -> LLMClient:
        if self.local is not None and model == self.local_model_id:
            return self.local
        return self.remote

    def complete(self, body: dict) -> LLMResponse:
        return self.client_for(body.get("model")).complete(body)

    @property
    def redacted_headers(self) -> dict:
        return self.remote.redacted_headers

    def close(self) -> None:
        self.remote.close()
        if self.local is not None:
            self.local.close()
