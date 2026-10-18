"""Which files of the open project the index is built from - `corpus.json`.

    {
      "include": ["**/*.md", "**/*.swift", ...],   glob patterns, `Path.glob` rules
      "exclude": [".build", "DerivedData", ...],   `.gitignore`-like: a bare name is a
                                                   file or folder anywhere, a path
                                                   with a `/` is from the root
      "max_file_kb": 256                           larger files are skipped: they are
                                                   generated, not written
    }

The same arrangement as `local_llm.json`: a file in the app folder, read at
start and edited nowhere but in the file. *Where* the patterns apply is not
in it: since day 31 that is the project folder opened in the Project tab
(`core.project`), set here as `root`. In a git repository the files are the
ones git knows about - tracked, or new and not ignored - so the project's own
`.gitignore` keeps build output, dependencies and secrets out before
`exclude` is even read (`rag.pipeline`). Uploads are not part of it: they
live in the index's own folder and are always in, as `uploads/<name>`
(`rag.indexing`).

No file is not an error: the defaults below are the same as the file's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from .pipeline import Corpus, load_corpus

#: The app folder - `corpus.json` sits there, and it is the project until another is opened.
PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "corpus.json"

DEFAULT_INCLUDE = ["**/*.md", "**/*.markdown", "**/*.swift", "**/*.py", "docs/**/*.txt",
                   "**/*.yaml", "**/*.yml", "**/*.graphql", "**/*.proto", "**/*.sql"]
DEFAULT_EXCLUDE = [".git", ".venv", "venv", "node_modules", "__pycache__", "data", ".build",
                   ".swiftpm", "DerivedData", "Pods", "Carthage", "*.xcodeproj", "*.xcworkspace",
                   "*.xcassets", "fixtures"]
DEFAULT_MAX_FILE_KB = 256
FIELDS = {"include", "exclude", "max_file_kb"}


@dataclass
class CorpusConfig:
    """Which files of the project are in the corpus, and where the project is."""

    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    max_file_kb: int = DEFAULT_MAX_FILE_KB
    #: The project folder the patterns apply to - set by the open project.
    root: Path = PROJECT_DIR
    #: The file this came from, or None for the defaults.
    path: Path | None = None

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "CorpusConfig":
        """Read `corpus.json`; with no file, the defaults."""
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: expected an object with include and exclude")
        if "root" in raw:
            raise ValueError(f"{path.name}: root is gone - the folder is the project opened in the Project tab")
        unknown = sorted(set(raw) - FIELDS)
        if unknown:
            raise ValueError(f"{path.name}: unknown field(s) {', '.join(unknown)}")
        for key in ("include", "exclude"):
            value = raw.get(key, [])
            if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
                raise ValueError(f"{path.name}: {key} must be a list of glob patterns")
        max_kb = raw.get("max_file_kb", DEFAULT_MAX_FILE_KB)
        if not isinstance(max_kb, int) or max_kb < 1:
            raise ValueError(f"{path.name}: max_file_kb must be a positive whole number")
        return cls(
            include=list(raw.get("include") or DEFAULT_INCLUDE),
            exclude=list(raw.get("exclude", DEFAULT_EXCLUDE)),
            max_file_kb=max_kb,
            path=path,
        )

    def at(self, root: Path) -> "CorpusConfig":
        """The same patterns over another project folder."""
        return replace(self, root=Path(root).resolve())

    @property
    def root_path(self) -> Path:
        return Path(self.root).resolve()

    def load_corpus(self) -> Corpus:
        """The files the patterns name, loaded into documents."""
        return load_corpus(self.include, self.root_path, self.exclude, max_bytes=self.max_file_kb * 1024)

    def describe(self) -> dict:
        """What the Project tab shows: the config in effect, and where it came from."""
        return {
            "config": self.path.name if self.path else None,
            "root_path": str(self.root_path),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "max_file_kb": self.max_file_kb,
        }
