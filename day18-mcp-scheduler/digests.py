"""Day 18: the half of "an agent that works 24/7" that lives in this app.

`events_mcp_server.py` holds the clock: it collects on schedule and leaves a
digest - an aggregate, computed by code - in its `events://digests` resource.
It cannot *deliver* one. MCP is request -> response and nothing here holds a
session open for the server to push on, so the server writes and this side
reads. `Courier` is that reader: a thread that looks at the resource every
half a minute and posts whatever is new into one chat, "Digests".

Three decisions in it, each of which is the difference between a demo and
something that can be left running:

  * **The cursors are ours and on disk.** The server's log is append-only and
    reading it changes nothing, so the app remembers, per watch, the last
    digest it delivered (`data/digests/state.json`). Restart the app and it
    carries on from there; restart the server and nothing is delivered twice.
    The log's `db_id` changes if the database is recreated, and cursors into a
    database that no longer exists are thrown away rather than trusted.
  * **Coming back is one digest, not a backlog.** The server runs 24/7 and
    this app does not: it is started when somebody wants it. A laptop closed
    for a week comes back to seven digests per watch, and posting them in a
    row would be seven stale messages and seven model calls. So when a watch
    has more than one waiting, the courier asks the server for **one**
    aggregate - `get_summary(new_since=...)`, everything first seen since the
    last digest this app actually delivered - and posts that as a single
    "while you were away" digest. The server counts it; nothing is merged or
    re-counted on this side.
  * **The model writes; if it cannot, the template does.** The numbers are
    already exact - the server counted them - so a failed model call costs the
    digest its prose, not its content. `fallback_text` renders the same
    aggregate as a plain list, and the message records which of the two wrote
    it.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

DIGEST_CONVERSATION_ID = "digests"
EVENTS_SERVER_ID = "cape-town-events"
DIGESTS_URI = "events://digests"

DIR_ENV = "CHAT_DIGESTS_DIR"
DEFAULT_DIR = Path(__file__).parent / "data" / "digests"
POLL_ENV = "DIGEST_POLL_SECONDS"
#: How often the courier looks. The latency of a digest, not a schedule - the
#: schedule is the server's. 0 switches the courier off.
DEFAULT_POLL_SECONDS = 30
#: How often a missing or failed catalogue of the events server is fetched
#: again. The server may simply not be up yet; asking every tick would be a
#: log line every thirty seconds for as long as it is down.
CATALOGUE_RETRY_SECONDS = 300
#: "Since the beginning" - what a catch-up asks for when this app has never
#: delivered anything for a watch.
EPOCH = "1970-01-01T00:00:00+00:00"

SYSTEM_PROMPT = (
    "You write a short digest of upcoming events for one person, from data a "
    "scheduler collected. The numbers you are given are exact - use them as "
    "they are and never recount. Write in Russian unless the person's profile "
    "asks for another language; keep event titles as they are in the listing."
)

TRIGGERS = {
    "schedule": "scheduled run",
    "catch-up": "catch-up run after downtime",
    "on-demand": "run you asked for",
    "reconnect": "everything new while the agent was offline",
}


def poll_seconds() -> float:
    try:
        return float(os.getenv(POLL_ENV) or DEFAULT_POLL_SECONDS)
    except ValueError:
        return DEFAULT_POLL_SECONDS


def prompt(summary: dict, trigger: str) -> str:
    """The one message the model is sent: what to write, and the aggregate.

    The events are cut to the fields a person needs - there is no reason to
    pay for coordinates and source ids in every digest.
    """
    events = [
        {k: e.get(k) for k in ("title", "when", "venue", "price", "url", "description", "interests", "is_new")}
        for e in summary.get("events") or []
    ]
    data = {
        "watch": summary.get("watch_name"),
        "interests": summary.get("interests"),
        "trigger": TRIGGERS.get(trigger, trigger),
        "counts": summary.get("counts"),
        "events_shown": len(events),
        "events_not_shown": summary.get("omitted", 0),
        "errors": summary.get("errors") or [],
        "digests_combined": summary.get("covers") or 1,
        "events": events,
    }
    return (
        "Write the digest for this watch.\n\n"
        "Format:\n"
        "1. One opening line with the totals: how many new events, how many this weekend, "
        "and the split by interest.\n"
        "2. Then every event from the list, nearest first, each as:\n"
        "   **Title** - date and time, venue\n"
        "   Two or three sentences at most: what it is and who would enjoy it. "
        "Use only the description given; if there is none, one line from the title and venue.\n"
        "   The link on its own line.\n"
        "3. If some events were not shown, say how many. If there are errors, one line naming "
        "the sources that could not be read.\n"
        "4. If there are no new events, say so in one sentence and stop.\n\n"
        "Data:\n" + json.dumps(data, ensure_ascii=False, indent=1)
    )


def fallback_text(summary: dict, trigger: str, reason: str = "") -> str:
    """The same digest, rendered by code - what is posted when no model answers."""
    counts = summary.get("counts") or {}
    split = ", ".join(f"{k}: {v}" for k, v in (counts.get("by_interest") or {}).items())
    lines = [
        f"{summary.get('watch_name', 'Events')}: {counts.get('new', 0)} new, "
        f"{counts.get('this_weekend', 0)} this weekend" + (f" ({split})" if split else "") +
        f" - {TRIGGERS.get(trigger, trigger)}.",
    ]
    for event in summary.get("events") or []:
        place = f", {event['venue']}" if event.get("venue") else ""
        lines.append("")
        lines.append(f"{event.get('title')} - {event.get('when')}{place}")
        if event.get("description"):
            text = event["description"]
            lines.append(text if len(text) <= 220 else text[:217].rstrip() + "...")
        lines.append(event.get("url") or "")
    if summary.get("omitted"):
        lines += ["", f"...and {summary['omitted']} more."]
    if summary.get("errors"):
        lines += ["", "Could not read: " + "; ".join(summary["errors"])]
    if reason:
        lines += ["", f"(Written by the template - the model was unavailable: {reason})"]
    return "\n".join(lines)


class DigestState:
    """Where the courier is up to: one small JSON file, rewritten atomically."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.dir = Path(directory or os.getenv(DIR_ENV) or DEFAULT_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self._lock = threading.Lock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {
            "db_id": data.get("db_id") or "",
            "cursor": int(data.get("cursor") or 0),
            # Per watch: the last digest delivered, and where its "new" ended.
            "cursors": dict(data.get("cursors") or {}),
            "cuts": dict(data.get("cuts") or {}),
            "combined": int(data.get("combined") or 0),
            "unread": int(data.get("unread") or 0),
            "delivered": int(data.get("delivered") or 0),
            "last_delivered_at": data.get("last_delivered_at") or "",
            "last_poll_at": data.get("last_poll_at") or "",
            "last_error": data.get("last_error") or "",
        }

    def update(self, **fields) -> dict:
        with self._lock:
            data = self.load()
            data.update(fields)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return data


def select(log: dict, state: dict) -> tuple[list[tuple[str, list[dict]]], dict]:
    """What in the server's log is still to be delivered, grouped by watch.

    Returns `[(watch_id, [digest, ...oldest first]), ...]` in the order the
    newest digest of each watch was written, and the state fields that change
    if the database is not the one the cursors belong to.
    """
    changes: dict = {}
    cursors = state["cursors"]
    if log.get("db_id") != state["db_id"]:
        changes.update(db_id=log.get("db_id") or "", cursors={}, cuts={}, cursor=0)
        cursors = {}
    groups: dict = {}
    for digest in sorted(log.get("digests") or [], key=lambda d: int(d.get("id") or 0)):
        watch = str(digest.get("watch_id"))
        if int(digest.get("id") or 0) > int(cursors.get(watch) or 0):
            groups.setdefault(watch, []).append(digest)
    ordered = sorted(groups.items(), key=lambda item: int(item[1][-1]["id"]))
    return ordered, changes


class Courier(threading.Thread):
    """Reads the digest log on a timer and hands each new digest to `deliver`.

    Knows nothing about MCP, models or chats: `read_log` returns the parsed
    resource (or raises), `deliver` posts one digest and returns nothing. That
    is what lets the test suite drive a poll with a planted log and no server.
    """

    def __init__(self, read_log, deliver, state: DigestState | None = None,
                 interval: float | None = None, catch_up=None) -> None:
        super().__init__(name="digest-courier", daemon=True)
        self.read_log = read_log
        self.deliver = deliver
        # `catch_up(watch_id, since_iso)` -> the server's aggregate of
        # everything new since then, or None if the watch no longer exists.
        # Without one, every waiting digest is delivered as it is.
        self.catch_up = catch_up
        self.state = state or DigestState()
        self.interval = poll_seconds() if interval is None else interval
        self.stopping = threading.Event()
        self.poll_lock = threading.Lock()

    def run(self) -> None:
        while not self.stopping.wait(self.interval):
            self.poll_once()

    def poll_once(self) -> int:
        """One look. Returns how many messages were posted. Never raises."""
        with self.poll_lock:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                log = self.read_log()
            except Exception as err:  # noqa: BLE001 - a server that is down is a state, not a crash
                self.state.update(last_poll_at=now, last_error=str(err)[:300])
                return 0
            state = self.state.load()
            groups, changes = select(log, state)
            if changes:
                state = self.state.update(**changes)
            posted = 0
            for watch, waiting in groups:
                try:
                    entries = self.plan(watch, waiting, state)
                    for entry in entries:
                        self.deliver(entry)
                        posted += 1
                except Exception as err:  # noqa: BLE001 - this watch is tried again next tick
                    self.state.update(last_poll_at=now,
                                      last_error=f"delivering for watch {watch}: {err}"[:300])
                    return posted
                last = waiting[-1]
                state = self.state.update(
                    cursor=max(state["cursor"], int(last["id"])),
                    cursors={**state["cursors"], watch: int(last["id"])},
                    cuts={**state["cuts"], watch: (last.get("summary") or {}).get("cut_at") or ""},
                    unread=state["unread"] + len(entries),
                    delivered=state["delivered"] + len(entries),
                    combined=state["combined"] + (len(waiting) if len(waiting) > 1 and len(entries) == 1 else 0),
                    last_delivered_at=now,
                )
            self.state.update(last_poll_at=now, last_error="")
            return posted

    def plan(self, watch: str, waiting: list[dict], state: dict) -> list[dict]:
        """What to post for one watch: its digest, or one catch-up for many."""
        if len(waiting) == 1 or self.catch_up is None:
            return waiting
        since = state["cuts"].get(watch) or EPOCH
        summary = self.catch_up(int(watch), since)
        if summary is None:
            # The watch is gone; its newest digest stands for the rest.
            return [waiting[-1]]
        last = waiting[-1]
        errors = sorted({e for d in waiting for e in (d.get("summary") or {}).get("errors") or []})
        return [{
            "id": last["id"],
            "watch_id": last.get("watch_id"),
            "trigger": "reconnect",
            "created_at": last.get("created_at"),
            "summary": {**summary, "trigger": "reconnect", "errors": errors,
                        "cut_at": (last.get("summary") or {}).get("cut_at") or "",
                        "covers": len(waiting), "since": since},
        }]

    def stop(self) -> None:
        self.stopping.set()
