"""One QA run: the app launched, a model driving it through the scenario, the verdict.

    QaRun          everything about a run - the scenario, the app, the simulator, the
                   model, the steps and checks (`tools.simulator.RunLog`), the verdict,
                   the model's answer, the diagnosis, what it cost - saved as `run.json`
                   next to its screenshots
    execute(run)   the run itself:
                     1. the app launched from scratch - the first step and screenshot
                     2. an agent (`core.agent.Agent`) with the simulator's tools only, calls
                        run one after another, asked to carry out the scenario
                     3. the verdict, from what was recorded rather than what was said
                     4. on a failure or a crash, the code diagnosis (`qa.diagnose`)
    verdict(...)   passed / failed / crashed / blocked / unchecked / incomplete

Where AI is in it: the model turns the scenario's words into actions on screens
it reads as text, finds its own way to each step (scrolling, going back,
looking again), decides whether each expectation holds and records the proof,
and - when something fails - reads the project's code to say why.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.agent import Agent, AgentConfig
from qa import crash as crash_module
from qa import simctl
from qa.config import QaConfig
from tools.registry import ToolRegistry
from tools.simulator import BridgeClient, BridgeError, RunLog, SimulatorProvider

SYSTEM = """You are a QA engineer testing an iOS app in the Simulator. You carry out a test scenario a person wrote, with your tools, and check every expectation in it.

App under test: {app} ({bundle_id}), on {device}.

How you see the app: every action answers with the screen it led to, as text - the elements with their refs ([e7]), types, labels and values, nested under their containers, and how much more there is above or below. Refs belong to one screen: always use refs from the latest screen. You see no images or colours - only what the accessibility tree says.

How to work:
- Do the scenario's steps in order. Find each element by its label. When it is not on the screen, scroll, go back (the navigation bar's back button), or look for another way - the way a person would. A list row with an arrow usually opens or expands when tapped.
- One action per call; read its answer before the next.
- For every expectation in the scenario - something that should be on the screen, a value, something that should disappear - call `check` as soon as the screen shows the answer, with the refs that prove it (or the texts that must be absent). Never record a check from an earlier screen.
- A passed run is turned into a regression test that repeats your checks, so make the evidence what will be true on every run: the switch, the title, the row that was searched for. When the expectation is only that a list has rows and the rows come from live or random data, pass a few of them as `examples` instead of `evidence`.
- A failed check does not end the run: record it and carry on with the rest of the scenario if you can.
- If the app crashes, record the expectation you were checking as failed and finish.
- When a step cannot be done (no such screen, button or field after a reasonable search), finish with `blocked` and say what was missing.
- At the end call `finish` - `passed` only if every check passed - and then answer in two or three sentences, in the language of the scenario.
- Do only what the scenario asks: no extra steps, and nothing changed in the app that the scenario does not change."""

START = """Scenario{title}:
{text}

The app has just been launched from scratch. Its screen:

{screen}"""

VERDICTS = ("passed", "failed", "crashed", "blocked", "unchecked", "incomplete", "stopped", "error")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


@dataclass
class QaRun:
    id: str
    scenario: dict
    app: dict
    device: dict
    model: str
    folder: Path
    status: str = "starting"
    verdict: str | None = None
    answer: str = ""
    error: str = ""
    diagnosis: dict | None = None
    usage: dict = field(default_factory=dict)
    tool_calls: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log: RunLog = field(default_factory=RunLog)
    test: dict | None = None
    events: list = field(default_factory=list)

    def note(self, text: str) -> None:
        self.events.append({"at": round(time.time() - self.started_at, 1), "text": text})

    def to_dict(self) -> dict:
        data = {
            "id": self.id, "scenario": self.scenario, "app": self.app, "device": self.device,
            "model": self.model, "status": self.status, "verdict": self.verdict, "answer": self.answer,
            "error": self.error, "diagnosis": self.diagnosis, "usage": self.usage,
            "tool_calls": self.tool_calls, "started_at": self.started_at, "finished_at": self.finished_at,
            "test": self.test, "events": list(self.events),
        }
        data.update(self.log.snapshot())
        return data

    def summary(self) -> dict:
        snapshot = self.log.snapshot()
        return {"id": self.id, "title": self.scenario.get("title") or self.scenario.get("text", "")[:60],
                "scenario_id": self.scenario.get("id"), "app": self.app.get("name"),
                "status": self.status, "verdict": self.verdict, "started_at": self.started_at,
                "steps": len(snapshot["steps"]), "checks": len(snapshot["checks"]),
                "failed_checks": sum(1 for c in snapshot["checks"] if not c["passed"]), "test": self.test}

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "run.json").write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1),
                                             encoding="utf-8")

    @classmethod
    def load(cls, folder: Path) -> "QaRun":
        data = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        run = cls(id=data["id"], scenario=data.get("scenario") or {}, app=data.get("app") or {},
                  device=data.get("device") or {}, model=data.get("model") or "", folder=folder,
                  status=data.get("status") or "done", verdict=data.get("verdict"),
                  answer=data.get("answer") or "", error=data.get("error") or "",
                  diagnosis=data.get("diagnosis"), usage=data.get("usage") or {},
                  tool_calls=data.get("tool_calls") or 0, started_at=data.get("started_at") or 0,
                  finished_at=data.get("finished_at"), test=data.get("test"), events=data.get("events") or [])
        run.log.steps = data.get("steps") or []
        run.log.checks = data.get("checks") or []
        run.log.finish = data.get("finish")
        run.log.crash = data.get("crash")
        if run.status not in ("done",):
            run.status, run.verdict = "done", run.verdict or "incomplete"  # the process ended mid-run
        return run


def verdict(snapshot: dict, *, cancelled: bool = False) -> str:
    """The run's outcome from what was recorded: a crash, the checks, then the model's own finish."""
    checks = snapshot.get("checks") or []
    finish = snapshot.get("finish") or {}
    if snapshot.get("crash"):
        return "crashed"
    if any(not c.get("passed") for c in checks):
        return "failed"
    if cancelled:
        return "stopped"
    status = finish.get("status")
    if status in ("failed", "blocked"):
        return status
    if status == "passed":
        return "passed" if checks else "unchecked"
    return "incomplete"


def add_usage(total: dict, usage: dict | None) -> dict:
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)):
            total[key] = total.get(key, 0) + value
    return total


def execute(run: QaRun, *, llm, config: QaConfig, client: BridgeClient, udid: str,
            project: Path | None = None, docs: Callable[[str, int], str] | None = None,
            crash_folder: Path | None = None, log: Callable[[str], None] = print) -> QaRun:
    """Carry out `run` on the simulator `udid` through a bridge that is already serving."""
    bundle_id = run.app["bundle_id"]

    def screenshot(name: str) -> str | None:
        path = simctl.screenshot(udid, run.folder / f"{name}.png")
        return path.name if path else None

    provider = SimulatorProvider(
        client, bundle_id, run.log, screenshot=screenshot,
        crash_lookup=lambda bid, since: crash_module.find_report(bid, since, crash_folder),
        app_name=run.app.get("name", ""), settle_seconds=config.settle_seconds,
        max_actions=config.max_actions, max_screen_chars=config.max_screen_chars)
    run.status = "running"
    run.note(f"Launching {run.app.get('name') or bundle_id}")
    try:
        first = provider.start(fresh=True)
    except BridgeError as exc:
        return _end(run, error=f"The app could not be launched: {exc}")
    system = SYSTEM.format(app=run.app.get("name") or bundle_id, bundle_id=bundle_id,
                           device=f"{run.device.get('name', '')} ({run.device.get('runtime', '')})")
    agent = Agent(llm, AgentConfig(model=config.model, system_prompt=system,
                                   reasoning_effort=config.reasoning_effort, temperature=config.temperature),
                  tools=ToolRegistry([provider]), max_tool_rounds=config.max_tool_rounds,
                  max_tool_calls=config.max_tool_calls, parallel_calls=False)
    title = run.scenario.get("title")
    message = START.format(title=f" ({title})" if title else "", text=run.scenario.get("text", ""), screen=first)
    run.note(f"The model ({config.model}) is carrying out the scenario")
    reply = agent.ask(message)
    run.answer = reply.answer if reply.error is None else ""
    run.tool_calls = len(reply.tool_calls)
    add_usage(run.usage, reply.usage)
    if reply.error is not None:
        run.error = reply.error
    run.verdict = verdict(run.log.snapshot(), cancelled=run.log.cancelled)
    if run.error and run.verdict == "incomplete":
        run.verdict = "error"
    run.note(f"Verdict: {run.verdict}")

    if run.verdict in ("failed", "crashed") and config.diagnose and project is not None and not run.log.cancelled:
        from qa.diagnose import diagnose  # imported here: only a failed run needs the file tools

        run.status = "diagnosing"
        run.note(f"Looking for the cause in the code of {project.name}")
        try:
            run.diagnosis = diagnose(run, llm=llm, config=config, project=project, docs=docs)
            add_usage(run.usage, run.diagnosis.get("usage"))
        except Exception as exc:  # noqa: BLE001 - a diagnosis that fails leaves the run's result standing
            run.diagnosis = {"text": "", "error": f"The diagnosis failed: {exc}"}
    return _end(run)


def _end(run: QaRun, error: str = "") -> QaRun:
    if error:
        run.error = error
        run.verdict = "error"
    run.status = "done"
    run.finished_at = time.time()
    run.save()
    return run
