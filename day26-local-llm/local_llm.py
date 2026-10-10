"""Day 26: a local LLM - llama.cpp's `llama-server`, started by the app.

Every model before today was somebody else's computer: a POST to
api.deepseek.com, a key in `.env`, a bill per token. This day puts one on
this machine. llama.cpp ships `llama-server`, an HTTP server that loads a
GGUF file onto the GPU (Metal, on a Mac) and answers the same
OpenAI-compatible `/v1/chat/completions` the rest of this app already
speaks - so the agent does not change at all. What changes is which URL a
request goes to, and that is decided by the model id alone:

    settings.model == "local-qwen3-8b"  ->  http://127.0.0.1:8081/v1   (llama-server)
    anything else                       ->  https://api.deepseek.com   (DeepSeek)

Everything about the local model lives in `local_llm.json`, next to this file,
and nowhere in the UI: which model, where it is served, how much context, how
it samples. The page only sees one more id in its model list. The fields:

    enabled                  false hides the model and starts nothing
    model_id                 the id in the model list (and in `settings.model`)
    autostart                start llama-server with the app; false = you start it
    binary                   the llama-server executable (on PATH, or a path)
    host, port               where it listens; a port that already answers is
                             used and left running, like day 20's MCP servers
    hf_repo                  `user/repo:quant` on Hugging Face, downloaded once
                             into llama.cpp's cache on the first start
    model_path               a local .gguf instead; wins over hf_repo when set
    context_size             tokens one request may hold (prompt + answer)
    parallel                 requests answered at the same time (server slots);
                             the server is given context_size x parallel
    gpu_layers               layers offloaded to the GPU; 99 = all of them
    flash_attn               on | off | auto
    sampling                 top_p, top_k, min_p, repeat_penalty as server
                             defaults - temperature comes from the chat's own
                             setting, like it does for DeepSeek
    extra_args               anything else for the command line, verbatim
    startup_timeout_seconds  how long to wait for the model to load (a first
                             start includes the download)
    request_timeout_seconds  how long one answer may take

Two DeepSeek parameters mean something else here, and the client translates
them on the way out (so the debug panel shows what was actually sent):

  * `reasoning_effort` - Qwen3 thinks or does not; there is no budget knob.
    `off` becomes `chat_template_kwargs: {enable_thinking: false}`, every
    other level leaves thinking on. llama-server hands the thoughts back as
    `reasoning_content`, the field DeepSeek uses, so the panel shows them.
  * `model` - the server serves one model under the alias it was started
    with, which is `model_id`; nothing to translate, but it is checked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from llm_client import LLMClient, LLMClientError, LLMResponse

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "local_llm.json"


@dataclass
class LocalLLMConfig:
    enabled: bool = True
    model_id: str = "local-qwen3-8b"
    autostart: bool = True
    binary: str = "llama-server"
    host: str = "127.0.0.1"
    port: int = 8081
    hf_repo: str = "Qwen/Qwen3-8B-GGUF:Q4_K_M"
    model_path: str = ""
    context_size: int = 16384
    parallel: int = 2
    gpu_layers: int = 99
    flash_attn: str = "auto"
    sampling: dict = field(default_factory=dict)
    extra_args: list = field(default_factory=list)
    startup_timeout_seconds: float = 900.0
    request_timeout_seconds: float = 600.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def model_name(self) -> str:
        """What is loaded, for people: the file, or the Hugging Face repo."""
        return Path(self.model_path).name if self.model_path else self.hf_repo


def load_config(path: Path = CONFIG_PATH) -> LocalLLMConfig:
    """Read `local_llm.json`. No file is not an error - it means no local model."""
    if not path.exists():
        return LocalLLMConfig(enabled=False)
    raw = json.loads(path.read_text(encoding="utf-8"))
    known = LocalLLMConfig.__dataclass_fields__
    unknown = sorted(set(raw) - set(known))
    if unknown:
        raise ValueError(f"{path.name}: unknown field(s) {', '.join(unknown)}")
    return LocalLLMConfig(**raw)


def autostart_enabled() -> bool:
    """`LOCAL_LLM_AUTOSTART=0` keeps the app from starting llama-server (the tests set it)."""
    return (os.getenv("LOCAL_LLM_AUTOSTART") or "1").strip().lower() not in ("0", "false", "no", "off")


def server_command(config: LocalLLMConfig) -> list[str]:
    """The llama-server command line the config comes to."""
    model = ["-m", config.model_path] if config.model_path else ["-hf", config.hf_repo]
    command = [
        config.binary, *model,
        "--host", config.host, "--port", str(config.port),
        "--alias", config.model_id,
        # The context is split between the slots, so each one gets what the
        # config calls one request's worth.
        "-c", str(config.context_size * config.parallel),
        "-np", str(config.parallel),
        "-ngl", str(config.gpu_layers),
        "-fa", config.flash_attn,
        # The model's own chat template, which is where Qwen3's tool calls and
        # its thinking switch live. Without it, `tools` would be ignored.
        "--jinja",
        # Thoughts come back separately as `reasoning_content`, not inside
        # the answer - the shape DeepSeek returns, so nothing above changes.
        "--reasoning-format", "deepseek",
    ]
    flags = {"top_p": "--top-p", "top_k": "--top-k", "min_p": "--min-p",
             "repeat_penalty": "--repeat-penalty"}
    for key, value in config.sampling.items():
        if key not in flags:
            raise ValueError(f"local_llm.json: unknown sampling field {key!r}")
        command += [flags[key], str(value)]
    return command + [str(arg) for arg in config.extra_args]


class LlamaCppClient(LLMClient):
    """The local server, spoken to exactly like DeepSeek, minus the key."""

    provider_name = "Local model (llama.cpp)"

    def __init__(self, config: LocalLLMConfig) -> None:
        self.config = config
        self.base_url = config.base_url + "/v1"
        super().__init__("", timeout=config.request_timeout_seconds)
        # No key: llama-server is started without one, and a bearer of
        # nothing would only be noise in the debug panel.
        self._headers.pop("Authorization", None)

    def translate(self, body: dict) -> dict:
        """The request as llama-server should get it (see the module docstring)."""
        body = dict(body)
        effort = body.pop("reasoning_effort", None)
        if effort is not None:
            body["chat_template_kwargs"] = {"enable_thinking": effort != "none"}
        return body

    def complete(self, body: dict) -> LLMResponse:
        try:
            return super().complete(self.translate(body))
        except LLMClientError as exc:
            if exc.status_code is None and str(exc).startswith("could not reach"):
                raise LLMClientError(
                    f"the local model is not running at {self.config.base_url}. "
                    "It starts with the app (local_llm.json, autostart) - see the "
                    "app's log for llama-server's own messages.",
                    url=exc.url,
                ) from exc
            raise

    def describe_failure(self, http_response: httpx.Response) -> str:
        if http_response.status_code == 503:
            return ("the local model is still loading (HTTP 503) - the first start "
                    "also downloads it. Try again in a moment.")
        return super().describe_failure(http_response)


class RoutingClient:
    """One `complete(body)` for the whole app, sent where `body["model"]` says.

    Every caller in server.py and rag_api.py was handed one client and kept
    it; they still are. This is that client, and it reads the model id off
    each request - so a facts update, a reranker or an invariant check for a
    local chat runs locally too, with no call site knowing there are two.
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


class LocalServer:
    """Start llama-server, watch it come up, stop it - if this app started it.

    The same three rules as day 20's MCP servers: a port that already
    answers is used and left alone; the app does not wait for the model to
    load (that is seconds, and the first time minutes of download); and what
    the server prints goes to the app's log with a prefix.
    """

    def __init__(self, config: LocalLLMConfig, log=print) -> None:
        self.config = config
        self.log = log
        self.process: subprocess.Popen | None = None
        self.state = "stopped"     # stopped | starting | ready | failed | external
        self.detail = ""

    def port_open(self) -> bool:
        import socket
        try:
            with socket.create_connection((self.config.host, self.config.port), timeout=0.3):
                return True
        except OSError:
            return False

    def start(self) -> "LocalServer":
        tag = "[llama  ]"
        if self.port_open():
            self.state, self.detail = "external", "already running - used, and left running"
            self.log(f"{tag} {self.config.base_url} {self.detail}")
            return self
        if shutil.which(self.config.binary) is None:
            self.state = "failed"
            self.detail = f"{self.config.binary!r} not found - brew install llama.cpp"
            self.log(f"{tag} not started: {self.detail}")
            return self
        command = server_command(self.config)
        self.process = subprocess.Popen(
            command, cwd=HERE, text=True, bufsize=1,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.state, self.detail = "starting", f"loading {self.config.model_name}"
        self.log(f"{tag} starting {self.config.base_url} (pid {self.process.pid}): {' '.join(command)}")
        threading.Thread(target=self.pump, daemon=True).start()
        threading.Thread(target=self.wait_ready, daemon=True).start()
        return self

    def pump(self) -> None:
        # llama-server is chatty (one line per tensor, per slot, per request);
        # only warnings, errors and the download progress are worth a line.
        assert self.process is not None and self.process.stdout is not None
        for line in iter(self.process.stdout.readline, ""):
            text = line.rstrip()
            if any(word in text.lower() for word in ("error", " w ", " e ", "download", "listening")):
                self.log(f"[llama  ] {text}")

    def wait_ready(self) -> None:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                self.state = "failed"
                self.detail = f"llama-server exited with {self.process.returncode}"
                self.log(f"[llama  ] {self.detail}")
                return
            try:
                if httpx.get(self.config.base_url + "/health", timeout=2).status_code == 200:
                    self.state, self.detail = "ready", "loaded"
                    self.log(f"[llama  ] ready: {self.config.model_id} = {self.config.model_name}")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        self.state = "failed"
        self.detail = f"not ready after {self.config.startup_timeout_seconds:.0f} s"
        self.log(f"[llama  ] {self.detail}")

    def stop(self, timeout: float = 10.0) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def status(self) -> dict:
        if self.state == "stopped" and self.port_open():
            self.state, self.detail = "external", "already running - started outside the app"
        return {"model_id": self.config.model_id, "model": self.config.model_name,
                "url": self.config.base_url, "state": self.state, "detail": self.detail}
