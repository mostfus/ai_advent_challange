"""Day 31: which project folder is open - one path, on disk.

    {"path": "/Users/me/src/MyApp", "opened_at": "2026-10-17T12:00:00+00:00"}

Nothing else about the project is stored: its git repository, its index and
its MCP servers are worked out from the path every time it is opened, so a
folder that moved or stopped being a repository is noticed rather than
remembered wrong. No file means no project was ever opened - the app then
works on its own folder (`core.project`).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from storage.common import DATA_DIR, now_iso

PROJECT_DIR_ENV = "CHAT_PROJECT_DIR"
DEFAULT_PROJECT_DIR = DATA_DIR / "project"


def project_dir_from_env() -> Path:
    """Where the open project is written down: `CHAT_PROJECT_DIR`, or `data/project`."""
    configured = os.getenv(PROJECT_DIR_ENV)
    return Path(configured).expanduser() if configured else DEFAULT_PROJECT_DIR


class ProjectStore:
    """The open project's path: one file, a lock, atomic replace."""

    FILENAME = "project.json"

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else project_dir_from_env()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / self.FILENAME
        self._lock = threading.RLock()

    def load(self) -> str | None:
        """The path last opened, or None. An unreadable file reads as none."""
        with self._lock:
            if not self.path.exists():
                return None
            try:
                data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                return None
        path = data.get("path") if isinstance(data, dict) else None
        return path if isinstance(path, str) and path.strip() else None

    def save(self, path: str) -> str:
        payload = json.dumps({"path": path, "opened_at": now_iso()}, ensure_ascii=False, indent=2)
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        return path
