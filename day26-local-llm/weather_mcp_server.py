"""Day 20: a third MCP server of our own - the weather, over Open-Meteo.

Day 20 is about an agent that has several servers in front of it and has to
pick the right one, send each call to the right place and keep going for as
many steps as a request takes. A flow worth routing needs servers that know
different things, and "is it going to rain that evening" is the question
every plan for going out eventually runs into - and neither Google Maps nor
an event listing can answer it.

  * `get_forecast` - Open-Meteo's forecast API, daily and hourly. Per day: the
    temperatures, the chance and amount of rain, the wind, and - because an
    evening out is decided by the evening, not by the day's maximum - the
    same numbers for 17:00-22:00 local time, with a one-word verdict.

Open-Meteo needs **no key** and has no account to make, which is why it was
picked over the alternatives: a day about orchestrating servers should not
start with a sign-up form. Place names are resolved through Open-Meteo's own
geocoding API, which knows cities and districts rather than street addresses;
`lat,lng` works for anything more exact.

Run it next to the agent (or run every server at once, `run_mcp_servers.py`):

    uv run weather_mcp_server.py        # http://127.0.0.1:8789/mcp

The verdict is code, not the model, for day 18's reason: "dry" is a threshold
on two numbers, and a threshold has one right answer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

HOST = "127.0.0.1"
PORT = 8789
ENDPOINT = "/mcp"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_SECONDS = 15.0
USER_AGENT = "day20-weather-mcp/0.1 (personal agent; open-meteo.com)"

#: Open-Meteo forecasts sixteen days ahead; past that there is nothing to ask.
MAX_DAYS = 16

#: The evening is what decides a night out. Local hours, inclusive at both ends.
EVENING_FROM, EVENING_TO = 17, 22

#: Where "dry" stops and "rain" starts. On the evening's highest hourly chance
#: of rain and the day's total: a 25 % chance and half a millimetre is an
#: evening nobody cancels anything for, 60 % or 5 mm is one people do.
DRY_BELOW_PERCENT = 30
DRY_BELOW_MM = 1.0
RAIN_FROM_PERCENT = 60
RAIN_FROM_MM = 5.0

Verdict = Literal["dry", "showers possible", "rain"]

LAT_LNG = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

#: WMO weather interpretation codes, as Open-Meteo documents them.
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


mcp = MCPServer(
    name="weather-local",
    title="Weather (local)",
    version="0.1.0",
    instructions=(
        "Weather forecasts for up to 16 days, from Open-Meteo. Use get_forecast "
        "whenever a plan depends on the weather: whether an evening will be dry, "
        "which day of a weekend is better for being outside, what to wear. Give a "
        "city or district name, or 'lat,lng'. Each day comes with an evening "
        "(17:00-22:00) forecast and a verdict: dry, showers possible or rain."
    ),
)


# --------------------------------------------------------------------------
# What the tool returns - the model becomes its `outputSchema`
# --------------------------------------------------------------------------


class DayForecast(BaseModel):
    date: str
    weekday: str
    summary: str
    t_min: float | None
    t_max: float | None
    precip_probability: int | None = Field(description="Highest hourly chance of rain that day, %")
    precip_mm: float | None
    wind_kmh: float | None
    evening_precip_probability: int | None = Field(
        description="Highest hourly chance of rain 17:00-22:00 local, %")
    evening_t: float | None = Field(description="Temperature at 19:00 local, °C")
    verdict: Verdict


class Forecast(BaseModel):
    location: str
    lat: float
    lng: float
    timezone: str
    days: list[DayForecast]
    source: str = "open-meteo.com"


# --------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------


def http_get(url: str, params: dict) -> dict:
    """One GET to Open-Meteo, as JSON. The one place tests replace."""
    try:
        response = httpx.get(url, params=params, timeout=TIMEOUT_SECONDS,
                             headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as err:
        raise ToolError(f"Open-Meteo could not be reached ({type(err).__name__})") from err
    if response.status_code != 200:
        try:
            reason = response.json().get("reason") or response.text[:200]
        except ValueError:
            reason = response.text[:200]
        raise ToolError(f"Open-Meteo answered {response.status_code}: {reason}")
    return response.json()


def resolve(location: str, language: str) -> tuple[float, float, str]:
    """`lat,lng` as given, or a place name through Open-Meteo's geocoder."""
    match = LAT_LNG.match(location)
    if match:
        return float(match.group(1)), float(match.group(2)), location.strip()
    # The geocoder matches names, not addresses: "Rosebank, Cape Town" finds
    # nothing, "Rosebank" finds four of them. So the first part is searched and
    # the rest, if any, is used to pick among the results.
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        raise ToolError("`location` is required")
    payload = http_get(GEOCODING_URL, {"name": parts[0], "count": 10, "language": language,
                                       "format": "json"})
    results = payload.get("results") or []
    if not results:
        raise ToolError(
            f"no place called {parts[0]!r} is known to the weather service. It knows cities "
            "and districts, not street addresses - give the city, or 'lat,lng'.")
    hints = [p.lower() for p in parts[1:]]

    def fits(result: dict) -> int:
        where = " ".join(str(result.get(k) or "") for k in
                         ("country", "country_code", "admin1", "admin2", "admin3")).lower()
        return sum(1 for hint in hints if hint in where)

    best = max(results, key=lambda r: (fits(r), r.get("population") or 0))
    label = ", ".join(str(best.get(k)) for k in ("name", "admin1", "country") if best.get(k))
    return float(best["latitude"]), float(best["longitude"]), label


def verdict_of(evening_percent: int | None, day_percent: int | None, mm: float | None) -> Verdict:
    percent = evening_percent if evening_percent is not None else (day_percent or 0)
    mm = mm or 0.0
    if percent >= RAIN_FROM_PERCENT or mm >= RAIN_FROM_MM:
        return "rain"
    if percent < DRY_BELOW_PERCENT and mm < DRY_BELOW_MM:
        return "dry"
    return "showers possible"


def as_int(value) -> int | None:
    return None if value is None else int(round(float(value)))


def build_days(payload: dict) -> list[DayForecast]:
    """Open-Meteo's parallel arrays, turned into one record per day."""
    daily = payload.get("daily") or {}
    hourly = payload.get("hourly") or {}
    by_day: dict[str, dict] = {}
    for stamp, percent, temp in zip(hourly.get("time") or [],
                                    hourly.get("precipitation_probability") or [],
                                    hourly.get("temperature_2m") or []):
        day, _, clock = str(stamp).partition("T")
        hour = int(clock[:2] or 0)
        slot = by_day.setdefault(day, {"evening": [], "t19": None})
        if EVENING_FROM <= hour <= EVENING_TO and percent is not None:
            slot["evening"].append(percent)
        if hour == 19:
            slot["t19"] = temp

    days = []
    columns = ("weather_code", "temperature_2m_min", "temperature_2m_max",
               "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max")
    rows = zip(daily.get("time") or [], *[daily.get(c) or [] for c in columns])
    for day, code, t_min, t_max, percent, mm, wind in rows:
        slot = by_day.get(day, {"evening": [], "t19": None})
        evening = max(slot["evening"]) if slot["evening"] else None
        days.append(DayForecast(
            date=day,
            weekday=date.fromisoformat(day).strftime("%A"),
            summary=WMO.get(int(code), f"weather code {code}") if code is not None else "unknown",
            t_min=t_min, t_max=t_max,
            precip_probability=as_int(percent),
            precip_mm=None if mm is None else round(float(mm), 1),
            wind_kmh=wind,
            evening_precip_probability=as_int(evening),
            evening_t=slot["t19"],
            verdict=verdict_of(as_int(evening), as_int(percent), mm),
        ))
    return days


# --------------------------------------------------------------------------
# The tool
# --------------------------------------------------------------------------


@mcp.tool(title="Weather forecast")
def get_forecast(
    location: Annotated[str, Field(description=(
        "A city or district name ('Cape Town', 'Muizenberg, South Africa') or 'lat,lng'. "
        "Not a street address."))],
    days: Annotated[int, Field(ge=1, le=MAX_DAYS, description=(
        "How many days from today. 3 covers 'this weekend' from a Thursday; ignored when "
        "`date` is given."))] = 3,
    date_from: Annotated[str, Field(description=(
        "Only from this day on, YYYY-MM-DD. Leave empty for today."))] = "",
    date_to: Annotated[str, Field(description=(
        "Up to and including this day, YYYY-MM-DD. Leave empty to use `days`."))] = "",
    language: Annotated[str, Field(description="Language for the place name, e.g. 'en' or 'ru'.")] = "en",
) -> Forecast:
    """Daily weather forecast with an evening (17:00-22:00) outlook and a verdict per day.

    For every day: summary, min/max temperature, chance and amount of rain,
    wind, and for the evening the highest chance of rain and the temperature at
    19:00, plus a verdict - dry, showers possible or rain. Use it to choose
    between days, or to check that an evening plan will not be rained out.
    """
    lat, lng, label = resolve(location.strip(), language)

    params = {
        "latitude": lat, "longitude": lng, "timezone": "auto",
        "daily": "weather_code,temperature_2m_min,temperature_2m_max,"
                 "precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
        "hourly": "precipitation_probability,temperature_2m",
    }
    start = end = None
    try:
        if date_from:
            start = date.fromisoformat(date_from.strip())
        if date_to:
            end = date.fromisoformat(date_to.strip())
    except ValueError as err:
        raise ToolError(f"dates must be YYYY-MM-DD ({err})") from err
    if start or end:
        start = start or date.today()
        end = end or start
        if end < start:
            raise ToolError(f"date_to {end} is before date_from {start}")
        if (end - start).days >= MAX_DAYS:
            raise ToolError(f"at most {MAX_DAYS} days at a time")
        params["start_date"], params["end_date"] = start.isoformat(), end.isoformat()
    else:
        params["forecast_days"] = days

    payload = http_get(FORECAST_URL, params)
    found = build_days(payload)
    if not found:
        raise ToolError("Open-Meteo returned no days for that range - it forecasts "
                        f"{MAX_DAYS} days ahead at most.")
    return Forecast(location=label, lat=lat, lng=lng,
                    timezone=str(payload.get("timezone") or ""), days=found)


def main() -> None:
    print(f"Weather MCP server on http://{HOST}:{PORT}{ENDPOINT} "
          f"(Open-Meteo, no key) - {datetime.now().isoformat(timespec='seconds')}")
    mcp.run(
        transport="streamable-http",
        host=HOST,
        port=PORT,
        streamable_http_path=ENDPOINT,
        json_response=True,
    )


if __name__ == "__main__":
    main()
