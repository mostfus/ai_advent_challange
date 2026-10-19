"""The XCUITest bridge's life: generated, built, started on a simulator, stopped - and the
generated scenario tests run with the same build.

    Bridge.ensure_built()    `xcodegen generate` (QA_TESTS_DIR: the suite in use) +
                             `xcodebuild build-for-testing`, only when a source changed -
                             the bridge's, or a generated test's
    Bridge.start(udid)       `xcodebuild test-without-building` of `BridgeTests.testServe`,
                             in the background, waited for until `/health` answers
    Bridge.stop()            `/shutdown`, and the process waited for (killed if it will not go)
    Bridge.run_tests(udid, classes)
                             the generated XCUITests, without a model - each class's result
                             and its failures, read from xcodebuild's output

The bridge and the tests are the same runner app, so the tests stop the bridge
first; the next run starts it again. Everything is built into `build_dir`
(`data/qa/bridge-build`), never into the bridge's folder.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from qa import simctl
from qa.config import APP_DIR
from qa.simctl import SimulatorError
from tools.simulator import BridgeClient

BRIDGE_DIR = APP_DIR / "bridge"
TESTS_DIR = APP_DIR / "qa_suite" / "tests"
SCHEME = "QABridge"
SERVE_TEST = "QABridgeUITests/BridgeTests/testServe"
TEST_BUNDLE = "QABridgeUITests"

CASE = re.compile(r"Test Case '-\[(?P<bundle>\w+)\.(?P<cls>\w+) (?P<test>\w+)\]' (?P<status>passed|failed|skipped)"
                  r"(?: \((?P<seconds>[\d.]+) seconds\))?")
FAILURE = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+): error: -\[\w+\.(?P<cls>\w+) (?P<test>\w+)\] : (?P<message>.*)$")


class Bridge:
    def __init__(self, port: int, build_dir: Path | str, *, bridge_dir: Path = BRIDGE_DIR,
                 tests_dir: Path = TESTS_DIR, log: Callable[[str], None] = print,
                 start_seconds: float = 180) -> None:
        self.port = port
        self.build_dir = Path(build_dir)
        self.bridge_dir = Path(bridge_dir)
        self.tests_dir = Path(tests_dir)
        self.log = log
        self.start_seconds = start_seconds
        self.client = BridgeClient(port)
        self.process: subprocess.Popen | None = None
        self.udid: str | None = None
        self.lock = threading.RLock()

    # -- building ----------------------------------------------------------------

    def sources(self) -> list[Path]:
        files = [self.bridge_dir / "project.yml"]
        for folder in (self.bridge_dir / "Host", self.bridge_dir / "UITests", self.tests_dir):
            if folder.is_dir():
                files.extend(sorted(folder.rglob("*.swift")))
        return files

    def fingerprint(self) -> str:
        digest = hashlib.sha1(str(self.tests_dir.resolve()).encode())
        for path in self.sources():
            digest.update(path.name.encode())
            try:
                digest.update(path.read_bytes())
            except OSError:
                pass
        return digest.hexdigest()

    def xctestrun(self) -> Path | None:
        found = sorted((self.build_dir / "Build" / "Products").glob("*.xctestrun"), key=lambda p: p.stat().st_mtime)
        return found[-1] if found else None

    def ensure_built(self) -> Path:
        """The bridge's `.xctestrun`, building it first when a source changed."""
        with self.lock:
            stamp = self.build_dir / "fingerprint"
            current = self.fingerprint()
            existing = self.xctestrun()
            if existing is not None and stamp.exists() and stamp.read_text().strip() == current:
                return existing
            if shutil.which("xcodegen") is None:
                raise SimulatorError("xcodegen is needed to build the bridge: brew install xcodegen")
            self.log("Building the simulator bridge (XCUITest) - once, and again only when it changes")
            self.tests_dir.mkdir(parents=True, exist_ok=True)
            generated = subprocess.run(["xcodegen", "generate", "--spec", "project.yml", "--quiet"],
                                       cwd=self.bridge_dir, capture_output=True, text=True, timeout=120,
                                       env={**os.environ, "QA_TESTS_DIR": str(self.tests_dir.resolve())})
            if generated.returncode != 0:
                raise SimulatorError(f"xcodegen failed: {(generated.stderr or generated.stdout).strip()[-500:]}")
            for old in (self.build_dir / "Build" / "Products").glob("*.xctestrun"):
                old.unlink()
            result = simctl.run(["xcodebuild", "build-for-testing", "-project", str(self.bridge_dir / "QABridge.xcodeproj"),
                                 "-scheme", SCHEME, "-destination", "generic/platform=iOS Simulator",
                                 "-derivedDataPath", str(self.build_dir)], timeout=900, check=False)
            if result.returncode != 0:
                errors = [line for line in (result.stdout + result.stderr).splitlines() if "error:" in line]
                raise SimulatorError("The bridge did not build:\n" + "\n".join(errors[:10] or result.stdout.splitlines()[-10:]))
            built = self.xctestrun()
            if built is None:
                raise SimulatorError("The bridge built, but no .xctestrun was written")
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(current)
            return built

    # -- serving -----------------------------------------------------------------

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None and self.client.health()

    def start(self, udid: str, cancelled: Callable[[], bool] = lambda: False) -> BridgeClient:
        """The bridge serving on `udid` - started if it is not already."""
        with self.lock:
            if self.running() and self.udid == udid:
                return self.client
            self.stop()
            if self.client.health():
                # A bridge left from an earlier process of this app: ask it to go.
                self.client.shutdown()
                self._wait_port_free()
            xctestrun = self.ensure_built()
            self.log(f"Starting the simulator bridge on 127.0.0.1:{self.port}")
            log_path = self.build_dir.parent / "bridge.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "TEST_RUNNER_QA_BRIDGE_PORT": str(self.port)}
            argv = ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
                    "-destination", f"id={udid}", "-derivedDataPath", str(self.build_dir),
                    f"-only-testing:{SERVE_TEST}"]
            with open(log_path, "w", encoding="utf-8") as out:
                self.process = subprocess.Popen(simctl.native(argv), stdout=out, stderr=subprocess.STDOUT, env=env)
            self.udid = udid
            deadline = time.monotonic() + self.start_seconds
            while not self.client.health(timeout=1.5):
                if self.process.poll() is not None:
                    tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
                    self.process = None
                    raise SimulatorError("The bridge stopped before it was ready:\n" + "\n".join(tail))
                if cancelled() or time.monotonic() > deadline:
                    self.stop()
                    raise SimulatorError("stopped" if cancelled() else
                                         f"The bridge did not come up in {self.start_seconds:.0f} s (see {log_path})")
                time.sleep(0.5)
            return self.client

    def _wait_port_free(self, seconds: float = 15) -> None:
        deadline = time.monotonic() + seconds
        while self.client.health(timeout=1) and time.monotonic() < deadline:
            time.sleep(0.5)

    def stop(self) -> None:
        with self.lock:
            process, self.process = self.process, None
            self.udid = None
            if process is None or process.poll() is not None:
                return
            self.client.shutdown()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    # -- the generated tests -------------------------------------------------------

    def run_tests(self, udid: str, classes: list[str], *, timeout: float = 900) -> dict:
        """Run these test classes on `udid`: `{"passed", "tests": [...], "log": tail}`."""
        with self.lock:
            self.stop()
            xctestrun = self.ensure_built()
            argv = ["xcodebuild", "test-without-building", "-xctestrun", str(xctestrun), "-destination", f"id={udid}",
                    "-derivedDataPath", str(self.build_dir)]
            argv += [f"-only-testing:{TEST_BUNDLE}/{name}" for name in classes]
            self.log(f"Running {len(classes)} generated test class(es): {', '.join(classes)}")
            result = simctl.run(argv, timeout=timeout, check=False)
            return parse_test_output(result.stdout + "\n" + result.stderr, classes, result.returncode)


def parse_test_output(output: str, classes: list[str], returncode: int = 0) -> dict:
    tests: dict[str, dict] = {}
    for line in output.splitlines():
        line = line.strip()
        case = CASE.search(line)
        if case and case.group("cls") != "BridgeTests":
            key = case.group("cls")
            entry = tests.setdefault(key, {"class": key, "status": "", "seconds": None, "failures": []})
            entry["status"] = case.group("status")
            entry["seconds"] = float(case.group("seconds")) if case.group("seconds") else None
            continue
        failure = FAILURE.match(line)
        if failure:
            key = failure.group("cls")
            entry = tests.setdefault(key, {"class": key, "status": "failed", "seconds": None, "failures": []})
            entry["failures"].append({"line": int(failure.group("line")), "message": failure.group("message")[:400]})
    for name in classes:
        tests.setdefault(name, {"class": name, "status": "not run", "seconds": None, "failures": []})
    ordered = [tests[name] for name in classes] + [t for k, t in tests.items() if k not in classes]
    passed = returncode == 0 and all(t["status"] == "passed" for t in ordered)
    tail = [line for line in output.splitlines() if line.strip()][-15:]
    return {"passed": passed, "tests": ordered, "log": "\n".join(tail)}
