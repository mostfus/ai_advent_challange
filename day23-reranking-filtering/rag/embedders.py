"""Embedders: strings in, unit vectors out - and nothing else is allowed to know how.

The rest of the package sees one interface:

    embedder.name                 "ollama:bge-m3" - written next to every index
    embedder.embed_documents(xs)  -> np.ndarray (n, dim), rows of length 1
    embedder.embed_query(x)       -> np.ndarray (dim,)
    embedder.status()             -> {"ok": ..., "detail": ...} for a UI to show

Documents and queries are separate calls because some models want them
marked differently (e5 wants "passage: " / "query: ", Qwen3 wants an
instruction in front of the query). bge-m3 wants neither, and the prefixes
default to empty - but the seam is where it has to be before a second model
needs it.

Vectors come back L2-normalised whatever the model does, so a dot product is
a cosine everywhere downstream and the store never has to ask.

**Ollama** is the one provider today: `POST /api/embed` with a batch of
strings, the same local server `ollama pull bge-m3` downloads into. It is an
HTTP call like every other model call in this app - no SDK. Adding a
provider is a subclass and a line in `PROVIDERS`; `make_embedder("ollama:bge-m3")`
is how a caller asks for one without importing it.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

import httpx
import numpy as np

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "bge-m3"


class EmbedderError(RuntimeError):
    """Written for a person: the UI prints it as-is."""


def normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


class Embedder(ABC):
    name: str = "embedder"
    batch_size: int = 16
    document_prefix: str = ""
    query_prefix: str = ""

    @abstractmethod
    def _embed(self, texts: list[str]) -> list[list[float]]:
        """One batch, raw - whatever the provider returns."""

    def status(self) -> dict:
        return {"ok": True, "detail": ""}

    def describe(self) -> dict:
        return {"name": self.name, "document_prefix": self.document_prefix, "query_prefix": self.query_prefix}

    def embed_documents(self, texts: list[str], on_batch=None) -> np.ndarray:
        """All of `texts`, in batches; `on_batch(done, total)` after each one."""
        rows: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [self.document_prefix + t for t in texts[i:i + self.batch_size]]
            rows.extend(self._embed(batch))
            if on_batch is not None:
                on_batch(min(i + self.batch_size, len(texts)), len(texts))
        if not rows:
            return np.zeros((0, 0), dtype=np.float32)
        return normalise(np.array(rows))

    def embed_query(self, text: str) -> np.ndarray:
        return normalise(np.array(self._embed([self.query_prefix + text])))[0]


class OllamaEmbedder(Embedder):
    def __init__(self, model: str = DEFAULT_OLLAMA_MODEL, base_url: str = DEFAULT_OLLAMA_URL,
                 batch_size: int = 16, timeout: float = 300.0,
                 document_prefix: str = "", query_prefix: str = ""):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self.timeout = timeout
        self.document_prefix = document_prefix
        self.query_prefix = query_prefix
        self.name = f"ollama:{model}"

    def describe(self) -> dict:
        return {**super().describe(), "provider": "ollama", "model": self.model, "url": self.base_url}

    def _embed(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/api/embed"
        try:
            response = httpx.post(url, json={"model": self.model, "input": texts, "truncate": True},
                                  timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise EmbedderError(
                f"Ollama is not answering at {self.base_url} ({exc.__class__.__name__}). "
                f"Start it with `ollama serve`."
            ) from exc
        if response.status_code == 404:
            raise EmbedderError(f"Ollama has no model {self.model!r}. Pull it with `ollama pull {self.model}`.")
        if response.status_code != 200:
            raise EmbedderError(f"Ollama answered {response.status_code}: {response.text[:300]}")
        embeddings = response.json().get("embeddings") or []
        if len(embeddings) != len(texts):
            raise EmbedderError(f"Ollama returned {len(embeddings)} vectors for {len(texts)} texts")
        return embeddings

    def status(self) -> dict:
        """Is the server up and the model pulled - asked without embedding anything."""
        started = time.monotonic()
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "detail": f"Ollama is not answering at {self.base_url} - run `ollama serve`",
                    "error": exc.__class__.__name__}
        names = [m.get("name", "") for m in response.json().get("models", [])]
        if not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return {"ok": False, "detail": f"model {self.model!r} is not pulled - run `ollama pull {self.model}`",
                    "models": names}
        return {"ok": True, "detail": f"{self.model} ready", "ms": round((time.monotonic() - started) * 1000)}


PROVIDERS = {"ollama": OllamaEmbedder}


def make_embedder(spec: str | None = None) -> Embedder:
    """`"ollama:bge-m3"` -> an embedder. Without a spec, `EMBEDDING_MODEL` from the env.

    `OLLAMA_URL` points the Ollama provider somewhere other than localhost.
    """
    spec = (spec or os.getenv("EMBEDDING_MODEL") or f"ollama:{DEFAULT_OLLAMA_MODEL}").strip()
    provider, _, model = spec.partition(":")
    if provider not in PROVIDERS:
        raise EmbedderError(f"unknown embedding provider {provider!r}; known: {', '.join(PROVIDERS)}")
    if provider == "ollama":
        return OllamaEmbedder(model=model or DEFAULT_OLLAMA_MODEL,
                              base_url=os.getenv("OLLAMA_URL") or DEFAULT_OLLAMA_URL)
    return PROVIDERS[provider](model)
