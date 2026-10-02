"""Day 20: several MCP servers, one agent - routing, the flow, and its checks.

    uv run python -m unittest discover -s tests -v

Two halves.

**The orchestrator on its own** (`RoutingTest`, `FlowTest`, `ChecksTest`):
routing against a caller made of dicts - each call reaches the server that
owns it, a name that does not route says which names would have, a server
that failed is not called again, an identical call is answered once - and
then what `build_flow` makes of a turn and what `scenarios.check` makes of
the flow, including the orders and handoffs it must refuse.

**The whole flow, over the wire** (`LongFlowTest`): four real MCP servers of
ours - events, weather, maps, planner - each started on its own port and
reached over HTTP by the hand-written client, exactly as the app reaches
them. Only the world outside them is fake: Google and Open-Meteo answer from
dicts, the events database is a temp file with two jazz nights in it. And the
model is a script that *reads the tool results it is given*: it picks the
concert on the evening the weather server says is dry, meets at the place
plan_meetup ranks first, measures the trip from there to the venue the
listing gave, and saves a plan out of all of it. So if a value is lost or
changed on its way from one server to the next, the plan on disk comes out
wrong - and the test reads the plan on disk.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
import mcp_client  # noqa: E402
import orchestrator  # noqa: E402
import scenarios  # noqa: E402
from agent import Agent, AgentConfig  # noqa: E402
from llm_client import LLMResponse  # noqa: E402
from mcp_client import ToolResult  # noqa: E402
from store import McpServer  # noqa: E402


def catalogue(*tools: str, instructions: str = "") -> dict:
    return {"ok": True, "instructions": instructions, "tools": [
        {"name": t, "title": t, "description": f"does {t}",
         "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}
        for t in tools]}


SERVERS = [
    McpServer(id="weather", label="Weather", url="http://w/mcp",
              catalogue=catalogue("get_forecast", instructions="Forecasts.")),
    McpServer(id="google-maps", label="Maps", url="http://m/mcp",
              catalogue=catalogue("compute_distance", "plan_meetup")),
    McpServer(id="planner", label="Planner", url="http://p/mcp",
              catalogue=catalogue("save_plan", "list_plans")),
    McpServer(id="deepwiki", label="DeepWiki", url="http://d/mcp", enabled=False,
              catalogue=catalogue("ask_question")),
    McpServer(id="broken", label="Never answered", url="http://b/mcp",
              catalogue={"ok": False, "error": "connection refused"}),
]


class FakeCaller:
    """`(route, arguments) -> ToolResult`, from a dict keyed by server/tool."""

    def __init__(self, answers: dict | None = None, down: tuple = ()) -> None:
        self.answers = answers or {}
        self.down = set(down)
        self.calls: list = []
        self.lock = threading.Lock()

    def __call__(self, route, arguments):
        with self.lock:
            self.calls.append((route.server_id, route.url, route.tool, dict(arguments)))
        if route.server_id in self.down:
            return ToolResult(ok=False, is_error=True, text="Error: connection refused",
                              transport_error="POST initialize: connection refused")
        answer = self.answers.get(f"{route.server_id}/{route.tool}", {"echo": arguments})
        if callable(answer):
            answer = answer(arguments)
        return ToolResult(ok=True, text=json.dumps(answer), structured=answer, elapsed_ms=5)


def build(caller=None, servers=SERVERS) -> orchestrator.Orchestrator:
    return orchestrator.Orchestrator.from_servers(
        servers, caller or FakeCaller(), notes={}, today=datetime(2026, 9, 27, 12, 0))


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


class RoutingTest(unittest.TestCase):
    def test_only_live_servers_are_offered(self) -> None:
        box = build()
        names = [f["function"]["name"] for f in box.functions]
        self.assertEqual(names, ["weather__get_forecast", "google-maps__compute_distance",
                                 "google-maps__plan_meetup", "planner__save_plan",
                                 "planner__list_plans"])
        self.assertEqual([c.id for c in box.cards], ["weather", "google-maps", "planner"])

    def test_the_guide_names_every_server_its_tools_and_today(self) -> None:
        guide = build().guide()
        self.assertIn("Today is Sunday, 2026-09-27", guide)
        self.assertIn("3 MCP servers", guide)
        self.assertIn("* weather - Weather: Forecasts.\n  tools: weather__get_forecast", guide)
        self.assertIn("google-maps__compute_distance, google-maps__plan_meetup", guide)
        self.assertIn("comes last", guide)
        self.assertNotIn("deepwiki", guide)

    def test_one_server_needs_no_guide(self) -> None:
        self.assertEqual(build(servers=SERVERS[:1]).guide(), "")

    def test_each_call_reaches_the_server_that_owns_it(self) -> None:
        caller = FakeCaller()
        box = build(caller)
        box.run("planner__save_plan", {"q": "a"})
        box.run("weather__get_forecast", {"q": "b"})
        result = box.run("google-maps__plan_meetup", {"q": "c"})
        self.assertEqual([(s, u, t) for s, u, t, _ in caller.calls], [
            ("planner", "http://p/mcp", "save_plan"),
            ("weather", "http://w/mcp", "get_forecast"),
            ("google-maps", "http://m/mcp", "plan_meetup"),
        ])
        self.assertEqual((result.server_id, result.tool), ("google-maps", "plan_meetup"))

    def test_the_right_tool_on_the_wrong_server_is_named(self) -> None:
        caller = FakeCaller()
        result = build(caller).run("google-maps__get_forecast", {})
        self.assertFalse(result.ok)
        self.assertEqual(caller.calls, [])
        self.assertEqual(result.routing_error, "unknown tool")
        self.assertIn("Maps has no tool 'get_forecast'", result.text)
        self.assertIn("Did you mean weather__get_forecast?", result.text)

    def test_a_bare_tool_name_is_pointed_at_its_server(self) -> None:
        result = build().run("save_plan", {})
        self.assertIn("'save_plan' has no server in front of it", result.text)
        self.assertIn("Did you mean planner__save_plan?", result.text)

    def test_a_server_that_is_not_connected_is_said_to_be(self) -> None:
        result = build().run("deepwiki__ask_question", {})
        self.assertIn("there is no server 'deepwiki' connected", result.text)

    def test_a_misspelt_name_gets_the_nearest(self) -> None:
        result = build().run("weather__get_forcast", {})
        self.assertIn("Did you mean weather__get_forecast?", result.text)

    def test_a_server_that_failed_is_not_called_again_this_turn(self) -> None:
        caller = FakeCaller(down=("google-maps",))
        box = build(caller)
        first = box.run("google-maps__plan_meetup", {"q": "a"})
        second = box.run("google-maps__compute_distance", {"q": "b"})
        other = box.run("weather__get_forecast", {"q": "c"})
        self.assertTrue(first.transport_error)
        self.assertEqual(second.routing_error, "server down")
        self.assertIn("did not answer earlier in this turn", second.text)
        self.assertTrue(other.ok)
        self.assertEqual([c[2] for c in caller.calls], ["plan_meetup", "get_forecast"])
        # A new turn is a new orchestrator: the server is tried again.
        build(caller).run("google-maps__compute_distance", {"q": "b"})
        self.assertEqual(caller.calls[-1][2], "compute_distance")

    def test_an_identical_call_is_answered_once(self) -> None:
        caller = FakeCaller()
        box = build(caller)
        first = box.run("weather__get_forecast", {"q": "Cape Town", "days": 3})
        again = box.run("weather__get_forecast", {"days": 3, "q": "Cape Town"})
        other = box.run("weather__get_forecast", {"q": "Cape Town", "days": 4})
        self.assertEqual(len(caller.calls), 2)
        self.assertFalse(first.cached)
        self.assertTrue(again.cached)
        self.assertEqual(again.structured, first.structured)
        self.assertFalse(other.cached)


# --------------------------------------------------------------------------
# The agent's side: rounds, parallel calls, the budget
# --------------------------------------------------------------------------


def reply(message: dict) -> LLMResponse:
    return LLMResponse(request={}, response={"choices": [{"message": message}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                                       "total_tokens": 12}},
                       url="fake", status_code=200, elapsed_ms=1.0)


def tool_call(n: int, name: str, arguments: dict) -> dict:
    return {"id": f"call_{n}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class Script:
    """An LLM client that plays a list of turns, one per request, and keeps the bodies."""

    redacted_headers: dict = {}

    def __init__(self, *turns) -> None:
        self.turns = list(turns)
        self.bodies: list = []

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(json.loads(json.dumps(body)))
        turn = self.turns.pop(0)
        return reply(turn(body) if callable(turn) else turn)


class AgentLoopTest(unittest.TestCase):
    def test_calls_asked_together_run_side_by_side_and_come_back_in_order(self) -> None:
        barrier = threading.Barrier(2, timeout=3)

        def slow(arguments):
            barrier.wait()   # breaks - and fails the test - if the calls ran one after the other
            return {"q": arguments["q"]}

        box = build(FakeCaller({"weather/get_forecast": slow, "google-maps/plan_meetup": slow}))
        script = Script(
            {"content": "", "tool_calls": [tool_call(1, "weather__get_forecast", {"q": "w"}),
                                           tool_call(2, "google-maps__plan_meetup", {"q": "m"})]},
            {"content": "done"},
        )
        answer = Agent(script, AgentConfig(), tools=box.functions, tool_runner=box.run,
                       routing=box.guide()).ask("go")
        self.assertEqual(answer.answer, "done")
        self.assertEqual([c["name"] for c in answer.tool_calls],
                         ["weather__get_forecast", "google-maps__plan_meetup"])
        self.assertEqual([c["round"] for c in answer.tool_calls], [1, 1])
        tool_messages = [m for m in script.bodies[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call_1", "call_2"])
        self.assertEqual(json.loads(tool_messages[0]["content"]), {"q": "w"})

    def test_the_guide_goes_right_after_the_system_prompt(self) -> None:
        box = build()
        script = Script({"content": "hi"})
        Agent(script, AgentConfig(system_prompt="You are a helpful assistant."),
              tools=box.functions, tool_runner=box.run, routing=box.guide(),
              invariants="no ORMs").ask("hello")
        messages = script.bodies[0]["messages"]
        self.assertEqual(messages[0]["content"], "You are a helpful assistant.")
        self.assertTrue(messages[1]["content"].startswith(orchestrator.GUIDE_HEAD))
        self.assertTrue(messages[-2]["content"].endswith("no ORMs"))

    def test_the_last_round_may_not_call_another_tool(self) -> None:
        box = build()
        ask_again = {"content": "", "tool_calls": [tool_call(1, "weather__get_forecast", {"q": "x"})]}
        script = Script(*([ask_again] * agent_module.MAX_TOOL_ROUNDS), {"content": "what I have"})
        answer = Agent(script, AgentConfig(), tools=box.functions, tool_runner=box.run).ask("loop")
        self.assertEqual(answer.answer, "what I have")
        self.assertEqual(len(script.bodies), agent_module.MAX_TOOL_ROUNDS + 1)
        self.assertNotIn("tool_choice", script.bodies[-2])
        self.assertEqual(script.bodies[-1]["tool_choice"], "none")
        # Ten identical calls, one of them made: the rest came from the first.
        self.assertEqual(sum(1 for c in answer.tool_calls if not c["cached"]), 1)

    def test_calls_past_the_budget_are_answered_not_run(self) -> None:
        caller = FakeCaller()
        box = build(caller)
        many = [tool_call(i, "weather__get_forecast", {"q": str(i)})
                for i in range(agent_module.MAX_TOOL_CALLS + 2)]
        script = Script({"content": "", "tool_calls": many}, {"content": "ok"})
        answer = Agent(script, AgentConfig(), tools=box.functions, tool_runner=box.run).ask("x")
        self.assertEqual(len(caller.calls), agent_module.MAX_TOOL_CALLS)
        self.assertEqual(len(answer.tool_calls), agent_module.MAX_TOOL_CALLS + 2)
        self.assertIn("used its", answer.tool_calls[-1]["error"])
        tools = [m for m in script.bodies[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tools), len(many))


# --------------------------------------------------------------------------
# The flow, and the checks
# --------------------------------------------------------------------------


def record(n, name, arguments, structured, round_no, ok=True, **extra):
    server, _, tool = name.partition("__")
    return {"id": f"call_{n}", "name": name, "arguments": arguments, "ok": ok,
            "structured": structured, "text": json.dumps(structured), "round": round_no,
            "server_id": server, "tool": tool, "cached": False, "routing_error": "",
            "elapsed_ms": 10, **extra}


QUESTION = "Anna is on Kloof St 10, Ben on Bree St 80. Jazz on Saturday, dinner first, save it."

GOOD_CALLS = [
    record(1, "cape-town-events__get_summary", {"interest": "jazz"},
           {"events": [{"title": "Kyle Shepherd Trio", "venue": "The Alma Cafe, Rosebank",
                        "starts_at": "2026-10-03T19:30:00+02:00", "url": "https://alma/kyle"}]}, 1),
    record(2, "weather__get_forecast", {"location": "Cape Town", "days": 7},
           {"days": [{"date": "2026-10-03", "verdict": "dry"}]}, 1),
    record(3, "google-maps__plan_meetup", {"people": ["Anna: Kloof St 10", "Ben: Bree St 80"],
                                           "query": "dinner"},
           {"options": [{"name": "Rooftop Coffee", "address": "Loop St", "lat": -33.924,
                         "lng": 18.4205}]}, 2),
    record(4, "google-maps__compute_distance", {"origin": "-33.924,18.4205",
                                                "destination": "The Alma Cafe, Rosebank"},
           {"duration_text": "13 min", "distance_text": "5.2 km"}, 3),
    record(5, "planner__save_plan", {"title": "Jazz night", "day": "2026-10-03", "steps": [
        {"time": "17:30", "title": "Dinner", "place": "Rooftop Coffee, Loop St"},
        {"time": "19:30", "title": "Kyle Shepherd Trio", "place": "The Alma Cafe, Rosebank",
         "note": "13 min by car", "link": "https://alma/kyle"}]},
           {"plan_id": "2026-10-03-jazz-night"}, 4),
]


class FlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = orchestrator.build_flow(GOOD_CALLS, QUESTION, {"weather": "Weather"})
        self.steps = {s["n"]: s for s in self.flow["steps"]}

    def inputs(self, n: int) -> dict:
        return {i["arg"]: i for i in self.steps[n]["inputs"]}

    def test_the_shape_of_the_turn(self) -> None:
        self.assertEqual(self.flow["calls"], 5)
        self.assertEqual(self.flow["rounds"], 4)
        self.assertEqual(self.flow["parallel_rounds"], [1])
        self.assertEqual(self.flow["servers"], ["cape-town-events", "weather", "google-maps", "planner"])
        self.assertEqual(self.steps[2]["label"], "Weather")
        self.assertIn("5 calls in 4 rounds across 4 servers", self.flow["summary"])

    def test_arguments_are_traced_to_the_call_that_produced_them(self) -> None:
        distance = self.inputs(4)
        self.assertEqual(distance["origin"]["from"], [3])        # the place plan_meetup ranked first
        self.assertEqual(distance["destination"]["from"], [1])   # the venue from the listing
        plan = self.inputs(5)
        self.assertEqual(plan["day"]["from"], [1, 2])            # in the listing and the forecast
        self.assertEqual(plan["steps[1].time"]["from"], [1])     # 19:30 out of 2026-10-03T19:30
        self.assertEqual(plan["steps[0].place"]["from"], [3])
        self.assertEqual(plan["steps[1].link"]["from"], [1])
        self.assertEqual(plan["steps[1].note"]["from"], [4])     # "13 min" of "13 min by car"
        self.assertEqual(plan["title"]["origin"], "model")       # "Jazz night" is the model's words
        self.assertEqual(plan["steps[0].time"]["origin"], "model")
        self.assertEqual(self.steps[5]["fed_by"], [1, 2, 3, 4])

    def test_the_user_is_a_source_too(self) -> None:
        people = self.inputs(3)["people[0]"]
        self.assertEqual((people["origin"], people["user"]), ("user", True))

    def test_calls_in_the_same_round_did_not_feed_each_other(self) -> None:
        # The forecast shares its date with the listing, but they ran side by side.
        self.assertEqual(self.steps[2]["fed_by"], [])
        self.assertTrue(all(h["from"] < h["to"] for h in self.flow["handoffs"]))
        self.assertGreaterEqual(self.flow["cross_server"], 4)

    def test_short_words_in_prose_are_not_handoffs(self) -> None:
        calls = [record(1, "events__get", {}, {"description": "live jazz and dinner every friday " * 5}, 1),
                 record(2, "maps__find", {"query": "dinner"}, {}, 2)]
        flow = orchestrator.build_flow(calls)
        self.assertEqual(flow["steps"][1]["fed_by"], [])


class ChecksTest(unittest.TestCase):
    scenario = scenarios.find("evening-out")

    def checks(self, calls: list) -> dict:
        return scenarios.check(self.scenario, orchestrator.build_flow(calls, QUESTION))

    def results(self, calls: list) -> dict:
        return {r["text"]: r for r in self.checks(calls)["results"]}

    def test_the_good_flow_passes_everything(self) -> None:
        checks = self.checks(GOOD_CALLS)
        self.assertTrue(checks["ok"], [r for r in checks["results"] if not r["ok"]])
        self.assertEqual(checks["passed"], checks["total"])

    def test_saving_before_measuring_fails_order_and_last(self) -> None:
        swapped = [dict(c) for c in GOOD_CALLS]
        swapped[3]["round"], swapped[4]["round"] = 5, 4
        swapped = [swapped[0], swapped[1], swapped[2], swapped[4], swapped[3]]
        results = self.results(swapped)
        self.assertFalse(results["ends with planner/save_plan"]["ok"])
        self.assertIn("last was #5 google-maps/compute_distance",
                      results["ends with planner/save_plan"]["detail"])

    def test_side_by_side_is_not_before(self) -> None:
        same = [dict(c) for c in GOOD_CALLS]
        same[2]["round"] = same[3]["round"] = 2
        results = self.results(same)
        check = results["google-maps/plan_meetup before google-maps/compute_distance"]
        self.assertFalse(check["ok"])
        self.assertIn("side by side", check["detail"])
        # ...and the place it would have measured from cannot have come from it either.
        self.assertFalse(results["google-maps/plan_meetup feeds google-maps/compute_distance"]["ok"])

    def test_a_value_that_did_not_travel_fails_feeds(self) -> None:
        typed = [dict(c) for c in GOOD_CALLS]
        typed[3] = dict(typed[3], arguments={"origin": "Somewhere Else", "destination": "Alma"})
        results = self.results(typed)
        self.assertFalse(results["google-maps/plan_meetup feeds google-maps/compute_distance"]["ok"])
        self.assertFalse(results["cape-town-events/get_summary feeds google-maps/compute_distance"]["ok"])

    def test_a_stray_server_and_a_routing_error_are_caught(self) -> None:
        extra = GOOD_CALLS[:4] + [
            record(5, "deepwiki__ask_question", {"q": "jazz"}, {}, 4, ok=False,
                   routing_error="unknown tool"),
            dict(GOOD_CALLS[4], round=5),
        ]
        results = self.results(extra)
        self.assertFalse(results["only cape-town-events, weather, google-maps, planner"]["ok"])
        self.assertFalse(results["every call routed"]["ok"])
        self.assertTrue(results["ends with planner/save_plan"]["ok"])

    def test_a_tool_that_never_succeeded_is_not_called(self) -> None:
        failed = GOOD_CALLS[:4] + [dict(GOOD_CALLS[4], ok=False, error="out of order")]
        results = self.results(failed)
        self.assertEqual(results["calls planner/save_plan"]["detail"], "called 1x, never succeeded")

    def test_a_turn_that_called_nothing_passes_nothing(self) -> None:
        checks = self.checks([])
        self.assertEqual(checks["passed"], 0)
        self.assertTrue(all(r["detail"] == "no tool was called" for r in checks["results"]))

    def test_every_scenario_names_servers_the_app_knows(self) -> None:
        known = {k["id"] for k in mcp_client.KNOWN_SERVERS}
        for scenario in scenarios.SCENARIOS:
            self.assertTrue(set(scenario.servers) <= known, scenario.id)
            for expect in scenario.expect:
                for ref in (expect.a, expect.b):
                    if ref:
                        self.assertIn(scenarios.split(ref)[0], scenario.servers, (scenario.id, ref))


# --------------------------------------------------------------------------
# The whole flow, over the wire: four real servers, one scripted model
# --------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """One ASGI app on its own port, in a thread, for as long as the test runs."""

    def __init__(self, app) -> None:
        import uvicorn

        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def fake_open_meteo(rainy: set):
    """Open-Meteo, answered from the request: every day dry except the ones in `rainy`."""
    def http_get(url, params):
        if "geocoding" in url:
            return {"results": [{"name": "Cape Town", "latitude": -33.92, "longitude": 18.42,
                                 "country": "South Africa", "admin1": "Western Cape",
                                 "population": 4_000_000}]}
        first = date.fromisoformat(params["start_date"]) if "start_date" in params else date.today()
        last = (date.fromisoformat(params["end_date"]) if "end_date" in params
                else first + timedelta(days=params["forecast_days"] - 1))
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        wet = [d.isoformat() in rainy for d in days]
        hours = [f"{d.isoformat()}T{h:02d}:00" for d in days for h in range(24)]
        return {
            "timezone": "Africa/Johannesburg",
            "daily": {"time": [d.isoformat() for d in days],
                      "weather_code": [63 if w else 1 for w in wet],
                      "temperature_2m_min": [12.0] * len(days), "temperature_2m_max": [21.0] * len(days),
                      "precipitation_probability_max": [85 if w else 5 for w in wet],
                      "precipitation_sum": [7.5 if w else 0.0 for w in wet],
                      "wind_speed_10m_max": [18.0] * len(days)},
            "hourly": {"time": hours,
                       "precipitation_probability": [85 if w else 5 for w in wet for _ in range(24)],
                       "temperature_2m": [16.0] * len(hours)},
        }
    return http_get


class FakeRoutesAndPlaces:
    """day 19's FakeGoogle for places and the matrix, plus computeRoutes."""

    def __init__(self) -> None:
        from test_meetup import FakeGoogle

        self.places = FakeGoogle()
        self.routes: list = []

    def __call__(self, url, body, field_mask, expect=dict):
        import maps_mcp_server as maps

        if url == maps.ROUTES_URL:
            self.routes.append(body)
            return {"routes": [{"distanceMeters": 5200, "duration": "780s",
                                "localizedValues": {"distance": {"text": "5.2 km"},
                                                    "duration": {"text": "13 min"}}}]}
        return self.places(url, body, field_mask, expect)


class ReadsItsTools:
    """A model that works the evening-out flow out of what the tools returned.

    It never hard-codes a place, a venue, a date or a time: every one of them
    is read out of a `tool` message. Which is why the plan on disk is a test
    of every handoff in between.
    """

    redacted_headers: dict = {}

    def __init__(self, people: list) -> None:
        self.people = people
        self.bodies: list = []

    @staticmethod
    def results(messages: list) -> dict:
        names = {}
        for message in messages:
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["function"]["name"]
        return {names[m["tool_call_id"]]: json.loads(m["content"])
                for m in messages if m["role"] == "tool"}

    def complete(self, body: dict) -> LLMResponse:
        self.bodies.append(body)
        got = self.results(body["messages"])
        n = len(self.bodies)
        if n == 1:
            return reply({"content": "", "tool_calls": [
                tool_call(1, "cape-town-events__get_summary", {"days_ahead": 7, "interest": "jazz"}),
                tool_call(2, "weather__get_forecast", {"location": "Cape Town", "days": 7}),
            ]})
        events = got["cape-town-events__get_summary"]["events"]
        verdicts = {d["date"]: d["verdict"] for d in got["weather__get_forecast"]["days"]}
        concert = next(e for e in events if verdicts.get(e["starts_at"][:10]) == "dry")
        if n == 2:
            return reply({"content": "", "tool_calls": [
                tool_call(3, "google-maps__plan_meetup", {"people": self.people, "query": "dinner"})]})
        place = got["google-maps__plan_meetup"]["options"][0]
        if n == 3:
            return reply({"content": "", "tool_calls": [
                tool_call(4, "google-maps__compute_distance",
                          {"origin": f"{place['lat']},{place['lng']}", "destination": concert["venue"]})]})
        trip = got["google-maps__compute_distance"]
        if n == 4:
            start = concert["starts_at"][11:16]
            dinner = f"{int(start[:2]) - 2:02d}:{start[3:]}"
            return reply({"content": "", "tool_calls": [tool_call(5, "planner__save_plan", {
                "title": f"Jazz: {concert['title']}",
                "day": concert["starts_at"][:10],
                "steps": [
                    {"time": dinner, "title": "Dinner", "place": f"{place['name']}, {place['address']}"},
                    {"time": start, "title": concert["title"], "place": concert["venue"],
                     "note": f"{trip['duration_text']} by car from dinner", "link": concert["url"]},
                ],
                "sources": ["cape-town-events", "weather", "google-maps"],
            })]})
        saved = got["planner__save_plan"]
        return reply({"content": f"Saved {saved['plan_id']}: {concert['title']} at {concert['venue']}."})


class LongFlowTest(unittest.TestCase):
    """events + weather -> plan_meetup -> compute_distance -> save_plan, over HTTP."""

    @classmethod
    def setUpClass(cls) -> None:
        import events_mcp_server as ev
        import maps_mcp_server as maps
        import planner_mcp_server as planner
        import weather_mcp_server as weather

        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = (ev.db, maps.google_post, weather.http_get, planner.PLANS_ENV)
        os.environ[planner.PLANS_ENV] = str(Path(cls.tmp.name) / "plans")

        # Two jazz nights: the first on a rainy evening, the second on a dry one.
        now = datetime.now(ev.TZ).replace(second=0, microsecond=0)
        wet_day, dry_day = (now + timedelta(days=2)).date(), (now + timedelta(days=3)).date()
        cls.wet_day, cls.dry_day = wet_day, dry_day
        ev.db = ev.EventsDB(Path(cls.tmp.name) / "events.db")
        at = lambda d, hh, mm: datetime(d.year, d.month, d.day, hh, mm, tzinfo=ev.TZ)  # noqa: E731
        ev.db.upsert_events([
            ev.Event(source_id="kalkbaytheatre", title="Rainy Night Jazz", starts_at=at(wet_day, 20, 0),
                     url="https://kalk/rainy", venue="Kalk Bay Theatre", categories=["Jazz"],
                     description="Jazz in the harbour."),
            ev.Event(source_id="almacafe", title="Kyle Shepherd Trio", starts_at=at(dry_day, 19, 30),
                     url="https://alma/kyle", venue="The Alma Cafe, Rosebank", categories=["Jazz"],
                     description="Jazz piano trio."),
        ], ev.utcnow())

        cls.google = FakeRoutesAndPlaces()
        maps.google_post = cls.google
        weather.http_get = fake_open_meteo({wet_day.isoformat()})

        def app(module):
            return module.mcp.streamable_http_app(streamable_http_path="/mcp", json_response=True,
                                                  host="127.0.0.1")

        cls.served = {
            "cape-town-events": Served(app(ev)),
            "weather": Served(app(weather)),
            "google-maps": Served(app(maps)),
            "planner": Served(app(planner)),
        }
        cls.modules = (ev, maps, weather, planner)

    @classmethod
    def tearDownClass(cls) -> None:
        for served in cls.served.values():
            served.stop()
        ev, maps, weather, planner = cls.modules
        ev.db, maps.google_post, weather.http_get, _ = cls.saved
        os.environ.pop(planner.PLANS_ENV, None)
        cls.tmp.cleanup()

    def connected(self) -> list:
        """The four servers as the app keeps them: a record and a real tools/list."""
        records = []
        for server_id, served in self.served.items():
            listing = mcp_client.MCPClient(served.url).list_tools()
            self.assertTrue(listing.ok, (server_id, listing.error))
            records.append(McpServer(id=server_id, label=server_id, url=served.url,
                                     catalogue=listing.to_dict(server_id)))
        return records

    def test_a_long_flow_across_four_servers(self) -> None:
        from test_meetup import PEOPLE

        box = orchestrator.Orchestrator.from_servers(
            self.connected(),
            lambda route, args: mcp_client.MCPClient(route.url).call_tool(route.tool, args))
        model = ReadsItsTools(PEOPLE)
        question = ("Jazz this weekend with Anna (Kloof St 10) and Ben (Bree St 80), "
                    "on a dry evening, dinner first, save the plan.")
        answer = Agent(model, AgentConfig(), tools=box.functions, tool_runner=box.run,
                       routing=box.guide()).ask(question)

        # The model was offered every server's tools, and told which is which.
        offered = [t["function"]["name"] for t in model.bodies[0]["tools"]]
        self.assertIn("planner__save_plan", offered)
        self.assertIn("cape-town-events__create_watch", offered)
        guide = model.bodies[0]["messages"][0]["content"]
        self.assertIn("4 MCP servers", guide)
        self.assertIn("Weather forecasts for up to 16 days", guide)   # the server's own instructions

        # Five calls, four rounds, every one of them ok and none from a cache.
        calls = answer.tool_calls
        self.assertEqual([c["name"] for c in calls], [
            "cape-town-events__get_summary", "weather__get_forecast", "google-maps__plan_meetup",
            "google-maps__compute_distance", "planner__save_plan"])
        self.assertTrue(all(c["ok"] and not c["cached"] for c in calls), [c.get("text") for c in calls])
        self.assertEqual([c["round"] for c in calls], [1, 1, 2, 3, 4])
        self.assertIn("Kyle Shepherd Trio", answer.answer)

        # The data travelled intact: the venue from the listing reached Routes...
        self.assertEqual(self.google.routes[-1]["destination"], {"address": "The Alma Cafe, Rosebank"})
        winner = calls[2]["structured"]["options"][0]
        self.assertEqual(self.google.routes[-1]["origin"]["location"]["latLng"],
                         {"latitude": winner["lat"], "longitude": winner["lng"]})

        # ...and the plan on disk is the dry evening, dinner at the fairest place, the trip time.
        ev, maps, weather, planner = self.modules
        saved = json.loads((Path(os.environ[planner.PLANS_ENV]) /
                            f"{calls[4]['structured']['plan_id']}.json").read_text())
        self.assertEqual(saved["date"], self.dry_day.isoformat())
        self.assertEqual([s["time"] for s in saved["steps"]], ["17:30", "19:30"])
        self.assertEqual(saved["steps"][0]["place"], f"{winner['name']}, {winner['address']}")
        self.assertEqual(saved["steps"][1]["place"], "The Alma Cafe, Rosebank")
        self.assertEqual(saved["steps"][1]["note"], "13 min by car from dinner")

        # And the flow says so, check by check.
        flow = orchestrator.build_flow(calls, question, box.labels())
        self.assertEqual(flow["servers"], ["cape-town-events", "weather", "google-maps", "planner"])
        self.assertEqual(flow["parallel_rounds"], [1])
        checks = scenarios.check(scenarios.find("evening-out"), flow)
        self.assertTrue(checks["ok"], [r for r in checks["results"] if not r["ok"]])

    def test_the_planner_refuses_a_plan_out_of_order_and_the_error_reaches_the_model(self) -> None:
        box = orchestrator.Orchestrator.from_servers(
            self.connected(),
            lambda route, args: mcp_client.MCPClient(route.url).call_tool(route.tool, args))
        result = box.run("planner__save_plan", {
            "title": "Backwards", "day": self.dry_day.isoformat(),
            "steps": [{"time": "19:30", "title": "Concert"}, {"time": "17:30", "title": "Dinner"}]})
        self.assertFalse(result.ok)
        self.assertTrue(result.is_error)
        self.assertEqual(result.transport_error, "")          # the tool ran; it said no
        self.assertIn("out of order", result.for_model())
        # A tool that answered is not a server that is down.
        self.assertEqual(box.down, {})

    def test_a_server_that_goes_away_mid_turn_is_routed_around(self) -> None:
        records = self.connected()
        dead = free_port()
        records[1] = McpServer(id="weather", label="weather", url=f"http://127.0.0.1:{dead}/mcp",
                               catalogue=records[1].catalogue)
        box = orchestrator.Orchestrator.from_servers(
            records, lambda route, args: mcp_client.MCPClient(route.url, timeout=2).call_tool(
                route.tool, args))
        first = box.run("weather__get_forecast", {"location": "Cape Town"})
        second = box.run("weather__get_forecast", {"location": "Cape Town", "days": 2})
        alive = box.run("planner__list_plans", {})
        self.assertTrue(first.transport_error)
        self.assertEqual(second.routing_error, "server down")
        self.assertTrue(alive.ok)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------
# `uv run server.py` starts the local servers - the rules, with nothing spawned
# --------------------------------------------------------------------------


class LocalServersTest(unittest.TestCase):
    def setUp(self) -> None:
        import run_mcp_servers

        self.module = run_mcp_servers
        self.saved = dict(run_mcp_servers.SERVERS)
        self.env = {k: os.environ.get(k) for k in ("EVENTS_MCP_URL", run_mcp_servers.AUTOSTART_ENV)}

    def tearDown(self) -> None:
        self.module.SERVERS.clear()
        self.module.SERVERS.update(self.saved)
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_a_port_that_already_answers_is_used_and_not_stopped(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        try:
            self.module.SERVERS["weather"] = ("weather_mcp_server.py", listener.getsockname()[1], "weather")
            lines: list = []
            local = self.module.LocalServers(["weather"], log=lines.append).start()
            self.assertEqual((local.found, local.started), (["weather"], {}))
            self.assertEqual(local.wait_ready(timeout=2), ["weather"])
            local.stop()
            self.assertIn("leaving it running", lines[0])
        finally:
            listener.close()

    def test_the_events_server_is_skipped_when_it_lives_elsewhere(self) -> None:
        os.environ["EVENTS_MCP_URL"] = "http://127.0.0.1:9788/mcp"
        local = self.module.LocalServers(["events"], log=lambda _: None).start()
        self.assertIn("EVENTS_MCP_URL", local.skipped["events"])
        self.assertEqual(local.started, {})
        os.environ["EVENTS_MCP_URL"] = "http://127.0.0.1:8788/mcp"   # the local one, by name
        self.assertEqual(self.module.events_elsewhere(), "")

    def test_autostart_can_be_switched_off(self) -> None:
        for value, expected in (("", True), ("1", True), ("0", False), ("off", False), ("false", False)):
            os.environ[self.module.AUTOSTART_ENV] = value
            self.assertEqual(self.module.autostart_enabled(), expected, value)

    def test_an_unknown_name_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.module.LocalServers(["maps", "nope"])
