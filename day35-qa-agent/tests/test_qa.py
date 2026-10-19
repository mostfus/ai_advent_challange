"""Day 35: the QA agent - the screen as text, the simulator tools, a whole run, the suite, CI.

    uv run python -m unittest discover -s tests -v

No simulator, no Xcode, no model: the bridge is `FakeBridge` (two screens of a
"Mountains" app), the model is a script that taps, types, checks and finishes,
and a crash report is written by hand. The real bridge and simulator were
checked live (see README, day 35).
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.agent import Agent, AgentConfig
from core.errors import Conflict, Invalid
from llm.client import LLMResponse
from qa import crash, runner, xcode, xcuitest
from qa.bridge import parse_test_output
from qa.config import QaConfig
from qa.report import run_markdown, suite_markdown
from qa.runner import QaRun, execute, verdict
from qa.suite import Suite
from tools.base import Tool
from tools.files import FilesProvider
from tools.registry import ToolRegistry
from tools.simulator import BridgeError, RunLog, SimulatorProvider, render_screen

BUNDLE = "com.example.mountains"


def node(kind, label="", frame=(0, 0, 0, 0), children=(), **extra):
    data = {"type": kind, "frame": list(frame)}
    if label:
        data["label"] = label
    data.update(extra)
    if children:
        data["children"] = list(children)
    return data


def menu_tree() -> dict:
    return node("Application", "Mountains", (0, 0, 402, 874), [node("Window", frame=(0, 0, 402, 874), children=[
        node("Other", frame=(0, 0, 402, 874), children=[
            node("NavigationBar", frame=(0, 62, 402, 54), id="Mountains", children=[
                node("StaticText", "Mountains", (150, 70, 100, 20))]),
            node("CollectionView", frame=(0, 116, 402, 758), children=[
                node("Cell", "Search", (16, 116, 370, 44), [
                    node("StaticText", "Search", (32, 128, 100, 20)),
                    node("Other", "Search", (341, 116, 44, 44), [node("Image", "Forward", (358, 130, 10, 14))]),
                    node("Other", "Search", (341, 116, 44, 44), [node("Image", "Forward", (358, 130, 10, 14))])]),
                node("Cell", frame=(16, 160, 370, 44), children=[
                    node("StaticText", "Wi-Fi", (32, 172, 60, 20)),
                    node("Switch", "Wi-Fi", (330, 166, 51, 31), value="1")]),
                node("Cell", "Far below", (16, 2000, 370, 44)),
                node("Other", "Vertical scroll bar, 3 pages", (369, 228, 30, 584)),
            ])])])])


def search_tree(results=("K2",), typed="") -> dict:
    cells = [node("Cell", frame=(10, 210 + 42 * i, 372, 32), children=[node("StaticText", name, (20, 216 + 42 * i, 300, 20))])
             for i, name in enumerate(results)]
    field = node("SearchField", frame=(8, 126, 386, 44), placeholder="Search",
                 children=[node("Image", "Search", (20, 138, 20, 19), id="magnifyingglass")], **({"value": typed} if typed else {}))
    return node("Application", "Mountains", (0, 0, 402, 874), [node("Window", frame=(0, 0, 402, 874), children=[
        node("NavigationBar", frame=(0, 62, 402, 54), id="Search", children=[
            node("Button", "Mountains", (16, 62, 44, 44), id="BackButton"),
            node("StaticText", "Search", (150, 70, 100, 20))]),
        field,
        node("CollectionView", frame=(0, 200, 402, 674), children=cells),
        *([node("Keyboard", frame=(0, 583, 402, 233), children=[node("Key", "q", (0, 600, 30, 40))])] if typed or results == () else []),
    ])])


class FakeBridge:
    """Two screens: the menu, and a search that finds K2 once something is typed."""

    def __init__(self, crash_after_typing: bool = False) -> None:
        self.screen = "menu"
        self.typed = ""
        self.calls: list = []
        self.crash_after_typing = crash_after_typing
        self.running = True

    def launch(self, bundle_id, fresh=True):
        self.calls.append(("launch", bundle_id, fresh))
        self.screen, self.typed, self.running = "menu", "", True
        return {"ok": True, "state": "foreground"}

    def tree(self, bundle_id):
        if not self.running:
            return {"ok": True, "state": "not_running"}
        if self.screen == "menu":
            return {"ok": True, "state": "foreground", "tree": menu_tree()}
        results = ("K2",) if self.typed == "K2" else (() if self.typed else ("Annapurna", "Everest", "K2"))
        return {"ok": True, "state": "foreground", "tree": search_tree(results, self.typed)}

    def tap(self, x, y, duration=0.0):
        self.calls.append(("tap", x, y))
        if self.screen == "menu" and 116 <= y <= 160:
            self.screen = "search"
        return {"ok": True}

    def type_text(self, bundle_id, text):
        self.calls.append(("type", text))
        if self.screen != "search":
            raise BridgeError("Neither element nor any descendant has keyboard focus")
        self.typed += text
        if self.crash_after_typing:
            self.running = False
        return {"ok": True}

    def swipe(self, start, end):
        self.calls.append(("swipe", start, end))
        return {"ok": True}


def provider(bridge=None, **kwargs) -> SimulatorProvider:
    return SimulatorProvider(bridge or FakeBridge(), BUNDLE, RunLog(), sleep=lambda s: None, **kwargs)


def call(p: SimulatorProvider, name: str, **args):
    tool = next(t for t in p.tools() if t.name == name)
    return tool.call(args)


class ScreenTest(unittest.TestCase):
    def test_menu_is_cut_to_what_a_tester_looks_at(self):
        screen = render_screen({"state": "foreground", "tree": menu_tree()})
        self.assertEqual(screen.title, "Mountains")
        self.assertIn('[e1] CollectionView (scrolls: 3 pages)', screen.text)
        self.assertIn('Cell "Search"', screen.text)
        self.assertIn('Cell "Wi-Fi"', screen.text)
        self.assertIn('Switch "Wi-Fi" = on', screen.text)
        self.assertIn("1 more element(s) below the screen", screen.text)
        self.assertNotIn("Forward", screen.text)            # decoration inside a cell
        self.assertEqual(screen.text.count('"Search"'), 1)  # the accessory listed twice is shown once
        self.assertNotIn('Text "Mountains"', screen.text)    # the navigation title is not repeated
        self.assertNotIn("scroll bar", screen.text)

    def test_refs_point_at_elements_and_their_middle(self):
        screen = render_screen({"state": "foreground", "tree": menu_tree()})
        cell = next(e for e in screen.elements.values() if e.label == "Search")
        self.assertEqual(cell.center((0, 0, 402, 874)), (201.0, 138.0))
        self.assertIs(screen.element(f"[{cell.ref}]"), cell)

    def test_field_keyboard_and_alert(self):
        screen = render_screen({"state": "foreground", "tree": search_tree((), ""),
                                "system_alert": node("Alert", "Allow?", (40, 300, 320, 200), [
                                    node("Button", "Allow", (40, 450, 160, 44))])})
        self.assertTrue(screen.text.splitlines()[1].startswith("SYSTEM ALERT"))
        self.assertIn('empty, placeholder="Search"', screen.text)
        self.assertTrue(screen.keyboard)
        allow = next(e for e in screen.elements.values() if e.label == "Allow")
        self.assertTrue(allow.system)

    def test_not_running(self):
        screen = render_screen({"state": "not_running"})
        self.assertIn("The app is not on the screen", screen.text)

    def test_signature_ignores_refs(self):
        a = render_screen({"state": "foreground", "tree": menu_tree()})
        b = render_screen({"state": "foreground", "tree": menu_tree()})
        self.assertEqual(a.signature, b.signature)


class ToolsTest(unittest.TestCase):
    def test_tap_type_check_finish(self):
        bridge = FakeBridge()
        p = provider(bridge)
        p.start()
        search = next(e.ref for e in p.screen.elements.values() if e.label == "Search")
        result = call(p, "tap", ref=search, why="Open search")
        self.assertTrue(result.ok)
        self.assertIn(("tap", 201.0, 138.0), bridge.calls)
        self.assertIn('NavigationBar "Search"', result.text)
        field = next(e.ref for e in p.screen.elements.values() if e.type == "SearchField")
        result = call(p, "type_text", text="K2", ref=field, why="Search for K2")
        self.assertIn('typed "K2"', result.text)
        k2 = next(e.ref for e in p.screen.elements.values() if e.type == "Cell")
        result = call(p, "check", expectation="K2 is found", passed=True, note="one row", evidence=[k2, "e99"],
                      absent=["Everest"])
        self.assertIn("Recorded check 1 (passed)", result.text)
        self.assertIn("e99", result.text)
        call(p, "finish", status="passed", summary="Found.")
        log = p.log.snapshot()
        self.assertEqual([s["action"] for s in log["steps"]], ["launch", "tap", "type"])
        self.assertEqual(log["checks"][0]["evidence"][0]["texts"], ["K2"])
        self.assertEqual(log["checks"][0]["step"], 3)
        self.assertEqual(log["finish"]["status"], "passed")
        self.assertEqual(verdict(log), "passed")

    def test_errors_go_back_to_the_model(self):
        p = provider()
        p.start()
        self.assertIn("no element 'e42'", call(p, "tap", ref="e42", why="x").text)
        typed = call(p, "type_text", text="K2", why="no field")
        self.assertTrue(typed.is_error)
        self.assertIn("Tap the field first", typed.text)

    def test_absent_text_that_is_there_is_pointed_out(self):
        p = provider()
        p.start()
        result = call(p, "check", expectation="no Search row", passed=True, note="", absent=["Search"])
        self.assertIn("IS on the screen", result.text)

    def test_budget_and_stop(self):
        p = provider(max_actions=1)
        p.start()
        call(p, "scroll", direction="down", why="more")
        self.assertIn("used its 1 actions", call(p, "scroll", direction="down", why="more").text)
        p.log.cancelled = True
        self.assertIn("stopped by the person", call(p, "look").text)

    def test_unchanged_screen_is_said(self):
        p = provider()
        p.start()
        self.assertIn("did not change", call(p, "scroll", direction="down", why="more").text)

    def test_crash_is_reported_with_its_log(self):
        bridge = FakeBridge(crash_after_typing=True)
        report = {"summary": "Crash: EXC_BAD_ACCESS - at Mountains  SearchViewController.filter"}
        p = provider(bridge, crash_lookup=lambda bundle_id, since: report)
        p.start()
        call(p, "tap", ref=next(e.ref for e in p.screen.elements.values() if e.label == "Search"), why="open")
        result = call(p, "type_text", text="K2", why="search")
        self.assertIn("NOT RUNNING", result.text)
        self.assertIn("EXC_BAD_ACCESS", result.text)
        self.assertEqual(verdict(p.log.snapshot()), "crashed")

    def test_calls_of_a_round_run_in_order(self):
        order = []

        def slow(args):
            time.sleep(0.05)
            order.append("first")
            return "ok"

        tools = [Tool("first", "", {}, slow), Tool("second", "", {}, lambda a: order.append("second") or "ok")]

        class Registry:
            def tools(self):
                return tools

        model = ScriptedQa(steps=[[("first", {}), ("second", {})]], prompt="")
        Agent(model, AgentConfig(model="m"), tools=ToolRegistry([Registry()]), parallel_calls=False).ask("go")
        self.assertEqual(order, ["first", "second"])


class VerdictTest(unittest.TestCase):
    def test_verdicts(self):
        passed = {"checks": [{"passed": True}], "finish": {"status": "passed"}}
        self.assertEqual(verdict(passed), "passed")
        self.assertEqual(verdict({**passed, "checks": [{"passed": False}]}), "failed")
        self.assertEqual(verdict({**passed, "crash": {"summary": "x"}}), "crashed")
        self.assertEqual(verdict({"checks": [], "finish": {"status": "passed"}}), "unchecked")
        self.assertEqual(verdict({"checks": [], "finish": {"status": "blocked"}}), "blocked")
        self.assertEqual(verdict({"checks": [], "finish": None}), "incomplete")
        self.assertEqual(verdict({"checks": [], "finish": None}, cancelled=True), "stopped")


class ScriptedQa:
    """Taps Search, types K2, checks, finishes and answers; any other request gets a plain answer."""

    redacted_headers: dict = {}

    def __init__(self, steps=None, prompt="You are a QA engineer", answer="K2 is found.") -> None:
        self.steps = steps
        self.prompt = prompt
        self.answer = answer
        self.bodies: list[dict] = []

    def plan(self, messages: list[dict]) -> list:
        if self.steps is not None:
            return self.steps
        screen = messages[-1]["content"]

        def ref(label):
            for line in screen.splitlines():
                if label in line and "[e" in line:
                    return line.split("[", 1)[1].split("]", 1)[0]
            return "e0"

        done = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        script = [
            lambda: [("tap", {"ref": ref('Cell "Search"'), "why": "Open search"})],
            lambda: [("type_text", {"ref": ref("SearchField"), "text": "K2", "why": "Search for K2"})],
            lambda: [("check", {"expectation": "K2 is in the results", "passed": True, "note": "one row",
                                "evidence": [ref('Cell "K2"')], "absent": ["Everest"]}),
                     ("finish", {"status": "passed", "summary": "K2 found."})],
        ]
        return script[done]() if done < len(script) else []

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(json.loads(json.dumps(body)))
        messages = body.get("messages") or []
        done = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        calls = self.plan(messages) if self.steps is None else (self.steps[done] if done < len(self.steps) else [])
        if calls and messages and messages[0]["content"].startswith(self.prompt):
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{done}_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}
        else:
            message = {"role": "assistant", "content": self.answer}
        return LLMResponse(request=body, response={"choices": [{"message": message}],
                                                   "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
                           url="fake://chat", status_code=200, elapsed_ms=1.0)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.object(runner.simctl, "screenshot", lambda udid, path: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_scenario(self, model, bridge=None, project=None) -> QaRun:
        run = QaRun(id="r1", scenario={"title": "Search finds K2", "text": "Search K2; expect K2, not Everest."},
                    app={"bundle_id": BUNDLE, "name": "Mountains"}, device={"name": "iPhone 17", "runtime": "iOS 26.4"},
                    model="deepseek-v4-flash", folder=self.dir / "r1")
        return execute(run, llm=model, config=QaConfig(settle_seconds=0), client=bridge or FakeBridge(),
                       udid="SIM", project=project, crash_folder=self.dir / "none", log=lambda line: None)

    def test_a_whole_run(self):
        model = ScriptedQa()
        run = self.run_scenario(model)
        data = run.to_dict()
        self.assertEqual(data["verdict"], "passed", data)
        self.assertEqual([s["action"] for s in data["steps"]], ["launch", "tap", "type"])
        self.assertEqual(data["answer"], "K2 is found.")
        self.assertTrue((self.dir / "r1" / "run.json").exists())
        first = model.bodies[0]
        self.assertTrue(first["messages"][0]["content"].startswith("You are a QA engineer"))
        self.assertIn("Search K2", first["messages"][-1]["content"])
        self.assertEqual({t["function"]["name"] for t in first["tools"]},
                         {"look", "tap", "type_text", "scroll", "launch_app", "wait", "check", "finish"})
        loaded = QaRun.load(self.dir / "r1")
        self.assertEqual(loaded.verdict, "passed")
        self.assertIn("## Checks", run_markdown(data))

    def test_failure_is_diagnosed_read_only(self):
        project = self.dir / "app"
        (project / "Sources").mkdir(parents=True)
        (project / "Sources" / "Search.swift").write_text("func filter(_ q: String) -> [String] { [] }\n")

        class Failing(ScriptedQa):
            def plan(self, messages):
                done = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
                return [[("check", {"expectation": "K2 is listed", "passed": False, "note": "no rows"}),
                         ("finish", {"status": "failed", "summary": "Nothing found."})]][done] if done < 1 else []

        class Developer(ScriptedQa):
            def complete(self, body):
                if body["messages"][0]["content"].startswith("You are a senior iOS developer"):
                    tools = {t["function"]["name"] for t in body["tools"]}
                    assert "write_file" not in tools and "edit_file" not in tools, tools
                    self.developer_tools = tools
                    return LLMResponse(request=body, response={"choices": [{"message": {
                        "role": "assistant", "content": "`Sources/Search.swift:1` returns nothing."}}]},
                        url="fake://", status_code=200, elapsed_ms=1.0)
                return Failing.complete(self, body)

            plan = Failing.plan

        model = Developer(answer="It failed.")
        run = self.run_scenario(model, project=project)
        self.assertEqual(run.verdict, "failed")
        self.assertEqual(run.diagnosis["text"], "`Sources/Search.swift:1` returns nothing.")
        self.assertIn("read_file", model.developer_tools)

    def test_no_finish_is_incomplete(self):
        run = self.run_scenario(ScriptedQa(steps=[]))
        self.assertEqual(run.verdict, "incomplete")


class CrashTest(unittest.TestCase):
    IPS = "\n".join([
        json.dumps({"app_name": "Mountains", "bundleID": BUNDLE, "timestamp": "2026-10-18 22:00:00.00 +0200",
                    "is_simulated": 1}),
        json.dumps({"procName": "Mountains", "exception": {"type": "EXC_BREAKPOINT", "signal": "SIGTRAP"},
                    "termination": {"indicator": "Trace/BPT trap: 5"}, "faultingThread": 0,
                    "asi": {"libswiftCore.dylib": ["Fatal error: Index out of range"]},
                    "threads": [{"queue": "com.apple.main-thread", "frames": [
                        {"imageIndex": 1, "symbol": "swift_fatalError"},
                        {"imageIndex": 0, "symbol": "SearchViewController.filter(_:)", "sourceFile": "Search.swift",
                         "sourceLine": 42}]}],
                    "usedImages": [{"name": "Mountains"}, {"name": "libswiftCore.dylib"}]}, indent=1),
    ])

    def test_parse(self):
        report = crash.parse(self.IPS, "Mountains.ips")
        self.assertEqual(report["exception"], "EXC_BREAKPOINT / SIGTRAP")
        self.assertIn("Fatal error: Index out of range", report["summary"])
        self.assertIn("SearchViewController.filter(_:)  (Search.swift:42)", report["summary"])
        self.assertTrue(report["frames"][1]["in_app"])

    def test_find_report_by_bundle_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Other.ips").write_text(json.dumps({"bundleID": "com.other"}) + "\n{}")
            (folder / "Mountains.ips").write_text(self.IPS)
            self.assertEqual(crash.find_report(BUNDLE, time.time() - 60, folder)["app"], "Mountains")
            self.assertIsNone(crash.find_report(BUNDLE, time.time() + 60, folder))


class XcuitestTest(unittest.TestCase):
    RUN = {
        "id": "r1", "scenario": {"title": "Поиск горы K2", "text": "Найди K2"}, "app": {"bundle_id": BUNDLE},
        "steps": [
            {"n": 1, "action": "launch", "arguments": {}},
            {"n": 2, "action": "tap", "why": "Open search", "arguments": {"x": 201, "y": 138},
             "target": {"type": "Cell", "label": "Search", "texts": ["Search"]}},
            {"n": 3, "action": "type", "why": "Type", "arguments": {"text": 'K2 "x"\n'},
             "target": {"type": "SearchField", "label": "", "texts": []}},
            {"n": 4, "action": "tap", "why": "broken", "error": "no focus", "arguments": {}},
            {"n": 5, "action": "tap", "why": "Unnamed", "arguments": {"x": 10, "y": 20},
             "target": {"type": "Other", "label": ""}},
        ],
        "checks": [
            {"n": 1, "step": 3, "passed": True, "expectation": "K2 found",
             "evidence": [{"type": "Cell", "label": "", "texts": ["K2"]},
                          {"type": "Switch", "label": "Wi-Fi", "value": "0"}],
             "examples": [{"type": "Cell", "texts": ["breeno-net"]}], "absent": ["Everest"]},
            {"n": 2, "step": 5, "passed": False, "expectation": "never", "evidence": [], "absent": []},
        ],
    }

    def test_generate(self):
        source = xcuitest.generate(self.RUN, "ScenarioPoiskGoryK2Tests")
        self.assertIn("final class ScenarioPoiskGoryK2Tests: XCTestCase {", source)
        self.assertIn(f'let app = QA.launch("{BUNDLE}")', source)
        self.assertIn('QA.tap(app.cells["Search"].firstMatch)', source)
        self.assertIn('QA.tap(app.searchFields.firstMatch)', source)
        self.assertIn('QA.type(app, "K2 \\"x\\"\\n")', source)
        self.assertIn('QA.expect(app.cells.containing(.staticText, identifier: "K2").firstMatch, "K2 found")', source)
        self.assertIn('QA.expectValue(app.switches["Wi-Fi"].firstMatch, "0", "K2 found")', source)
        self.assertIn('QA.expectRows(app.cells, besides: ["K2", "Wi-Fi"], "K2 found")', source)
        self.assertIn('QA.expectAbsent(app, "Everest", "K2 found")', source)
        self.assertIn("QA.tap(app, x: 10.0, y: 20.0)", source)
        self.assertNotIn("broken", source)
        self.assertNotIn("never", source)

    def test_names(self):
        self.assertEqual(xcuitest.class_name("Поиск горы K2"), "ScenarioPoiskGoryK2Tests")
        self.assertEqual(xcuitest.class_name("Wi-Fi off!"), "ScenarioWiFiOffTests")
        self.assertEqual(xcuitest.swift("a\tb\x01"), '"a\\tb\\u{1}"')


class SuiteTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            suite = Suite.load(path)
            first = suite.add("Поиск горы K2", "Найди K2", BUNDLE, "Mountains")
            second = suite.add("Поиск горы K2", "Ещё раз", BUNDLE)
            self.assertEqual((first["id"], second["id"]), ("poisk-gory-k2", "poisk-gory-k2-2"))
            suite.attach_test(first["id"], "ScenarioPoiskGoryK2Tests", "final class ScenarioPoiskGoryK2Tests: X {}", "r1")
            suite.attach_test(second["id"], "ScenarioPoiskGoryK2Tests", "final class ScenarioPoiskGoryK2Tests: X {}", "r2")
            again = Suite.load(path)
            self.assertEqual(again.get(second["id"])["test"]["class"], "ScenarioPoiskGoryK2Tests2")
            self.assertIn("ScenarioPoiskGoryK2Tests2:", (Path(tmp) / "tests" / "ScenarioPoiskGoryK2Tests2.swift").read_text())
            again.delete(first["id"])
            self.assertFalse((Path(tmp) / "tests" / "ScenarioPoiskGoryK2Tests.swift").exists())
            with self.assertRaises(ValueError):
                again.add("", "  ")

    def test_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "suite.json"
            path.write_text(json.dumps({"scenarios": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}))
            with self.assertRaisesRegex(ValueError, "suite.json: scenario id 'a' is used twice"):
                Suite.load(path)


class ConfigTest(unittest.TestCase):
    def write(self, data) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=tempfile.gettempdir())
        tmp.write(json.dumps(data))
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def test_the_shipped_file_loads(self):
        config = QaConfig.load()
        self.assertEqual(config.for_model("local-qwen3-8b").reasoning_effort, "off")
        self.assertEqual(config.for_model("local-qwen3-8b").max_screen_chars, 3500)

    def test_bad_values_name_the_file(self):
        for data, message in [({"bridge_port": 80}, "bridge_port"), ({"nope": 1}, "unknown setting"),
                              ({"reasoning_effort": "lots"}, "reasoning_effort"),
                              ({"models": {"m": {"max_actions": 0}}}, "models.m")]:
            path = self.write(data)
            with self.assertRaisesRegex(ValueError, f"{path.name}.*{message}|{message}"):
                QaConfig.load(path)


class XcodeAndBridgeTest(unittest.TestCase):
    def test_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ["App.xcodeproj", "App.xcworkspace", "Pods/Pods.xcodeproj", "Sub/Lib.xcodeproj",
                           "App.xcodeproj/project.xcworkspace", "bridge/QABridge.xcodeproj"]:
                (root / folder).mkdir(parents=True)
            found = [c["path"] for c in xcode.find_containers(root)]
            self.assertEqual(found, ["App.xcworkspace", "App.xcodeproj", "Sub/Lib.xcodeproj"])

    def test_test_output(self):
        output = "\n".join([
            "Test Case '-[QABridgeUITests.ScenarioATests testScenario]' started.",
            "/x/ScenarioBTests.swift:21: error: -[QABridgeUITests.ScenarioBTests testScenario] : XCTAssertTrue failed - Not found",
            "Test Case '-[QABridgeUITests.ScenarioATests testScenario]' passed (3.5 seconds).",
            "Test Case '-[QABridgeUITests.ScenarioBTests testScenario]' failed (7.0 seconds).",
        ])
        result = parse_test_output(output, ["ScenarioATests", "ScenarioBTests", "ScenarioCTests"], 65)
        self.assertFalse(result["passed"])
        statuses = {t["class"]: t["status"] for t in result["tests"]}
        self.assertEqual(statuses, {"ScenarioATests": "passed", "ScenarioBTests": "failed", "ScenarioCTests": "not run"})
        self.assertEqual(result["tests"][1]["failures"][0]["line"], 21)
        self.assertIn("ScenarioBTests", suite_markdown([], result))


class ServiceTest(unittest.TestCase):
    def test_suite_and_runs_through_the_service(self):
        from core.runtime import AgentRuntime

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("qa.simctl.devices", return_value=[]):
            rt = AgentRuntime.build(llm=ScriptedQa(), data_dir=tmp, seed=False)
            rt.qa.suite_path = Path(tmp) / "suite" / "suite.json"
            report = rt.qa.report()
            self.assertEqual(report["devices"], [])
            self.assertEqual(report["suite"]["scenarios"], [])
            saved = rt.qa.save_scenario("Search", "Find K2", BUNDLE, "Mountains")
            self.assertEqual(saved["scenario"]["id"], "search")
            with self.assertRaises(Invalid):
                rt.qa.start_run(udid="SIM", bundle_id="", text="x")
            with self.assertRaises(Conflict):
                rt.qa.stop()
            # A passed run, saved as the runner leaves it, becomes a test.
            run = QaRun(id="20261018-000000-abcd", scenario={"id": "search", "title": "Search", "text": "Find K2"},
                        app={"bundle_id": BUNDLE, "name": "Mountains"}, device={}, model="m",
                        folder=rt.qa.dir / "runs" / "20261018-000000-abcd", status="done", verdict="passed")
            run.log.steps = [{"n": 1, "action": "launch", "arguments": {}}]
            run.save()
            made = rt.qa.make_test(run.id)
            self.assertEqual(made["scenario"]["test"]["class"], "ScenarioSearchTests")
            self.assertIn("final class ScenarioSearchTests", rt.qa.test_source("search"))
            self.assertEqual(rt.qa.report()["runs"][0]["test"]["class"], "ScenarioSearchTests")


if __name__ == "__main__":
    unittest.main()
