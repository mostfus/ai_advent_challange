"""`review.json` - how the reviewer reads a pull request and how much it sends.

The same arrangement as `local_llm.json` and `corpus.json`: a file in the app
folder, edited by hand. A repository under review can have its own file (the
action's `config` input): its fields are laid over this one's, and its
`models` entries over this one's per model. The action's `model` and
`language` inputs override both.

    model                 the model id: a DeepSeek model, or the local one from local_llm.json
    reasoning_effort      how long a DeepSeek model thinks: "medium" by default; "off" (= "none"),
                          "minimal", "low", "high", "xhigh", "ultra", "max"; null = the model's own
    temperature           sent as is
    language              the language comments are written in
    detect_copies         find copied files (`git diff -C -C`) and review only their differences
    whole_file_lines      a changed file up to this long is shown whole, the change marked in it
    context_lines         a longer one: this many unchanged lines around each change
    max_file_lines        at most this many lines of one file go up
    batch_chars           one request's diff budget; a bigger pull request is split by files
    max_batches           requests per review; files beyond them are listed, not reviewed
    parallel              requests at the same time
    excerpts_per_file     RAG: chunks searched for each changed file
    excerpts_max          ... and at most this many per request, the closest first
    excerpt_chars         ... in at most this many characters
    max_inline_comments   comments on the code per review; the rest are in the summary only
    skip                  glob patterns of files not reviewed (generated, lock files)
    models                per model id, fields that differ for it (a small local context)
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONFIG_PATH = HERE / "review.json"

#: What DeepSeek accepts (its 422 lists them), plus "off" - the app's name for "none".
REASONING_EFFORTS = (None, "off", "none", "minimal", "low", "medium", "high", "xhigh", "ultra", "max")

DEFAULT_SKIP = ["*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Package.resolved",
                "*.pbxproj", "*.xcscheme", "*.storyboard", "*.xib", "*.min.js", "*.min.css",
                "*.map", "*.svg", "*.snap"]


@dataclass
class ReviewConfig:
    model: str = "deepseek-v4-flash"
    reasoning_effort: str | None = "medium"
    temperature: float = 0.2
    language: str = "English"
    detect_copies: bool = True
    whole_file_lines: int = 400
    context_lines: int = 8
    max_file_lines: int = 900
    batch_chars: int = 60_000
    max_batches: int = 6
    parallel: int = 3
    excerpts_per_file: int = 4
    excerpts_max: int = 8
    excerpt_chars: int = 14_000
    max_inline_comments: int = 20
    skip: list[str] = field(default_factory=lambda: list(DEFAULT_SKIP))
    models: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = CONFIG_PATH, overlay: Path | str | None = None) -> "ReviewConfig":
        """`review.json` (no file: the defaults), with a repository's own file over it.

        The overlay was asked for by name, so a missing one is an error, not the defaults.
        """
        path = Path(path)
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        where = path.name
        if overlay is not None:
            overlay = Path(overlay)
            if not overlay.is_file():
                raise ValueError(f"{overlay}: the review config file does not exist")
            over = json.loads(overlay.read_text(encoding="utf-8"))
            if not isinstance(over, dict):
                raise ValueError(f"{overlay.name}: expected an object")
            models = dict(raw.get("models") or {})
            for name, fields_ in (over.get("models") or {}).items():
                models[name] = {**(models.get(name) or {}), **fields_}
            raw = {**raw, **over, "models": models}
            where = f"{path.name} + {overlay.name}"
        return cls.from_dict(raw, where)

    @classmethod
    def from_dict(cls, raw: dict, where: str = "review.json") -> "ReviewConfig":
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: expected an object")
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ValueError(f"{where}: unknown field(s) {', '.join(unknown)}")
        for name, overrides in (raw.get("models") or {}).items():
            bad = sorted(set(overrides) - known | ({"models", "model"} & set(overrides)))
            if bad:
                raise ValueError(f"{where}: models.{name}: unknown field(s) {', '.join(bad)}")
            check_effort(overrides.get("reasoning_effort"), f"{where}: models.{name}")
        check_effort(raw.get("reasoning_effort"), where)
        return cls(**raw)

    def for_model(self, model: str | None = None) -> "ReviewConfig":
        """This config with `model` set and its `models` overrides applied."""
        model = model or self.model
        overrides = {k: v for k, v in (self.models.get(model) or {}).items() if k not in ("models", "model")}
        return replace(self, model=model, **overrides)

    def skipped(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(path, p) for p in self.skip)


def check_effort(value, where: str) -> None:
    """A typo here would be an HTTP 422 on every request of every review: refused on load instead."""
    if value not in REASONING_EFFORTS:
        known = ", ".join(e for e in REASONING_EFFORTS if e)
        raise ValueError(f"{where}: reasoning_effort {value!r} is not one of {known} (or null)")
