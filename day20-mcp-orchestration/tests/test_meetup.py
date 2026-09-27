"""Day 19: plan_meetup, the pipeline behind one tool, with a Google made of dicts.

    uv run python -m unittest discover -s tests -v

Every request the pipeline makes goes through `maps_mcp_server.google_post`,
which is replaced here by `FakeGoogle`. The fake is not a list of canned
answers in call order: it answers each request *from what the request says*.
Places are geocoded by the text asked for, and the route matrix is computed
from the coordinates in its body - so a person or a place handed from one
step to the next with the wrong coordinates, or in the wrong order, gets the
wrong durations back, and the ranking comes out wrong where these tests can
see it. That is the check on "the data passed between the tools correctly":
not that each step ran, but that what reached the end is what started out.
"""

from __future__ import annotations

import asyncio
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import maps_mcp_server as maps  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

# Three people around Cape Town's city bowl, and four places between them.
ADDRESSES = {
    "Kloof St 10": {"latitude": -33.9300, "longitude": 18.4100},
    "Bree St 80": {"latitude": -33.9180, "longitude": 18.4200},
}
THIRD = "-33.9250,18.4300"  # given as coordinates: no geocoding call for it

PLACES = [
    {"id": "p0", "name": "Corner Cafe", "at": (-33.9245, 18.4195), "rating": 4.2, "reviews": 100},
    {"id": "p1", "name": "Rooftop Coffee", "at": (-33.9240, 18.4205), "rating": 4.8, "reviews": 500},
    {"id": "p2", "name": "Far Bakery", "at": (-33.9250, 18.4190), "rating": 4.6, "reviews": 300},
    {"id": "p3", "name": "Famous Roastery", "at": (-33.9235, 18.4210), "rating": 4.9, "reviews": 1000},
]

# Seconds from each person (rows, in the order given) to each place (columns).
# Longest trips: 700, 1000, 2400, 1300. The fairest is 700, the tolerance is
# five minutes, so p0 and p1 are "equally fair" and p1 wins on rating; p3 is
# rated highest of all and still loses, because somebody would travel 22 min.
DURATIONS = [
    [600, 900, 300, 1200],
    [700, 950, 2400, 1100],
    [650, 1000, 400, 1300],
]

PEOPLE = ["Anna: Kloof St 10", "Ben: Bree St 80", THIRD]


def key(point: dict) -> tuple:
    return round(point["latitude"], 6), round(point["longitude"], 6)


class FakeGoogle:
    """`google_post`, answered from the request. Records every body sent."""

    def __init__(self, durations=DURATIONS, places=PLACES, unreachable=(), drop=(),
                 shuffle_seed: int = 7) -> None:
        self.durations = durations
        self.places = places
        self.unreachable = set(unreachable)
        self.drop = set(drop)
        self.seed = shuffle_seed
        self.calls: list = []
        people = [ADDRESSES["Kloof St 10"], ADDRESSES["Bree St 80"],
                  {"latitude": -33.9250, "longitude": 18.4300}]
        self.person_at = {key(p): i for i, p in enumerate(people)}
        self.place_at = {key({"latitude": p["at"][0], "longitude": p["at"][1]}): j
                         for j, p in enumerate(places)}

    def __call__(self, url, body, field_mask, expect=dict):
        self.calls.append((url, body))
        if url == maps.ROUTE_MATRIX_URL:
            assert expect is list
            return self.matrix(body)
        assert url == maps.PLACES_SEARCH_URL
        if "locationRestriction" in body:
            return {"places": [self.place(p) for p in self.places][: body["pageSize"]]}
        point = ADDRESSES.get(body["textQuery"])
        if not point:
            return {"places": []}
        return {"places": [{"location": point, "formattedAddress": body["textQuery"] + ", Cape Town"}]}

    @staticmethod
    def place(p: dict) -> dict:
        return {
            "id": p["id"], "displayName": {"text": p["name"]},
            "location": {"latitude": p["at"][0], "longitude": p["at"][1]},
            "rating": p["rating"], "userRatingCount": p["reviews"],
            "shortFormattedAddress": p["name"] + " street", "googleMapsUri": "https://maps/" + p["id"],
        }

    def matrix(self, body: dict) -> list:
        """Durations looked up by coordinates, answered out of order, zeros left out.

        Exactly what the real API does to the three things a client gets
        wrong: order (streamed as computed), index 0 (proto3 JSON omits it)
        and an unreachable pair (an element with a condition, not a gap).
        """
        out = []
        for o, origin in enumerate(body["origins"]):
            person = self.person_at[key(origin["waypoint"]["location"]["latLng"])]
            for d, dest in enumerate(body["destinations"]):
                place = self.place_at[key(dest["waypoint"]["location"]["latLng"])]
                if (person, place) in self.drop:
                    continue
                element = {"originIndex": o, "destinationIndex": d, "status": {}}
                if (person, place) in self.unreachable:
                    element["condition"] = "ROUTE_NOT_FOUND"
                else:
                    seconds = self.durations[person][place]
                    element.update(condition="ROUTE_EXISTS", duration=f"{seconds}s",
                                   distanceMeters=seconds * 8)
                out.append({k: v for k, v in element.items() if v != 0})
        random.Random(self.seed).shuffle(out)
        return out


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.real_post = maps.google_post

    def tearDown(self) -> None:
        maps.google_post = self.real_post

    def run_with(self, fake: FakeGoogle, **kwargs):
        maps.google_post = fake
        return maps.run_meetup(kwargs.pop("people", PEOPLE), **kwargs)

    # --- the chain runs, in order, and what comes out is what went in -----

    def test_chain_runs_every_step_in_order(self) -> None:
        fake = FakeGoogle()
        result = self.run_with(fake, query="coffee", limit=4)
        self.assertEqual([s.step for s in result.trace],
                         ["geocode", "center", "search", "matrix", "rank"])
        self.assertTrue(all(s.ok for s in result.trace))
        # Two geocodes (the third person was coordinates), one search, one matrix.
        urls = [url for url, _ in fake.calls]
        self.assertEqual(urls, [maps.PLACES_SEARCH_URL] * 3 + [maps.ROUTE_MATRIX_URL])
        self.assertEqual(result.trace[0].produced, "3 points")
        self.assertEqual(result.trace[2].produced, "4 places")
        self.assertEqual(result.trace[3].produced, "12 routes, 0 unreachable")

    def test_search_step_receives_the_center_step_output(self) -> None:
        fake = FakeGoogle()
        result = self.run_with(fake)
        search_body = fake.calls[2][1]
        box = search_body["locationRestriction"]["rectangle"]
        self.assertLess(box["low"]["latitude"], result.center_lat)
        self.assertGreater(box["high"]["latitude"], result.center_lat)
        self.assertEqual(search_body["textQuery"], "cafe")

    def test_matrix_step_receives_exactly_the_search_results(self) -> None:
        fake = FakeGoogle()
        self.run_with(fake)
        body = fake.calls[-1][1]
        sent = [key(d["waypoint"]["location"]["latLng"]) for d in body["destinations"]]
        self.assertEqual(sent, [key({"latitude": p["at"][0], "longitude": p["at"][1]}) for p in PLACES])
        origins = [key(o["waypoint"]["location"]["latLng"]) for o in body["origins"]]
        self.assertEqual(len(origins), 3)

    def test_out_of_order_matrix_lands_in_the_right_cells(self) -> None:
        # Whatever order Google streams the matrix in, every person's time to
        # every place is the one in DURATIONS - checked for several orders.
        for seed in range(5):
            result = self.run_with(FakeGoogle(shuffle_seed=seed), limit=4)
            by_place = {o.place_id: o for o in result.options}
            for j, place in enumerate(PLACES):
                trips = [t.duration_seconds for t in by_place[place["id"]].trips]
                self.assertEqual(trips, [row[j] for row in DURATIONS], f"seed {seed}, {place['id']}")

    def test_names_and_order_of_people_survive_the_chain(self) -> None:
        result = self.run_with(FakeGoogle())
        self.assertEqual([p.name for p in result.people], ["Anna", "Ben", "Person 3"])
        for option in result.options:
            self.assertEqual([t.person for t in option.trips], ["Anna", "Ben", "Person 3"])

    # --- the ranking ------------------------------------------------------

    def test_fairest_first_then_best_rated_among_the_fair(self) -> None:
        result = self.run_with(FakeGoogle(), limit=4)
        self.assertEqual([o.place_id for o in result.options], ["p1", "p0", "p3", "p2"])
        self.assertEqual([o.fair for o in result.options], [True, True, False, False])
        winner = result.options[0]
        self.assertEqual(winner.longest_seconds, 1000)
        self.assertEqual(winner.spread_seconds, 100)
        self.assertEqual((result.candidates, result.reachable, result.fair), (4, 4, 2))
        self.assertTrue(result.verdict.startswith("Rooftop Coffee: the longest trip is 17 min (Person 3)"))

    def test_limit_cuts_the_options_not_the_counts(self) -> None:
        result = self.run_with(FakeGoogle(), limit=1)
        self.assertEqual(len(result.options), 1)
        self.assertEqual(result.reachable, 4)

    def test_a_place_somebody_cannot_reach_is_dropped(self) -> None:
        result = self.run_with(FakeGoogle(unreachable={(1, 1)}), limit=4)
        self.assertNotIn("p1", [o.place_id for o in result.options])
        self.assertEqual(result.options[0].place_id, "p0")
        self.assertEqual(result.trace[3].produced, "11 routes, 1 unreachable")

    def test_a_rating_from_three_reviews_does_not_beat_nine_hundred(self) -> None:
        self.assertLess(maps.rating_score(5.0, 3), maps.rating_score(4.7, 900))
        self.assertEqual(maps.rating_score(None, 0), maps.RATING_PRIOR)

    # --- where the chain stops, and what it says --------------------------

    def test_a_missing_pair_stops_the_chain_at_the_matrix(self) -> None:
        with self.assertRaises(ToolError) as caught:
            self.run_with(FakeGoogle(drop={(2, 3)}))
        message = str(caught.exception)
        self.assertIn("stopped at step 4 (matrix)", message)
        self.assertIn("11 of 12 pairs", message)
        self.assertIn("geocode: 3 points", message)
        self.assertIn("search: 4 places", message)

    def test_nothing_found_stops_the_chain_at_the_search(self) -> None:
        fake = FakeGoogle(places=[])
        with self.assertRaises(ToolError) as caught:
            self.run_with(fake)
        self.assertIn("stopped at step 3 (search)", str(caught.exception))
        self.assertNotIn(maps.ROUTE_MATRIX_URL, [url for url, _ in fake.calls])

    def test_an_unknown_address_stops_the_chain_at_the_first_step(self) -> None:
        with self.assertRaises(ToolError) as caught:
            self.run_with(FakeGoogle(), people=["Anna: Kloof St 10", "Nowhere 404"])
        message = str(caught.exception)
        self.assertIn("stopped at step 1 (geocode)", message)
        self.assertIn("Done before it: nothing", message)

    def test_nobody_can_reach_anything(self) -> None:
        everything = {(i, j) for i in range(3) for j in range(4)}
        with self.assertRaises(ToolError) as caught:
            self.run_with(FakeGoogle(unreachable=everything))
        self.assertIn("stopped at step 5 (rank)", str(caught.exception))

    def test_one_person_is_not_a_meetup(self) -> None:
        with self.assertRaises(ToolError):
            self.run_with(FakeGoogle(), people=["Anna: Kloof St 10"])

    def test_candidates_are_capped_by_the_matrix_limit(self) -> None:
        # Transit allows 100 pairs a request: six people leave room for 16
        # places. Driving allows 625, so the search's own cap of 20 holds.
        people = ["-33.93,18.41", "-33.92,18.42", "-33.925,18.43",
                  "-33.93,18.43", "-33.92,18.41", "-33.925,18.42"]
        maps.google_post = fake = FakeGoogle(places=[])
        for mode, size in (("TRANSIT", 16), ("DRIVE", 20)):
            fake.calls.clear()
            with self.assertRaises(ToolError):
                maps.run_meetup(people, travel_mode=mode)
            self.assertEqual(fake.calls[0][1]["pageSize"], size)


class HelpersTest(unittest.TestCase):
    def test_split_person(self) -> None:
        self.assertEqual(maps.split_person("Anna: Nevsky 28", 0), ("Anna", "Nevsky 28"))
        self.assertEqual(maps.split_person("-33.9,18.4", 1), ("Person 2", "-33.9,18.4"))
        self.assertEqual(maps.split_person("Cape Town, 5 Long St", 2), ("Person 3", "Cape Town, 5 Long St"))

    def test_middle_across_the_antimeridian(self) -> None:
        middle = maps.spherical_mean([{"latitude": 0, "longitude": 179},
                                      {"latitude": 0, "longitude": -179}])
        self.assertAlmostEqual(abs(middle["longitude"]), 180, places=6)

    def test_radius_grows_with_the_spread_and_is_bounded(self) -> None:
        near = [maps.Person(name="a", given="", address="", lat=-33.92, lng=18.42),
                maps.Person(name="b", given="", address="", lat=-33.921, lng=18.421)]
        far = [maps.Person(name="a", given="", address="", lat=-33.9, lng=18.4),
               maps.Person(name="b", given="", address="", lat=-34.1, lng=18.6)]
        self.assertEqual(maps.step_center(near, None)[1], maps.MIN_SEARCH_RADIUS)
        self.assertGreater(maps.step_center(far, None)[1], maps.MIN_SEARCH_RADIUS)
        self.assertEqual(maps.step_center(far, 1500)[1], 1500)


class ToolTest(unittest.TestCase):
    """The pipeline as the agent sees it: one MCP tool, one structured result."""

    def setUp(self) -> None:
        self.real_post = maps.google_post
        maps.google_post = FakeGoogle()

    def tearDown(self) -> None:
        maps.google_post = self.real_post

    def test_one_tool_is_exported_for_the_whole_pipeline(self) -> None:
        names = [t.name for t in asyncio.run(maps.mcp.list_tools())]
        self.assertIn("plan_meetup", names)
        for step in ("geocode", "center", "search", "matrix", "rank"):
            self.assertFalse(any(step in name for name in names), step)

    def test_call_returns_structured_options_and_trace(self) -> None:
        result = asyncio.run(maps.mcp.call_tool("plan_meetup", {"people": PEOPLE, "query": "coffee"}))
        self.assertFalse(result.is_error)
        data = result.structured_content
        self.assertEqual(data["options"][0]["place_id"], "p1")
        self.assertEqual(len(data["trace"]), 5)

    def test_arguments_are_validated_before_the_pipeline_starts(self) -> None:
        maps.google_post = fake = FakeGoogle()
        with self.assertRaises(ToolError):
            asyncio.run(maps.mcp.call_tool("plan_meetup", {"people": ["only me"]}))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
