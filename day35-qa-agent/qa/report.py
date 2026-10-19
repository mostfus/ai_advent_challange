"""A run - or a whole suite's runs - as Markdown: the QA tab's download, CI's job summary and PR comment.

    run_markdown(run)        one run: verdict, the model's summary, every step with its
                             screenshot, the checks, a crash, the code diagnosis
    suite_markdown(results)  the suite in CI: a table of scenarios and generated tests
"""

from __future__ import annotations

ICONS = {"passed": "✅", "failed": "❌", "crashed": "💥", "blocked": "⛔", "unchecked": "⚠️",
         "incomplete": "⏳", "stopped": "⏹", "error": "⚠️", "not run": "–", "skipped": "–"}


def icon(verdict: str | None) -> str:
    return ICONS.get(verdict or "", "•")


def _cell(text) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _target(step: dict) -> str:
    target = step.get("target") or {}
    if not target:
        return ""
    name = target.get("label") or (target.get("texts") or [""])[0] or target.get("identifier") or ""
    return f'{target.get("type")} "{name}"'


def run_markdown(run: dict, *, images: bool = True) -> str:
    scenario = run.get("scenario") or {}
    lines = [f"# {icon(run.get('verdict'))} {scenario.get('title') or 'QA run'} - {run.get('verdict')}", ""]
    lines += [f"- App: {(run.get('app') or {}).get('name')} (`{(run.get('app') or {}).get('bundle_id')}`)",
              f"- Simulator: {(run.get('device') or {}).get('name')} ({(run.get('device') or {}).get('runtime')})",
              f"- Model: {run.get('model')}, {run.get('tool_calls', 0)} tool calls"]
    usage = run.get("usage") or {}
    if usage.get("total_tokens"):
        lines.append(f"- Tokens: {usage.get('total_tokens')}")
    lines += ["", "## Scenario", "", scenario.get("text", ""), ""]
    if run.get("answer"):
        lines += ["## The agent's summary", "", run["answer"], ""]
    if run.get("error"):
        lines += ["## Error", "", f"```\n{run['error']}\n```", ""]
    checks = run.get("checks") or []
    if checks:
        lines += ["## Checks", "", "| # | Expectation | Result | What the agent saw |", "|---|---|---|---|"]
        for check in checks:
            lines.append(f"| {check['n']} | {_cell(check['expectation'])} | {'✅' if check['passed'] else '❌'} "
                         f"| {_cell(check.get('note'))} |")
        lines.append("")
    crash = run.get("crash")
    if crash:
        lines += ["## Crash", "", crash.get("summary", ""), ""]
        if crash.get("frames"):
            lines += ["```", *[f["text"] for f in crash["frames"][:15]], "```", ""]
    diagnosis = run.get("diagnosis") or {}
    if diagnosis.get("text") or diagnosis.get("error"):
        lines += ["## Code diagnosis", "", diagnosis.get("text") or diagnosis.get("error"), ""]
    lines += ["## Steps", ""]
    for step in run.get("steps") or []:
        what = step["action"] + (f" {_target(step)}" if _target(step) else "")
        text = (step.get("arguments") or {}).get("text")
        if text:
            what += f" ← {text!r}"
        lines.append(f"{step['n']}. **{what}** - {step.get('why') or ''}"
                     + (f" _(error: {step['error']})_" if step.get("error") else ""))
        if images and step.get("screenshot"):
            lines.append(f"   <img src=\"{step['screenshot']}\" width=\"220\">")
    return "\n".join(lines) + "\n"


def suite_markdown(results: list[dict], tests: dict | None = None, *, title: str = "QA agent") -> str:
    lines = [f"## {title}", ""]
    if results:
        passed = sum(1 for r in results if r.get("verdict") == "passed")
        lines += [f"**AI scenarios: {passed} of {len(results)} passed**", "",
                  "| | Scenario | Verdict | Checks | Steps | Notes |", "|---|---|---|---|---|---|"]
        for run in results:
            checks = run.get("checks") or []
            failed = [c for c in checks if not c.get("passed")]
            notes = run.get("error") or ("; ".join(c["expectation"] for c in failed)
                                         or (run.get("crash") or {}).get("summary") or "")
            lines.append(f"| {icon(run.get('verdict'))} | {_cell((run.get('scenario') or {}).get('title'))} "
                         f"| {run.get('verdict')} | {len(checks) - len(failed)}/{len(checks)} "
                         f"| {len(run.get('steps') or [])} | {_cell(notes)[:200]} |")
        lines.append("")
        for run in results:
            diagnosis = (run.get("diagnosis") or {}).get("text")
            if diagnosis:
                lines += [f"<details><summary>Code diagnosis: {_cell((run.get('scenario') or {}).get('title'))}</summary>",
                          "", diagnosis, "", "</details>", ""]
    if tests is not None:
        lines += [f"**Generated XCUITests: {'passed' if tests.get('passed') else 'FAILED'}**", "",
                  "| | Test | Result | Seconds | Failure |", "|---|---|---|---|---|"]
        for test in tests.get("tests") or []:
            failure = (test.get("failures") or [{}])[0].get("message", "") if test.get("failures") else ""
            lines.append(f"| {icon(test['status'])} | {test['class']} | {test['status']} "
                         f"| {test.get('seconds') or ''} | {_cell(failure)[:200]} |")
        lines.append("")
    return "\n".join(lines)
