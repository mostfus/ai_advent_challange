"""Day 17: the other end of the wire - an MCP server of our own, over Google Maps.

Day 16 wrote a client by hand and pointed it at other people's servers. This
file is the thing it was pointed at - and, unlike the client, it is built on
the official SDK (`mcp`, `MCPServer`, formerly FastMCP). The asymmetry is on
purpose: the client is the part this app has opinions about (it is sync, it
shows every request in the panel), while the server's protocol work -
handshake, version negotiation, sessions, argument validation, `outputSchema`,
DNS-rebinding protection - is exactly the kind of thing worth not rewriting.
What is left in this file is the part that is actually ours: functions over
Google's APIs.

  * `compute_distance`  - Routes API (`directions/v2:computeRoutes`). Route
    distance and travel time from A to B, plus the straight line between them.
  * `find_restaurants`  - Places API (New) (`places:searchText`). Up to five
    restaurants around a point, sorted the way the caller asked, returned as
    mini-cards in `structuredContent` so the page can draw them.
  * `plan_meetup`       - day 19. Where 2-6 people should meet: a pipeline of
    five steps (geocode -> center -> search -> matrix -> rank) behind one
    tool, over Places and the Routes route matrix
    (`distanceMatrix/v2:computeRouteMatrix`). The model makes one call; the
    data passes between the steps in code, and each handoff is checked.

**Not** the legacy Distance Matrix / Places APIs: a Google Cloud project made
after March 2025 cannot enable them, and a new organisation is exactly that.

Run it next to the agent:

    uv run maps_mcp_server.py          # http://127.0.0.1:8787/mcp

It reads `GOOGLE_MAPS_API_KEY` from `.env`. The key travels in the
`X-Goog-Api-Key` header, never in a URL, and never leaves this process: the
agent talks MCP to this server and has no idea a key exists.
"""

from __future__ import annotations

import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

HOST = "127.0.0.1"
PORT = 8787
ENDPOINT = "/mcp"

API_KEY_ENV = "GOOGLE_MAPS_API_KEY"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_TIMEOUT_SECONDS = 20.0

#: The card limit is the tool's promise, not a default: a model asking for
#: twenty gets five.
MAX_CARDS = 5
#: How many candidates are fetched before sorting. Sorting five results by
#: rating is not sorting - it is reordering whatever relevance chose.
CANDIDATES = 20
DEFAULT_RADIUS = 1500
MAX_RADIUS = 50000

TravelMode = Literal["DRIVE", "WALK", "BICYCLE", "TRANSIT", "TWO_WHEELER"]
SortBy = Literal["relevance", "rating", "distance", "popularity"]

GOOGLE_MAPS_TRAVEL = {
    "DRIVE": "driving", "WALK": "walking", "BICYCLE": "bicycling",
    "TRANSIT": "transit", "TWO_WHEELER": "driving",
}

PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": "free",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}

LAT_LNG = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


mcp = MCPServer(
    name="google-maps-local",
    title="Google Maps (local)",
    version="0.1.0",
    instructions=(
        "Distances, restaurant search and meeting points over Google Maps. Use "
        "compute_distance for 'how far / how long from A to B'; use find_restaurants "
        "for 'where to eat near X'; use plan_meetup for 'where should we meet' when "
        "two or more people start from different places. Places can be addresses, "
        "place names or 'lat,lng'."
    ),
)


# --------------------------------------------------------------------------
# What the tools return - the models become each tool's `outputSchema`
# --------------------------------------------------------------------------


class Distance(BaseModel):
    origin: str
    destination: str
    travel_mode: str
    distance_meters: int
    distance_text: str
    duration_seconds: int
    duration_text: str
    straight_line_meters: int | None = None
    maps_url: str


class RestaurantCard(BaseModel):
    name: str
    cuisine: str = ""
    rating: float | None = None
    reviews: int = 0
    price: str = ""
    address: str = ""
    distance_meters: int
    open_now: bool | None = None
    maps_url: str = ""
    website: str = ""


class Restaurants(BaseModel):
    location: str
    lat: float
    lng: float
    query: str
    sort_by: str
    radius_meters: int
    cards: list[RestaurantCard]


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------
#
# `ToolError` is how a tool says "this did not work, and here is why" - the
# SDK turns it into a result with `isError: true` and the message in it, which
# is what the model reads. Anything else raised is treated as a crash and the
# model only learns that the tool failed.


def api_key() -> str:
    load_dotenv()
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise ToolError(f"{API_KEY_ENV} is not set. Add it to .env and restart the maps server.")
    return key


def google_post(url: str, body: dict, field_mask: str, expect: type = dict):
    """One POST to a Google Maps Platform API, errors turned into sentences.

    The field mask is not optional on these APIs - a request without one is
    refused - and it is also the bill: Google prices a call by the most
    expensive field asked for, so each tool asks for exactly what it draws.

    `expect` is the shape of a good answer: every API here answers with an
    object except the route matrix (day 19), which answers with an array.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key(),
        "X-Goog-FieldMask": field_mask,
    }
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=GOOGLE_TIMEOUT_SECONDS)
    except httpx.HTTPError as err:
        raise ToolError(f"could not reach Google Maps ({type(err).__name__})") from err
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code != 200:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = (error or {}).get("message") if isinstance(error, dict) else ""
        hint = ""
        if response.status_code == 403:
            hint = (" Check that the API is enabled for the key's project "
                    "(Routes API / Places API (New)) and that billing is on.")
        raise ToolError(f"Google Maps refused the request (HTTP {response.status_code}): "
                        f"{message or response.text[:200]}{hint}")
    return payload if isinstance(payload, expect) else expect()


def parse_lat_lng(text: str) -> dict | None:
    match = LAT_LNG.match(text or "")
    if not match:
        return None
    lat, lng = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"latitude": lat, "longitude": lng}


def waypoint(text: str) -> dict:
    """What Routes wants for a point: coordinates if we have them, else the text.

    Routes geocodes an `address` itself, so an address costs no extra call.
    """
    point = parse_lat_lng(text)
    if point:
        return {"location": {"latLng": point}}
    return {"address": text}


def resolve(location: str, language: str) -> tuple[dict, str]:
    """A place name to coordinates, via Places rather than the Geocoding API.

    Places is already needed for the restaurants, so resolving through it means
    one API to enable instead of two.
    """
    point = parse_lat_lng(location)
    if point:
        return point, location.strip()
    payload = google_post(
        PLACES_SEARCH_URL,
        {"textQuery": location, "languageCode": language, "pageSize": 1},
        "places.location,places.formattedAddress,places.displayName",
    )
    places = payload.get("places") or []
    if not places or not places[0].get("location"):
        raise ToolError(f"could not find a place called {location!r}")
    place = places[0]
    label = place.get("formattedAddress") or (place.get("displayName") or {}).get("text") or location
    return place["location"], label


def haversine(a: dict, b: dict) -> int:
    """Metres along the Earth's surface between two `{latitude, longitude}`."""
    lat1, lng1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lng2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return int(round(2 * 6371000 * math.asin(math.sqrt(h))))


def bounding_box(center: dict, radius: int) -> dict:
    """The rectangle around a circle, for `locationRestriction`.

    Text Search only *restricts* to a rectangle - a circle is merely a bias,
    and a bias lets a famous place across town outrank the one around the
    corner. The corners of the box are trimmed by distance afterwards, so the
    result is the circle after all.
    """
    dlat = radius / 111320
    dlng = radius / (111320 * max(0.01, math.cos(math.radians(center["latitude"]))))
    return {
        "rectangle": {
            "low": {"latitude": center["latitude"] - dlat, "longitude": center["longitude"] - dlng},
            "high": {"latitude": center["latitude"] + dlat, "longitude": center["longitude"] + dlng},
        }
    }


def human_distance(meters: int) -> str:
    return f"{meters} m" if meters < 1000 else f"{meters / 1000:.1f} km"


def human_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes = round(rest / 60)
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{max(1, minutes)} min"


# --------------------------------------------------------------------------
# The two tools
# --------------------------------------------------------------------------


@mcp.tool(title="Distance from A to B")
def compute_distance(
    origin: Annotated[str, Field(description="Point A: an address, a place name, or 'lat,lng'.")],
    destination: Annotated[str, Field(description="Point B: an address, a place name, or 'lat,lng'.")],
    travel_mode: Annotated[TravelMode, Field(
        description="How to travel. DRIVE unless the user said otherwise.")] = "DRIVE",
    language: Annotated[str, Field(
        description="BCP-47 language for the human-readable values, e.g. 'ru' or 'en'.")] = "ru",
) -> Distance:
    """Route distance and travel time between two places via Google Maps Routes.

    Works by car, on foot, by bicycle, by public transport or by two-wheeler.
    Also returns the straight-line distance between the two points.
    """
    origin, destination = origin.strip(), destination.strip()
    if not origin or not destination:
        raise ToolError("both `origin` and `destination` are required")

    payload = google_post(
        ROUTES_URL,
        {
            "origin": waypoint(origin),
            "destination": waypoint(destination),
            "travelMode": travel_mode,
            "languageCode": language,
            "units": "METRIC",
        },
        "routes.distanceMeters,routes.duration,routes.localizedValues,"
        "routes.legs.startLocation,routes.legs.endLocation",
    )
    routes = payload.get("routes") or []
    if not routes:
        raise ToolError(
            f"Google found no {travel_mode.lower()} route from {origin!r} to {destination!r}. "
            "Try another travel mode, or check the addresses."
        )
    route = routes[0]
    meters = int(route.get("distanceMeters") or 0)
    seconds = int(str(route.get("duration") or "0s").rstrip("s") or 0)
    local = route.get("localizedValues") or {}

    straight = None
    legs = route.get("legs") or []
    if legs:
        start = (legs[0].get("startLocation") or {}).get("latLng")
        end = (legs[-1].get("endLocation") or {}).get("latLng")
        if start and end:
            straight = haversine(start, end)

    return Distance(
        origin=origin,
        destination=destination,
        travel_mode=travel_mode,
        distance_meters=meters,
        distance_text=(local.get("distance") or {}).get("text") or human_distance(meters),
        duration_seconds=seconds,
        duration_text=(local.get("duration") or {}).get("text") or human_duration(seconds),
        straight_line_meters=straight,
        maps_url="https://www.google.com/maps/dir/?" + urlencode({
            "api": 1, "origin": origin, "destination": destination,
            "travelmode": GOOGLE_MAPS_TRAVEL[travel_mode],
        }),
    )


def card_from_place(place: dict, center: dict) -> RestaurantCard:
    hours = place.get("currentOpeningHours") or {}
    return RestaurantCard(
        name=(place.get("displayName") or {}).get("text") or "(unnamed)",
        cuisine=(place.get("primaryTypeDisplayName") or {}).get("text") or "",
        rating=place.get("rating"),
        reviews=int(place.get("userRatingCount") or 0),
        price=PRICE_LEVELS.get(place.get("priceLevel") or "", ""),
        address=place.get("shortFormattedAddress") or place.get("formattedAddress") or "",
        distance_meters=haversine(center, place.get("location") or center),
        open_now=hours.get("openNow"),
        maps_url=place.get("googleMapsUri") or "",
        website=place.get("websiteUri") or "",
    )


SORT_KEYS = {
    # Google's own order is the relevance order - so "relevance" is a stable
    # sort by nothing.
    "relevance": lambda card: 0,
    "rating": lambda card: (-(card.rating or 0), -card.reviews),
    "distance": lambda card: card.distance_meters,
    "popularity": lambda card: (-card.reviews, -(card.rating or 0)),
}


@mcp.tool(title="Restaurants nearby")
def find_restaurants(
    location: Annotated[str, Field(
        description="Where to search around: an address, a place name, or 'lat,lng'.")],
    query: Annotated[str, Field(
        description="What kind of food or place, e.g. 'sushi', 'georgian', 'cheap pizza', "
                    "'romantic dinner'. Empty for any restaurant.")] = "",
    radius_meters: Annotated[int, Field(
        ge=100, le=MAX_RADIUS, description="Search radius around the location.")] = DEFAULT_RADIUS,
    sort_by: Annotated[SortBy, Field(
        description="How to order the cards: 'best'/'top rated' -> rating, "
                    "'closest'/'nearby' -> distance, 'popular' -> popularity, "
                    "otherwise relevance.")] = "relevance",
    open_now: Annotated[bool, Field(description="Only places open right now.")] = False,
    min_rating: Annotated[float | None, Field(
        ge=0, le=5, description="Drop places rated below this.")] = None,
    limit: Annotated[int, Field(
        ge=1, le=MAX_CARDS, description=f"How many cards, at most {MAX_CARDS}.")] = MAX_CARDS,
    language: Annotated[str, Field(description="BCP-47 language for names and addresses.")] = "ru",
) -> Restaurants:
    """Find restaurants around a location via Google Places, as at most 5 mini-cards.

    Each card has name, cuisine, rating, number of reviews, price level,
    address, distance from the location, whether it is open now and a Google
    Maps link. The cards are shown to the user as they are, so the answer
    should pick and comment rather than repeat every field.
    """
    location = location.strip()
    if not location:
        raise ToolError("`location` is required")
    query = query.strip()

    center, label = resolve(location, language)

    body = {
        "textQuery": f"{query} restaurant" if query else "restaurant",
        "includedType": "restaurant",
        "languageCode": language,
        "pageSize": CANDIDATES,
        "locationRestriction": bounding_box(center, radius_meters),
        # Google can sort by distance itself, and does it better than we can
        # when there are more than twenty candidates in the box.
        "rankPreference": "DISTANCE" if sort_by == "distance" else "RELEVANCE",
    }
    if open_now:
        body["openNow"] = True
    if min_rating is not None:
        # Places accepts 0.5 steps only; the exact threshold is applied below.
        body["minRating"] = math.floor(min_rating * 2) / 2

    payload = google_post(
        PLACES_SEARCH_URL,
        body,
        "places.displayName,places.primaryTypeDisplayName,places.rating,"
        "places.userRatingCount,places.priceLevel,places.shortFormattedAddress,"
        "places.formattedAddress,places.location,places.currentOpeningHours.openNow,"
        "places.googleMapsUri,places.websiteUri",
    )
    cards = [card_from_place(p, center) for p in payload.get("places") or []]
    cards = [c for c in cards if c.distance_meters <= radius_meters]
    if min_rating is not None:
        cards = [c for c in cards if (c.rating or 0) >= min_rating]
    cards.sort(key=SORT_KEYS[sort_by])

    return Restaurants(
        location=label,
        lat=center["latitude"],
        lng=center["longitude"],
        query=query,
        sort_by=sort_by,
        radius_meters=radius_meters,
        cards=cards[: min(limit, MAX_CARDS)],
    )


# --------------------------------------------------------------------------
# Day 19: plan_meetup - a pipeline of five steps behind one tool
# --------------------------------------------------------------------------
#
# "Where should the three of us meet?" is not a call to `find_restaurants`:
# the answer is the place that is fair to everybody, and finding it means a
# travel time from every person to every candidate - a matrix - and then a
# choice made over that matrix. A model can call the pieces, but it would be
# copying twenty places and sixty durations from one tool's answer into the
# next one's arguments, and doing the arithmetic in its head. So the chain is
# code, and the model sees one tool:
#
#     people ─► 1 geocode ─► 2 center ─► 3 search ─► 4 matrix ─► 5 rank ─► Meetup
#               addresses    the middle   places      person ×    fairest,
#               → points     + a radius   around it   place       then best
#
# Each step reads what the steps before it produced, and each one's output is
# checked before the next step is given it: a count that does not add up
# stops the chain at the step that broke it, rather than coming out at the
# end as a confident wrong answer. What each step received, produced and
# checked goes back in `trace`, so the handoffs can be seen, not just trusted.

ROUTE_MATRIX_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

MIN_PEOPLE, MAX_PEOPLE = 2, 6
#: Google's cap on origins × destinations in one matrix request. It is what
#: sets how many candidates step 3 may hand on: six people by transit leave
#: room for sixteen places, not twenty.
MATRIX_LIMIT = 625
MATRIX_LIMITS = {"TRANSIT": 100}
#: The search area when nobody gave one: a share of how far apart people are,
#: within these bounds. Two people across the street still get a neighbourhood.
MIN_SEARCH_RADIUS, MAX_SEARCH_RADIUS = 800, 10000
SEARCH_RADIUS_SHARE = 0.4
#: Two places whose longest trips differ by a minute are equally fair; which
#: one is *better* is then a question for the ratings. The tolerance is five
#: minutes or a tenth of the longest trip, whichever is more.
FAIR_SLACK_SECONDS = 300
FAIR_SLACK_SHARE = 0.10
#: A 5.0 from three reviews is not better than a 4.7 from nine hundred: every
#: rating is pulled toward 4.0 as if it had twenty more reviews of 4.0.
RATING_PRIOR, RATING_PRIOR_WEIGHT = 4.0, 20
MAX_OPTIONS = 5

NAMED_PERSON = re.compile(r"^\s*([^:,\d]{1,30}?)\s*:\s*(\S.*)$")


class Person(BaseModel):
    name: str
    given: str
    address: str
    lat: float
    lng: float


class Trip(BaseModel):
    person: str
    duration_seconds: int
    duration_text: str
    distance_meters: int


class MeetupOption(BaseModel):
    place_id: str
    name: str
    address: str = ""
    rating: float | None = None
    reviews: int = 0
    price: str = ""
    open_now: bool | None = None
    maps_url: str = ""
    lat: float
    lng: float
    longest_seconds: int
    longest_text: str
    spread_seconds: int
    spread_text: str
    total_seconds: int
    fair: bool
    trips: list[Trip]


class PipelineStep(BaseModel):
    step: str
    took_ms: int
    received: str
    produced: str
    checked: str
    ok: bool = True


class Meetup(BaseModel):
    query: str
    travel_mode: str
    people: list[Person]
    center_lat: float
    center_lng: float
    radius_meters: int
    candidates: int
    reachable: int
    fair: int
    options: list[MeetupOption]
    verdict: str
    trace: list[PipelineStep]


@dataclass
class Candidate:
    """A place from step 3, as steps 4 and 5 need it."""

    id: str
    name: str
    location: dict
    address: str = ""
    rating: float | None = None
    reviews: int = 0
    price: str = ""
    open_now: bool | None = None
    maps_url: str = ""


class Trace:
    """The record of a run, one entry per step, written as the steps run.

    A step that raises `ToolError` is recorded too, and the error the model
    reads is rewritten to say which step it was and what had been done by
    then - "no route" is a different problem at step 4 than "no such place"
    at step 1, and the model should be able to tell the user which.
    """

    def __init__(self) -> None:
        self.steps: list[PipelineStep] = []

    @contextmanager
    def step(self, name: str, received: str):
        record = {"produced": "", "checked": ""}
        started = time.perf_counter()
        try:
            yield record
        except ToolError as err:
            done = "; ".join(f"{s.step}: {s.produced}" for s in self.steps) or "nothing"
            self.steps.append(PipelineStep(
                step=name, took_ms=ms_since(started), received=received,
                produced="", checked=str(err), ok=False))
            raise ToolError(
                f"plan_meetup stopped at step {len(self.steps)} ({name}): {err}. "
                f"Done before it: {done}.") from err
        self.steps.append(PipelineStep(
            step=name, took_ms=ms_since(started), received=received,
            produced=record["produced"], checked=record["checked"]))


def ms_since(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def split_person(text: str, index: int) -> tuple[str, str]:
    """'Anna: Nevsky 28' -> ('Anna', 'Nevsky 28'); an address alone gets a number.

    A name has no digits and no commas, which keeps 'lat,lng' and most
    addresses from being read as one.
    """
    match = NAMED_PERSON.match(text or "")
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return f"Person {index + 1}", (text or "").strip()


def spherical_mean(points: list[dict]) -> dict:
    """The middle of points on a sphere, not of their numbers.

    Averaging latitudes and longitudes is wrong across the antimeridian and
    slightly wrong everywhere else; averaging the unit vectors is not.
    """
    x = y = z = 0.0
    for p in points:
        lat, lng = math.radians(p["latitude"]), math.radians(p["longitude"])
        x += math.cos(lat) * math.cos(lng)
        y += math.cos(lat) * math.sin(lng)
        z += math.sin(lat)
    n = len(points)
    x, y, z = x / n, y / n, z / n
    return {"latitude": math.degrees(math.atan2(z, math.hypot(x, y))),
            "longitude": math.degrees(math.atan2(y, x))}


def rating_score(rating: float | None, reviews: int) -> float:
    if rating is None:
        return RATING_PRIOR
    return (rating * reviews + RATING_PRIOR * RATING_PRIOR_WEIGHT) / (reviews + RATING_PRIOR_WEIGHT)


def latlng_waypoint(point: dict) -> dict:
    return {"waypoint": {"location": {"latLng": {
        "latitude": point["latitude"], "longitude": point["longitude"]}}}}


# --- the five steps ---------------------------------------------------------
#
# Plain functions over plain values: the pipeline below is what passes one's
# output to the next, so each of them can be tested alone with a fake Google.


def step_geocode(people: list[str], language: str) -> list[Person]:
    out = []
    for index, text in enumerate(people):
        name, where = split_person(text, index)
        if not where:
            raise ToolError(f"person {index + 1} has no address")
        point, label = resolve(where, language)
        out.append(Person(name=name, given=where, address=label,
                          lat=point["latitude"], lng=point["longitude"]))
    return out


def step_center(people: list[Person], radius: int | None) -> tuple[dict, int]:
    center = spherical_mean([{"latitude": p.lat, "longitude": p.lng} for p in people])
    if radius is None:
        spread = max(haversine(center, {"latitude": p.lat, "longitude": p.lng}) for p in people)
        radius = int(round(spread * SEARCH_RADIUS_SHARE / 100) * 100)
        radius = max(MIN_SEARCH_RADIUS, min(MAX_SEARCH_RADIUS, radius))
    return center, radius


def step_search(center: dict, radius: int, query: str, open_now: bool,
                language: str, page_size: int) -> list[Candidate]:
    body = {
        "textQuery": query,
        "languageCode": language,
        "pageSize": page_size,
        "locationRestriction": bounding_box(center, radius),
        "rankPreference": "RELEVANCE",
    }
    if open_now:
        body["openNow"] = True
    payload = google_post(
        PLACES_SEARCH_URL,
        body,
        "places.id,places.displayName,places.rating,places.userRatingCount,"
        "places.priceLevel,places.shortFormattedAddress,places.formattedAddress,"
        "places.location,places.currentOpeningHours.openNow,places.googleMapsUri",
    )
    out: list[Candidate] = []
    seen = set()
    for place in payload.get("places") or []:
        location = place.get("location")
        place_id = place.get("id") or ""
        if not location or not place_id or place_id in seen:
            continue
        if haversine(center, location) > radius:
            continue
        seen.add(place_id)
        out.append(Candidate(
            id=place_id,
            name=(place.get("displayName") or {}).get("text") or "(unnamed)",
            location=location,
            address=place.get("shortFormattedAddress") or place.get("formattedAddress") or "",
            rating=place.get("rating"),
            reviews=int(place.get("userRatingCount") or 0),
            price=PRICE_LEVELS.get(place.get("priceLevel") or "", ""),
            open_now=(place.get("currentOpeningHours") or {}).get("openNow"),
            maps_url=place.get("googleMapsUri") or "",
        ))
    return out[:page_size]


def step_matrix(people: list[Person], candidates: list[Candidate],
                travel_mode: str) -> list[list[tuple[int, int] | None]]:
    """Every person to every candidate, in one request: `cells[person][place]`.

    The matrix is the handoff most worth checking, because Google does not
    answer it in order. Each element says which origin and which destination
    it is for, and it is those indices - not the element's position - that
    put it in its cell. Two more things the wire format does: an index of 0
    is left out entirely (proto3 JSON omits zeros), and an unreachable pair
    is an element with a condition, not a missing one.
    """
    body = {
        "origins": [latlng_waypoint({"latitude": p.lat, "longitude": p.lng}) for p in people],
        "destinations": [latlng_waypoint(c.location) for c in candidates],
        "travelMode": travel_mode,
    }
    payload = google_post(
        ROUTE_MATRIX_URL, body,
        "originIndex,destinationIndex,duration,distanceMeters,status,condition",
        expect=list,
    )
    rows, cols = len(people), len(candidates)
    cells: list[list[tuple[int, int] | None]] = [[None] * cols for _ in range(rows)]
    seen = set()
    for element in payload:
        o = int(element.get("originIndex") or 0)
        d = int(element.get("destinationIndex") or 0)
        if not (0 <= o < rows and 0 <= d < cols):
            raise ToolError(f"Google answered for a pair nobody asked about (origin {o}, place {d})")
        if (o, d) in seen:
            raise ToolError(f"Google answered twice for origin {o}, place {d}")
        seen.add((o, d))
        failed = (element.get("status") or {}).get("code")
        condition = element.get("condition")
        if failed or condition not in (None, "ROUTE_EXISTS") or element.get("duration") is None:
            continue
        seconds = int(str(element["duration"]).rstrip("s") or 0)
        cells[o][d] = (seconds, int(element.get("distanceMeters") or 0))
    if len(seen) != rows * cols:
        raise ToolError(f"the matrix came back with {len(seen)} of {rows * cols} pairs")
    return cells


def step_rank(people: list[Person], candidates: list[Candidate],
              cells: list[list[tuple[int, int] | None]]) -> tuple[list[MeetupOption], int]:
    """Fairest first, and among the equally fair, the best.

    "Fair" is the longest trip anybody has to make: a place ten minutes from
    two people and fifty from the third is not a meeting point, whatever its
    average. Everything within the tolerance of the fairest counts as fair,
    and those are ordered by rating; the rest follow, fairest first.
    """
    options: list[MeetupOption] = []
    for j, place in enumerate(candidates):
        column = [cells[i][j] for i in range(len(people))]
        if any(cell is None for cell in column):
            continue
        seconds = [cell[0] for cell in column]
        longest, spread = max(seconds), max(seconds) - min(seconds)
        options.append(MeetupOption(
            place_id=place.id, name=place.name, address=place.address, rating=place.rating,
            reviews=place.reviews, price=place.price, open_now=place.open_now,
            maps_url=place.maps_url,
            lat=place.location["latitude"], lng=place.location["longitude"],
            longest_seconds=longest, longest_text=human_duration(longest),
            spread_seconds=spread, spread_text=human_duration(spread) if spread else "0 min",
            total_seconds=sum(seconds), fair=False,
            trips=[Trip(person=p.name, duration_seconds=cell[0],
                        duration_text=human_duration(cell[0]), distance_meters=cell[1])
                   for p, cell in zip(people, column)],
        ))
    if not options:
        return [], 0
    best = min(o.longest_seconds for o in options)
    slack = max(FAIR_SLACK_SECONDS, int(best * FAIR_SLACK_SHARE))
    for option in options:
        option.fair = option.longest_seconds <= best + slack

    def order(option: MeetupOption):
        if option.fair:
            return (0, -rating_score(option.rating, option.reviews), option.total_seconds)
        return (1, option.longest_seconds, option.total_seconds)

    options.sort(key=order)
    return options, slack


def verdict_of(winner: MeetupOption, fair: int, reachable: int, slack: int) -> str:
    slowest = max(winner.trips, key=lambda t: t.duration_seconds)
    text = (f"{winner.name}: the longest trip is {winner.longest_text} ({slowest.person}), "
            f"and nobody travels more than {winner.spread_text} longer than anyone else.")
    if fair > 1:
        text += (f" {fair} of {reachable} reachable places are within {human_duration(slack)} "
                 f"of the fairest; of those, this one is rated best.")
    return text


# --- the pipeline -----------------------------------------------------------


def run_meetup(people: list[str], query: str = "cafe", travel_mode: str = "DRIVE",
               radius_meters: int | None = None, open_now: bool = False,
               limit: int = 3, language: str = "ru") -> Meetup:
    people = [p for p in (people or []) if p and p.strip()]
    if not MIN_PEOPLE <= len(people) <= MAX_PEOPLE:
        raise ToolError(f"plan_meetup needs {MIN_PEOPLE} to {MAX_PEOPLE} people, got {len(people)}")
    query = (query or "").strip() or "cafe"
    trace = Trace()

    with trace.step("geocode", f"{len(people)} addresses") as s:
        points = step_geocode(people, language)
        if len(points) != len(people):
            raise ToolError(f"{len(people)} addresses went in, {len(points)} points came out")
        s["produced"] = f"{len(points)} points"
        s["checked"] = "one point per person"

    with trace.step("center", f"{len(points)} points") as s:
        center, radius = step_center(points, radius_meters)
        lats, lngs = [p.lat for p in points], [p.lng for p in points]
        if not (min(lats) - 1e-6 <= center["latitude"] <= max(lats) + 1e-6
                and min(lngs) - 1e-6 <= center["longitude"] <= max(lngs) + 1e-6):
            raise ToolError("the middle came out outside the area the people are in")
        s["produced"] = (f"{center['latitude']:.5f},{center['longitude']:.5f}, "
                         f"radius {human_distance(radius)}")
        s["checked"] = "inside the people's bounding box"

    page_size = min(CANDIDATES, MATRIX_LIMITS.get(travel_mode, MATRIX_LIMIT) // len(points))
    with trace.step("search", f"'{query}' within {human_distance(radius)} of the middle") as s:
        candidates = step_search(center, radius, query, open_now, language, page_size)
        if not candidates:
            raise ToolError(f"Google found no '{query}' within {human_distance(radius)} of the "
                            "middle - try a wider radius or another kind of place")
        if len({c.id for c in candidates}) != len(candidates):
            raise ToolError("the same place came back twice")
        s["produced"] = f"{len(candidates)} places"
        s["checked"] = f"unique, all within the radius, at most {page_size} for the matrix"

    pairs = len(points) * len(candidates)
    with trace.step("matrix", f"{len(points)} people × {len(candidates)} places") as s:
        cells = step_matrix(points, candidates, travel_mode)
        unreachable = sum(cell is None for row in cells for cell in row)
        s["produced"] = f"{pairs - unreachable} routes, {unreachable} unreachable"
        s["checked"] = f"all {pairs} pairs answered once, put in place by their indices"

    with trace.step("rank", f"{len(candidates)} places × {len(points)} trips") as s:
        options, slack = step_rank(points, candidates, cells)
        if not options:
            raise ToolError(f"none of the {len(candidates)} places can be reached by everybody "
                            f"by {travel_mode.lower()} - try another travel mode")
        known = {c.id for c in candidates}
        for option in options:
            if option.place_id not in known or len(option.trips) != len(points):
                raise ToolError(f"{option.name!r} does not line up with the search or the people")
        fair = sum(o.fair for o in options)
        s["produced"] = f"{len(options)} reachable by all, {fair} fair"
        s["checked"] = "every option came from the search, with one trip per person"

    return Meetup(
        query=query, travel_mode=travel_mode, people=points,
        center_lat=center["latitude"], center_lng=center["longitude"], radius_meters=radius,
        candidates=len(candidates), reachable=len(options), fair=fair,
        options=options[: min(limit, MAX_OPTIONS)],
        verdict=verdict_of(options[0], fair, len(options), slack),
        trace=trace.steps,
    )


@mcp.tool(title="Where to meet")
def plan_meetup(
    people: Annotated[list[str], Field(
        min_length=MIN_PEOPLE, max_length=MAX_PEOPLE,
        description="Where each person starts, 2 to 6 of them: 'Name: address', "
                    "a plain address or 'lat,lng'.")],
    query: Annotated[str, Field(
        description="What kind of place to meet at, e.g. 'coffee', 'georgian restaurant', "
                    "'bar', 'park'.")] = "cafe",
    travel_mode: Annotated[TravelMode, Field(
        description="How everybody travels. DRIVE unless the user said otherwise.")] = "DRIVE",
    radius_meters: Annotated[int | None, Field(
        ge=MIN_SEARCH_RADIUS, le=MAX_SEARCH_RADIUS,
        description="How far from the middle to look. Leave empty to size it from how "
                    "far apart the people are.")] = None,
    open_now: Annotated[bool, Field(description="Only places open right now.")] = False,
    limit: Annotated[int, Field(
        ge=1, le=MAX_OPTIONS, description=f"How many options, at most {MAX_OPTIONS}.")] = 3,
    language: Annotated[str, Field(description="BCP-47 language for names and addresses.")] = "ru",
) -> Meetup:
    """Find the fairest place for 2-6 people to meet: one call runs a whole pipeline.

    Geocodes everybody, takes the middle, searches places there, gets travel
    time from every person to every place, and ranks them: smallest longest
    trip first, the best-rated among the equally fair. Returns the top options
    with each person's travel time, a one-line verdict, and a trace of the
    pipeline's steps. The options are shown to the user as cards, so the
    answer should recommend and explain rather than list every time.
    """
    return run_meetup(people, query, travel_mode, radius_meters, open_now, limit, language)


def main() -> None:
    load_dotenv()
    if not os.getenv(API_KEY_ENV):
        print(f"warning: {API_KEY_ENV} is not set - every tool will answer with an error")
    print(f"Google Maps MCP server on http://{HOST}:{PORT}{ENDPOINT}")
    # `json_response`: every answer is one application/json body rather than
    # an SSE stream - these tools have nothing to stream. The client handles
    # both anyway (day 16).
    mcp.run(
        transport="streamable-http",
        host=HOST,
        port=PORT,
        streamable_http_path=ENDPOINT,
        json_response=True,
    )


if __name__ == "__main__":
    main()
