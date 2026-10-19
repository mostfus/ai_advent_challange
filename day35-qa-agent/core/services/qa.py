"""Day 35: the QA tab's service - the simulator, the app under test, runs, the suite and its tests.

    report()              the config, the simulators, the open project's Xcode projects, the
                          suite, the job running now and the last runs - what the tab draws
    apps(udid)            the apps installed on a simulator
    schemes(container)    the schemes of one of the open project's Xcode projects
    build(...)            job: boot the simulator, build the scheme for it, install the app
    start_run(...)        job: boot, start the bridge, carry out the scenario (`qa.runner`)
    run_tests(...)        job: the suite's generated XCUITests, without a model
    stop()                stop the job: a run ends at its next tool call, a build is killed
    run(id), run_file(id, name), run_report(id)
    save_scenario / update_scenario / delete_scenario
    make_test(run_id)     a passed run -> an XCUITest in the suite
    shutdown()            the bridge stopped, with the app

One job at a time: every job drives the same simulator through the same
bridge. The jobs run in a thread of their own and the tab polls `report()`
and `run(id)`; a run is readable while it happens - its steps appear one by
one. The bridge is kept running between runs (starting it is the slow part)
and stopped when the app stops.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from core import agent as agent_module
from core.errors import Conflict, Invalid, NotFound
from qa import simctl, xcode, xcuitest
from qa.bridge import Bridge
from qa.config import APP_DIR, QaConfig
from qa.report import run_markdown
from qa.runner import QaRun, execute, new_id
from qa.simctl import SimulatorError
from qa.suite import SUITE_PATH, Suite

if TYPE_CHECKING:
    from core.runtime import AgentRuntime

MAX_RUNS_LISTED = 40
MAX_LOG_LINES = 400


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "running"
    log: list = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    run_id: str | None = None
    cancelled: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def say(self, line: str) -> None:
        self.log.append(line)
        del self.log[:-MAX_LOG_LINES]

    def snapshot(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "log": list(self.log[-60:]), "result": self.result, "error": self.error, "run_id": self.run_id,
                "started_at": self.started_at, "finished_at": self.finished_at}


class QaService:
    def __init__(self, rt: "AgentRuntime", config: QaConfig | None = None, data_dir: Path | None = None,
                 suite_path: Path | None = None) -> None:
        self.rt = rt
        self.config = config if config is not None else QaConfig()
        self.dir = Path(data_dir) if data_dir is not None else APP_DIR / "data" / "qa"
        self.suite_path = Path(suite_path) if suite_path is not None else SUITE_PATH
        self.bridge = Bridge(self.config.bridge_port, self.dir / "bridge-build",
                             tests_dir=self.suite_path.parent / "tests", log=self._log,
                             start_seconds=self.config.bridge_start_seconds)
        self.job: Job | None = None
        self.runs: dict[str, QaRun] = {}
        self.lock = threading.Lock()
        self._devices: tuple[float, list] | None = None

    # -- reading ---------------------------------------------------------------------

    def _log(self, line: str) -> None:
        print(f"[qa] {line}", flush=True)
        job = self.job
        if job is not None and job.status == "running":
            job.say(line)

    def devices(self, fresh: bool = False) -> list[dict]:
        cached = self._devices
        if not fresh and cached and time.time() - cached[0] < 20:
            return cached[1]
        try:
            found = simctl.devices()
        except SimulatorError:
            found = []
        self._devices = (time.time(), found)
        return found

    def suite(self) -> Suite:
        try:
            return Suite.load(self.suite_path)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc

    def report(self) -> dict:
        project = self.rt.project.current()
        devices = self.devices()
        try:
            default = simctl.find_device(self.config.device, devices)["udid"] if devices else None
        except SimulatorError:
            default = devices[0]["udid"] if devices else None
        models = list(agent_module.MODELS)
        return {
            "config": self.config.to_dict(),
            "config_file": self.config.path.name if self.config.path else None,
            "models": models,
            "devices": devices,
            "default_device": default,
            "project": {**project.describe(), "containers": xcode.find_containers(project.path)},
            "suite": self.suite().to_dict(),
            "job": self.job.snapshot() if self.job else None,
            "bridge": {"running": self.bridge.process is not None and self.bridge.process.poll() is None,
                       "udid": self.bridge.udid, "port": self.bridge.port},
            "runs": [run.summary() for run in self._recent_runs()],
        }

    def _recent_runs(self) -> list[QaRun]:
        folder = self.dir / "runs"
        if folder.is_dir():
            for path in sorted(folder.iterdir(), reverse=True)[:MAX_RUNS_LISTED]:
                if path.name not in self.runs and (path / "run.json").exists():
                    try:
                        self.runs[path.name] = QaRun.load(path)
                    except (OSError, ValueError, KeyError):
                        continue
        return sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True)[:MAX_RUNS_LISTED]

    def apps(self, udid: str) -> list[dict]:
        self._device(udid, boot=False)
        try:
            return simctl.apps(udid)
        except SimulatorError as exc:
            raise Invalid(f"The simulator's apps could not be read: {exc} (is it booted?)") from exc

    def _container(self, relative: str) -> Path:
        project = self.rt.project.current()
        known = {c["path"] for c in xcode.find_containers(project.path)}
        if relative not in known:
            raise Invalid(f"{relative!r} is not an Xcode project in {project.name}")
        return project.path / relative

    def schemes(self, container: str) -> list[str]:
        try:
            return xcode.schemes(self._container(container))
        except SimulatorError as exc:
            raise Invalid(str(exc)) from exc

    def run(self, run_id: str) -> dict:
        return self._run(run_id).to_dict()

    def _run(self, run_id: str) -> QaRun:
        run = self.runs.get(run_id)
        if run is None:
            folder = self.dir / "runs" / run_id
            if not run_id.replace("-", "").isalnum() or not (folder / "run.json").exists():
                raise NotFound(f"No QA run {run_id!r}")
            run = self.runs[run_id] = QaRun.load(folder)
        return run

    def run_file(self, run_id: str, name: str) -> Path:
        run = self._run(run_id)
        path = (run.folder / name).resolve()
        if path.parent != run.folder.resolve() or path.suffix != ".png" or not path.is_file():
            raise NotFound(f"No file {name!r} in run {run_id}")
        return path

    def run_report(self, run_id: str) -> str:
        return run_markdown(self.run(run_id))

    # -- jobs --------------------------------------------------------------------------

    def _start_job(self, kind: str, title: str, work) -> Job:
        with self.lock:
            if self.job is not None and self.job.status == "running":
                raise Conflict(f"{self.job.title} is still running - wait for it or stop it")
            job = self.job = Job(id=new_id(), kind=kind, title=title)

        def target() -> None:
            try:
                job.result = work(job)
                job.status = "stopped" if job.cancelled else "done"
            except (SimulatorError, Invalid, Conflict) as exc:
                job.error = getattr(exc, "detail", None) or str(exc)
                job.status = "stopped" if job.cancelled else "failed"
                job.say(f"Error: {job.error}")
            except Exception as exc:  # noqa: BLE001 - a job's failure is shown, never raised into a thread
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
                job.say(f"Error: {job.error}")
            finally:
                job.finished_at = time.time()

        threading.Thread(target=target, name=f"qa-{kind}", daemon=True).start()
        return job

    def stop(self) -> dict:
        job = self.job
        if job is None or job.status != "running":
            raise Conflict("Nothing is running")
        job.cancelled = True
        job.say("Stopping...")
        if job.run_id and job.run_id in self.runs:
            self.runs[job.run_id].log.cancelled = True
        return job.snapshot()

    def _device(self, udid: str, *, boot: bool = True, job: Job | None = None) -> dict:
        devices = self.devices()
        match = next((d for d in devices if d["udid"] == udid), None)
        if match is None:
            devices = self.devices(fresh=True)
            match = next((d for d in devices if d["udid"] == udid), None)
        if match is None:
            raise Invalid(f"No simulator with UDID {udid}")
        if boot and match["state"] != "Booted":
            if job:
                job.say(f"Booting {match['name']} ({match['runtime']})")
            simctl.boot(udid)
            self._devices = None
            match = {**match, "state": "Booted"}
        if boot and self.config.show_simulator:
            simctl.show(udid)
        return match

    def build(self, udid: str, container: str, scheme: str) -> dict:
        path = self._container(container)
        if not scheme:
            raise Invalid("Pick a scheme")

        def work(job: Job) -> dict:
            self._device(udid, job=job)
            derived = self.dir / "derived" / xcuitest.class_name(path.parent.name + "-" + path.stem)
            info = xcode.build(path, scheme, udid, derived, log=job.say, timeout=self.config.build_timeout_seconds,
                               cancelled=lambda: job.cancelled)
            job.say(f"Installing {info['name']} on the simulator")
            simctl.install(udid, info["path"])
            job.say(f"Ready: {info['name']} ({info['bundle_id']})")
            return {"app": info}

        return self._start_job("build", f"Building {scheme}", work).snapshot()

    def start_run(self, *, udid: str, bundle_id: str, text: str, title: str = "", scenario_id: str | None = None,
                  model: str | None = None, diagnose: bool = True) -> dict:
        text = (text or "").strip()
        if not text:
            raise Invalid("Write the scenario first")
        if not bundle_id:
            raise Invalid("Pick the app to test")
        model = model or self.config.model
        if model not in agent_module.MODELS:
            raise Invalid(f"Unknown model {model!r}")
        config = self.config.for_model(model)
        if scenario_id:
            scenario = self.suite().get(scenario_id)
            if scenario is None:
                raise NotFound(f"No scenario {scenario_id!r} in the suite")
        project = self.rt.project.current()
        run_id = new_id()
        run = QaRun(id=run_id, scenario={"id": scenario_id, "title": (title or "").strip(), "text": text},
                    app={"bundle_id": bundle_id, "name": bundle_id}, device={"udid": udid}, model=model,
                    folder=self.dir / "runs" / run_id)

        def work(job: Job) -> dict:
            job.run_id = run_id
            self.runs[run_id] = run
            try:
                device = self._device(udid, job=job)
                run.device = device
                installed = {a["bundle_id"]: a for a in simctl.apps(udid)}
                if bundle_id not in installed:
                    raise Invalid(f"{bundle_id} is not installed on {device['name']} - build it, or pick another app")
                run.app = {"bundle_id": bundle_id, "name": installed[bundle_id]["name"]}
                run.note("Starting the simulator bridge")
                client = self.bridge.start(udid, cancelled=lambda: job.cancelled)
                docs = self.rt.project.search_docs if self.rt.index.built() is not None else None
                execute(run, llm=self.rt.llm, config=config, client=client, udid=udid,
                        project=project.path if diagnose else None, docs=docs, log=job.say)
            except (SimulatorError, Invalid) as exc:
                run.error = "" if job.cancelled else (getattr(exc, "detail", None) or str(exc))
                run.verdict = "stopped" if job.cancelled else "error"
                run.status, run.finished_at = "done", time.time()
                run.save()
                raise
            return {"run": run.summary()}

        job = self._start_job("run", f"Running: {run.scenario['title'] or text[:40]}", work)
        job.run_id = run_id
        self.runs[run_id] = run
        return {"job": job.snapshot(), "run": run.summary()}

    def run_tests(self, udid: str, scenario_ids: list[str] | None = None) -> dict:
        suite = self.suite()
        chosen = [s for s in suite.scenarios if s.get("test") and (not scenario_ids or s["id"] in scenario_ids)]
        if not chosen:
            raise Invalid("No generated tests to run: make one from a passed run first")
        classes = [s["test"]["class"] for s in chosen]

        def work(job: Job) -> dict:
            self._device(udid, job=job)
            result = self.bridge.run_tests(udid, classes)
            fresh = self.suite()
            by_class = {t["class"]: t for t in result["tests"]}
            for scenario in fresh.scenarios:
                test = scenario.get("test") or {}
                outcome = by_class.get(test.get("class"))
                if outcome:
                    failure = outcome["failures"][0]["message"] if outcome["failures"] else ""
                    test["last"] = {"status": outcome["status"], "seconds": outcome["seconds"], "failure": failure,
                                    "at": time.strftime("%Y-%m-%d %H:%M")}
            fresh.save()
            job.say("All passed" if result["passed"] else "Some tests failed")
            return result

        return self._start_job("tests", f"Running {len(classes)} XCUITest(s)", work).snapshot()

    # -- the suite -----------------------------------------------------------------------

    def save_scenario(self, title: str, text: str, bundle_id: str = "", app_name: str = "") -> dict:
        suite = self.suite()
        try:
            scenario = suite.add(title, text, bundle_id, app_name)
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        return {"scenario": scenario, "suite": suite.to_dict()}

    def update_scenario(self, scenario_id: str, title: str, text: str) -> dict:
        suite = self.suite()
        try:
            scenario = suite.update(scenario_id, title, text)
        except KeyError as exc:
            raise NotFound(f"No scenario {scenario_id!r}") from exc
        except ValueError as exc:
            raise Invalid(str(exc)) from exc
        return {"scenario": scenario, "suite": suite.to_dict()}

    def delete_scenario(self, scenario_id: str) -> dict:
        suite = self.suite()
        try:
            suite.delete(scenario_id)
        except KeyError as exc:
            raise NotFound(f"No scenario {scenario_id!r}") from exc
        return {"suite": suite.to_dict()}

    def make_test(self, run_id: str) -> dict:
        """A passed run as an XCUITest in the suite - the run's scenario added to the suite if it was not in it."""
        run = self._run(run_id)
        if run.status != "done" or run.verdict != "passed":
            raise Conflict("Only a passed run can become a test")
        suite = self.suite()
        scenario_id = run.scenario.get("id")
        if not scenario_id or suite.get(scenario_id) is None:
            scenario = suite.add(run.scenario.get("title") or "", run.scenario.get("text") or "",
                                 run.app.get("bundle_id", ""), run.app.get("name", ""))
            scenario_id = scenario["id"]
            run.scenario["id"] = scenario_id
        name = xcuitest.class_name(suite.get(scenario_id)["title"])
        source = xcuitest.generate(run.to_dict(), name)
        scenario = suite.attach_test(scenario_id, name, source, run.id)
        run.test = {"scenario_id": scenario_id, "class": scenario["test"]["class"], "file": scenario["test"]["file"]}
        run.save()
        return {"scenario": scenario, "source": source, "suite": suite.to_dict()}

    def test_source(self, scenario_id: str) -> str:
        scenario = self.suite().get(scenario_id)
        if scenario is None or not scenario.get("test"):
            raise NotFound(f"Scenario {scenario_id!r} has no generated test")
        path = self.suite_path.parent / scenario["test"]["file"]
        if not path.is_file():
            raise NotFound(f"{scenario['test']['file']} is missing")
        return path.read_text(encoding="utf-8")

    def shutdown(self) -> None:
        job = self.job
        if job is not None and job.status == "running":
            job.cancelled = True
            if job.run_id in self.runs:
                self.runs[job.run_id].log.cancelled = True
        self.bridge.stop()
