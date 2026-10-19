"""The crash report of an app that stopped running mid-run, read from its `.ips` file.

A simulator app's crash is reported on the Mac, in `~/Library/Logs/DiagnosticReports`,
as an `.ips`: one line of JSON (the app, its bundle id, the time), then the
report itself as JSON - the exception, why the process was terminated, the
crashed thread's frames and the images they belong to. Swift's own message
("Fatal error: Index out of range") is in the application-specific information.

    find_report(bundle_id, since)   the newest report of that app written since `since`
    parse(text, file)               a report's essentials, and a one-line summary
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPORTS = Path.home() / "Library" / "Logs" / "DiagnosticReports"
MAX_FRAMES = 15


def find_report(bundle_id: str, since: float, folder: Path | None = None) -> dict | None:
    folder = folder or REPORTS
    try:
        candidates = sorted(folder.glob("*.ips"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for path in candidates:
        try:
            if path.stat().st_mtime < since - 2:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = text.split("\n", 1)[0]
        try:
            header = json.loads(head)
        except ValueError:
            continue
        if header.get("bundleID") == bundle_id:
            return parse(text, path)
    return None


def _demangle(symbols: list[str]) -> dict:
    """Swift symbols the report left mangled, made readable - best effort."""
    mangled = sorted({s for s in symbols if s.startswith(("$s", "_$s"))})
    if not mangled:
        return {}
    try:
        out = subprocess.run(["xcrun", "swift-demangle", "--simplified", *mangled], capture_output=True,
                             text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    readable = {}
    for line in out.splitlines():
        if " ---> " in line:
            before, after = line.split(" ---> ", 1)
            readable[before.strip()] = after.strip()
    return readable


def parse(text: str, path: Path | str | None = None) -> dict:
    head, _, rest = text.partition("\n")
    try:
        header = json.loads(head)
        body = json.loads(rest) if rest.strip() else {}
    except ValueError:
        return {"file": str(path or ""), "summary": "A crash report was found but could not be read."}
    exception = body.get("exception") or {}
    termination = body.get("termination") or {}
    images = body.get("usedImages") or []
    threads = body.get("threads") or []
    index = body.get("faultingThread", 0)
    thread = threads[index] if isinstance(index, int) and 0 <= index < len(threads) else (threads[0] if threads else {})
    raw_frames = (thread.get("frames") or [])[:MAX_FRAMES]
    readable = _demangle([str(f.get("symbol") or "") for f in raw_frames])
    app = header.get("app_name") or body.get("procName") or ""
    frames = []
    for frame in raw_frames:
        image = images[frame["imageIndex"]].get("name", "?") if isinstance(frame.get("imageIndex"), int) \
            and frame["imageIndex"] < len(images) else "?"
        symbol = str(frame.get("symbol") or "???")
        line = f"{image}  {readable.get(symbol, symbol)}"
        if frame.get("sourceFile"):
            line += f"  ({frame['sourceFile']}:{frame.get('sourceLine', '?')})"
        frames.append({"image": image, "text": line, "in_app": image == app})
    messages = []
    for values in (body.get("asi") or {}).values():
        messages.extend(str(v) for v in (values if isinstance(values, list) else [values]))
    kind = " / ".join(x for x in (exception.get("type"), exception.get("signal")) if x)
    first_app_frame = next((f["text"] for f in frames if f["in_app"]), frames[0]["text"] if frames else "")
    summary = f"Crash: {kind or 'the process was killed'}"
    if messages:
        summary += f" - {messages[0][:200]}"
    if first_app_frame:
        summary += f" - at {first_app_frame}"
    return {
        "file": str(path or ""),
        "app": app,
        "bundle_id": header.get("bundleID"),
        "time": header.get("timestamp"),
        "exception": kind,
        "exception_message": exception.get("message") or "",
        "termination": termination.get("indicator") or termination.get("namespace") or "",
        "messages": messages[:5],
        "thread": thread.get("queue") or f"thread {index}",
        "frames": frames,
        "summary": summary,
    }
