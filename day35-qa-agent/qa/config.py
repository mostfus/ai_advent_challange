"""`qa_agent.json`: what a QA run uses - the model, the simulator, the budget, the bridge.

    model, reasoning_effort, temperature   the model that drives the app
    device                                 the simulator by name ("iPhone 17") or UDID
    bridge_port                            where the XCUITest bridge listens, on 127.0.0.1
    show_simulator                         open Simulator.app so the person can watch (not in CI)
    max_actions                            taps, typing, scrolls and relaunches one run may do
    max_tool_rounds, max_tool_calls        the agent loop's budget (looks and checks count too)
    settle_seconds                         the pause after an action before the screen is read
    max_screen_chars                       how much of one screen the model is shown
    diagnose                               after a failure, look for the cause in the project's code
    diagnosis_tool_rounds/_calls           that diagnosis's budget
    build_timeout_seconds                  a scheme's build for the simulator
    bridge_start_seconds                   how long the bridge may take to come up
    models                                 per model id, any of the above overridden -
                                           a small local model gets a shorter screen

`QaConfig.load()` raises `ValueError` naming the file for a bad value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "qa_agent.json"

REASONING = ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max")
INTS = ("bridge_port", "max_actions", "max_tool_rounds", "max_tool_calls", "max_screen_chars",
        "diagnosis_tool_rounds", "diagnosis_tool_calls", "build_timeout_seconds", "bridge_start_seconds")


@dataclass(frozen=True)
class QaConfig:
    model: str = "deepseek-v4-flash"
    reasoning_effort: str = "low"
    temperature: float | None = 0.2
    device: str = "iPhone 17"
    bridge_port: int = 22087
    show_simulator: bool = True
    max_actions: int = 40
    max_tool_rounds: int = 70
    max_tool_calls: int = 90
    settle_seconds: float = 0.4
    max_screen_chars: int = 6000
    diagnose: bool = True
    diagnosis_tool_rounds: int = 16
    diagnosis_tool_calls: int = 30
    build_timeout_seconds: int = 1200
    bridge_start_seconds: int = 180
    models: dict = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | str = CONFIG_PATH) -> "QaConfig":
        """The file, or the defaults when there is none."""
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path.name}: not valid JSON ({exc})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected an object")
        known = {f.name for f in fields(cls)} - {"path"}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"{path.name}: unknown setting(s) {', '.join(unknown)}")
        models = raw.get("models") or {}
        if not isinstance(models, dict) or not all(isinstance(v, dict) for v in models.values()):
            raise ValueError(f"{path.name}: models maps a model id to an object of settings")
        for model, overrides in models.items():
            bad = sorted(set(overrides) - known - {"models"})
            if bad or "models" in overrides:
                raise ValueError(f"{path.name}: models.{model}: unknown setting(s) {', '.join(bad or ['models'])}")
            cls._checked({**raw, **overrides}, f"{path.name}: models.{model}")
        values = cls._checked(raw, path.name)
        return cls(**values, models=models, path=path)

    @classmethod
    def _checked(cls, raw: dict, where: str) -> dict:
        values = {k: v for k, v in raw.items() if k != "models"}
        for key in INTS:
            if key in values:
                try:
                    number = int(values[key])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{where}: {key} must be a positive whole number") from exc
                if number < 1:
                    raise ValueError(f"{where}: {key} must be a positive whole number")
                values[key] = number
        if "bridge_port" in values and not 1024 <= values["bridge_port"] <= 65535:
            raise ValueError(f"{where}: bridge_port must be between 1024 and 65535")
        if "settle_seconds" in values:
            try:
                values["settle_seconds"] = float(values["settle_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where}: settle_seconds must be a number") from exc
            if not 0 <= values["settle_seconds"] <= 10:
                raise ValueError(f"{where}: settle_seconds must be between 0 and 10")
        if values.get("temperature") is not None:
            try:
                values["temperature"] = float(values["temperature"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where}: temperature must be a number or null") from exc
        if "reasoning_effort" in values and values["reasoning_effort"] not in REASONING:
            raise ValueError(f"{where}: reasoning_effort is one of {', '.join(REASONING)}")
        for key in ("model", "device"):
            if key in values and (not isinstance(values[key], str) or not values[key].strip()):
                raise ValueError(f"{where}: {key} must be a non-empty string")
        for key in ("show_simulator", "diagnose"):
            if key in values and not isinstance(values[key], bool):
                raise ValueError(f"{where}: {key} must be true or false")
        return values

    def for_model(self, model: str | None) -> "QaConfig":
        """These settings for `model`, with its overrides from `models` applied."""
        if not model:
            return self
        overrides = {k: v for k, v in (self.models.get(model) or {}).items()}
        return replace(self, model=model, **overrides)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("path", "models")}
