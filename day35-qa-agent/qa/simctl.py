"""The simulators, through `xcrun simctl`: which there are, boot one, show it, its apps, a screenshot.

    run(argv)              a developer tool, run natively - on a self-hosted runner that is
                           an x86_64 build under Rosetta, Xcode's tools are still run as arm64
    devices()              the available iOS simulators, newest runtime first
    find_device(wanted)    one of them by name or UDID - a booted one first
    boot(udid) / show(udid)
    apps(udid)             the apps a person installed (not Apple's, not the bridge's own)
    install(udid, app)
    screenshot(udid, path)
"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
from functools import lru_cache
from pathlib import Path

#: The bridge's own apps, never offered as something to test.
BRIDGE_PREFIX = "dev.aiadvent.qa."


class SimulatorError(RuntimeError):
    """A simulator command failed; the message says which and why."""


@lru_cache(maxsize=1)
def translated() -> bool:
    """Is this process an x86_64 build running under Rosetta on Apple silicon?"""
    try:
        out = subprocess.run(["sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out == "1"


def native(argv: list[str]) -> list[str]:
    """`argv`, run as arm64 when this process is translated - an x86_64 runner would otherwise
    start Xcode's universal tools as x86_64 too."""
    return ["arch", "-arm64", *argv] if translated() else list(argv)


def run(argv: list[str], timeout: float = 120, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(native(argv), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SimulatorError(f"{argv[0]} is not installed (Xcode's command line tools are needed)") from exc
    except subprocess.TimeoutExpired as exc:
        raise SimulatorError(f"`{' '.join(argv[:4])}` took longer than {timeout:.0f} s") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise SimulatorError(f"`{' '.join(argv[:4])}` failed: {detail[-1] if detail else result.returncode}")
    return result


def _runtime_version(identifier: str) -> tuple:
    numbers = re.findall(r"\d+", identifier.rsplit(".", 1)[-1])
    return tuple(int(n) for n in numbers)


def devices() -> list[dict]:
    """Every available iOS simulator: name, UDID, runtime ("iOS 26.4"), state."""
    raw = json.loads(run(["xcrun", "simctl", "list", "devices", "available", "-j"]).stdout or "{}")
    found = []
    for runtime, entries in (raw.get("devices") or {}).items():
        if ".iOS-" not in runtime:
            continue
        version = _runtime_version(runtime)
        for entry in entries:
            if not entry.get("isAvailable", True):
                continue
            found.append({"udid": entry["udid"], "name": entry["name"], "state": entry.get("state", ""),
                          "runtime": "iOS " + ".".join(str(n) for n in version), "_version": version})
    found.sort(key=lambda d: (d["_version"], d["name"]), reverse=True)
    for device in found:
        device.pop("_version")
    return found


def find_device(wanted: str, available: list[dict] | None = None) -> dict:
    """The simulator called `wanted` (or with that UDID): booted first, then the newest runtime."""
    available = devices() if available is None else available
    wanted = (wanted or "").strip()
    matches = [d for d in available if d["udid"] == wanted] or [d for d in available if d["name"] == wanted]
    if not matches:
        names = sorted({d["name"] for d in available})
        raise SimulatorError(f"No simulator called {wanted!r}. Available: {', '.join(names) or 'none'}")
    booted = [d for d in matches if d["state"] == "Booted"]
    return (booted or matches)[0]


def boot(udid: str) -> None:
    """Boot the simulator and wait until it has finished booting."""
    result = run(["xcrun", "simctl", "boot", udid], check=False)
    if result.returncode != 0 and "current state: Booted" not in (result.stderr or ""):
        raise SimulatorError(f"Could not boot the simulator: {(result.stderr or '').strip()}")
    run(["xcrun", "simctl", "bootstatus", udid, "-b"], timeout=300)


def show(udid: str) -> None:
    """Bring Simulator.app forward on this device, so a person can watch the run."""
    subprocess.run(["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", udid],
                   capture_output=True, timeout=30)


def apps(udid: str) -> list[dict]:
    """The apps installed by a person (or a build) on the simulator: bundle id, name, path."""
    listed = run(["xcrun", "simctl", "listapps", udid]).stdout
    converted = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", "-"], input=listed.encode(),
                               capture_output=True, timeout=30)
    if converted.returncode != 0:
        raise SimulatorError("Could not read the simulator's app list")
    raw = plistlib.loads(converted.stdout)
    found = []
    for bundle_id, info in (raw or {}).items():
        if info.get("ApplicationType") != "User" or bundle_id.startswith(BRIDGE_PREFIX):
            continue
        found.append({"bundle_id": bundle_id,
                      "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id,
                      "path": info.get("Path", "")})
    found.sort(key=lambda a: a["name"].lower())
    return found


def install(udid: str, app: Path | str) -> None:
    run(["xcrun", "simctl", "install", udid, str(app)], timeout=300)


def screenshot(udid: str, path: Path | str) -> Path | None:
    """A PNG of the simulator's screen at `path` - or None when it could not be taken."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run(["xcrun", "simctl", "io", udid, "screenshot", "--type=png", str(path)], timeout=30)
    except SimulatorError:
        return None
    return path if path.exists() else None


def bundle_info(app: Path | str) -> dict:
    """A built `.app`'s bundle id and name, from its Info.plist."""
    plist = Path(app) / "Info.plist"
    try:
        info = plistlib.loads(plist.read_bytes())
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SimulatorError(f"{plist} could not be read") from exc
    bundle_id = info.get("CFBundleIdentifier")
    if not bundle_id:
        raise SimulatorError(f"{plist} has no CFBundleIdentifier")
    return {"bundle_id": bundle_id, "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id,
            "path": str(app)}
