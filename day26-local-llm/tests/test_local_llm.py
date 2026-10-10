"""Day 26: the local model - its config, its command line, and which client a request goes to.

    uv run python -m unittest discover -s tests -v

No llama-server and no network: the config is read from temporary files,
the command line is only built, and the two clients behind the router are
fakes that remember what they were sent.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import local_llm  # noqa: E402
from llm_client import LLMResponse  # noqa: E402


class Recorder:
    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[dict] = []
        self.redacted_headers = {"X-From": name}

    def complete(self, body: dict) -> LLMResponse:
        self.sent.append(body)
        return LLMResponse(request=body, response={"by": self.name}, url=self.name,
                           status_code=200, elapsed_ms=1.0)

    def close(self) -> None:
        pass


class Config(unittest.TestCase):
    def write(self, data: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(data, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_the_shipped_config_loads(self):
        config = local_llm.load_config()
        self.assertTrue(config.model_id)
        self.assertTrue(config.hf_repo or config.model_path)

    def test_no_file_means_no_local_model(self):
        self.assertFalse(local_llm.load_config(Path("/nonexistent/local_llm.json")).enabled)

    def test_an_unknown_field_is_an_error_not_a_silent_default(self):
        with self.assertRaisesRegex(ValueError, "ctx_size"):
            local_llm.load_config(self.write({"ctx_size": 4096}))

    def test_the_command_line(self):
        config = local_llm.load_config(self.write({
            "model_id": "local-x", "port": 9001, "context_size": 8192, "parallel": 3,
            "hf_repo": "org/model-GGUF:Q4_K_M", "sampling": {"top_k": 20}, "extra_args": ["--mlock"],
        }))
        command = local_llm.server_command(config)
        joined = " ".join(command)
        self.assertIn("-hf org/model-GGUF:Q4_K_M", joined)
        self.assertIn("--alias local-x", joined)
        self.assertIn("--port 9001", joined)
        self.assertIn("-c 24576", joined)          # per request x slots
        self.assertIn("-np 3", joined)
        self.assertIn("--jinja", command)
        self.assertIn("--top-k 20", joined)
        self.assertEqual(command[-1], "--mlock")

    def test_a_local_file_wins_over_the_repo(self):
        config = local_llm.LocalLLMConfig(model_path="/models/a.gguf")
        command = local_llm.server_command(config)
        self.assertIn("-m", command)
        self.assertNotIn("-hf", command)
        self.assertEqual(config.model_name, "a.gguf")

    def test_an_unknown_sampling_field_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "temperature"):
            local_llm.server_command(local_llm.LocalLLMConfig(sampling={"temperature": 0.5}))


class Translation(unittest.TestCase):
    def setUp(self):
        self.client = local_llm.LlamaCppClient(local_llm.LocalLLMConfig(port=9002))

    def tearDown(self):
        self.client.close()

    def test_reasoning_off_switches_thinking_off(self):
        body = self.client.translate({"model": "m", "messages": [], "reasoning_effort": "none"})
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_any_other_effort_leaves_it_on(self):
        for effort in ("low", "high", "max"):
            body = self.client.translate({"model": "m", "reasoning_effort": effort})
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})

    def test_nothing_else_is_touched(self):
        original = {"model": "m", "temperature": 0.3, "response_format": {"type": "json_object"}}
        self.assertEqual(self.client.translate(original), original)

    def test_no_bearer_goes_to_the_local_server(self):
        self.assertNotIn("Authorization", self.client.redacted_headers)

    def test_a_server_that_is_not_there_says_so(self):
        with self.assertRaisesRegex(Exception, "local model is not running"):
            self.client.complete({"model": "m", "messages": []})


class Routing(unittest.TestCase):
    def setUp(self):
        self.remote, self.local = Recorder("remote"), Recorder("local")
        self.router = local_llm.RoutingClient(self.remote, self.local, "local-qwen3-8b")

    def test_the_local_id_goes_local(self):
        self.assertEqual(self.router.complete({"model": "local-qwen3-8b"}).response["by"], "local")
        self.assertEqual(len(self.local.sent), 1)

    def test_everything_else_goes_to_deepseek(self):
        for model in ("deepseek-v4-flash", "deepseek-v4-pro", None):
            self.assertEqual(self.router.complete({"model": model}).response["by"], "remote")
        self.assertEqual(self.local.sent, [])

    def test_without_a_local_model_everything_is_remote(self):
        router = local_llm.RoutingClient(self.remote)
        self.assertEqual(router.complete({"model": "local-qwen3-8b"}).response["by"], "remote")


if __name__ == "__main__":
    unittest.main()
