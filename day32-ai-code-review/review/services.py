"""The models a review needs on the runner, started for the review and stopped after it.

    Ollama(embedder).start() / .stop()    bge-m3 for RAG: `ollama serve` if nothing answers,
                                          the model pulled if it is missing
    local_model(config).start() / .stop() llama-server from `local_llm.json`, when the review's
                                          model is the local one

The rule is `LocalServer`'s (day 26): a server that already answers is used
and left running; one this process started is stopped when the review ends,
whatever happened - a self-hosted runner is somebody's computer, and a job
that left a model loaded on it would hold gigabytes of memory until reboot.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import httpx

from llm import local as local_llm
from rag.embedders import OllamaEmbedder


class ServiceError(RuntimeError):
    pass


class Ollama:
    def __init__(self, embedder: OllamaEmbedder, binary: str = "ollama", log=print,
                 startup_seconds: float = 60, pull_seconds: float = 1800) -> None:
        self.embedder = embedder
        self.binary = binary
        self.log = log
        self.startup_seconds = startup_seconds
        self.pull_seconds = pull_seconds
        self.process: subprocess.Popen | None = None

    def answering(self) -> bool:
        try:
            return httpx.get(f"{self.embedder.base_url}/api/tags", timeout=2).status_code == 200
        except httpx.HTTPError:
            return False

    def start(self) -> "Ollama":
        if not self.answering():
            if shutil.which(self.binary) is None:
                raise ServiceError(f"`{self.binary}` is not installed on this runner (brew install ollama)")
            self.log(f"[ollama ] starting `{self.binary} serve` for the review")
            self.process = subprocess.Popen([self.binary, "serve"], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + self.startup_seconds
            while not self.answering():
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self.stop()
                    raise ServiceError("Ollama did not start")
                time.sleep(0.5)
        status = self.embedder.status()
        if not status.get("ok", True) and "not pulled" in (status.get("detail") or ""):
            self.log(f"[ollama ] pulling {self.embedder.model} (once per runner)")
            try:
                response = httpx.post(f"{self.embedder.base_url}/api/pull",
                                      json={"model": self.embedder.model, "stream": False},
                                      timeout=self.pull_seconds)
            except httpx.HTTPError as exc:
                self.stop()
                raise ServiceError(f"could not pull {self.embedder.model}: {exc.__class__.__name__}") from exc
            if response.status_code != 200:
                self.stop()
                raise ServiceError(f"could not pull {self.embedder.model}: {response.text[:200]}")
        return self

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.log("[ollama ] stopping the Ollama this review started")
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


class LocalModel:
    """llama-server for a review on the local model - started, and waited for."""

    def __init__(self, config: local_llm.LocalLLMConfig, log=print) -> None:
        self.config = config
        self.server = local_llm.LocalServer(config, log=log)

    def start(self) -> "LocalModel":
        self.server.start()
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while self.server.state == "starting" and time.monotonic() < deadline:
            time.sleep(1)
        if self.server.state not in ("ready", "external"):
            self.stop()
            raise ServiceError(f"the local model is not available: {self.server.detail}")
        return self

    def stop(self) -> None:
        self.server.stop()
