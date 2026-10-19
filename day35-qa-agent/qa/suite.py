"""The QA suite (`qa_suite/`): the scenarios worth keeping, and the XCUITests made from them.

    qa_suite/suite.json
        app        what CI builds: the repository, the Xcode project or workspace in it,
                   the scheme, and the bundle id the build installs
        device     the simulator CI runs on
        scenarios  each: id, title, the scenario's text, the app's bundle id, and - once a
                   run of it passed and was turned into one - its generated test
    qa_suite/tests/<Class>.swift
        the generated XCUITests; the bridge's UI-test target compiles this folder

It is part of the repository, not of `data/`: the CI job on the runner reads the
same suite the QA tab writes. `Suite.load()` raises `ValueError` naming the
file for a bad one.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from qa.config import APP_DIR
from qa.xcuitest import words

SUITE_DIR = APP_DIR / "qa_suite"
SUITE_PATH = SUITE_DIR / "suite.json"
MAX_TEXT = 4000
_lock = threading.Lock()


class Suite:
    def __init__(self, path: Path = SUITE_PATH, app: dict | None = None, device: str | None = None,
                 scenarios: list[dict] | None = None) -> None:
        self.path = Path(path)
        self.app = app or {}
        self.device = device
        self.scenarios = scenarios or []

    @property
    def tests_dir(self) -> Path:
        return self.path.parent / "tests"

    @classmethod
    def load(cls, path: Path | str = SUITE_PATH) -> "Suite":
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path.name}: not valid JSON ({exc})") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected an object")
        app = raw.get("app") or {}
        if not isinstance(app, dict):
            raise ValueError(f"{path.name}: app must be an object")
        scenarios = raw.get("scenarios") or []
        if not isinstance(scenarios, list):
            raise ValueError(f"{path.name}: scenarios must be a list")
        seen = set()
        for entry in scenarios:
            if not isinstance(entry, dict) or not entry.get("id") or not str(entry.get("text") or "").strip():
                raise ValueError(f"{path.name}: every scenario needs an id and a text")
            if entry["id"] in seen:
                raise ValueError(f"{path.name}: scenario id {entry['id']!r} is used twice")
            seen.add(entry["id"])
        return cls(path, app, raw.get("device"), scenarios)

    def save(self) -> None:
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"app": self.app, "device": self.device, "scenarios": self.scenarios}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def to_dict(self) -> dict:
        return {"path": str(self.path.relative_to(APP_DIR)) if self.path.is_relative_to(APP_DIR) else str(self.path),
                "app": self.app, "device": self.device, "scenarios": self.scenarios}

    def get(self, scenario_id: str) -> dict | None:
        return next((s for s in self.scenarios if s["id"] == scenario_id), None)

    def _new_id(self, title: str) -> str:
        base = "-".join(words(title)[:6]) or "scenario"
        candidate, n = base, 2
        while self.get(candidate):
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    @staticmethod
    def _clean(title: str, text: str) -> tuple[str, str]:
        title, text = " ".join(str(title or "").split())[:120], str(text or "").strip()
        if not text:
            raise ValueError("The scenario is empty")
        if len(text) > MAX_TEXT:
            raise ValueError(f"The scenario is longer than {MAX_TEXT} characters")
        return title or text.splitlines()[0][:60], text

    def add(self, title: str, text: str, bundle_id: str = "", app_name: str = "") -> dict:
        title, text = self._clean(title, text)
        scenario = {"id": self._new_id(title), "title": title, "text": text, "bundle_id": bundle_id,
                    "app_name": app_name, "test": None, "created_at": time.strftime("%Y-%m-%d %H:%M")}
        self.scenarios.append(scenario)
        self.save()
        return scenario

    def update(self, scenario_id: str, title: str, text: str) -> dict:
        scenario = self.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        scenario["title"], scenario["text"] = self._clean(title, text)
        self.save()
        return scenario

    def delete(self, scenario_id: str) -> None:
        scenario = self.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        test = scenario.get("test") or {}
        if test.get("file"):
            (self.path.parent / test["file"]).unlink(missing_ok=True)
        self.scenarios.remove(scenario)
        self.save()

    def attach_test(self, scenario_id: str, class_name: str, source: str, run_id: str) -> dict:
        scenario = self.get(scenario_id)
        if scenario is None:
            raise KeyError(scenario_id)
        taken = {(s.get("test") or {}).get("class") for s in self.scenarios if s is not scenario}
        name, n = class_name, 2
        while name in taken:
            name, n = f"{class_name}{n}", n + 1
        source = source.replace(f"final class {class_name}:", f"final class {name}:")
        old = (scenario.get("test") or {}).get("file")
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        file = self.tests_dir / f"{name}.swift"
        file.write_text(source, encoding="utf-8")
        if old and old != f"tests/{file.name}":
            (self.path.parent / old).unlink(missing_ok=True)
        scenario["test"] = {"class": name, "file": f"tests/{file.name}", "from_run": run_id,
                            "generated_at": time.strftime("%Y-%m-%d %H:%M")}
        self.save()
        return scenario
