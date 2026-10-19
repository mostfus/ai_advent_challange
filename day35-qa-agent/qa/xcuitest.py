"""A passed run, turned into an XCUITest that repeats it without a model.

Every step the model took is written down with the element it acted on - its
type, identifier, label, texts - so the test finds each element the way a
person would name it (`app.cells["Lists"]`, `app.searchFields.firstMatch`)
rather than by where it was. Each check becomes an assertion: the evidence
elements must exist (and a switch or a field must hold the value it had), and
the texts listed as absent must not be on the screen.

The helpers the test calls (`QA.launch`, `QA.tap`, ...) are in
`bridge/UITests/QASupport.swift`; the test goes into `qa_suite/tests`, which the
bridge's UI-test target compiles - the app's own project is never touched.

    class_name(title)       `ScenarioMountainsSearchTests`
    generate(run, name)     the Swift source
"""

from __future__ import annotations

import re
import time
import unicodedata

TYPE_QUERIES = {
    "Button": "buttons", "Cell": "cells", "StaticText": "staticTexts", "TextField": "textFields",
    "SecureTextField": "secureTextFields", "SearchField": "searchFields", "Switch": "switches",
    "Toggle": "toggles", "Slider": "sliders", "SegmentedControl": "segmentedControls", "Link": "links",
    "Image": "images", "TextView": "textViews", "Other": "otherElements", "NavigationBar": "navigationBars",
    "CollectionView": "collectionViews", "Table": "tables", "ScrollView": "scrollViews", "Tab": "tabs",
    "PickerWheel": "pickerWheels", "Stepper": "steppers", "MenuItem": "menuItems", "Alert": "alerts",
    "DatePicker": "datePickers", "TabBar": "tabBars", "Toolbar": "toolbars",
}
FIELDS = {"TextField", "SecureTextField", "SearchField", "TextView"}
VALUED = {"Switch", "Toggle", "Slider", "TextField", "SearchField", "TextView", "SegmentedControl", "Stepper"}
SPRINGBOARD = 'XCUIApplication(bundleIdentifier: "com.apple.springboard")'

TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def words(text: str) -> list[str]:
    text = (text or "").lower().translate(TRANSLIT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", text)


def class_name(title: str) -> str:
    parts = [w.capitalize() for w in words(title)][:6] or ["Untitled"]
    name = "Scenario" + "".join(parts) + "Tests"
    return name if not name[8].isdigit() else "Scenario_" + name[8:]


def swift(text: str) -> str:
    """A Swift string literal."""
    out = []
    for ch in str(text):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\u{{{ord(ch):x}}}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def comment(text: str) -> str:
    return " ".join(str(text or "").split())[:160]


def query(element: dict | None) -> str | None:
    """How the test finds `element` - or None when only its position would."""
    if not element:
        return None
    collection = TYPE_QUERIES.get(element.get("type", ""))
    if not collection:
        return None
    root = SPRINGBOARD if element.get("system") else "app"
    identifier, label = element.get("identifier") or "", element.get("label") or ""
    texts = element.get("texts") or []
    if identifier:
        return f"{root}.{collection}[{swift(identifier)}].firstMatch"
    if label:
        return f"{root}.{collection}[{swift(label)}].firstMatch"
    if texts:
        return f"{root}.{collection}.containing(.staticText, identifier: {swift(texts[0])}).firstMatch"
    if element.get("type") in FIELDS:
        return f"{root}.{collection}.firstMatch"
    return None


def point(step: dict) -> tuple[float, float] | None:
    args = step.get("arguments") or {}
    if "x" in args and "y" in args:
        return float(args["x"]), float(args["y"])
    return None


def generate(run: dict, name: str) -> str:
    scenario = run.get("scenario") or {}
    bundle_id = (run.get("app") or {}).get("bundle_id", "")
    steps = run.get("steps") or []
    checks = run.get("checks") or []
    by_step: dict[int, list[dict]] = {}
    for check in checks:
        by_step.setdefault(int(check.get("step") or 0), []).append(check)

    body: list[str] = []

    def emit(line: str = "") -> None:
        body.append(("        " + line) if line else "")

    def emit_checks(after: int) -> None:
        for check in by_step.get(after, []):
            if not check.get("passed"):
                continue
            message = swift(check.get("expectation", ""))
            emit(f"// Check {check['n']}: {comment(check.get('expectation'))}")
            for element in check.get("evidence") or []:
                found = query(element)
                if not found:
                    continue
                if element.get("type") in VALUED and element.get("value"):
                    emit(f"QA.expectValue({found}, {swift(element['value'])}, {message})")
                else:
                    emit(f"QA.expect({found}, {message})")
            examples = check.get("examples") or []
            if examples:
                # Rows that may differ between runs: "the list has rows other than the known ones".
                known = sorted({n for e in check.get("evidence") or [] for n in
                                [e.get("label"), *(e.get("texts") or [])] if n})
                for kind in dict.fromkeys(e.get("type") for e in examples):
                    collection = TYPE_QUERIES.get(kind or "")
                    if collection:
                        listed = ", ".join(swift(n) for n in known)
                        emit(f"QA.expectRows(app.{collection}, besides: [{listed}], {message})")
            for text in check.get("absent") or []:
                emit(f"QA.expectAbsent(app, {swift(text)}, {message})")

    emit(f"let app = QA.launch({swift(bundle_id)})")
    emit_checks(1)
    for step in steps[1:]:
        if step.get("error"):
            continue
        action = step.get("action")
        target = step.get("target")
        found = query(target)
        shown = (target.get("label") or (target.get("texts") or [""])[0]) if target else ""
        where = (f'{target.get("type")} "{shown}"' if shown else str(target.get("type"))) if target else ""
        if action in ("look",):
            emit_checks(step["n"])
            continue
        emit()
        emit(f"// Step {step['n']}: {comment(step.get('why') or action)}" + (f" - {where}" if where else ""))
        if action in ("tap", "long_press"):
            spot = point(step)
            if found:
                emit(f"QA.{'press' if action == 'long_press' else 'tap'}({found})")
            elif spot:
                emit(f"QA.tap(app, x: {spot[0]}, y: {spot[1]})")
        elif action == "type":
            if found:
                emit(f"QA.tap({found})")
            emit(f"QA.type(app, {swift((step.get('arguments') or {}).get('text', ''))})")
        elif action == "scroll":
            args = step.get("arguments") or {}
            start, end = args.get("from") or [0, 0], args.get("to") or [0, 0]
            emit(f"QA.swipe(app, from: ({start[0]}, {start[1]}), to: ({end[0]}, {end[1]}))")
        elif action == "wait":
            emit(f"Thread.sleep(forTimeInterval: {float((step.get('arguments') or {}).get('seconds', 1))})")
        elif action == "launch":
            emit("app.terminate()")
            emit("app.launch()")
        emit_checks(step["n"])

    header = [
        f"// Generated by the QA agent (day 35) from run {run.get('id', '')} on {time.strftime('%Y-%m-%d')}.",
        f"// Scenario: {comment(scenario.get('title') or '')}",
    ]
    header += ["//   " + comment(line) for line in (scenario.get("text") or "").splitlines() if line.strip()]
    header += ["// Re-run it from the QA tab (Run tests) or in CI; regenerate it from a new passed run.",
               "", "import XCTest", "", f"final class {name}: XCTestCase {{",
               "    override func setUp() {", "        continueAfterFailure = false", "    }", "",
               "    func testScenario() {"]
    return "\n".join(header + body + ["    }", "}", ""])
