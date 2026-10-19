"""An Xcode project's schemes, and a scheme built for a simulator and installed on it.

    find_containers(folder)   the workspaces and projects in a folder (not inside one another,
                              not in Pods or build output) - what "Build from the project" offers
    schemes(container)        the container's schemes
    build(...)                `xcodebuild build` for one simulator into a derived-data folder of
                              this app's own (the project's folder is not written), and the
                              built `.app`: its path, bundle id and name

The project itself is never changed: the build writes only under `derived_data`.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable

from qa import simctl
from qa.simctl import SimulatorError

SKIP_DIRS = {".git", "Pods", "Carthage", "DerivedData", "build", ".build", "node_modules", ".swiftpm"}
MAX_DEPTH = 3


def find_containers(folder: Path | str) -> list[dict]:
    """Workspaces first, then projects: `{"path", "kind", "name"}`, relative to `folder`."""
    root = Path(folder)
    found: list[dict] = []

    def walk(path: Path, depth: int) -> None:
        try:
            entries = sorted(path.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            if entry.suffix in (".xcworkspace", ".xcodeproj"):
                if entry.stem == "QABridge":
                    continue  # this app's own bridge, never an app to test
                found.append({"path": entry.relative_to(root).as_posix(),
                              "kind": "workspace" if entry.suffix == ".xcworkspace" else "project",
                              "name": entry.stem})
                continue  # a project's own embedded workspace is not a second one
            if depth < MAX_DEPTH:
                walk(entry, depth + 1)

    walk(root, 1)
    found.sort(key=lambda c: (c["kind"] != "workspace", c["path"].count("/"), c["path"]))
    return found


def _container_args(container: Path) -> list[str]:
    flag = "-workspace" if container.suffix == ".xcworkspace" else "-project"
    return [flag, str(container)]


def schemes(container: Path | str) -> list[str]:
    container = Path(container)
    result = simctl.run(["xcodebuild", "-list", "-json", *_container_args(container)], timeout=120)
    try:
        raw = json.loads(result.stdout[result.stdout.find("{"):])
    except ValueError as exc:
        raise SimulatorError(f"xcodebuild -list gave no JSON for {container.name}") from exc
    entry = raw.get("workspace") or raw.get("project") or {}
    return list(entry.get("schemes") or [])


def build(container: Path | str, scheme: str, udid: str, derived_data: Path | str, *,
          log: Callable[[str], None] = print, timeout: float = 1200,
          cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Build `scheme` for the simulator `udid`; the built app's `{"path", "bundle_id", "name"}`."""
    container = Path(container)
    derived_data = Path(derived_data)
    argv = ["xcodebuild", *_container_args(container), "-scheme", scheme, "-configuration", "Debug",
            "-destination", f"id={udid}", "-derivedDataPath", str(derived_data), "build"]
    log(f"$ xcodebuild -scheme {scheme!r} -destination id={udid} build")
    started = time.monotonic()
    process = subprocess.Popen(simctl.native(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               cwd=str(container.parent))
    errors: list[str] = []
    tail: list[str] = []
    try:
        for line in process.stdout:
            line = line.rstrip()
            tail = (tail + [line])[-30:]
            if " error: " in line or line.startswith("error:"):
                errors.append(line)
                log(line)
            elif line.startswith(("** BUILD", "CompileSwift ", "Ld ", "CodeSign ")):
                log(line[:200])
            if cancelled() or time.monotonic() - started > timeout:
                process.kill()
                raise SimulatorError("the build was stopped" if cancelled() else
                                     f"the build took longer than {timeout:.0f} s")
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
    if process.returncode != 0:
        detail = "\n".join(errors[:8]) or "\n".join(tail[-8:])
        raise SimulatorError(f"The build failed:\n{detail}")
    products = derived_data / "Build" / "Products"
    apps = [p for p in products.glob("*-iphonesimulator/*.app") if not p.name.endswith("-Runner.app")]
    if not apps:
        raise SimulatorError(f"The build succeeded, but no .app was found under {products}")
    apps.sort(key=lambda p: (p.stem != scheme, -p.stat().st_mtime))
    info = simctl.bundle_info(apps[0])
    log(f"Built {apps[0].name} ({info['bundle_id']}) in {time.monotonic() - started:.0f} s")
    return info
