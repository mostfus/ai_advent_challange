"""Day 18: an MCP server that keeps working when nobody is asking it anything.

Every tool so far - ours on day 17, other people's on day 16 - is a function:
the model asks, it answers, and between two questions nothing happens at all.
This one has a **clock**. It watches the Cape Town event listings on a
schedule, writes what it finds into SQLite, and hands back *aggregates* - what
is new since the last digest, what is on this weekend - rather than the raw
pile.

Three things a tool with a clock has to get right, and none of them is the
protocol:

  * **Who holds the clock.** Not the model - it has none and cannot wake up.
    Not the agent app - an MCP call is request -> response, and nothing here
    holds a connection open for the server to call back on. So the schedule
    lives in this process, in a thread (`Scheduler`), next to the tools.
  * **Surviving a restart.** A 24/7 process is restarted all the time. Every
    watch keeps its `next_run_at` in the database, and a run that was missed
    while the server was down is made up **once** when it comes back - a
    week of downtime is one catch-up run, not seven.
  * **Aggregates, not rows.** The code counts, dedups, filters and groups;
    the model only ever sees the result. Hundreds of events through a model
    would be paid for in tokens and still get the arithmetic wrong.

Collecting costs **no tokens at all**. Every source in `SOURCES` was chosen
because it publishes its events in a machine-readable shape - schema.org
JSON-LD, The Events Calendar's REST API, or iCal - and was checked against the
live site before it went on the list. A source that needed a model to read it
would put a bill on a clock, which is the one thing this day must not do.

Two kinds of execution, both in the brief:

  * **periodic** - each watch runs daily at a time of day (Cape Town time) or
    every N minutes, and a scheduled run that found something new leaves a
    digest behind;
  * **deferred** - `run_now` does not make the caller wait for a dozen sites.
    It queues the run and returns at once; the result arrives later as a
    digest, which the agent app picks up from the `events://digests` resource
    and posts into its "Digests" chat.

Run it next to the agent:

    uv run events_mcp_server.py        # http://127.0.0.1:8788/mcp

No API key. The database is `data/events/events.db` (override: EVENTS_DB).
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import logging

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

# httpx logs every request at INFO; one line per listing per run is the
# scheduler's own log below, not the library's.
logging.getLogger("httpx").setLevel(logging.WARNING)

HOST = "127.0.0.1"
PORT = 8788
ENDPOINT = "/mcp"

DB_ENV = "EVENTS_DB"
DEFAULT_DB = Path(__file__).parent / "data" / "events" / "events.db"
TICK_ENV = "EVENTS_TICK_SECONDS"
#: How often the scheduler looks at the clock. A due watch waits at most this
#: long; a `run_now` does not wait at all (it wakes the thread).
DEFAULT_TICK_SECONDS = 30

#: Cape Town has no daylight saving, so this is UTC+2 all year - but it is
#: named rather than hard-coded, because "07:00" means 07:00 *there*.
TZ = ZoneInfo("Africa/Johannesburg")
TZ_NAME = "Africa/Johannesburg (SAST, UTC+2)"
CITY = "Cape Town"
#: The centre and reach of "Cape Town" for events that carry coordinates.
#: 50 km takes in the Winelands towns (Stellenbosch ~40 km) and stops short of
#: Hermanus, which is Western Cape but a different outing.
CENTRE = (-33.9249, 18.4241)
AREA_KM = 50

USER_AGENT = ("day18-events-agent/0.1 (personal daily digest of Cape Town events; "
              "one request per listing per run)")
FETCH_TIMEOUT_SECONDS = 20.0
#: Per run, how many *new* events may be looked up on their own page to fill
#: in a missing description or a date-only start. New ones only: an event
#: already on file has already been looked at.
ENRICH_PER_SOURCE = 12
#: A pause between two requests to the same site. Politeness, not throttling
#: evasion - it is one run a day.
PAUSE_SECONDS = 0.4

DEFAULT_DAILY_AT = "07:00"
MIN_EVERY_MINUTES = 5
MAX_EVENTS_IN_SUMMARY = 25
DEFAULT_SUMMARY_EVENTS = 15
DIGESTS_KEPT = 50
DESCRIPTION_CHARS = 420


# --------------------------------------------------------------------------
# The sources - every one checked against the live site before it was listed
# --------------------------------------------------------------------------
#
# `kind` is what sort of place it is; `tags` are what the model reads when it
# picks sources for somebody's interests. `format` decides the parser, and is
# the reason each one is here: all three shapes are read by code.
#
# What was checked and left out is as much a decision as what was kept:
# Computicket and Howler render their listings in the browser, ra.co, Cape
# Town Tourism and The Inside Guide refuse a script outright, and a dozen
# venue sites publish their events as prose. None of them is on this list,
# because a source that cannot be read by code cannot run on a clock for free.

SOURCES: tuple = (
    {
        "id": "quicket",
        "name": "Quicket",
        "kind": "ticketing",
        "tags": ["music", "festivals", "markets", "food", "comedy", "theatre",
                 "workshops", "sport", "family", "nightlife"],
        "format": "jsonld",
        # Quicket's own search, narrowed to Cape Town. It is national, so the
        # area filter below still runs on everything it returns.
        "urls": [f"https://www.quicket.co.za/events/south-africa?search=cape%20town&page={n}"
                 for n in (1, 2, 3)],
        "filter_area": True,
        "note": "South Africa's biggest self-service ticketing site: gigs, markets, "
                "festivals, workshops. Searched for Cape Town.",
    },
    {
        "id": "eventbrite",
        "name": "Eventbrite - Cape Town",
        "kind": "ticketing",
        "tags": ["networking", "business", "workshops", "music", "wellness", "food", "tech"],
        "format": "jsonld",
        "urls": ["https://www.eventbrite.com/d/south-africa--cape-town/events/"],
        # Eventbrite's "Cape Town" page also carries online events and the odd
        # listing from another country; the coordinates settle it.
        "filter_area": True,
        "note": "Networking, workshops, talks and concerts listed for Cape Town.",
    },
    {
        "id": "luma",
        "name": "Luma - Cape Town",
        "kind": "community",
        "tags": ["tech", "startups", "ai", "networking"],
        "format": "jsonld",
        "urls": ["https://luma.com/capetown"],
        "note": "Tech and startup community events in Cape Town.",
    },
    {
        "id": "meetup",
        "name": "Meetup - Cape Town",
        "kind": "community",
        "tags": ["tech", "ai", "meetups", "networking", "hobbies"],
        "format": "jsonld",
        "urls": ["https://www.meetup.com/find/?location=za--Cape%20Town&source=EVENTS"],
        "note": "Popular in-person meetups near Cape Town, mostly tech.",
    },
    {
        "id": "capetownetc",
        "name": "Cape Town Etc - What's on",
        "kind": "guide",
        "tags": ["music", "theatre", "comedy", "food", "festivals", "art", "nightlife", "family"],
        "format": "ical",
        "urls": ["https://www.capetownetc.com/whats-on/?ical=1"],
        "note": "A city guide's editorially picked what's-on calendar.",
    },
    {
        "id": "artscape",
        "name": "Artscape Theatre Centre",
        "kind": "venue",
        "tags": ["theatre", "dance", "musicals", "opera", "classical"],
        "format": "tribe",
        "urls": ["https://www.artscape.co.za/wp-json/tribe/events/v1/events"],
        "venue": "Artscape Theatre Centre, Foreshore",
        "note": "The city's big performing-arts centre: theatre, dance, musicals, opera.",
    },
    {
        "id": "kalkbaytheatre",
        "name": "Kalk Bay Theatre",
        "kind": "venue",
        "tags": ["theatre", "live-music", "music", "comedy"],
        "format": "tribe",
        "urls": ["https://www.kalkbaytheatre.co.za/wp-json/tribe/events/v1/events"],
        "venue": "Kalk Bay Theatre, Kalk Bay",
        "note": "A small dinner theatre in a converted church: shows and live music.",
    },
    {
        "id": "norval",
        "name": "Norval Foundation",
        "kind": "venue",
        "tags": ["art", "exhibitions", "workshops", "family", "wellness"],
        "format": "tribe",
        "urls": ["https://www.norvalfoundation.org/wp-json/tribe/events/v1/events"],
        "venue": "Norval Foundation, Steenberg",
        "note": "Art museum and sculpture garden: openings, workshops, family days.",
    },
    {
        "id": "almacafe",
        "name": "The Alma Cafe",
        "kind": "venue",
        "tags": ["jazz", "live-music", "music"],
        "format": "jsonld",
        "urls": ["https://www.almacafe.co.za/"],
        "venue": "The Alma Cafe, Rosebank",
        # The site stamps local times with +00:00 ("18:30+00:00" for a show
        # the page itself says starts at 6:30 pm). Read as Cape Town time.
        "offset_is_local": True,
        "note": "A tiny Rosebank venue with live jazz and acoustic sets most nights.",
    },
)

SOURCE_IDS = tuple(s["id"] for s in SOURCES)

#: Towns and suburbs that count as Cape Town for a listing that carries an
#: address but no coordinates - Quicket's case.
AREA_WORDS = (
    "cape town", "stellenbosch", "paarl", "franschhoek", "somerset west", "strand",
    "gordon's bay", "durbanville", "bellville", "brackenfell", "kuils river",
    "milnerton", "table view", "century city", "sea point", "green point",
    "camps bay", "woodstock", "observatory", "salt river", "rondebosch",
    "newlands", "claremont", "wynberg", "constantia", "tokai", "muizenberg",
    "kalk bay", "fish hoek", "simon's town", "noordhoek", "kommetjie",
    "hout bay", "llandudno", "gardens", "tamboerskloof", "rosebank",
    "pinelands", "goodwood", "parow", "mitchells plain", "khayelitsha",
    "langa", "gugulethu", "blouberg", "bloubergstrand", "melkbosstrand",
    "steenberg", "bergvliet", "plumstead", "kenilworth", "mowbray",
)


# --------------------------------------------------------------------------
# What the tools return - the models become each tool's `outputSchema`
# --------------------------------------------------------------------------


class Interest(BaseModel):
    name: str = Field(description="What the user called it, e.g. 'jazz'.")
    keywords: list[str] = Field(default_factory=list, description=(
        "Words that mark an event as belonging to this interest, matched against "
        "title, description, categories and venue. Include synonyms and English "
        "words - the listings are in English. Empty means the name itself."))


class SourceInfo(BaseModel):
    id: str
    name: str
    kind: str
    tags: list[str]
    format: str
    url: str
    note: str


class Sources(BaseModel):
    city: str
    timezone: str
    sources: list[SourceInfo]


class RunInfo(BaseModel):
    id: int
    trigger: str
    status: str
    started_at: str
    finished_at: str = ""
    fetched: int = 0
    new_events: int = 0
    matched: int = 0
    errors: list[str] = Field(default_factory=list)


class WatchInfo(BaseModel):
    id: int
    name: str
    interests: list[Interest]
    sources: list[str]
    schedule: str
    schedule_kind: str
    daily_at: str = ""
    every_minutes: int = 0
    paused: bool
    next_run_at: str
    run_queued: bool
    upcoming_matched: int
    last_run: RunInfo | None = None
    last_digest_at: str = ""


class Watches(BaseModel):
    timezone: str
    now: str
    watches: list[WatchInfo]


class Queued(BaseModel):
    watch_id: int
    queued: bool
    message: str


class Deleted(BaseModel):
    watch_id: int
    deleted: bool


class EventCard(BaseModel):
    title: str
    when: str
    starts_at: str
    ends_at: str = ""
    all_day: bool = False
    venue: str = ""
    url: str
    source: str
    interests: list[str] = Field(default_factory=list)
    description: str = ""
    price: str = ""
    is_new: bool = False


class Counts(BaseModel):
    total: int
    new: int
    this_weekend: int
    by_interest: dict[str, int]
    by_source: dict[str, int]
    by_day: dict[str, int]


class Summary(BaseModel):
    watch_id: int | None
    watch_name: str
    interests: list[str]
    generated_at: str
    window_from: str
    window_to: str
    only_new: bool
    counts: Counts
    events: list[EventCard]
    omitted: int
    note: str = ""


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else ""


def from_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(dt: datetime | None) -> str:
    """For people and models: Cape Town time, to the minute."""
    return dt.astimezone(TZ).isoformat(timespec="minutes") if dt else ""


def human_when(start: datetime, end: datetime | None, all_day: bool) -> str:
    """`Sat 3 Oct, 18:00` - or a range, for a show with a run."""
    s = start.astimezone(TZ)
    day = f"{s:%a} {s.day} {s:%b}"
    if all_day:
        text = day
    else:
        text = f"{day}, {s:%H:%M}"
    if end:
        e = end.astimezone(TZ)
        if e.date() != s.date() and (e - s) > timedelta(hours=20):
            text += f" - {e:%a} {e.day} {e:%b}"
    return text


def parse_when(value, *, offset_is_local: bool = False) -> tuple[datetime | None, bool]:
    """A listing's date to an aware UTC datetime, and whether it had a time.

    Four shapes turn up across nine sources: `2026-10-03` (a day, no time),
    `...T07:00:00Z`, `...T18:00:00.000+02:00`, and a naive
    `2026-09-30 19:00:00`. A missing offset means Cape Town time - every
    source here is in Cape Town.
    """
    text = str(value or "").strip()
    if not text:
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        return datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(timezone.utc), True
    text = text.replace("Z", "+00:00")
    # Python 3.10's fromisoformat wants exactly 3 or 6 fractional digits.
    text = re.sub(r"(\.\d{1,6})\d*", r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None, False
    if dt.tzinfo is None or offset_is_local:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc), False


def next_daily(daily_at: str, after: datetime) -> datetime:
    """The next `HH:MM` in Cape Town strictly after `after`."""
    hour, minute = (int(x) for x in daily_at.split(":"))
    here = after.astimezone(TZ)
    candidate = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= here:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def next_run(watch: dict, after: datetime) -> datetime:
    if watch["schedule_kind"] == "interval":
        return after + timedelta(minutes=int(watch["every_minutes"]))
    return next_daily(watch["daily_at"] or DEFAULT_DAILY_AT, after)


def describe_schedule(watch: dict) -> str:
    if watch["schedule_kind"] == "interval":
        return f"every {watch['every_minutes']} min"
    return f"daily at {watch['daily_at']} Cape Town time"


def clean_daily_at(text: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2})[:.h](\d{2})\s*", str(text or ""))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ToolError(f"daily_at must be HH:MM in Cape Town time, got {text!r}")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


# --------------------------------------------------------------------------
# Reading the three formats
# --------------------------------------------------------------------------


@dataclass
class Event:
    """One listing, normalised. The unit everything else counts."""

    source_id: str
    title: str
    starts_at: datetime
    url: str
    ends_at: datetime | None = None
    all_day: bool = False
    venue: str = ""
    address: str = ""
    description: str = ""
    categories: list = field(default_factory=list)
    price: str = ""
    lat: float | None = None
    lng: float | None = None

    @property
    def uid(self) -> str:
        """Identity is the page. Failing that, what and when."""
        basis = self.url or f"{self.source_id}|{self.title}|{iso(self.starts_at)}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


LD_JSON = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
                     re.S | re.I)
TAG = re.compile(r"<[^>]+>")
META = r"<meta[^>]+(?:property|name)=[\"']{name}[\"'][^>]*content=[\"']([^\"']*)"


def text_of(raw) -> str:
    """HTML or entity-laden text to one clean line."""
    value = html.unescape(TAG.sub(" ", str(raw or "")))
    return " ".join(value.split())


def canonical_url(url: str) -> str:
    """Strip tracking, and fold Eventbrite's per-country domains into one.

    The same Eventbrite event arrives as `.es`, `.ca` or `.com` depending on
    the organiser, and a query string of recommendation ids from Meetup.
    Neither changes which event it is.
    """
    url = html.unescape(str(url or "").strip())
    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    if re.match(r"(www\.)?eventbrite\.[a-z.]+$", host):
        host = "www.eventbrite.com"
    return urlunsplit((parts.scheme or "https", host, parts.path, "", ""))


def is_event_type(node: dict) -> bool:
    kinds = node.get("@type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    return any(isinstance(k, str) and (k.endswith("Event") or k == "Festival") for k in kinds)


def walk_events(node, out: list) -> None:
    if isinstance(node, dict):
        if is_event_type(node):
            out.append(node)
            return
        for value in node.values():
            walk_events(value, out)
    elif isinstance(node, list):
        for value in node:
            walk_events(value, out)


def jsonld_nodes(page: str) -> list[dict]:
    nodes: list = []
    for block in LD_JSON.findall(page or ""):
        try:
            walk_events(json.loads(block.strip()), nodes)
        except ValueError:
            continue
    return nodes


def first(value):
    return value[0] if isinstance(value, list) and value else value


def place_of(node: dict) -> tuple[str, str, float | None, float | None]:
    """Venue name, address line, and coordinates if any, from `location`."""
    location = first(node.get("location"))
    if not isinstance(location, dict):
        return "", "", None, None
    name = text_of(location.get("name"))
    if "://" in name:
        # Organisers paste booking links into the venue field.
        name = ""
    address = location.get("address")
    if isinstance(address, dict):
        parts = [address.get(k) for k in ("streetAddress", "addressLocality", "addressRegion")]
        line = ", ".join(text_of(p) for p in parts if p and str(p).strip())
    else:
        line = text_of(address)
    geo = location.get("geo") if isinstance(location.get("geo"), dict) else location
    try:
        lat = float(geo.get("latitude")) if geo.get("latitude") not in (None, "") else None
        lng = float(geo.get("longitude")) if geo.get("longitude") not in (None, "") else None
    except (TypeError, ValueError):
        lat = lng = None
    return name, line, lat, lng


def price_of(node: dict) -> str:
    offer = first(node.get("offers"))
    if not isinstance(offer, dict):
        return ""
    price = offer.get("price") if offer.get("price") is not None else offer.get("lowPrice")
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return ""
    return "free" if amount == 0 else f"R{amount:g}"


def event_from_jsonld(node: dict, source: dict) -> Event | None:
    status = str(node.get("eventStatus") or "")
    if status.endswith("EventCancelled") or status.endswith("EventPostponed"):
        return None
    # "What's on in Cape Town" means somewhere you can go.
    if str(node.get("eventAttendanceMode") or "").endswith("OnlineEventAttendanceMode"):
        return None
    title = text_of(node.get("name"))
    start, all_day = parse_when(node.get("startDate"), offset_is_local=bool(source.get("offset_is_local")))
    url = canonical_url(first(node.get("url")) or "")
    if not title or start is None or not url:
        return None
    end, _ = parse_when(node.get("endDate"), offset_is_local=bool(source.get("offset_is_local")))
    venue, address, lat, lng = place_of(node)
    return Event(
        source_id=source["id"],
        title=title,
        starts_at=start,
        ends_at=end,
        all_day=all_day,
        url=url,
        venue=venue or source.get("venue", ""),
        address=address,
        description=text_of(node.get("description"))[:DESCRIPTION_CHARS],
        price=price_of(node),
        lat=lat,
        lng=lng,
    )


def parse_jsonld(page: str, source: dict) -> list[Event]:
    events = [event_from_jsonld(node, source) for node in jsonld_nodes(page)]
    return [e for e in events if e is not None]


def parse_tribe(payload: dict, source: dict) -> list[Event]:
    """The Events Calendar's REST API - the WordPress plugin half the venue
    sites in town run on. Structured to the field, including a UTC start."""
    events = []
    for item in (payload or {}).get("events") or []:
        if not isinstance(item, dict):
            continue
        start, _ = parse_when((item.get("utc_start_date") or "") + "+00:00")
        if start is None:
            start, _ = parse_when(item.get("start_date"))
        end, _ = parse_when((item.get("utc_end_date") or "") + "+00:00") if item.get("utc_end_date") else (None, False)
        title = text_of(item.get("title"))
        url = canonical_url(item.get("url") or "")
        if not title or start is None or not url:
            continue
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        address = ", ".join(text_of(venue.get(k)) for k in ("address", "city") if venue.get(k))
        events.append(Event(
            source_id=source["id"],
            title=title,
            starts_at=start,
            ends_at=end,
            all_day=bool(item.get("all_day")),
            url=url,
            venue=text_of(venue.get("venue")) or source.get("venue", ""),
            address=address,
            description=text_of(item.get("excerpt") or item.get("description"))[:DESCRIPTION_CHARS],
            categories=[text_of(c.get("name")) for c in item.get("categories") or [] if isinstance(c, dict)],
            price=text_of(item.get("cost")),
        ))
    return events


def ical_unescape(value: str) -> str:
    return (value.replace("\\n", "\n").replace("\\N", "\n")
                 .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def ical_time(params: str, value: str) -> tuple[datetime | None, bool]:
    """`DTSTART;TZID=...:20260820T190000` and its friends.

    TZIDs seen in the wild include `UTC+2`, which is not a zone name - so an
    offset spelled that way is read as an offset, a real zone name as a zone,
    and anything else as Cape Town, where every source here is.
    """
    value = value.strip()
    if "VALUE=DATE" in params.upper() or re.fullmatch(r"\d{8}", value):
        try:
            day = datetime.strptime(value[:8], "%Y%m%d")
        except ValueError:
            return None, False
        return day.replace(tzinfo=TZ).astimezone(timezone.utc), True
    try:
        naive = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    if value.endswith("Z"):
        return naive.replace(tzinfo=timezone.utc), False
    tz = TZ
    match = re.search(r"TZID=([^;:]+)", params)
    if match:
        name = match.group(1).strip('"')
        offset = re.fullmatch(r"(?:UTC|GMT)([+-]\d{1,2})(?::?(\d{2}))?", name)
        if offset:
            hours = int(offset.group(1))
            minutes = int(offset.group(2) or 0) * (1 if hours >= 0 else -1)
            tz = timezone(timedelta(hours=hours, minutes=minutes))
        else:
            try:
                tz = ZoneInfo(name)
            except Exception:  # noqa: BLE001 - an unknown zone falls back to Cape Town
                tz = TZ
    return naive.replace(tzinfo=tz).astimezone(timezone.utc), False


def parse_ical(text: str, source: dict) -> list[Event]:
    # Unfold: a line starting with a space or tab continues the one before.
    lines: list = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                event = event_from_ical(current, source)
                if event:
                    events.append(event)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, _, params = head.partition(";")
        current[name.upper()] = (params, value)
    return events


def event_from_ical(fields: dict, source: dict) -> Event | None:
    if fields.get("STATUS", ("", ""))[1].strip().upper() == "CANCELLED":
        return None
    start, all_day = ical_time(*fields.get("DTSTART", ("", "")))
    end, _ = ical_time(*fields["DTEND"]) if "DTEND" in fields else (None, False)
    title = text_of(ical_unescape(fields.get("SUMMARY", ("", ""))[1]))
    url = canonical_url(fields.get("URL", ("", ""))[1])
    if not title or start is None or not url:
        return None
    location = text_of(ical_unescape(fields.get("LOCATION", ("", ""))[1]))
    categories = [c.strip() for c in ical_unescape(fields.get("CATEGORIES", ("", ""))[1]).split(",") if c.strip()]
    return Event(
        source_id=source["id"],
        title=title,
        starts_at=start,
        ends_at=end,
        all_day=all_day,
        url=url,
        venue=location.split(",")[0].strip(),
        address=location,
        description=text_of(ical_unescape(fields.get("DESCRIPTION", ("", ""))[1]))[:DESCRIPTION_CHARS],
        categories=categories,
    )


# --------------------------------------------------------------------------
# Fetching - the only network code, kept small so tests can replace it
# --------------------------------------------------------------------------


def http_get(url: str, accept: str = "text/html,application/json;q=0.9,*/*;q=0.8") -> str:
    """GET one URL and return the body, or raise with a sentence."""
    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT, "Accept": accept},
                             timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as err:
        raise RuntimeError(f"could not reach {urlsplit(url).netloc} ({type(err).__name__})") from err
    if response.status_code != 200:
        raise RuntimeError(f"{urlsplit(url).netloc} answered HTTP {response.status_code}")
    return response.text


def collect_source(source: dict, today: date) -> list[Event]:
    """Every current event one source lists. Raises if it could not be read."""
    fmt = source["format"]
    events: list = []
    for index, url in enumerate(source["urls"]):
        if index:
            time.sleep(PAUSE_SECONDS)
        if fmt == "tribe":
            # Paged; `start_date` keeps it to what has not happened yet.
            page, pages = 1, 1
            while page <= min(pages, 4):
                query = urlencode({"per_page": 50, "page": page, "start_date": today.isoformat()})
                payload = json.loads(http_get(f"{url}?{query}", accept="application/json"))
                events += parse_tribe(payload, source)
                pages = int(payload.get("total_pages") or 1)
                page += 1
                if page <= min(pages, 4):
                    time.sleep(PAUSE_SECONDS)
        elif fmt == "ical":
            events += parse_ical(http_get(url, accept="text/calendar"), source)
        else:
            events += parse_jsonld(http_get(url), source)
    return events


def in_area(event: Event) -> bool:
    if event.lat is not None and event.lng is not None:
        return haversine_km(CENTRE, (event.lat, event.lng)) <= AREA_KM
    text = f"{event.venue} {event.address}".lower()
    return any(word in text for word in AREA_WORDS)


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def enrich(event: Event) -> None:
    """Fill in a missing description or a date-only start from the event's page.

    Several listings carry a title and a day and nothing else - Eventbrite's
    search results have no time and no text. The event's own page usually has
    both, in the same schema.org shape, so one request fixes it. Only ever
    done for an event seen for the first time, so each page is fetched once.
    """
    page = http_get(event.url)
    for node in jsonld_nodes(page):
        detail = event_from_jsonld(node, {"id": event.source_id, "venue": event.venue})
        if detail is None:
            continue
        if detail.description and not event.description:
            event.description = detail.description
        if event.all_day and not detail.all_day and detail.starts_at.date() == event.starts_at.astimezone(TZ).date():
            event.starts_at, event.all_day = detail.starts_at, False
            event.ends_at = detail.ends_at or event.ends_at
        break
    if not event.description:
        for name in ("og:description", "description"):
            match = re.search(META.format(name=re.escape(name)), page, re.I)
            if match and text_of(match.group(1)):
                event.description = text_of(match.group(1))[:DESCRIPTION_CHARS]
                break


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------
#
# Four tables and a key-value one. Events are shared by every watch: "which
# events belong to this watch" is its sources and interests applied at read
# time, so two watches over the same source fetch it once and neither owns
# the rows. "New" is `first_seen_at` later than the watch's last digest - the
# one fact a list of current events cannot tell you and a database can.

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    interests        TEXT NOT NULL,
    sources          TEXT NOT NULL,
    schedule_kind    TEXT NOT NULL,
    daily_at         TEXT,
    every_minutes    INTEGER,
    paused           INTEGER NOT NULL DEFAULT 0,
    next_run_at      TEXT NOT NULL,
    run_requested_at TEXT,
    last_run_at      TEXT,
    last_digest_at   TEXT,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    uid           TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    starts_at     TEXT NOT NULL,
    ends_at       TEXT,
    all_day       INTEGER NOT NULL DEFAULT 0,
    url           TEXT NOT NULL,
    venue         TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    categories    TEXT NOT NULL DEFAULT '[]',
    price         TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_start ON events (starts_at);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    INTEGER NOT NULL,
    trigger     TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER NOT NULL DEFAULT 0,
    new_events  INTEGER NOT NULL DEFAULT 0,
    matched     INTEGER NOT NULL DEFAULT 0,
    errors      TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS digests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id   INTEGER NOT NULL,
    run_id     INTEGER,
    trigger    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL
);
"""


class EventsDB:
    """One file, one connection per call.

    The scheduler thread and the SDK's worker threads both come through here,
    and an `sqlite3` connection belongs to the thread that made it - so each
    operation opens its own. WAL mode lets a tool read while a run is writing.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.getenv(DB_ENV) or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('db_id', ?)",
                       (secrets.token_hex(8),))
            # A run that was in flight when the process died did not finish,
            # and must not sit there claiming to be running forever.
            db.execute("UPDATE runs SET status = 'interrupted', finished_at = ? "
                       "WHERE status = 'running'", (iso(utcnow()),))

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]

    def execute(self, sql: str, args: tuple = ()) -> int:
        with closing(self.connect()) as db, db:
            return db.execute(sql, args).lastrowid

    @property
    def db_id(self) -> str:
        return self.query("SELECT value FROM meta WHERE key = 'db_id'")[0]["value"]

    # -- watches ---------------------------------------------------------

    def watch(self, watch_id: int) -> dict | None:
        rows = self.query("SELECT * FROM watches WHERE id = ?", (watch_id,))
        return decode_watch(rows[0]) if rows else None

    def watches(self) -> list[dict]:
        return [decode_watch(r) for r in self.query("SELECT * FROM watches ORDER BY id")]

    def due(self, now: datetime) -> list[dict]:
        """Watches whose time has come, or that somebody asked to run."""
        return [w for w in self.watches() if not w["paused"] and
                (w["run_requested_at"] or from_iso(w["next_run_at"]) <= now)]

    # -- events ----------------------------------------------------------

    def known(self, uids: list[str]) -> set:
        if not uids:
            return set()
        marks = ",".join("?" * len(uids))
        return {r["uid"] for r in self.query(f"SELECT uid FROM events WHERE uid IN ({marks})", tuple(uids))}

    def upsert_events(self, events: list[Event], seen_at: datetime) -> None:
        """Insert new rows; refresh existing ones but never their first sighting."""
        stamp = iso(seen_at)
        with closing(self.connect()) as db, db:
            for e in events:
                db.execute(
                    """INSERT INTO events (uid, source_id, title, starts_at, ends_at, all_day, url,
                                           venue, address, description, categories, price,
                                           first_seen_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(uid) DO UPDATE SET
                           title = excluded.title, starts_at = excluded.starts_at,
                           ends_at = excluded.ends_at, all_day = excluded.all_day,
                           venue = excluded.venue, address = excluded.address,
                           description = CASE WHEN excluded.description != ''
                                              THEN excluded.description ELSE events.description END,
                           categories = excluded.categories, price = excluded.price,
                           last_seen_at = excluded.last_seen_at""",
                    (e.uid, e.source_id, e.title, iso(e.starts_at), iso(e.ends_at),
                     int(e.all_day), e.url, e.venue, e.address, e.description,
                     json.dumps(e.categories, ensure_ascii=False), e.price, stamp, stamp),
                )

    def upcoming(self, sources: list[str], now: datetime, until: datetime) -> list[dict]:
        """Events from these sources still to come (or still running) before `until`."""
        if not sources:
            return []
        marks = ",".join("?" * len(sources))
        rows = self.query(
            f"""SELECT * FROM events
                WHERE source_id IN ({marks})
                  AND starts_at <= ?
                  AND COALESCE(NULLIF(ends_at, ''), starts_at) >= ?
                ORDER BY starts_at""",
            (*sources, iso(until), iso(now - timedelta(hours=3))),
        )
        for row in rows:
            row["categories"] = json.loads(row["categories"] or "[]")
        return rows


def decode_watch(row: dict) -> dict:
    row = dict(row)
    row["interests"] = json.loads(row["interests"] or "[]")
    row["sources"] = json.loads(row["sources"] or "[]")
    row["paused"] = bool(row["paused"])
    return row


# --------------------------------------------------------------------------
# Aggregation - the part the model is never asked to do
# --------------------------------------------------------------------------


def keyword_list(interest: dict) -> list[str]:
    words = [w.strip().lower() for w in interest.get("keywords") or [] if w and w.strip()]
    return words or [str(interest.get("name") or "").strip().lower()]


def matching_interests(row: dict, interests: list[dict]) -> list[str]:
    """Which of the watch's interests an event belongs to. No interests: all."""
    if not interests:
        return []
    haystack = " ".join([row["title"], row["description"], row["venue"],
                         " ".join(row.get("categories") or [])]).lower()
    hits = []
    for interest in interests:
        for word in keyword_list(interest):
            # A whole word, plural allowed: `play` is not `playing`.
            if word and re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?:s|es)?(?![a-z0-9])", haystack):
                hits.append(interest["name"])
                break
    return hits


def dedup_key(row: dict) -> str:
    """The same show on two listings: same name, same day."""
    title = re.sub(r"[^a-z0-9]+", "", row["title"].lower())
    return f"{title[:40]}|{from_iso(row['starts_at']).astimezone(TZ).date()}"


def weekend_bounds(now: datetime) -> tuple[datetime, datetime]:
    """This weekend in Cape Town: Friday 17:00 to Monday 00:00.

    From Monday to Friday that is the coming one; on Saturday and Sunday it is
    the one already under way.
    """
    here = now.astimezone(TZ)
    friday = here.date() + timedelta(days=4 - here.weekday())
    start = datetime.combine(friday, dtime(17, 0), tzinfo=TZ)
    end = datetime.combine(friday + timedelta(days=3), dtime(0, 0), tzinfo=TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def summarise(db: EventsDB, watch: dict | None, *, now: datetime, days_ahead: int,
              only_new: bool, interest: str = "", limit: int = DEFAULT_SUMMARY_EVENTS,
              new_since: datetime | None = None) -> Summary:
    """Aggregate a watch's events: counts first, then the few worth reading.

    Everything a digest says in numbers comes from here, computed over *all*
    matching events; the list of cards is then cut to `limit`, nearest first.
    That split is the whole of "code counts, the model writes": the model gets
    exact totals it did not have to add up, and a handful of events it can
    actually say something about.
    """
    sources = watch["sources"] if watch else list(SOURCE_IDS)
    interests = watch["interests"] if watch else []
    if interest:
        interests = [i for i in interests if i["name"].lower() == interest.lower()] or \
                    [{"name": interest, "keywords": [interest]}]
    until = now + timedelta(days=days_ahead)
    since = new_since if new_since is not None else from_iso((watch or {}).get("last_digest_at"))

    seen: set = set()
    cards: list = []
    by_interest: dict = {}
    by_source: dict = {}
    by_day: dict = {}
    new_count = 0
    weekend_from, weekend_to = weekend_bounds(now)
    weekend = 0
    for row in db.upcoming(sources, now, until):
        hits = matching_interests(row, interests)
        if interests and not hits:
            continue
        key = dedup_key(row)
        if key in seen:
            continue
        seen.add(key)
        is_new = since is None or from_iso(row["first_seen_at"]) > since
        if only_new and not is_new:
            continue
        start = from_iso(row["starts_at"])
        end = from_iso(row["ends_at"])
        new_count += int(is_new)
        for name in hits:
            by_interest[name] = by_interest.get(name, 0) + 1
        by_source[row["source_id"]] = by_source.get(row["source_id"], 0) + 1
        day = max(start, now).astimezone(TZ).date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        if start < weekend_to and (end or start) >= weekend_from:
            weekend += 1
        cards.append(EventCard(
            title=row["title"],
            when=human_when(start, end, bool(row["all_day"])),
            starts_at=local(start),
            ends_at=local(end),
            all_day=bool(row["all_day"]),
            venue=row["venue"],
            url=row["url"],
            source=row["source_id"],
            interests=hits,
            description=row["description"],
            price=row["price"],
            is_new=is_new,
        ))

    limit = max(1, min(int(limit), MAX_EVENTS_IN_SUMMARY))
    return Summary(
        watch_id=watch["id"] if watch else None,
        watch_name=watch["name"] if watch else "All sources",
        interests=[i["name"] for i in interests],
        generated_at=local(now),
        window_from=local(now),
        window_to=local(until),
        only_new=only_new,
        counts=Counts(total=len(cards), new=new_count, this_weekend=weekend,
                      by_interest=by_interest, by_source=by_source, by_day=dict(sorted(by_day.items()))),
        events=cards[:limit],
        omitted=max(0, len(cards) - limit),
        note="" if cards else "Nothing on file matches yet - the watch may not have run.",
    )


# --------------------------------------------------------------------------
# One run, and the thread that decides when
# --------------------------------------------------------------------------


def run_watch(db: EventsDB, watch: dict, trigger: str, now: datetime | None = None) -> dict:
    """Collect every source of one watch, store, and leave a digest if due.

    A source that fails is written down and skipped - one site down does not
    cost the other eight. A scheduled run leaves a digest only when there is
    something new, because a daily "nothing new" is noise; an on-demand run
    always does, because somebody is waiting for an answer.
    """
    now = now or utcnow()
    today = now.astimezone(TZ).date()
    run_id = db.execute("INSERT INTO runs (watch_id, trigger, status, started_at) VALUES (?, ?, 'running', ?)",
                        (watch["id"], trigger, iso(now)))
    errors, fetched, new_total = [], 0, 0
    for source in [s for s in SOURCES if s["id"] in watch["sources"]]:
        try:
            events = collect_source(source, today)
        except Exception as err:  # noqa: BLE001 - one bad source is a line, not a crash
            errors.append(f"{source['id']}: {err}")
            continue
        if source.get("filter_area"):
            events = [e for e in events if in_area(e)]
        events = [e for e in events if (e.ends_at or e.starts_at) >= now - timedelta(hours=3)]
        unique = {e.uid: e for e in events}
        fresh = [e for uid, e in unique.items() if uid not in db.known(list(unique))]
        for event in [e for e in fresh if not e.description or e.all_day][:ENRICH_PER_SOURCE]:
            try:
                time.sleep(PAUSE_SECONDS)
                enrich(event)
            except Exception:  # noqa: BLE001 - a listing without its page is still a listing
                pass
        db.upsert_events(list(unique.values()), now)
        fetched += len(unique)
        new_total += len(fresh)

    summary = summarise(db, watch, now=now, days_ahead=30, only_new=True, limit=MAX_EVENTS_IN_SUMMARY)
    status = "failed" if errors and not fetched else ("partial" if errors else "ok")
    finished = utcnow()
    with closing(db.connect()) as conn, conn:
        conn.execute("UPDATE runs SET status = ?, finished_at = ?, fetched = ?, new_events = ?, "
                     "matched = ?, errors = ? WHERE id = ?",
                     (status, iso(finished), fetched, new_total, summary.counts.new,
                      json.dumps(errors, ensure_ascii=False), run_id))
        update = {"last_run_at": iso(now), "run_requested_at": None}
        if trigger != "on-demand":
            update["next_run_at"] = iso(next_run(watch, now))
        digest_id = None
        if trigger == "on-demand" or summary.counts.new:
            payload = summary.model_dump()
            payload.update({"trigger": trigger, "errors": errors, "run_id": run_id})
            digest_id = conn.execute(
                "INSERT INTO digests (watch_id, run_id, trigger, created_at, payload) VALUES (?, ?, ?, ?, ?)",
                (watch["id"], run_id, trigger, iso(finished), json.dumps(payload, ensure_ascii=False)),
            ).lastrowid
            update["last_digest_at"] = iso(now)
            conn.execute("DELETE FROM digests WHERE id <= ?", (digest_id - DIGESTS_KEPT,))
        sets = ", ".join(f"{k} = ?" for k in update)
        conn.execute(f"UPDATE watches SET {sets} WHERE id = ?", (*update.values(), watch["id"]))
    return {"run_id": run_id, "status": status, "fetched": fetched, "new": new_total,
            "matched_new": summary.counts.new, "digest_id": digest_id, "errors": errors}


class Scheduler(threading.Thread):
    """The clock. Wakes every `tick` seconds, or at once when poked.

    Which trigger a run gets is decided from the clock alone, and it is worth
    saying what each means:

      * `on-demand` - somebody called `run_now`. Does not move the schedule.
      * `catch-up`  - the run time went by while this process was down. Runs
        once, however many were missed, and the next one is computed from
        *now* - so a server that was off for a week does not fire seven times.
      * `schedule`  - on time.
    """

    def __init__(self, db: EventsDB, tick: float | None = None) -> None:
        super().__init__(name="events-scheduler", daemon=True)
        self.db = db
        self.tick = float(tick or os.getenv(TICK_ENV) or DEFAULT_TICK_SECONDS)
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.lock = threading.Lock()

    def run(self) -> None:
        while not self.stopping.is_set():
            self.run_due()
            self.wake.wait(self.tick)
            self.wake.clear()

    def run_due(self, now: datetime | None = None) -> list[dict]:
        results = []
        with self.lock:
            for watch in self.db.due(now or utcnow()):
                current = now or utcnow()
                if watch["run_requested_at"]:
                    trigger = "on-demand"
                elif current - from_iso(watch["next_run_at"]) > timedelta(seconds=self.tick * 2 + 60):
                    trigger = "catch-up"
                else:
                    trigger = "schedule"
                try:
                    result = run_watch(self.db, watch, trigger, current)
                except Exception as err:  # noqa: BLE001 - the clock must keep ticking
                    print(f"[events] watch {watch['id']} crashed: {err}")
                    self.db.execute("UPDATE watches SET run_requested_at = NULL, next_run_at = ? WHERE id = ?",
                                    (iso(next_run(watch, current)), watch["id"]))
                    continue
                print(f"[events] watch {watch['id']} {trigger}: {result['status']}, "
                      f"{result['fetched']} listed, {result['new']} new")
                results.append(result)
        return results

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------

db: EventsDB | None = None
scheduler: Scheduler | None = None


def get_db() -> EventsDB:
    global db
    if db is None:
        db = EventsDB()
    return db


@asynccontextmanager
async def lifespan(_server):
    """Start the clock with the server, stop it with the server."""
    global scheduler
    scheduler = Scheduler(get_db())
    scheduler.start()
    try:
        yield {}
    finally:
        scheduler.stop()


mcp = MCPServer(
    name="cape-town-events",
    title="Cape Town events (scheduled)",
    version="0.1.0",
    instructions=(
        "Watches Cape Town event listings on a schedule and keeps them in a database. "
        "To start: list_sources, pick the sources whose tags fit the user's interests, "
        "then create_watch. For 'what's on' questions use get_summary - it answers "
        "from what is already collected, instantly. run_now collects fresh data in "
        "the background; the result arrives later as a digest, so do not wait for it."
    ),
    lifespan=lifespan,
)


def watch_info(watch: dict, now: datetime) -> WatchInfo:
    last = get_db().query("SELECT * FROM runs WHERE watch_id = ? ORDER BY id DESC LIMIT 1", (watch["id"],))
    upcoming = summarise(get_db(), watch, now=now, days_ahead=30, only_new=False, limit=1).counts.total
    return WatchInfo(
        id=watch["id"],
        name=watch["name"],
        interests=[Interest(**i) for i in watch["interests"]],
        sources=watch["sources"],
        schedule=describe_schedule(watch),
        schedule_kind=watch["schedule_kind"],
        daily_at=watch["daily_at"] or "",
        every_minutes=watch["every_minutes"] or 0,
        paused=watch["paused"],
        next_run_at=local(from_iso(watch["next_run_at"])),
        run_queued=bool(watch["run_requested_at"]),
        upcoming_matched=upcoming,
        last_run=RunInfo(**{**last[0], "errors": json.loads(last[0]["errors"] or "[]"),
                            "finished_at": last[0]["finished_at"] or ""}) if last else None,
        last_digest_at=local(from_iso(watch["last_digest_at"])),
    )


def require_watch(watch_id: int) -> dict:
    watch = get_db().watch(watch_id)
    if watch is None:
        raise ToolError(f"there is no watch {watch_id}; list_watches shows the ones that exist")
    return watch


def poke() -> None:
    if scheduler is not None:
        scheduler.wake.set()


@mcp.tool(title="Event sources")
def list_sources() -> Sources:
    """The verified Cape Town event sources a watch can collect from.

    Each has a kind (ticketing, guide, venue, community) and tags describing
    what it lists. Pick the ones whose tags fit the user's interests - venues
    for local scenes, ticketing and guides for breadth.
    """
    return Sources(city=CITY, timezone=TZ_NAME, sources=[
        SourceInfo(id=s["id"], name=s["name"], kind=s["kind"], tags=s["tags"],
                   format=s["format"], url=s["urls"][0], note=s["note"])
        for s in SOURCES
    ])


@mcp.tool(title="Create an event watch")
def create_watch(
    interests: Annotated[list[Interest], Field(description=(
        "The user's interests, each with keywords to match event text. Empty for everything."))],
    sources: Annotated[list[str], Field(description="Source ids from list_sources.")],
    name: Annotated[str, Field(description="A short name for this watch, e.g. 'Jazz & tech'.")] = "",
    schedule: Annotated[Literal["daily", "interval"], Field(description=(
        "'daily' (the default: once a day at daily_at) or 'interval' (every every_minutes)."))] = "daily",
    daily_at: Annotated[str, Field(description="HH:MM, Cape Town time, for a daily watch.")] = DEFAULT_DAILY_AT,
    every_minutes: Annotated[int, Field(ge=MIN_EVERY_MINUTES, le=7 * 24 * 60, description=(
        "Minutes between runs, for an interval watch."))] = 60,
    collect_now: Annotated[bool, Field(description=(
        "Also queue a first collection right away, so there is data before the "
        "first scheduled run. Its digest arrives later."))] = True,
) -> WatchInfo:
    """Start watching Cape Town event sources for the user's interests, on a schedule.

    Collection runs in the background - daily by default - and every run that
    finds new matching events leaves a digest for the user. Nothing is
    collected during this call.
    """
    unknown = [s for s in sources if s not in SOURCE_IDS]
    if unknown or not sources:
        raise ToolError(f"unknown or missing source ids: {unknown or '(none)'}; "
                        f"use ids from list_sources: {', '.join(SOURCE_IDS)}")
    clean = []
    for interest in interests:
        label = " ".join(interest.name.split())[:40]
        if label:
            clean.append({"name": label, "keywords": [k.strip()[:40] for k in interest.keywords if k.strip()][:20]})
    now = utcnow()
    watch = {
        "schedule_kind": schedule,
        "daily_at": clean_daily_at(daily_at) if schedule == "daily" else None,
        "every_minutes": every_minutes if schedule == "interval" else None,
    }
    label = " ".join((name or ", ".join(i["name"] for i in clean) or "Everything").split())[:60]
    watch_id = get_db().execute(
        "INSERT INTO watches (name, interests, sources, schedule_kind, daily_at, every_minutes, "
        "next_run_at, run_requested_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (label, json.dumps(clean, ensure_ascii=False), json.dumps(list(dict.fromkeys(sources))),
         schedule, watch["daily_at"], watch["every_minutes"], iso(next_run(watch, now)),
         iso(now) if collect_now else None, iso(now)),
    )
    poke()
    return watch_info(get_db().watch(watch_id), now)


@mcp.tool(title="Event watches")
def list_watches() -> Watches:
    """Every watch: interests, sources, schedule, next run, and how the last run went."""
    now = utcnow()
    return Watches(timezone=TZ_NAME, now=local(now),
                   watches=[watch_info(w, now) for w in get_db().watches()])


@mcp.tool(title="Change a watch")
def update_watch(
    watch_id: int,
    paused: Annotated[bool | None, Field(description="Pause or resume. Omit to leave as is.")] = None,
    schedule: Annotated[Literal["daily", "interval"] | None, Field(description="New schedule kind.")] = None,
    daily_at: Annotated[str | None, Field(description="New HH:MM, Cape Town time.")] = None,
    every_minutes: Annotated[int | None, Field(ge=MIN_EVERY_MINUTES, le=7 * 24 * 60)] = None,
    interests: Annotated[list[Interest] | None, Field(description="Replace the interests.")] = None,
    sources: Annotated[list[str] | None, Field(description="Replace the source ids.")] = None,
) -> WatchInfo:
    """Pause, resume, reschedule, or change what a watch looks for. Only given fields change."""
    watch = require_watch(watch_id)
    if sources is not None:
        unknown = [s for s in sources if s not in SOURCE_IDS]
        if unknown or not sources:
            raise ToolError(f"unknown or missing source ids: {unknown or '(none)'}")
        watch["sources"] = list(dict.fromkeys(sources))
    if interests is not None:
        watch["interests"] = [{"name": i.name.strip()[:40], "keywords": [k.strip() for k in i.keywords if k.strip()][:20]}
                              for i in interests if i.name.strip()]
    if schedule is not None:
        watch["schedule_kind"] = schedule
    if daily_at is not None:
        watch["daily_at"] = clean_daily_at(daily_at)
    if every_minutes is not None:
        watch["every_minutes"] = every_minutes
    if watch["schedule_kind"] == "daily" and not watch["daily_at"]:
        watch["daily_at"] = DEFAULT_DAILY_AT
    if watch["schedule_kind"] == "interval" and not watch["every_minutes"]:
        watch["every_minutes"] = 60
    if paused is not None:
        watch["paused"] = paused
    now = utcnow()
    rescheduled = schedule is not None or daily_at is not None or every_minutes is not None or paused is False
    get_db().execute(
        "UPDATE watches SET interests = ?, sources = ?, schedule_kind = ?, daily_at = ?, every_minutes = ?, "
        "paused = ?, next_run_at = ? WHERE id = ?",
        (json.dumps(watch["interests"], ensure_ascii=False), json.dumps(watch["sources"]),
         watch["schedule_kind"], watch["daily_at"], watch["every_minutes"], int(watch["paused"]),
         iso(next_run(watch, now)) if rescheduled else watch["next_run_at"], watch_id),
    )
    return watch_info(get_db().watch(watch_id), now)


@mcp.tool(title="Delete a watch")
def delete_watch(watch_id: int) -> Deleted:
    """Stop and forget a watch. Collected events stay; other watches may use them."""
    require_watch(watch_id)
    with closing(get_db().connect()) as conn, conn:
        conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        conn.execute("DELETE FROM runs WHERE watch_id = ?", (watch_id,))
    return Deleted(watch_id=watch_id, deleted=True)


@mcp.tool(title="Collect now (in the background)")
def run_now(watch_id: int) -> Queued:
    """Queue a fresh collection for a watch and return immediately.

    Deferred on purpose: a run visits every source and can take a minute. The
    result arrives as a digest in the user's Digests chat. To answer the user
    right now, use get_summary on what is already collected.
    """
    watch = require_watch(watch_id)
    if watch["paused"]:
        raise ToolError(f"watch {watch_id} is paused; resume it with update_watch first")
    already = bool(watch["run_requested_at"])
    if not already:
        get_db().execute("UPDATE watches SET run_requested_at = ? WHERE id = ?", (iso(utcnow()), watch_id))
    poke()
    return Queued(watch_id=watch_id, queued=True, message=(
        "Already queued - " if already else "Queued - ") +
        "collection runs in the background; a digest will be posted when it finishes.")


@mcp.tool(title="Events summary")
def get_summary(
    watch_id: Annotated[int | None, Field(description=(
        "Which watch. Omit when there is exactly one; otherwise list_watches first."))] = None,
    days_ahead: Annotated[int, Field(ge=1, le=90, description=(
        "How far ahead to look: 2-3 for 'this weekend', 7 for 'this week'."))] = 7,
    only_new: Annotated[bool, Field(description=(
        "Only events first seen since this watch's last digest."))] = False,
    interest: Annotated[str, Field(description="Narrow to one of the watch's interests, or any word.")] = "",
    limit: Annotated[int, Field(ge=1, le=MAX_EVENTS_IN_SUMMARY, description="How many event cards.")] = DEFAULT_SUMMARY_EVENTS,
) -> Summary:
    """Aggregated upcoming events from what is already collected - instant, no fetching.

    Counts (total, new, this weekend, per interest, per source, per day) cover
    every matching event; the cards are the nearest `limit` of them, each with
    title, time (Cape Town), venue, link and a short description to summarise.
    """
    watches = get_db().watches()
    if watch_id is None:
        if len(watches) > 1:
            raise ToolError("there are several watches; pass watch_id (see list_watches)")
        watch = watches[0] if watches else None
    else:
        watch = require_watch(watch_id)
    return summarise(get_db(), watch, now=utcnow(), days_ahead=days_ahead, only_new=only_new,
                     interest=interest, limit=limit)


@mcp.resource("events://digests", name="digests", title="Digests waiting to be delivered",
              mime_type="application/json")
def digests_resource() -> str:
    """The digest log, newest last - read by the host application, not the model.

    A resource and not a tool on purpose: in MCP, tools are what the *model*
    decides to call and resources are what the *application* reads. Delivering
    a digest into a chat is the app's job, and the app keeps its own cursor -
    this log is append-only, so reading it changes nothing and can be retried.
    `db_id` changes when the database is recreated, which is how a reader
    knows its cursor belongs to a database that no longer exists.
    """
    rows = get_db().query("SELECT * FROM digests ORDER BY id DESC LIMIT ?", (DIGESTS_KEPT,))
    return json.dumps({
        "db_id": get_db().db_id,
        "digests": [{"id": r["id"], "watch_id": r["watch_id"], "trigger": r["trigger"],
                     "created_at": local(from_iso(r["created_at"])), "summary": json.loads(r["payload"])}
                    for r in reversed(rows)],
    }, ensure_ascii=False)


def main() -> None:
    load_dotenv()
    get_db()
    print(f"Cape Town events MCP server on http://{HOST}:{PORT}{ENDPOINT} - database {get_db().path}")
    mcp.run(transport="streamable-http", host=HOST, port=PORT,
            streamable_http_path=ENDPOINT, json_response=True)


if __name__ == "__main__":
    main()
