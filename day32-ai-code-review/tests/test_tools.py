"""The tool registry, the MCP provider behind it, and the agent loop that calls through it.

    uv run python -m unittest discover -s tests -v

No model, no MCP server, no network. The registry is exercised with a fake
in-process provider (the shape a local files/search/git provider will have);
the MCP provider is built from stored catalogues and given a caller that
answers from dicts; the agent is driven by a scripted LLM.
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from datetime import datetime

from core import agent as agent_module
from core.agent import Agent, AgentConfig
from llm.client import LLMResponse
from storage import McpServer
from tools import Tool, ToolRegistry, ToolResult
from tools.mcp_provider import GUIDE_HEAD, McpProvider

SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}


class FakeLocalProvider:
    """An in-process provider: two plain functions and one coroutine."""

    def __init__(self) -> None:
        self.calls: list = []

    def tools(self) -> list[Tool]:
        return [
            Tool("read_file", "Read a file of the repository.", SCHEMA, self.read_file, source="local"),
            Tool("count_lines", "Count the lines of a text.", SCHEMA, self.count_lines, source="local"),
            Tool("slow_search", "Search, asynchronously.", SCHEMA, self.slow_search, source="local"),
        ]

    def guide(self) -> str:
        return "--- Local tools ---\nThey read the checked-out repository."

    def read_file(self, arguments: dict) -> str:
        self.calls.append(("read_file", arguments))
        return f"contents of {arguments.get('q')}"

    def count_lines(self, arguments: dict) -> dict:
        self.calls.append(("count_lines", arguments))
        return {"lines": len(str(arguments.get("q", "")).splitlines())}

    async def slow_search(self, arguments: dict) -> ToolResult:
        await asyncio.sleep(0)
        self.calls.append(("slow_search", arguments))
        return ToolResult(ok=True, text="found it", structured={"hits": [arguments.get("q")]})


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.local = FakeLocalProvider()
        self.registry = ToolRegistry([self.local])

    def test_it_lists_every_tool_as_a_function(self) -> None:
        functions = self.registry.functions()
        self.assertEqual([f["function"]["name"] for f in functions], ["read_file", "count_lines", "slow_search"])
        self.assertEqual(functions[0]["type"], "function")
        self.assertEqual(functions[0]["function"]["parameters"], SCHEMA)
        self.assertEqual(len(self.registry), 3)

    def test_sync_and_async_calls_come_back_as_results(self) -> None:
        text = self.registry.call("read_file", {"q": "README.md"})
        self.assertTrue(text.ok)
        self.assertEqual(text.for_model(), "contents of README.md")
        self.assertEqual((text.server_id, text.tool), ("local", "read_file"))
        counted = self.registry.call("count_lines", {"q": "a\nb\nc"})
        self.assertEqual(json.loads(counted.for_model()), {"lines": 3})
        found = self.registry.call("slow_search", {"q": "needle"})
        self.assertEqual(found.structured, {"hits": ["needle"]})
        again = asyncio.run(self.registry.acall("slow_search", {"q": "hay"}))
        self.assertEqual(again.structured, {"hits": ["hay"]})

    def test_a_name_no_tool_has_is_an_answer_with_the_nearest_names(self) -> None:
        result = self.registry.call("read_fiel", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.routing_error, "unknown tool")
        self.assertIn("Did you mean read_file?", result.text)
        self.assertEqual(self.local.calls, [])

    def test_providers_are_joined_and_the_first_name_wins(self) -> None:
        class Shadow:
            def tools(self):
                return [Tool("read_file", "a second one", SCHEMA, lambda a: "shadow"),
                        Tool("other", "something else", SCHEMA, lambda a: "other")]

        registry = ToolRegistry([self.local]).add(Shadow())
        self.assertEqual([t.name for t in registry.tools()],
                         ["read_file", "count_lines", "slow_search", "other"])
        self.assertEqual(registry.call("read_file", {"q": "x"}).text, "contents of x")
        self.assertEqual(registry.call("other", {}).text, "other")
        self.assertEqual(registry.guide(), self.local.guide())

    def test_an_empty_registry_offers_nothing(self) -> None:
        self.assertFalse(ToolRegistry())
        self.assertEqual((ToolRegistry().functions(), ToolRegistry().guide()), ([], ""))


# --------------------------------------------------------------------------
# The MCP servers as one provider
# --------------------------------------------------------------------------


def catalogue(*tools: str, instructions: str = "") -> dict:
    return {"ok": True, "instructions": instructions, "tools": [
        {"name": t, "title": t, "description": f"does {t}", "input_schema": SCHEMA} for t in tools]}


SERVERS = [
    McpServer(id="docs", label="Docs", url="http://d/mcp",
              catalogue=catalogue("search", instructions="Library documentation.")),
    McpServer(id="wiki", label="Wiki", url="http://w/mcp", catalogue=catalogue("ask_question", "read_page")),
    McpServer(id="issues", label="Issues", url="http://i/mcp", catalogue=catalogue("list_issues", "get_issue")),
    McpServer(id="off", label="Switched off", url="http://o/mcp", enabled=False, catalogue=catalogue("nothing")),
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


def mcp(caller=None, servers=SERVERS) -> ToolRegistry:
    provider = McpProvider.from_servers(servers, caller or FakeCaller(), notes={},
                                        today=datetime(2026, 9, 27, 12, 0))
    return ToolRegistry([provider])


class McpProviderTest(unittest.TestCase):
    def test_only_live_servers_are_offered(self) -> None:
        names = [f["function"]["name"] for f in mcp().functions()]
        self.assertEqual(names, ["docs__search", "wiki__ask_question", "wiki__read_page",
                                 "issues__list_issues", "issues__get_issue"])

    def test_the_guide_names_every_server_its_tools_and_today(self) -> None:
        guide = mcp().guide()
        self.assertTrue(guide.startswith(GUIDE_HEAD))
        self.assertIn("Today is Sunday, 2026-09-27", guide)
        self.assertIn("3 MCP servers", guide)
        self.assertIn("* docs - Docs: Library documentation.\n  tools: docs__search", guide)
        self.assertNotIn("off__", guide)

    def test_one_server_needs_no_guide(self) -> None:
        self.assertEqual(mcp(servers=SERVERS[:1]).guide(), "")

    def test_each_call_reaches_the_server_that_owns_it(self) -> None:
        caller = FakeCaller()
        registry = mcp(caller)
        registry.call("issues__get_issue", {"q": "a"})
        result = registry.call("wiki__read_page", {"q": "b"})
        self.assertEqual([(s, u, t) for s, u, t, _ in caller.calls],
                         [("issues", "http://i/mcp", "get_issue"), ("wiki", "http://w/mcp", "read_page")])
        self.assertEqual((result.server_id, result.tool), ("wiki", "read_page"))

    def test_the_right_tool_on_the_wrong_server_is_named(self) -> None:
        caller = FakeCaller()
        result = mcp(caller).call("docs__read_page", {})
        self.assertFalse(result.ok)
        self.assertEqual(caller.calls, [])
        self.assertEqual(result.routing_error, "unknown tool")
        self.assertIn("Docs has no tool 'read_page'", result.text)
        self.assertIn("Did you mean wiki__read_page?", result.text)

    def test_a_bare_tool_name_is_pointed_at_its_server(self) -> None:
        result = mcp().call("get_issue", {})
        self.assertIn("'get_issue' has no server in front of it", result.text)
        self.assertIn("Did you mean issues__get_issue?", result.text)

    def test_a_server_that_is_not_connected_is_said_to_be(self) -> None:
        self.assertIn("there is no server 'off' connected", mcp().call("off__nothing", {}).text)

    def test_a_misspelt_name_gets_the_nearest(self) -> None:
        self.assertIn("Did you mean wiki__ask_question?", mcp().call("wiki__ask_questoin", {}).text)

    def test_a_server_that_failed_is_not_called_again_this_turn(self) -> None:
        caller = FakeCaller(down=("wiki",))
        registry = mcp(caller)
        first = registry.call("wiki__ask_question", {"q": "a"})
        second = registry.call("wiki__read_page", {"q": "b"})
        other = registry.call("docs__search", {"q": "c"})
        self.assertTrue(first.transport_error)
        self.assertEqual(second.routing_error, "server down")
        self.assertIn("did not answer earlier in this turn", second.text)
        self.assertTrue(other.ok)
        self.assertEqual([c[2] for c in caller.calls], ["ask_question", "search"])
        # A new turn is a new provider: the server is tried again.
        mcp(caller).call("wiki__read_page", {"q": "b"})
        self.assertEqual(caller.calls[-1][2], "read_page")

    def test_an_identical_call_is_answered_once(self) -> None:
        caller = FakeCaller()
        registry = mcp(caller)
        first = registry.call("docs__search", {"q": "httpx", "limit": 3})
        again = registry.call("docs__search", {"limit": 3, "q": "httpx"})
        other = registry.call("docs__search", {"q": "httpx", "limit": 4})
        self.assertEqual(len(caller.calls), 2)
        self.assertFalse(first.cached)
        self.assertTrue(again.cached)
        self.assertEqual(again.structured, first.structured)
        self.assertFalse(other.cached)

    def test_mcp_and_local_tools_share_one_registry(self) -> None:
        local = FakeLocalProvider()
        provider = McpProvider.from_servers(SERVERS[:2], FakeCaller(), notes={})
        registry = ToolRegistry([provider, local])
        self.assertIn("read_file", [t.name for t in registry.tools()])
        self.assertIn("docs__search", [t.name for t in registry.tools()])
        self.assertEqual(registry.call("read_file", {"q": "a.py"}).text, "contents of a.py")
        self.assertEqual(registry.call("docs__search", {"q": "x"}).server_id, "docs")


# --------------------------------------------------------------------------
# The agent's side: it lists and calls through the registry
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
    def test_a_local_tool_is_offered_called_and_answered_with(self) -> None:
        local = FakeLocalProvider()
        script = Script(
            {"content": "", "tool_calls": [tool_call(1, "read_file", {"q": "core/turn.py"})]},
            {"content": "It runs a turn."},
        )
        answer = Agent(script, AgentConfig(system_prompt="sys"), tools=ToolRegistry([local])).ask("what?")
        self.assertEqual(answer.answer, "It runs a turn.")
        self.assertEqual([f["function"]["name"] for f in script.bodies[0]["tools"]],
                         ["read_file", "count_lines", "slow_search"])
        self.assertEqual(script.bodies[0]["messages"][1]["content"], local.guide())
        self.assertEqual(local.calls, [("read_file", {"q": "core/turn.py"})])
        tool_message = [m for m in script.bodies[1]["messages"] if m["role"] == "tool"][0]
        self.assertEqual(tool_message["content"], "contents of core/turn.py")
        self.assertEqual((answer.tool_calls[0]["server_id"], answer.tool_calls[0]["round"]), ("local", 1))

    def test_no_registry_means_no_tools_in_the_request(self) -> None:
        script = Script({"content": "hi"})
        Agent(script, AgentConfig()).ask("hello")
        self.assertNotIn("tools", script.bodies[0])

    def test_calls_asked_together_run_side_by_side_and_come_back_in_order(self) -> None:
        barrier = threading.Barrier(2, timeout=3)

        def slow(arguments):
            barrier.wait()   # breaks - and fails the test - if the calls ran one after the other
            return {"q": arguments["q"]}

        registry = mcp(FakeCaller({"docs/search": slow, "wiki/read_page": slow}))
        script = Script(
            {"content": "", "tool_calls": [tool_call(1, "docs__search", {"q": "d"}),
                                           tool_call(2, "wiki__read_page", {"q": "w"})]},
            {"content": "done"},
        )
        answer = Agent(script, AgentConfig(), tools=registry).ask("go")
        self.assertEqual(answer.answer, "done")
        self.assertEqual([c["name"] for c in answer.tool_calls], ["docs__search", "wiki__read_page"])
        self.assertEqual([c["round"] for c in answer.tool_calls], [1, 1])
        tool_messages = [m for m in script.bodies[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call_1", "call_2"])
        self.assertEqual(json.loads(tool_messages[0]["content"]), {"q": "d"})

    def test_the_guide_goes_right_after_the_system_prompt(self) -> None:
        script = Script({"content": "hi"})
        Agent(script, AgentConfig(system_prompt="You are a helpful assistant."), tools=mcp(),
              invariants="no ORMs").ask("hello")
        messages = script.bodies[0]["messages"]
        self.assertEqual(messages[0]["content"], "You are a helpful assistant.")
        self.assertTrue(messages[1]["content"].startswith(GUIDE_HEAD))
        self.assertTrue(messages[-2]["content"].endswith("no ORMs"))

    def test_the_last_round_may_not_call_another_tool(self) -> None:
        ask_again = {"content": "", "tool_calls": [tool_call(1, "docs__search", {"q": "x"})]}
        script = Script(*([ask_again] * agent_module.MAX_TOOL_ROUNDS), {"content": "what I have"})
        answer = Agent(script, AgentConfig(), tools=mcp()).ask("loop")
        self.assertEqual(answer.answer, "what I have")
        self.assertEqual(len(script.bodies), agent_module.MAX_TOOL_ROUNDS + 1)
        self.assertNotIn("tool_choice", script.bodies[-2])
        self.assertEqual(script.bodies[-1]["tool_choice"], "none")
        # Ten identical calls, one of them made: the rest came from the first.
        self.assertEqual(sum(1 for c in answer.tool_calls if not c["cached"]), 1)

    def test_calls_past_the_budget_are_answered_not_run(self) -> None:
        caller = FakeCaller()
        many = [tool_call(i, "docs__search", {"q": str(i)}) for i in range(agent_module.MAX_TOOL_CALLS + 2)]
        script = Script({"content": "", "tool_calls": many}, {"content": "ok"})
        answer = Agent(script, AgentConfig(), tools=mcp(caller)).ask("x")
        self.assertEqual(len(caller.calls), agent_module.MAX_TOOL_CALLS)
        self.assertEqual(len(answer.tool_calls), agent_module.MAX_TOOL_CALLS + 2)
        self.assertIn("used its", answer.tool_calls[-1]["error"])
        tools = [m for m in script.bodies[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tools), len(many))


if __name__ == "__main__":
    unittest.main()
