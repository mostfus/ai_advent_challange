"""Day 20: the fourth MCP server of our own - a planner, where a flow ends.

Day 19's pipeline stopped at "the answer is the card in the chat", and said
so under *What is deliberately not here*. A flow across several servers needs
somewhere to finish: every step before the last one *finds* something, and
the last one has to *do* something with what was found - write it down, in a
form that outlives the chat. This server is that step.

  * `save_plan`   - an itinerary: a title, a day, and timed steps, each with a
    place. Written to `data/plans/<id>.md` (to read) and `<id>.json` (to load).
  * `list_plans`  - what has been saved.
  * `get_plan`    - one of them, whole.
  * `delete_plan` - forget one.

Two checks run before anything is written, and they are the ones a model
assembling a plan out of four other tools' answers actually gets wrong:

  * **the steps are in time order.** Dinner at 19:30 after a concert at 19:00
    is a plan that was put together in the wrong order, and it is refused with
    the two steps named - so the model fixes the plan rather than the file
    quietly holding a wrong one.
  * **the day is a real day, and not in the past.**

Run it next to the agent (or run every server at once, `run_mcp_servers.py`):

    uv run planner_mcp_server.py        # http://127.0.0.1:8790/mcp

Where the files go is `PLANS_DIR` (default `data/plans/`). Nothing here talks
to the internet.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

HOST = "127.0.0.1"
PORT = 8790
ENDPOINT = "/mcp"

PLANS_ENV = "PLANS_DIR"
DEFAULT_DIR = Path(__file__).parent / "data" / "plans"

MAX_STEPS = 20
MAX_PLANS_LISTED = 50
TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
SLUG = re.compile(r"[^a-z0-9]+")

mcp = MCPServer(
    name="planner-local",
    title="Planner (local)",
    version="0.1.0",
    instructions=(
        "Saves plans - an evening out, a day, a trip - as timed itineraries the user "
        "can open later. Call save_plan LAST, once everything in the plan has been "
        "looked up with the other tools: every step's time and place should come "
        "from their results, not be made up. Steps must be in time order. "
        "list_plans and get_plan read back what was saved."
    ),
)

_lock = threading.Lock()


# --------------------------------------------------------------------------
# What the tools take and return
# --------------------------------------------------------------------------


class Step(BaseModel):
    time: str = Field(description="Local start time, HH:MM (24h).")
    title: str = Field(description="What happens, e.g. 'Dinner' or 'Concert: Kyle Shepherd Trio'.")
    place: str = Field(default="", description="Where: the name and/or address, as a tool returned it.")
    note: str = Field(default="", description="Anything worth knowing: travel time, booking, weather.")
    link: str = Field(default="", description="A URL for the step, if a tool gave one.")


class Plan(BaseModel):
    plan_id: str
    title: str
    date: str
    weekday: str
    steps: list[Step]
    notes: str = ""
    sources: list[str] = Field(default_factory=list)
    path: str
    markdown: str
    created_at: str


class PlanRow(BaseModel):
    plan_id: str
    title: str
    date: str
    steps: int
    path: str
    created_at: str


class Plans(BaseModel):
    plans: list[PlanRow]
    folder: str


class Deleted(BaseModel):
    plan_id: str
    deleted: bool


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------


def plans_dir() -> Path:
    folder = Path(os.getenv(PLANS_ENV) or DEFAULT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def today() -> date:
    """The planner's idea of today. The one place tests move the clock."""
    return date.today()


def new_id(title: str, day: str, folder: Path) -> str:
    base = f"{day}-{SLUG.sub('-', title.lower()).strip('-')[:40] or 'plan'}"
    candidate, n = base, 2
    while (folder / f"{candidate}.json").exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def render_markdown(plan: dict) -> str:
    lines = [f"# {plan['title']}", "", f"**{plan['weekday']}, {plan['date']}**", ""]
    for step in plan["steps"]:
        head = f"- **{step['time']}** — {step['title']}"
        if step.get("place"):
            head += f" · {step['place']}"
        lines.append(head)
        if step.get("note"):
            lines.append(f"  - {step['note']}")
        if step.get("link"):
            lines.append(f"  - {step['link']}")
    if plan.get("notes"):
        lines += ["", plan["notes"]]
    if plan.get("sources"):
        lines += ["", "_Put together from: " + ", ".join(plan["sources"]) + "_"]
    return "\n".join(lines) + "\n"


def load(plan_id: str) -> dict:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", plan_id or ""):
        raise ToolError(f"not a plan id: {plan_id!r}")
    path = plans_dir() / f"{plan_id}.json"
    if not path.exists():
        raise ToolError(f"no plan {plan_id!r} - see list_plans")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The tools
# --------------------------------------------------------------------------


@mcp.tool(title="Save a plan")
def save_plan(
    title: Annotated[str, Field(min_length=1, max_length=120, description="A short name for the plan.")],
    day: Annotated[str, Field(description="The day of the plan, YYYY-MM-DD.")],
    steps: Annotated[list[Step], Field(min_length=1, max_length=MAX_STEPS, description=(
        "The itinerary, in time order. Take times, places and links from the other "
        "tools' results."))],
    notes: Annotated[str, Field(description="Anything else: the weather, who is coming.")] = "",
    sources: Annotated[list[str], Field(description=(
        "Which tools the plan was put together from, e.g. ['weather', 'google-maps'].")),
    ] = [],  # noqa: B006 - pydantic copies defaults
) -> Plan:
    """Save a timed itinerary for one day. Call this last, after looking everything up.

    Refuses steps that are out of time order or a day in the past, naming what
    is wrong, so the plan can be fixed and saved again. Returns the plan with
    its id, the file it was written to and the same plan as Markdown.
    """
    try:
        when = date.fromisoformat(day.strip())
    except ValueError as err:
        raise ToolError(f"`day` must be YYYY-MM-DD, got {day!r}") from err
    if when < today():
        raise ToolError(f"{when} is in the past (today is {today()}) - check the day")

    previous = None
    for number, step in enumerate(steps, start=1):
        step.time = step.time.strip()
        if not TIME.match(step.time):
            raise ToolError(f"step {number} ({step.title!r}): time must be HH:MM, got {step.time!r}")
        if previous is not None and step.time < previous[1]:
            raise ToolError(
                f"the steps are out of order: step {number} ({step.title!r}) at {step.time} "
                f"comes after step {previous[0]} at {previous[1]}. Put the steps in time "
                "order and save again.")
        previous = (number, step.time)

    with _lock:
        folder = plans_dir()
        plan_id = new_id(title, when.isoformat(), folder)
        record = {
            "plan_id": plan_id,
            "title": title.strip(),
            "date": when.isoformat(),
            "weekday": when.strftime("%A"),
            "steps": [s.model_dump() for s in steps],
            "notes": notes.strip(),
            "sources": [s.strip() for s in sources if s.strip()],
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        markdown = render_markdown(record)
        md_path = folder / f"{plan_id}.md"
        md_path.write_text(markdown, encoding="utf-8")
        record["path"] = str(md_path)
        record["markdown"] = markdown
        (folder / f"{plan_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return Plan(**record)


@mcp.tool(title="Saved plans")
def list_plans() -> Plans:
    """Every saved plan, newest day first: id, title, day and number of steps."""
    folder = plans_dir()
    rows = []
    for path in folder.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append(PlanRow(plan_id=record["plan_id"], title=record["title"], date=record["date"],
                            steps=len(record.get("steps") or []), path=record.get("path", ""),
                            created_at=record.get("created_at", "")))
    rows.sort(key=lambda r: (r.date, r.created_at), reverse=True)
    return Plans(plans=rows[:MAX_PLANS_LISTED], folder=str(folder))


@mcp.tool(title="Read a plan")
def get_plan(plan_id: Annotated[str, Field(description="The id save_plan or list_plans gave.")]) -> Plan:
    """One saved plan, whole, with its Markdown."""
    return Plan(**load(plan_id))


@mcp.tool(title="Delete a plan")
def delete_plan(plan_id: Annotated[str, Field(description="The id of the plan to delete.")]) -> Deleted:
    """Delete a saved plan and its Markdown file."""
    load(plan_id)
    with _lock:
        for suffix in (".json", ".md"):
            (plans_dir() / f"{plan_id}{suffix}").unlink(missing_ok=True)
    return Deleted(plan_id=plan_id, deleted=True)


def main() -> None:
    load_dotenv()
    print(f"Planner MCP server on http://{HOST}:{PORT}{ENDPOINT} - plans in {plans_dir()}")
    mcp.run(
        transport="streamable-http",
        host=HOST,
        port=PORT,
        streamable_http_path=ENDPOINT,
        json_response=True,
    )


if __name__ == "__main__":
    main()
