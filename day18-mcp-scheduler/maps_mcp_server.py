"""Day 17: the other end of the wire - an MCP server of our own, over Google Maps.

Day 16 wrote a client by hand and pointed it at other people's servers. This
file is the thing it was pointed at - and, unlike the client, it is built on
the official SDK (`mcp`, `MCPServer`, formerly FastMCP). The asymmetry is on
purpose: the client is the part this app has opinions about (it is sync, it
shows every request in the panel), while the server's protocol work -
handshake, version negotiation, sessions, argument validation, `outputSchema`,
DNS-rebinding protection - is exactly the kind of thing worth not rewriting.
What is left in this file is the part that is actually ours: two functions
over Google's APIs.

  * `compute_distance`  - Routes API (`directions/v2:computeRoutes`). Route
    distance and travel time from A to B, plus the straight line between them.
  * `find_restaurants`  - Places API (New) (`places:searchText`). Up to five
    restaurants around a point, sorted the way the caller asked, returned as
    mini-cards in `structuredContent` so the page can draw them.

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
        "Distances and restaurant search over Google Maps. Use compute_distance for "
        "'how far / how long from A to B'; use find_restaurants for 'where to eat "
        "near X'. Places can be addresses, place names or 'lat,lng'."
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


def google_post(url: str, body: dict, field_mask: str) -> dict:
    """One POST to a Google Maps Platform API, errors turned into sentences.

    The field mask is not optional on these APIs - a request without one is
    refused - and it is also the bill: Google prices a call by the most
    expensive field asked for, so each tool asks for exactly what it draws.
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
    return payload if isinstance(payload, dict) else {}


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


def main() -> None:
    load_dotenv()
    if not os.getenv(API_KEY_ENV):
        print(f"warning: {API_KEY_ENV} is not set - both tools will answer with an error")
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
