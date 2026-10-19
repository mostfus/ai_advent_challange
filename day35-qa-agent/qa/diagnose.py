"""After a failed or crashed run: a second agent reads the project's code to say why.

It gets what the run saw - the scenario, the steps it took and the screens'
titles, the check that failed and what was on the screen, the crash report's
stack - and the project's files read-only (`tools.files.FilesProvider` with
`read_only=True`: list, read, search, the index by meaning; nothing written).
It answers with suspects as `path:line` and a suggested fix, naming only lines
it has read. The QA agent tests the app as a person would; this one looks at it
as its developer does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.agent import Agent, AgentConfig
from core.project import git_toplevel
from qa.config import QaConfig
from tools.files import FilesConfig, FilesProvider
from tools.registry import ToolRegistry

SYSTEM = """You are a senior iOS developer. A QA run of the app below has just failed in the Simulator. Find the code most likely responsible in the project's source, with your tools: search and read - you cannot change files.

Project folder: {path} (every path you pass or get is relative to it)

Work like this: start from what the run gives you - the screen titles, the labels of the elements involved, the crash's stack frames - search the code for them, read the code around the hits, and follow it to the cause.

Answer in the language of the scenario, briefly:
1. What went wrong, in one or two sentences.
2. Suspects: each as `path:line` - what the code there does and why it is suspect.
3. A suggested fix.
Name only files and lines a tool has shown you. If the code you can see does not explain the failure, say so plainly."""

MAX_SCREEN_CHARS = 3000


def brief(run) -> str:
    """What the run saw, for the developer agent."""
    snapshot = run.log.snapshot()
    lines = [f"Scenario: {run.scenario.get('title') or ''}", run.scenario.get("text", ""), "",
             f"App: {run.app.get('name')} ({run.app.get('bundle_id')}); verdict: {run.verdict}", "", "Steps:"]
    for step in snapshot["steps"]:
        target = (step.get("target") or {})
        what = step["action"]
        if target:
            what += f" {target.get('type')} \"{target.get('label') or (target.get('texts') or [''])[0]}\""
        if step.get("arguments", {}).get("text"):
            what += f" text={step['arguments']['text']!r}"
        lines.append(f"  {step['n']}. {what} -> screen \"{step.get('title') or ''}\""
                     + (f" (error: {step['error']})" if step.get("error") else ""))
    failed = [c for c in snapshot["checks"] if not c["passed"]]
    if failed:
        lines += ["", "Failed checks:"]
        for check in failed:
            lines.append(f"  - {check['expectation']}: {check.get('note') or ''} (on screen \"{check.get('title') or ''}\")")
    crash = snapshot.get("crash")
    if crash:
        lines += ["", crash.get("summary") or "The app crashed."]
        if crash.get("messages"):
            lines.append("Message: " + " | ".join(crash["messages"][:3]))
        for frame in (crash.get("frames") or [])[:12]:
            lines.append("  " + frame["text"])
    last = snapshot["steps"][-1]["screen"] if snapshot["steps"] else ""
    if last:
        lines += ["", "The last screen the run saw:", last[:MAX_SCREEN_CHARS]]
    if run.answer:
        lines += ["", "The QA agent's own summary: " + run.answer]
    return "\n".join(lines)


def diagnose(run, *, llm, config: QaConfig, project: Path,
             docs: Callable[[str, int], str] | None = None) -> dict:
    files = FilesProvider(project, FilesConfig(), git=git_toplevel(project) is not None, docs=docs, read_only=True)
    agent = Agent(llm, AgentConfig(model=config.model, system_prompt=SYSTEM.format(path=project),
                                   reasoning_effort=config.reasoning_effort, temperature=config.temperature),
                  tools=ToolRegistry([files]), max_tool_rounds=config.diagnosis_tool_rounds,
                  max_tool_calls=config.diagnosis_tool_calls)
    reply = agent.ask(brief(run))
    calls = [{"name": c.get("name"), "arguments": {k: str(v)[:120] for k, v in (c.get("arguments") or {}).items()}}
             for c in reply.tool_calls]
    return {"text": reply.answer if reply.error is None else "", "error": reply.error or "",
            "project": str(project), "calls": calls, "usage": reply.usage or {}}
