"""Day 20: scenarios - long flows over several servers, and the checks they are held to.

"The agent chose the right tools and called them in the right order" is a
claim about a transcript, and a claim nobody checks is a claim about how the
demo went. So each scenario here is a request that needs tools on several
servers, plus what a correct run of it must look like - written down before
it runs, and checked against the flow (`orchestrator.build_flow`) after:

  * `calls  a`         - `a` was called, and succeeded;
  * `before a b`       - the first successful `a` ran in an earlier round than
                         the first successful `b`;
  * `feeds  a b`       - some argument of a `b` call was found in the result of
                         an `a` call from an earlier round: the data travelled;
  * `last   a`         - the last successful call was `a` (the delivery step);
  * `only   servers`   - nothing was called on any other server;
  * `clean`            - no call failed to route (unknown tool, dead server).

A tool is named `server/tool` - `google-maps/plan_meetup` - by the server's
id in the MCP panel, so a scenario is written against the catalogue rather
than against spellings the model might produce.

"before" is about *rounds*, not positions: two calls asked for together ran
side by side, and neither came first. "feeds" is the stronger claim and the
one the brief is actually about - an order can be right by luck, a value
from the weather's answer turning up in the plan cannot.

Run one against the app (which must be running, with the servers up):

    uv run scenarios.py                        # the list, and what each needs
    uv run scenarios.py evening-out            # run it, print the flow and the checks

It goes through `/api/chat` like the page does, in a chat of its own, so the
run is also in the app to be looked at afterwards.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Expect:
    kind: str
    a: str = ""
    b: str = ""
    servers: tuple = ()

    def describe(self) -> str:
        if self.kind == "calls":
            return f"calls {self.a}"
        if self.kind == "before":
            return f"{self.a} before {self.b}"
        if self.kind == "feeds":
            return f"{self.a} feeds {self.b}"
        if self.kind == "last":
            return f"ends with {self.a}"
        if self.kind == "only":
            return "only " + ", ".join(self.servers)
        if self.kind == "clean":
            return "every call routed"
        return self.kind


def calls(a: str) -> Expect:
    return Expect("calls", a)


def before(a: str, b: str) -> Expect:
    return Expect("before", a, b)


def feeds(a: str, b: str) -> Expect:
    return Expect("feeds", a, b)


def last(a: str) -> Expect:
    return Expect("last", a)


def only(*servers: str) -> Expect:
    return Expect("only", servers=tuple(servers))


CLEAN = Expect("clean")


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    prompt: str
    servers: tuple
    expect: tuple = field(default_factory=tuple)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "servers": list(self.servers),
            "expect": [e.describe() for e in self.expect],
            "note": self.note,
        }


EVENTS, MAPS, WEATHER, PLANNER, DEEPWIKI = (
    "cape-town-events", "google-maps", "weather", "planner", "deepwiki")

SCENARIOS = (
    Scenario(
        id="evening-out",
        title="A jazz evening: events → weather → meeting point → trip → plan",
        prompt=(
            "В эти выходные хотим втроём сходить на джаз в Кейптауне. Анна живёт на "
            "Kloof Street 10, Бен — на Bree Street 80, я буду на V&A Waterfront. Найди "
            "джазовый концерт на выходных, выбери вечер без дождя, подбери, где нам "
            "поужинать перед концертом так, чтобы всем было честно добираться на машине, "
            "посчитай, сколько ехать от ресторана до площадки, и сохрани план вечера."
        ),
        servers=(EVENTS, WEATHER, MAPS, PLANNER),
        expect=(
            calls(f"{EVENTS}/get_summary"),
            calls(f"{WEATHER}/get_forecast"),
            calls(f"{MAPS}/plan_meetup"),
            calls(f"{MAPS}/compute_distance"),
            calls(f"{PLANNER}/save_plan"),
            before(f"{EVENTS}/get_summary", f"{MAPS}/compute_distance"),
            before(f"{MAPS}/plan_meetup", f"{MAPS}/compute_distance"),
            before(f"{WEATHER}/get_forecast", f"{PLANNER}/save_plan"),
            feeds(f"{MAPS}/plan_meetup", f"{MAPS}/compute_distance"),
            feeds(f"{EVENTS}/get_summary", f"{MAPS}/compute_distance"),
            feeds(f"{MAPS}/plan_meetup", f"{PLANNER}/save_plan"),
            feeds(f"{EVENTS}/get_summary", f"{PLANNER}/save_plan"),
            last(f"{PLANNER}/save_plan"),
            only(EVENTS, WEATHER, MAPS, PLANNER),
            CLEAN,
        ),
        note="Needs a watch on the events server with jazz in it, and a Google Maps key.",
    ),
    Scenario(
        id="weather-meetup",
        title="Weather decides the place: weather → meeting point → plan",
        prompt=(
            "В субботу встречаемся втроём в Кейптауне: Анна на Kloof Street 10, Бен на "
            "Bree Street 80, я на V&A Waterfront. Посмотри погоду на субботний вечер: если "
            "будет сухо — найди бар с террасой, если дождь — уютную кофейню; добираемся на "
            "машине, место должно быть честным для всех. Сохрани план на субботу, встреча в 18:00."
        ),
        servers=(WEATHER, MAPS, PLANNER),
        expect=(
            calls(f"{WEATHER}/get_forecast"),
            calls(f"{MAPS}/plan_meetup"),
            calls(f"{PLANNER}/save_plan"),
            before(f"{WEATHER}/get_forecast", f"{MAPS}/plan_meetup"),
            feeds(f"{MAPS}/plan_meetup", f"{PLANNER}/save_plan"),
            feeds(f"{WEATHER}/get_forecast", f"{PLANNER}/save_plan"),
            last(f"{PLANNER}/save_plan"),
            only(WEATHER, MAPS, PLANNER),
            CLEAN,
        ),
        note="Needs a Google Maps key. No events server.",
    ),
    Scenario(
        id="study-sunday",
        title="A remote server in the flow: DeepWiki + weather → plan",
        prompt=(
            "Составь мне план на воскресенье в Кейптауне: утром, в 10:00, разобраться, как в "
            "репозитории modelcontextprotocol/python-sdk устроен транспорт Streamable HTTP "
            "(спроси DeepWiki и выпиши главное в заметку к этому шагу), в 15:00 — прогулка по "
            "Company's Garden, если не будет дождя, иначе — Iziko South African Museum. "
            "Сохрани план."
        ),
        servers=(DEEPWIKI, WEATHER, PLANNER),
        expect=(
            calls(f"{DEEPWIKI}/ask_wiki_question"),
            calls(f"{WEATHER}/get_forecast"),
            calls(f"{PLANNER}/save_plan"),
            before(f"{DEEPWIKI}/ask_wiki_question", f"{PLANNER}/save_plan"),
            before(f"{WEATHER}/get_forecast", f"{PLANNER}/save_plan"),
            feeds(f"{WEATHER}/get_forecast", f"{PLANNER}/save_plan"),
            last(f"{PLANNER}/save_plan"),
            only(DEEPWIKI, WEATHER, PLANNER, MAPS),
            CLEAN,
        ),
        note="DeepWiki is on the internet; the other two are ours. No keys at all.",
    ),
)


def find(scenario_id: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.id == scenario_id), None)


# --------------------------------------------------------------------------
# Checking a flow
# --------------------------------------------------------------------------


def split(ref: str) -> tuple[str, str]:
    server, _, tool = ref.partition("/")
    return server, tool


def matching(flow: dict, ref: str, ok_only: bool = True) -> list:
    server, tool = split(ref)
    return [s for s in flow.get("steps") or []
            if s["server"] == server and s["tool"] == tool and (s["ok"] or not ok_only)]


def judge(expect: Expect, flow: dict) -> tuple[bool, str]:
    steps = flow.get("steps") or []
    if not steps:
        # "Nothing stray was called" and "every call routed" are both true of
        # a turn that called nothing - and a scenario that passed anything by
        # doing nothing would be a check that rewards giving up.
        return False, "no tool was called"
    if expect.kind == "calls":
        found = matching(flow, expect.a)
        if found:
            return True, "step " + ", ".join(f"#{s['n']}" for s in found)
        tried = matching(flow, expect.a, ok_only=False)
        return False, (f"called {len(tried)}x, never succeeded" if tried else "never called")

    if expect.kind == "before":
        first_a, first_b = matching(flow, expect.a), matching(flow, expect.b)
        if not first_a or not first_b:
            return False, f"{'first' if not first_a else 'second'} was never called successfully"
        a, b = first_a[0], first_b[0]
        if a["round"] < b["round"]:
            return True, f"#{a['n']} in round {a['round']}, #{b['n']} in round {b['round']}"
        if a["round"] == b["round"]:
            return False, f"both in round {a['round']} - side by side, so neither came first"
        return False, f"#{b['n']} in round {b['round']} came before #{a['n']} in round {a['round']}"

    if expect.kind == "feeds":
        sources = {s["n"] for s in matching(flow, expect.a)}
        for step in matching(flow, expect.b, ok_only=False):
            for item in step["inputs"]:
                hit = sources.intersection(item["from"])
                if hit:
                    return True, f"#{step['n']}.{item['arg']} = {item['value']!r} from #{min(hit)}"
        if not sources:
            return False, "the source was never called successfully"
        return False, "no argument of it was found in the source's result"

    if expect.kind == "last":
        done = [s for s in steps if s["ok"]]
        if not done:
            return False, "nothing succeeded"
        tail = done[-1]
        server, tool = split(expect.a)
        ok = tail["server"] == server and tail["tool"] == tool
        return ok, f"last was #{tail['n']} {tail['server']}/{tail['tool']}"

    if expect.kind == "only":
        stray = [s for s in steps if s["server"] not in expect.servers]
        if not stray:
            return True, "servers used: " + (", ".join(flow.get("servers") or []) or "none")
        return False, "also called " + ", ".join(f"#{s['n']} {s['server']}/{s['tool']}" for s in stray)

    if expect.kind == "clean":
        bad = [s for s in steps if s.get("routing_error")]
        if not bad:
            return True, f"{len(steps)} of {len(steps)} routed"
        return False, "; ".join(f"#{s['n']} {s['name']}: {s['routing_error']}" for s in bad)

    return False, f"unknown check {expect.kind!r}"


def check(scenario: Scenario, flow: dict) -> dict:
    results = []
    for expect in scenario.expect:
        ok, detail = judge(expect, flow)
        results.append({"kind": expect.kind, "text": expect.describe(), "ok": ok, "detail": detail})
    passed = sum(1 for r in results if r["ok"])
    return {
        "scenario": scenario.id,
        "title": scenario.title,
        "passed": passed,
        "total": len(results),
        "ok": passed == len(results),
        "results": results,
    }


# --------------------------------------------------------------------------
# Running one against the app
# --------------------------------------------------------------------------


def print_flow(flow: dict) -> None:
    print(f"\nFLOW  {flow.get('summary', '')}")
    for step in flow.get("steps") or []:
        mark = "✓" if step["ok"] else "✗"
        extra = " (cached)" if step["cached"] else ""
        print(f"  {mark} #{step['n']}  round {step['round']}  {step['server']:<18} "
              f"{step['tool']:<18} {step['elapsed_ms']:>6} ms{extra}")
        for item in step["inputs"]:
            if item["from"]:
                print(f"        {item['arg']} = {item['value']!r}  ← "
                      + ", ".join(f"#{n}" for n in item["from"]))
        if step["error"]:
            print(f"        error: {step['error']}")


def print_checks(checks: dict) -> None:
    print(f"\nCHECKS  {checks['passed']}/{checks['total']}  {checks['title']}")
    for result in checks["results"]:
        print(f"  {'PASS' if result['ok'] else 'FAIL'}  {result['text']:<58} {result['detail']}")


def main(argv: list) -> int:
    import time

    import httpx

    base = "http://127.0.0.1:8000"
    if "--base" in argv:
        base = argv[argv.index("--base") + 1].rstrip("/")
        argv = [a for a in argv if a not in ("--base", base)]
    if len(argv) < 2:
        for scenario in SCENARIOS:
            print(f"{scenario.id:<16} {scenario.title}\n{'':<16} needs: {', '.join(scenario.servers)}"
                  f"{' - ' + scenario.note if scenario.note else ''}\n")
        print("uv run scenarios.py <id>   runs one against the app on " + base)
        return 0

    scenario = find(argv[1])
    if scenario is None:
        print(f"no scenario {argv[1]!r}; one of: " + ", ".join(s.id for s in SCENARIOS))
        return 2
    with httpx.Client(timeout=600) as client:
        ready = client.post(f"{base}/api/mcp/refresh").json()
        missing = [sid for sid in scenario.servers
                   if not any(s["id"] == sid and s["enabled"] and (s.get("catalogue") or {}).get("ok")
                              for s in ready.get("servers") or [])]
        if missing:
            print("not connected, or not answering: " + ", ".join(missing)
                  + "\n(start them: uv run run_mcp_servers.py)")
            return 2
        conversation = f"scenario-{scenario.id}-{int(time.time())}"
        print(f"{scenario.title}\n\nYOU  {scenario.prompt}")
        reply = client.post(f"{base}/api/chat", json={
            "message": scenario.prompt,
            "conversation_id": conversation,
            "scenario": scenario.id,
        })
        body = reply.json()
    if reply.status_code != 200:
        print(f"error {reply.status_code}: {body.get('detail')}")
        return 1
    print(f"\nAGENT  {body.get('answer')}")
    flow = body.get("flow") or {}
    print_flow(flow)
    if flow.get("checks"):
        print_checks(flow["checks"])
    print(f"\n(chat {conversation!r} in the app)")
    return 0 if (flow.get("checks") or {}).get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
