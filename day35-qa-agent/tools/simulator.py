"""Day 35: the iOS Simulator as tools - what the QA agent sees and does.

    BridgeClient       HTTP to the XCUITest bridge running in the simulator (`bridge/`)
    render_screen()    the app's accessibility tree as the text the model reads, with a
                       ref (`e7`) on every element it may act on or cite
    Screen, Element    that text, and what each ref is: type, label, value, frame
    RunLog             what one run did: the steps (action, why, screen, screenshot), the
                       checks with their evidence, the verdict the model gave, a crash
    SimulatorProvider  the tools, over one app on one simulator:
                         look        the current screen
                         tap         an element, by ref (long_press for a long press)
                         type_text   into a field - tapping it first when a ref is given
                         scroll      a list or the screen, towards a direction
                         launch_app  start the app again, from scratch
                         wait        let the app settle, then look
                         check       one expectation of the scenario: passed or not, and the
                                     elements that prove it
                         finish      the verdict: passed, failed or blocked

The model never sees XCUITest, coordinates it has to compute, or pixels: it
reads a screen as text and acts by ref, and `SimulatorProvider` turns a ref
back into a point on the screen. Every action answers with the screen it led
to, so "tap, then what happened?" is one call - and the calls of one round
run one after another, in order (`Agent(parallel_calls=False)`): two taps
asked together are two taps in a row, never side by side.

Starting the bridge, the simulator and the app is `qa`'s (`qa.bridge`,
`qa.simctl`), and so is finding a crash report (`qa.crash`): the provider is
handed a client, a screenshot callable and a crash lookup, which is what lets
the tests run it against fakes.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from tools.base import Tool, ToolResult

# ----------------------------------------------------------------- the bridge


class BridgeError(RuntimeError):
    """The bridge could not be reached, or a command it ran failed - the message says which."""


class BridgeClient:
    """The bridge's commands (`bridge/UITests/BridgeTests.swift`), one JSON request each."""

    def __init__(self, port: int, host: str = "127.0.0.1", timeout: float = 60.0) -> None:
        self.base = f"http://{host}:{port}"
        self.timeout = timeout

    def call(self, path: str, body: dict | None = None, method: str = "POST",
             timeout: float | None = None) -> dict:
        """The answer as a dict. Raises `BridgeError` when the bridge is not there."""
        try:
            response = httpx.request(method, self.base + path, json=body if method == "POST" else None,
                                     timeout=timeout or self.timeout)
            answer = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BridgeError(f"the simulator bridge did not answer {path}: {exc}") from exc
        return answer if isinstance(answer, dict) else {"ok": False, "error": "not a JSON object"}

    def act(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        """`call`, raising `BridgeError` with the bridge's reason when the command failed."""
        answer = self.call(path, body, timeout=timeout)
        if not answer.get("ok"):
            raise BridgeError(str(answer.get("error") or f"{path} failed"))
        return answer

    def health(self, timeout: float = 2.0) -> bool:
        try:
            return bool(self.call("/health", method="GET", timeout=timeout).get("ok"))
        except BridgeError:
            return False

    def launch(self, bundle_id: str, fresh: bool = True) -> dict:
        return self.act("/launch", {"bundle_id": bundle_id, "fresh": fresh}, timeout=120)

    def tree(self, bundle_id: str) -> dict:
        return self.act("/tree", {"bundle_id": bundle_id})

    def tap(self, x: float, y: float, duration: float = 0.0) -> dict:
        return self.act("/tap", {"x": x, "y": y, "duration": duration})

    def type_text(self, bundle_id: str, text: str) -> dict:
        return self.act("/type", {"bundle_id": bundle_id, "text": text})

    def swipe(self, start: tuple[float, float], end: tuple[float, float]) -> dict:
        return self.act("/swipe", {"from": list(start), "to": list(end)})

    def state(self, bundle_id: str) -> str:
        return str(self.act("/state", {"bundle_id": bundle_id}).get("state") or "unknown")

    def terminate(self, bundle_id: str) -> dict:
        return self.act("/terminate", {"bundle_id": bundle_id})

    def shutdown(self) -> None:
        try:
            self.call("/shutdown", timeout=5)
        except BridgeError:
            pass


# ----------------------------------------------------------------- the screen

#: Elements the model may act on: each gets a ref and a line.
INTERACTIVE = {"Button", "Cell", "TextField", "SecureTextField", "SearchField", "Switch", "Toggle",
               "Slider", "SegmentedControl", "Link", "Tab", "PickerWheel", "Stepper", "MenuItem",
               "TextView", "CheckBox", "RadioButton", "PopUpButton", "MenuButton", "ToolbarButton",
               "DatePicker", "Image", "Other"}
#: Elements that group others: a line of their own, and their elements indented under it.
CONTAINERS = {"NavigationBar", "TabBar", "Toolbar", "Alert", "Sheet", "CollectionView", "Table",
              "ScrollView", "WebView", "Map", "Picker", "SegmentedControl"}
#: Scrollable containers - the ones `scroll` is meant for.
SCROLLABLE = {"CollectionView", "Table", "ScrollView", "WebView"}
#: Never shown.
SKIPPED = {"StatusBar", "Key", "ScrollBar", "Window", "Application"}
#: Types whose value is what the user changed - a check records it.
VALUED = {"Switch", "Toggle", "Slider", "TextField", "SecureTextField", "SearchField", "TextView",
          "SegmentedControl", "PickerWheel", "Stepper", "DatePicker", "CheckBox", "RadioButton"}
SCROLL_BAR = re.compile(r"^(Vertical|Horizontal) scroll bar, (\d+) pages?$")
SHORT_TYPES = {"StaticText": "Text", "SecureTextField": "SecureField"}


@dataclass
class Element:
    """What a ref stands for. `texts` are a cell's or a button's own texts, in order."""

    ref: str
    type: str
    label: str = ""
    identifier: str = ""
    value: str = ""
    placeholder: str = ""
    frame: tuple = (0.0, 0.0, 0.0, 0.0)
    texts: list = field(default_factory=list)
    enabled: bool = True
    selected: bool = False
    system: bool = False

    @property
    def name(self) -> str:
        """What a person would call it: its label, or its first text, or its identifier."""
        return self.label or (self.texts[0] if self.texts else "") or self.identifier

    def center(self, screen: tuple) -> tuple[float, float]:
        """The middle of the part of it that is on the screen."""
        x, y, w, h = self.frame
        sx, sy, sw, sh = screen
        left, top = max(x, sx), max(y, sy)
        right, bottom = min(x + w, sx + sw), min(y + h, sy + sh)
        if right <= left or bottom <= top:
            return (round(x + w / 2, 1), round(y + h / 2, 1))
        return (round((left + right) / 2, 1), round((top + bottom) / 2, 1))

    def describe(self) -> str:
        kind = SHORT_TYPES.get(self.type, self.type)
        name = self.name
        return f'{kind} "{name}"' if name else f"{kind} (no label)"

    def to_dict(self) -> dict:
        return {"ref": self.ref, "type": self.type, "label": self.label, "identifier": self.identifier,
                "value": self.value, "placeholder": self.placeholder, "frame": list(self.frame),
                "texts": list(self.texts), "enabled": self.enabled, "selected": self.selected,
                "system": self.system}


@dataclass
class Screen:
    """One look at the app: the text the model reads, and what its refs are."""

    text: str
    elements: dict
    size: tuple
    state: str = "foreground"
    title: str = ""
    keyboard: bool = False
    signature: str = ""

    def element(self, ref: str) -> Element | None:
        return self.elements.get(str(ref or "").strip().lstrip("[").rstrip("]"))


def _frame(node: dict) -> tuple:
    raw = node.get("frame") or [0, 0, 0, 0]
    try:
        return tuple(float(v) for v in raw[:4])
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)


def _overlap(frame: tuple, screen: tuple) -> bool:
    x, y, w, h = frame
    sx, sy, sw, sh = screen
    return w > 0 and h > 0 and x < sx + sw and x + w > sx and y < sy + sh and y + h > sy


def _texts(node: dict) -> list[str]:
    """The texts under a node, in order, each once."""
    found: list[str] = []

    def walk(n: dict) -> None:
        if n.get("type") == "StaticText":
            text = n.get("label") or n.get("value") or ""
            if text and text not in found:
                found.append(text)
        for child in n.get("children") or []:
            walk(child)

    for child in node.get("children") or []:
        walk(child)
    return found


def _quote(text: str, limit: int = 120) -> str:
    text = " ".join(str(text).split())
    return '"' + (text if len(text) <= limit else text[:limit] + "...") + '"'


class _Renderer:
    def __init__(self, screen: tuple, max_texts: int = 6) -> None:
        self.screen = screen
        self.max_texts = max_texts
        self.lines: list[str] = []
        self.elements: dict[str, Element] = {}
        self.seen: set = set()
        self.below = 0
        self.above = 0
        self.keyboard = False
        self.title = ""
        self.count = 0

    def ref(self) -> str:
        self.count += 1
        return f"e{self.count}"

    def add(self, node: dict, depth: int, *, system: bool = False, kind: str | None = None) -> Element:
        element = Element(ref=self.ref(), type=kind or str(node.get("type") or "Other"),
                          label=str(node.get("label") or node.get("title") or ""),
                          identifier=str(node.get("id") or ""), value=str(node.get("value") or ""),
                          placeholder=str(node.get("placeholder") or ""), frame=_frame(node),
                          enabled=node.get("enabled", True) is not False,
                          selected=bool(node.get("selected")), system=system)
        self.elements[element.ref] = element
        return element

    def duplicate(self, node: dict) -> bool:
        """XCUITest lists some views twice - an accessory, the keyboard's buttons."""
        key = (node.get("type"), node.get("label"), node.get("value"), tuple(node.get("frame") or ()))
        if key in self.seen:
            return True
        self.seen.add(key)
        return False

    def line(self, depth: int, text: str) -> None:
        self.lines.append("  " * depth + text)

    def walk(self, node: dict, depth: int, *, system: bool = False, inside: str = "") -> None:
        kind = str(node.get("type") or "Other")
        label = str(node.get("label") or "")
        frame = _frame(node)
        if kind == "Keyboard":
            self.keyboard = True
            return
        if kind in SKIPPED and kind not in ("Window", "Application"):
            return
        if kind == "Other" and SCROLL_BAR.match(label):
            return
        children = node.get("children") or []
        visible = _overlap(frame, self.screen)

        if kind in ("Window", "Application"):
            for child in children:
                self.walk(child, depth, system=system, inside=inside)
            return

        if kind == "NavigationBar" and visible:
            label = label or str(node.get("id") or "")
            self.title = label or self.title
            self.line(depth, f"NavigationBar {_quote(label)}" if label else "NavigationBar")
            for child in children:
                self.walk(child, depth + 1, system=system, inside="NavigationBar")
            return

        if kind in CONTAINERS and kind != "SegmentedControl":
            if not visible:
                return
            pages = self._pages(node)
            element = self.add(node, depth, system=system)
            hint = f" (scrolls: {pages} pages)" if pages else ""
            head = f"[{element.ref}] {kind}"
            if label:
                head += f" {_quote(label)}"
            self.line(depth, head + hint)
            for child in children:
                self.walk(child, depth + 1, system=system, inside=kind)
            return

        if kind == "StaticText":
            text = label or str(node.get("value") or "")
            if not text:
                return
            if not visible:
                self._count_off(frame)
                return
            if inside == "NavigationBar" and text == self.title:
                return
            if self.duplicate(node):
                return
            element = self.add(node, depth, system=system)
            self.line(depth, f"[{element.ref}] Text {_quote(text)}")
            return

        if kind in INTERACTIVE:
            texts = _texts(node)
            has_name = bool(label or node.get("id") or texts or node.get("value") or node.get("placeholder"))
            # An unnamed image or plain view is decoration; an unnamed button is still a button.
            if kind in ("Image", "Other") and (not label or inside in ("Cell", "Button")):
                for child in children:
                    self.walk(child, depth, system=system, inside=inside)
                return
            if not visible:
                if has_name:
                    self._count_off(frame)
                return
            if self.duplicate(node):
                return
            element = self.add(node, depth, system=system)
            element.texts = texts[: self.max_texts]
            self.line(depth, self._describe(element))
            # A cell's or a button's texts are on its line; what else is in it gets its own.
            for child in children:
                self._walk_controls(child, depth + 1, system=system)
            return

        for child in children:
            self.walk(child, depth, system=system, inside=inside)

    def _walk_controls(self, node: dict, depth: int, *, system: bool) -> None:
        kind = str(node.get("type") or "Other")
        if kind in INTERACTIVE and kind not in ("Image", "Other", "Cell"):
            self.walk(node, depth, system=system, inside="Cell")
            return
        for child in node.get("children") or []:
            self._walk_controls(child, depth, system=system)

    def _describe(self, element: Element) -> str:
        kind = SHORT_TYPES.get(element.type, element.type)
        parts = [f"[{element.ref}] {kind}"]
        name = element.label or (element.texts[0] if element.texts else "")
        if name:
            parts.append(_quote(name))
        extra = [t for t in element.texts if t != name]
        if extra:
            parts.append("- " + " · ".join(_quote(t, 80) for t in extra))
        if element.identifier and element.identifier != element.label:
            parts.append(f"id={element.identifier}")
        if element.type in ("Switch", "Toggle", "CheckBox"):
            parts.append("= on" if element.value in ("1", "true") else "= off")
        elif element.value and element.value != element.label and element.value != element.placeholder:
            parts.append(f"value={_quote(element.value)}")
        elif element.type in ("TextField", "SecureTextField", "SearchField", "TextView") and element.placeholder:
            parts.append(f"empty, placeholder={_quote(element.placeholder)}")
        if not element.name:
            cx, cy = element.center(self.screen)
            parts.append(f"at {cx:.0f},{cy:.0f}")
        if not element.enabled:
            parts.append("(disabled)")
        if element.selected:
            parts.append("(selected)")
        return " ".join(parts)

    def _pages(self, node: dict) -> int:
        pages = 0
        for child in node.get("children") or []:
            match = SCROLL_BAR.match(str(child.get("label") or ""))
            if match:
                pages = max(pages, int(match.group(2)))
        return pages if pages > 1 else 0

    def _count_off(self, frame: tuple) -> None:
        x, y, w, h = frame
        sx, sy, sw, sh = self.screen
        if y >= sy + sh:
            self.below += 1
        elif y + h <= sy:
            self.above += 1


def render_screen(payload: dict, *, max_chars: int = 6000, app_name: str = "") -> Screen:
    """The bridge's `/tree` answer as a `Screen`.

    The tree is cut down to what a tester would look at: controls, texts and
    the containers they sit in, each once, only the part on the screen, and
    a count of what is above or below it. A system alert over the app comes
    first - it is what a tap would land on.
    """
    state = str(payload.get("state") or "unknown")
    tree = payload.get("tree") or {}
    size = _frame(tree)[2:] if tree else (0.0, 0.0)
    screen_rect = (0.0, 0.0, size[0] or 10000.0, size[1] or 10000.0)
    renderer = _Renderer(screen_rect)

    alert = payload.get("system_alert")
    if alert:
        renderer.line(0, "SYSTEM ALERT over the app - answer it first:")
        renderer.walk(alert, 1, system=True, inside="Alert")
        renderer.line(0, "")
    if tree:
        renderer.walk(tree, 0)

    name = app_name or str(tree.get("label") or "")
    head = [f"App: {name}" if name else "App", f"State: {state.replace('_', ' ')}"]
    if size[0]:
        head.append(f"Screen: {size[0]:.0f}x{size[1]:.0f} points")
    lines = [" | ".join(head)]
    if state != "foreground":
        lines.append("The app is not on the screen.")
    body = renderer.lines or (["(nothing on the screen has a label - try waiting, or launch_app)"]
                              if state == "foreground" else [])
    lines.extend(body)
    tail = []
    if renderer.below:
        tail.append(f"{renderer.below} more element(s) below the screen - scroll down to reach them")
    if renderer.above:
        tail.append(f"{renderer.above} more above - scroll up")
    if renderer.keyboard:
        tail.append("The keyboard is up: type_text types into the focused field")
    lines.extend(tail)
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (screen cut - scroll or look at one part at a time)"
    signature = hashlib.sha1(re.sub(r"\[e\d+\] ", "", "\n".join(body)).encode()).hexdigest()[:12]
    return Screen(text=text, elements=renderer.elements, size=tuple(size), state=state,
                  title=renderer.title, keyboard=renderer.keyboard, signature=signature)


# -------------------------------------------------------------------- the run


class RunLog:
    """What one QA run did, shared between the agent's thread and whoever watches it.

    A step is one action and the screen it led to; a check is one expectation
    of the scenario with its evidence; `finish` is the model's verdict and
    `crash` what a crash report said. Everything is plain data, read through
    `snapshot()` under the lock.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = time.time()
        self.steps: list[dict] = []
        self.checks: list[dict] = []
        self.finish: dict | None = None
        self.crash: dict | None = None
        self.cancelled = False

    def add_step(self, step: dict) -> dict:
        with self.lock:
            step["n"] = len(self.steps) + 1
            self.steps.append(step)
            return step

    def add_check(self, check: dict) -> dict:
        with self.lock:
            check["n"] = len(self.checks) + 1
            check["step"] = len(self.steps)
            self.checks.append(check)
            return check

    def set_finish(self, finish: dict) -> None:
        with self.lock:
            self.finish = finish

    def set_crash(self, crash: dict) -> None:
        with self.lock:
            self.crash = crash

    def actions(self) -> int:
        with self.lock:
            return sum(1 for step in self.steps if step.get("action") not in ("launch", "look", "wait"))

    def snapshot(self) -> dict:
        with self.lock:
            return {"steps": [dict(s) for s in self.steps], "checks": [dict(c) for c in self.checks],
                    "finish": dict(self.finish) if self.finish else None,
                    "crash": dict(self.crash) if self.crash else None}


# ------------------------------------------------------------------ the tools

DIRECTIONS = ("down", "up", "left", "right")
STATUSES = ("passed", "failed", "blocked")


class SimulatorProvider:
    """The QA agent's tools over one app (`bundle_id`) in one simulator."""

    source = "simulator"

    def __init__(self, client: BridgeClient, bundle_id: str, log: RunLog, *,
                 screenshot: Callable[[str], str | None] | None = None,
                 crash_lookup: Callable[[str, float], dict | None] | None = None,
                 app_name: str = "", settle_seconds: float = 0.4, max_actions: int = 40,
                 max_screen_chars: int = 6000, max_wait_seconds: float = 10.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client = client
        self.bundle_id = bundle_id
        self.log = log
        self.screenshot = screenshot
        self.crash_lookup = crash_lookup
        self.app_name = app_name
        self.settle_seconds = settle_seconds
        self.max_actions = max_actions
        self.max_screen_chars = max_screen_chars
        self.max_wait_seconds = max_wait_seconds
        self.sleep = sleep
        self.screen: Screen | None = None
        self._lock = threading.Lock()

    # -- what the model is offered ---------------------------------------------

    def tools(self) -> list[Tool]:
        why = {"type": "string", "description": "One short sentence: why this step, in the scenario's words."}
        ref = {"type": "string", "description": "The element's ref from the latest screen, e.g. `e7`."}
        return [
            self._tool("look", self._look, "Look at the app's current screen again, without doing anything.",
                       {}),
            self._tool("tap", self._tap,
                       "Tap an element of the latest screen by its ref. Answers with the screen it led to.",
                       {"ref": ref, "why": why, "long_press": {"type": "boolean",
                                                               "description": "Press and hold (about a second)."}},
                       ["ref", "why"]),
            self._tool("type_text", self._type,
                       "Type text into a field. With `ref`, the field is tapped first so it has focus; "
                       "without it, the text goes to the field that already has focus. Put \\n at the "
                       "end to press Return (to submit a search, say).",
                       {"text": {"type": "string", "description": "What to type."}, "ref": ref, "why": why},
                       ["text", "why"]),
            self._tool("scroll", self._scroll,
                       "Scroll to reveal more: `down` shows what is below, `up` what is above, `left`/"
                       "`right` for a horizontal list. With `ref` (a list, or anything in it) only that "
                       "area scrolls; without it, the middle of the screen.",
                       {"direction": {"type": "string", "enum": list(DIRECTIONS)}, "ref": ref, "why": why},
                       ["direction", "why"]),
            self._tool("launch_app", self._launch,
                       "Start the app again from scratch - when it is somewhere you cannot get back from.",
                       {"why": why}, ["why"]),
            self._tool("wait", self._wait,
                       "Wait for the app to finish something (loading, an animation), then look again.",
                       {"seconds": {"type": "number", "description": f"1 to {self.max_wait_seconds:.0f}."},
                        "why": why}, ["seconds"]),
            self._tool("check", self._check,
                       "Record one expectation of the scenario as checked: what was expected, whether "
                       "the current screen shows it, and the evidence - the refs of the elements that "
                       "prove it, and/or texts that must NOT be on the screen. Call it once per "
                       "expectation, right when the screen shows the answer.",
                       {"expectation": {"type": "string", "description": "The expectation, as the scenario says it."},
                        "passed": {"type": "boolean"},
                        "evidence": {"type": "array", "items": {"type": "string"},
                                     "description": "Refs of the elements that show the expectation holds (or does not) "
                                                    "and would be the same on every run - a title, a switch, the row that was searched for."},
                        "examples": {"type": "array", "items": {"type": "string"},
                                     "description": "Refs of a few rows that only show THAT a list has items, when the rows "
                                                    "themselves may differ between runs (live or random data: networks, a feed, times)."},
                        "absent": {"type": "array", "items": {"type": "string"},
                                   "description": "Texts that must not be on the screen for the expectation to hold."},
                        "note": {"type": "string", "description": "What you saw, in one sentence."}},
                       ["expectation", "passed", "note"]),
            self._tool("finish", self._finish,
                       "End the run with a verdict: `passed` - every step done and every check passed; "
                       "`failed` - a check failed or the app misbehaved (crashed, wrong screen, error); "
                       "`blocked` - the scenario could not be carried out (no such screen or element).",
                       {"status": {"type": "string", "enum": list(STATUSES)},
                        "summary": {"type": "string", "description": "Two or three sentences for the person."}},
                       ["status", "summary"]),
        ]

    def guide(self) -> str:
        return ""

    def _tool(self, name: str, run: Callable[[dict], str], description: str, properties: dict,
              required: list | None = None) -> Tool:
        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        def call(arguments: dict) -> ToolResult:
            with self._lock:  # one action at a time, whatever the caller does
                if self.log.cancelled:
                    return ToolResult(ok=False, is_error=True, tool=name, server_id=self.source,
                                      text="Error: the run was stopped by the person. Do not call any "
                                           "more tools - answer with what you found so far.")
                try:
                    text = run(arguments or {})
                except BridgeError as exc:
                    return ToolResult(ok=False, is_error=True, text=f"Error: {exc}", tool=name,
                                      server_id=self.source)
            return ToolResult(ok=True, text=text, tool=name, server_id=self.source)

        return Tool(name=name, description=description, input_schema=schema, call=call, source=self.source)

    # -- seeing ------------------------------------------------------------------

    def observe(self, action: str, *, why: str = "", target: Element | None = None,
                arguments: dict | None = None, error: str = "") -> tuple[Screen, dict]:
        """Look at the screen after an action, and write the step down."""
        started = time.time()
        payload = self.client.tree(self.bundle_id)
        screen = render_screen(payload, max_chars=self.max_screen_chars, app_name=self.app_name)
        before = self.screen
        self.screen = screen
        step = {
            "action": action, "why": why, "arguments": arguments or {},
            "target": target.to_dict() if target else None,
            "error": error, "screen": screen.text, "title": screen.title, "state": screen.state,
            "changed": before is None or before.signature != screen.signature,
            "at": round(started - self.log.started, 2), "screenshot": None,
        }
        if self.screenshot is not None:
            step["screenshot"] = self.screenshot(f"step-{len(self.log.steps) + 1:02d}")
        if screen.state == "not_running" and self.log.crash is None:
            self.log.set_crash(self._find_crash())
        return screen, self.log.add_step(step)

    def _find_crash(self) -> dict:
        report = None
        if self.crash_lookup is not None:
            # The report is written a moment after the process dies.
            for _ in range(6):
                report = self.crash_lookup(self.bundle_id, self.log.started)
                if report:
                    break
                self.sleep(0.5)
        return report or {"summary": "The app stopped running, and no crash report was found for it."}

    def _answer(self, done: str, screen: Screen, step: dict) -> str:
        parts = [done]
        if not step["changed"] and step["action"] not in ("look", "launch"):
            parts.append("(The screen did not change.)")
        if screen.state == "not_running":
            crash = self.log.crash or {}
            parts.append("THE APP IS NOT RUNNING ANY MORE - it crashed or quit. "
                         + (crash.get("summary") or "")
                         + " Record what was expected with check (passed false), then finish with status failed.")
        elif screen.state != "foreground":
            parts.append("The app went to the background - another app is in front of it.")
        parts.append("")
        parts.append(screen.text)
        return "\n".join(parts)

    def start(self, fresh: bool = True) -> str:
        """Launch the app for the run - the first step - and return its screen."""
        self.client.launch(self.bundle_id, fresh=fresh)
        self.sleep(self.settle_seconds)
        screen, step = self.observe("launch", why="Start the app for the scenario")
        return screen.text

    def _rect(self) -> tuple:
        size = self.screen.size if self.screen is not None else ()
        return (0.0, 0.0, *size) if len(size) == 2 and size[0] else (0.0, 0.0, 10000.0, 10000.0)

    def _need_screen(self) -> Screen:
        if self.screen is None:
            self.observe("look")
        return self.screen

    def _element(self, ref) -> Element:
        screen = self._need_screen()
        element = screen.element(ref)
        if element is None:
            known = ", ".join(list(screen.elements)[:8])
            raise BridgeError(f"there is no element {ref!r} on the latest screen (refs change with every "
                              f"screen; these are on it now: {known}...). Use a ref from the latest screen.")
        return element

    def _budget(self) -> None:
        if self.log.actions() >= self.max_actions:
            raise BridgeError(f"the run has used its {self.max_actions} actions. Record the checks you can "
                              f"and call finish (blocked, if the scenario could not be completed).")

    # -- the tools -----------------------------------------------------------------

    def _look(self, args: dict) -> str:
        screen, step = self.observe("look", why=str(args.get("why") or ""))
        return self._answer("The current screen:", screen, step)

    def _tap(self, args: dict) -> str:
        self._budget()
        element = self._element(args.get("ref"))
        x, y = element.center(self._rect())
        long_press = bool(args.get("long_press"))
        self.client.tap(x, y, duration=1.0 if long_press else 0.0)
        self.sleep(self.settle_seconds)
        action = "long_press" if long_press else "tap"
        screen, step = self.observe(action, why=str(args.get("why") or ""), target=element,
                                    arguments={"ref": element.ref, "x": x, "y": y})
        return self._answer(f"Done: {action} on [{element.ref}] {element.describe()}.", screen, step)

    def _type(self, args: dict) -> str:
        self._budget()
        text = str(args.get("text") or "")
        if not text:
            raise BridgeError("text is empty")
        target = None
        if args.get("ref"):
            target = self._element(args.get("ref"))
            x, y = target.center(self._rect())
            self.client.tap(x, y)
            self.sleep(self.settle_seconds)
        try:
            self.client.type_text(self.bundle_id, text)
        except BridgeError as exc:
            raise BridgeError(f"{exc}. Tap the field first (pass its ref).") from exc
        self.sleep(self.settle_seconds)
        screen, step = self.observe("type", why=str(args.get("why") or ""), target=target,
                                    arguments={"text": text, "ref": target.ref if target else None})
        into = f" into [{target.ref}] {target.describe()}" if target else ""
        return self._answer(f"Done: typed {_quote(text)}{into}.", screen, step)

    def _scroll(self, args: dict) -> str:
        self._budget()
        direction = str(args.get("direction") or "down").lower()
        if direction not in DIRECTIONS:
            raise BridgeError(f"direction is one of {', '.join(DIRECTIONS)}")
        screen = self._need_screen()
        width, height = screen.size if screen.size else (390.0, 844.0)
        target = None
        if args.get("ref"):
            target = self._element(args.get("ref"))
            x, y, w, h = target.frame
            left, top = max(x, 0), max(y, 0)
            right, bottom = min(x + w, width), min(y + h, height)
        else:
            left, top, right, bottom = 0.0, height * 0.2, width, height * 0.85
        cx, cy = (left + right) / 2, (top + bottom) / 2
        span_y, span_x = (bottom - top) * 0.3, (right - left) * 0.3
        moves = {"down": ((cx, cy + span_y), (cx, cy - span_y)), "up": ((cx, cy - span_y), (cx, cy + span_y)),
                 "right": ((cx + span_x, cy), (cx - span_x, cy)), "left": ((cx - span_x, cy), (cx + span_x, cy))}
        start, end = moves[direction]
        start, end = tuple(round(v, 1) for v in start), tuple(round(v, 1) for v in end)
        self.client.swipe(start, end)
        self.sleep(self.settle_seconds)
        screen, step = self.observe("scroll", why=str(args.get("why") or ""), target=target,
                                    arguments={"direction": direction, "from": list(start), "to": list(end),
                                               "ref": target.ref if target else None})
        return self._answer(f"Done: scrolled {direction}.", screen, step)

    def _launch(self, args: dict) -> str:
        self._budget()
        self.client.launch(self.bundle_id, fresh=True)
        self.sleep(self.settle_seconds)
        screen, step = self.observe("launch", why=str(args.get("why") or ""))
        return self._answer("Done: the app was started again from scratch.", screen, step)

    def _wait(self, args: dict) -> str:
        try:
            seconds = float(args.get("seconds") or 1)
        except (TypeError, ValueError):
            seconds = 1.0
        seconds = min(max(seconds, 0.5), self.max_wait_seconds)
        self.sleep(seconds)
        screen, step = self.observe("wait", why=str(args.get("why") or ""), arguments={"seconds": seconds})
        return self._answer(f"Waited {seconds:g} s.", screen, step)

    def _check(self, args: dict) -> str:
        screen = self._need_screen()
        expectation = str(args.get("expectation") or "").strip()
        if not expectation:
            raise BridgeError("expectation is empty")
        evidence, examples, unknown = [], [], []
        for key, found in (("evidence", evidence), ("examples", examples)):
            for ref in args.get(key) or []:
                element = screen.element(ref)
                (found.append(element.to_dict()) if element else unknown.append(str(ref)))
        absent = [str(t).strip() for t in (args.get("absent") or []) if str(t).strip()]
        # An "absent" text that is on the screen is a contradiction the model should see.
        present = [t for t in absent if any(t in (e.label, e.value, *e.texts) for e in screen.elements.values())]
        steps = self.log.steps
        check = self.log.add_check({
            "expectation": expectation, "passed": bool(args.get("passed")),
            "note": str(args.get("note") or ""), "evidence": evidence, "examples": examples, "absent": absent,
            "screenshot": steps[-1].get("screenshot") if steps else None,
            "title": screen.title,
        })
        mark = "passed" if check["passed"] else "FAILED"
        answer = f"Recorded check {check['n']} ({mark}): {expectation}"
        if unknown:
            answer += f"\nNot on the latest screen, so not kept as evidence: {', '.join(unknown)}."
        if present and check["passed"]:
            answer += (f"\nCareful: {', '.join(_quote(t) for t in present)} IS on the screen, though you "
                       f"listed it as absent. Record the check again if that changes the result.")
        return answer

    def _finish(self, args: dict) -> str:
        status = str(args.get("status") or "").lower()
        if status not in STATUSES:
            raise BridgeError(f"status is one of {', '.join(STATUSES)}")
        self.log.set_finish({"status": status, "summary": str(args.get("summary") or "").strip()})
        return "Recorded. Now answer the person in two or three sentences: the verdict and why."
