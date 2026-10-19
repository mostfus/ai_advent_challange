"""`python -m qa` - the QA suite in CI, on a self-hosted Mac runner.

    python -m qa --project "$APP_CHECKOUT" [--suite qa_suite/suite.json] [--scheme ...]
                 [--device "iPhone 17"] [--model ...] [--scenario ID ...] [--no-ai] [--no-tests]
                 [--out qa-report]

The app is built from its checkout (the suite's `app`: project and scheme) for
the simulator and installed; then every scenario of the suite is carried out
by the QA agent - a failure traced into the checkout's code - and every
generated XCUITest is run, without a model. What it leaves in `--out`: each
run's folder (`run.json`, `report.md`, the screenshots) and `summary.md`, which
also becomes the job summary and - in a `pull_request` job, with
`GITHUB_TOKEN` - one comment on the pull request, updated on every push.

The exit code is 1 when a scenario did not pass or a test failed, 2 when the
suite could not be run at all. A simulator this command booted is shut down
again, and the bridge is always stopped: the runner is somebody's Mac.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from llm import local as local_llm
from llm.client import DeepSeekClient, LLMClientError
from qa import simctl, xcode
from qa.bridge import Bridge
from qa.config import APP_DIR, CONFIG_PATH, QaConfig
from qa.report import run_markdown, suite_markdown
from qa.runner import QaRun, execute, new_id
from qa.simctl import SimulatorError
from qa.suite import SUITE_PATH, Suite

MARKER = "<!-- qa-agent-summary -->"


def log(line: str) -> None:
    print(line, flush=True)


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m qa", description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True, help="the app's checkout")
    parser.add_argument("--suite", default=str(SUITE_PATH))
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--container", default="", help="the .xcodeproj/.xcworkspace in the checkout (default: the suite's)")
    parser.add_argument("--scheme", default="", help="default: the suite's")
    parser.add_argument("--device", default="", help="simulator name or UDID (default: the suite's, then qa_agent.json's)")
    parser.add_argument("--model", default="", help="overrides qa_agent.json")
    parser.add_argument("--scenario", action="append", default=[], help="only these scenario ids")
    parser.add_argument("--no-ai", action="store_true", help="skip the AI scenarios")
    parser.add_argument("--no-tests", action="store_true", help="skip the generated XCUITests")
    parser.add_argument("--out", default="qa-report", help="where the reports and screenshots go")
    return parser.parse_args(argv)


def build_llm(config: QaConfig):
    local = local_llm.load_config()
    if local.enabled and config.model == local.model_id:
        from review.services import LocalModel

        return local_llm.LlamaCppClient(local), LocalModel(local, log=log).start()
    return DeepSeekClient.from_env(timeout=300), None


def publish(markdown: str) -> None:
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    event, token, repo = os.getenv("GITHUB_EVENT_PATH"), os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    if not (event and token and repo):
        return
    from review.github import DEFAULT_API, GitHub, GitHubError, PullRequest

    try:
        pr = PullRequest.from_event_file(event)
    except (KeyError, ValueError, OSError):
        return  # not a pull_request job: the job summary is the report
    github = GitHub(token, repo, api_url=os.getenv("GITHUB_API_URL", DEFAULT_API))
    try:
        github.upsert_comment(pr.number, MARKER, MARKER + "\n" + markdown)
        log(f"Posted the QA summary to pull request #{pr.number}")
    except GitHubError as exc:
        log(f"::warning::The QA summary was not posted: {exc}")
    finally:
        github.close()


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    load_dotenv(APP_DIR / ".env")
    try:
        config = QaConfig.load(args.config).for_model(args.model or None)
        suite = Suite.load(args.suite)
    except (ValueError, OSError) as exc:
        log(f"::error::QA config: {exc}")
        return 2
    project = Path(args.project).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = suite.app or {}
    container_name = args.container or app.get("project") or ""
    scheme = args.scheme or app.get("scheme") or ""
    if not container_name or not scheme:
        log("::error::Name the Xcode project and scheme: --container/--scheme, or `app` in the suite")
        return 2
    scenarios = [s for s in suite.scenarios if not args.scenario or s["id"] in args.scenario]

    booted_here = False
    bridge = Bridge(config.bridge_port, out / "bridge-build", tests_dir=Path(args.suite).resolve().parent / "tests",
                    log=log, start_seconds=config.bridge_start_seconds)
    model_server = None
    results: list[dict] = []
    tests = None
    try:
        device = simctl.find_device(args.device or suite.device or config.device)
        if device["state"] != "Booted":
            log(f"Booting {device['name']} ({device['runtime']})")
            simctl.boot(device["udid"])
            booted_here = True
        udid = device["udid"]
        info = xcode.build(project / container_name, scheme, udid, out / "derived", log=log,
                           timeout=config.build_timeout_seconds)
        simctl.install(udid, info["path"])
        log(f"Installed {info['name']} ({info['bundle_id']}) on {device['name']}")

        if not args.no_ai and scenarios:
            llm, model_server = build_llm(config)
            client = bridge.start(udid)
            for scenario in scenarios:
                run_id = new_id()
                run = QaRun(id=run_id, scenario={"id": scenario["id"], "title": scenario["title"], "text": scenario["text"]},
                            app={"bundle_id": scenario.get("bundle_id") or info["bundle_id"], "name": info["name"]},
                            device=device, model=config.model, folder=out / "runs" / run_id)
                log(f"::group::Scenario: {scenario['title']}")
                started = time.monotonic()
                execute(run, llm=llm, config=config, client=client, udid=udid, project=project, log=log)
                data = run.to_dict()
                (run.folder / "report.md").write_text(run_markdown(data), encoding="utf-8")
                log(f"{data['verdict']} in {time.monotonic() - started:.0f} s - {data['answer']}")
                log("::endgroup::")
                results.append(data)
            bridge.stop()

        classes = [s["test"]["class"] for s in scenarios if s.get("test")]
        if not args.no_tests and classes:
            tests = bridge.run_tests(udid, classes)
            log(f"Generated XCUITests: {'passed' if tests['passed'] else 'FAILED'}")
    except (SimulatorError, LLMClientError) as exc:
        log(f"::error::The QA suite could not be run: {exc}")
        markdown = suite_markdown(results, tests) + f"\n**The suite stopped:** {exc}\n"
        (out / "summary.md").write_text(markdown, encoding="utf-8")
        publish(markdown)
        return 2
    finally:
        bridge.stop()
        if model_server is not None:
            model_server.stop()
        if booted_here:
            simctl.run(["xcrun", "simctl", "shutdown", device["udid"]], check=False)

    markdown = suite_markdown(results, tests)
    (out / "summary.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    publish(markdown)
    failed = any(r.get("verdict") != "passed" for r in results) or (tests is not None and not tests["passed"])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
