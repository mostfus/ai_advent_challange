"""Day 18: the scheduler and the courier, with no network and no model.

    uv run python -m unittest discover -s tests -v

`tests/ui-test.js` covers what the page does with a digest. This covers the
parts no page can see: the three parsers against the shapes real sources
send, the area filter, the clock (including the catch-up rule), what counts
as new, and the courier's cursor. Every fetch goes through
`events_mcp_server.http_get`, which is replaced here by a dictionary of pages.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import digests  # noqa: E402
import events_mcp_server as ev  # noqa: E402
import store as store_module  # noqa: E402

UTC = timezone.utc
ALMA = next(s for s in ev.SOURCES if s["id"] == "almacafe")
KALK = next(s for s in ev.SOURCES if s["id"] == "kalkbaytheatre")
ETC = next(s for s in ev.SOURCES if s["id"] == "capetownetc")


def ld_page(*events: dict) -> str:
    return ('<html><head><script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@graph": list(events)})
            + "</script></head><body>listing</body></html>")


def alma_event(name: str, start: str, slug: str) -> dict:
    return {"@type": "Event", "name": name, "startDate": start,
            "url": f"https://www.almacafe.co.za/alma-event/{slug}/"}


def tribe_payload(*titles_starts: tuple) -> dict:
    return {"total": len(titles_starts), "total_pages": 1, "events": [
        {"title": title, "utc_start_date": start, "utc_end_date": "", "all_day": False,
         "url": f"https://www.kalkbaytheatre.co.za/event/{title.lower().replace(' ', '-')}/",
         "excerpt": "<p>GENRE: LIVE MUSIC</p>", "cost": "R280", "venue": [],
         "categories": [{"name": "Music"}, {"name": "Theater"}]}
        for title, start in titles_starts
    ]}


class FakeWeb:
    """`http_get`, answered from a dict. Records every URL asked for."""

    def __init__(self, pages: dict) -> None:
        self.pages = pages
        self.asked: list = []

    def __call__(self, url: str, accept: str = "") -> str:
        self.asked.append(url)
        for prefix, body in self.pages.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, str) else json.dumps(body)
        raise RuntimeError(f"{url} answered HTTP 404")


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ev.EventsDB(Path(self.tmp.name) / "events.db")
        self._http, self._pause = ev.http_get, ev.PAUSE_SECONDS
        ev.PAUSE_SECONDS = 0

    def tearDown(self) -> None:
        ev.http_get, ev.PAUSE_SECONDS = self._http, self._pause
        self.tmp.cleanup()

    def add_watch(self, sources: list, interests: list | None = None, *,
                  next_run_at: datetime, daily_at: str = "07:00", requested: bool = False) -> dict:
        watch_id = self.db.execute(
            "INSERT INTO watches (name, interests, sources, schedule_kind, daily_at, next_run_at, "
            "run_requested_at, created_at) VALUES (?, ?, ?, 'daily', ?, ?, ?, ?)",
            ("test", json.dumps(interests or []), json.dumps(sources), daily_at,
             ev.iso(next_run_at), ev.iso(next_run_at) if requested else None, ev.iso(next_run_at)))
        return self.db.watch(watch_id)


class Parsing(unittest.TestCase):
    def test_four_date_shapes_all_land_in_utc(self):
        self.assertEqual(ev.parse_when("2026-10-11T07:00:00Z")[0].isoformat(), "2026-10-11T07:00:00+00:00")
        self.assertEqual(ev.parse_when("2026-09-30T18:00:00.000+02:00")[0].hour, 16)
        self.assertEqual(ev.parse_when("2026-09-30 19:00:00")[0].hour, 17)  # naive = Cape Town
        start, all_day = ev.parse_when("2026-10-03")
        self.assertTrue(all_day)
        self.assertEqual(start.astimezone(ev.TZ).date().isoformat(), "2026-10-03")

    def test_a_site_that_stamps_local_time_as_utc_is_corrected(self):
        # The Alma writes 18:30+00:00 for a show its own page says is at 6:30 pm.
        start, _ = ev.parse_when("2026-10-02T18:30:00+00:00", offset_is_local=True)
        self.assertEqual(start.astimezone(ev.TZ).strftime("%H:%M"), "18:30")

    def test_jsonld_keeps_events_and_drops_online_and_cancelled_ones(self):
        page = ld_page(
            {"@type": "MusicEvent", "name": "Jazz &amp; Blues", "startDate": "2026-10-02T18:30:00+02:00",
             "url": "https://www.eventbrite.es/e/jazz-tickets-1?aff=x",
             "location": {"@type": "Place", "name": "5 Park Rd",
                          "address": {"streetAddress": "5 Park Road", "addressLocality": "Cape Town"},
                          "geo": {"latitude": "-33.93", "longitude": "18.41"}},
             "offers": [{"price": 0}]},
            {"@type": "Event", "name": "Webinar", "startDate": "2026-10-02",
             "url": "https://x.test/w", "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode"},
            {"@type": "Event", "name": "Called off", "startDate": "2026-10-02",
             "url": "https://x.test/c", "eventStatus": "https://schema.org/EventCancelled"},
            {"@type": "Organization", "name": "not an event"},
        )
        events = ev.parse_jsonld(page, {"id": "eventbrite"})
        self.assertEqual([e.title for e in events], ["Jazz & Blues"])
        event = events[0]
        # Eventbrite's per-country domains and tracking fold into one identity.
        self.assertEqual(event.url, "https://www.eventbrite.com/e/jazz-tickets-1")
        self.assertEqual((event.venue, event.price), ("5 Park Rd", "free"))
        self.assertTrue(ev.in_area(event))

    def test_tribe_reads_the_utc_start_and_falls_back_to_the_source_venue(self):
        events = ev.parse_tribe(tribe_payload(("LIAM UNDRUGGED", "2026-10-01 17:00:00")), KALK)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].starts_at.astimezone(ev.TZ).strftime("%H:%M"), "19:00")
        self.assertEqual(events[0].venue, "Kalk Bay Theatre, Kalk Bay")
        self.assertEqual(events[0].categories, ["Music", "Theater"])
        self.assertEqual(events[0].description, "GENRE: LIVE MUSIC")

    def test_ical_unfolds_lines_and_reads_an_offset_spelled_as_a_zone(self):
        text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
                "DTSTART;TZID=UTC+2:20260820T190000\r\nDTEND;TZID=UTC+2:20261016T040000\r\n"
                "SUMMARY:Madame JoJo's\\, with Lilly\r\n"
                "DESCRIPTION:Cabaret\\nand burlesque for six Thursdays \r\n only\r\n"
                "URL:https://www.capetownetc.com/whats-on-single/jojo/\r\n"
                "LOCATION:Barrack Street\\, Cape Town\r\nEND:VEVENT\r\n"
                "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261101\r\nSUMMARY:All day\r\n"
                "URL:https://www.capetownetc.com/whats-on-single/day/\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        events = ev.parse_ical(text, ETC)
        self.assertEqual([e.title for e in events], ["Madame JoJo's, with Lilly", "All day"])
        self.assertEqual(events[0].starts_at.astimezone(ev.TZ).strftime("%H:%M"), "19:00")
        self.assertEqual(events[0].description, "Cabaret and burlesque for six Thursdays only")
        self.assertEqual(events[0].venue, "Barrack Street")
        self.assertTrue(events[1].all_day)

    def test_the_area_filter_uses_coordinates_first_and_the_address_second(self):
        near = ev.Event("q", "a", datetime.now(UTC), "u", lat=-33.93, lng=18.86)   # Stellenbosch
        far = ev.Event("q", "b", datetime.now(UTC), "u", lat=-26.2, lng=28.04)     # Johannesburg
        by_name = ev.Event("q", "c", datetime.now(UTC), "u", venue="The Kitchen", address="Woodstock")
        elsewhere = ev.Event("q", "d", datetime.now(UTC), "u", address="Braamfontein, Johannesburg")
        self.assertEqual([ev.in_area(e) for e in (near, far, by_name, elsewhere)], [True, False, True, False])


class Clock(Base):
    def test_daily_is_in_cape_town_time_and_always_in_the_future(self):
        after = datetime(2026, 9, 27, 4, 0, tzinfo=UTC)  # 06:00 in Cape Town
        self.assertEqual(ev.local(ev.next_daily("07:00", after)), "2026-09-27T07:00+02:00")
        after = datetime(2026, 9, 27, 5, 0, tzinfo=UTC)  # exactly 07:00 there: tomorrow
        self.assertEqual(ev.local(ev.next_daily("07:00", after)), "2026-09-28T07:00+02:00")

    def test_a_week_of_downtime_is_one_catch_up_run_not_seven(self):
        ev.http_get = FakeWeb({"https://www.almacafe.co.za/": ld_page(
            alma_event("Quartet", "2026-10-02T18:30:00+00:00", "quartet"))})
        now = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
        watch = self.add_watch(["almacafe"], next_run_at=now - timedelta(days=7))
        results = ev.Scheduler(self.db, tick=30).run_due(now)
        self.assertEqual(len(results), 1)
        run = self.db.query("SELECT trigger FROM runs")
        self.assertEqual([r["trigger"] for r in run], ["catch-up"])
        # And the next one is computed from now, not from the missed slot.
        self.assertEqual(ev.local(ev.from_iso(self.db.watch(watch["id"])["next_run_at"])),
                         "2026-09-28T07:00+02:00")
        self.assertEqual(ev.Scheduler(self.db, tick=30).run_due(now + timedelta(minutes=1)), [])

    def test_an_on_demand_run_does_not_move_the_schedule(self):
        ev.http_get = FakeWeb({"https://www.almacafe.co.za/": ld_page()})
        now = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
        slot = now + timedelta(hours=20)
        watch = self.add_watch(["almacafe"], next_run_at=slot, requested=True)
        ev.Scheduler(self.db, tick=30).run_due(now)
        after = self.db.watch(watch["id"])
        self.assertIsNone(after["run_requested_at"])
        self.assertEqual(after["next_run_at"], ev.iso(slot))
        self.assertEqual(self.db.query("SELECT trigger FROM runs")[0]["trigger"], "on-demand")

    def test_a_run_interrupted_by_a_crash_is_not_left_running(self):
        self.db.execute("INSERT INTO runs (watch_id, trigger, status, started_at) VALUES (1, 'schedule', 'running', 'x')")
        reopened = ev.EventsDB(self.db.path)
        self.assertEqual(reopened.query("SELECT status FROM runs")[0]["status"], "interrupted")


class Runs(Base):
    def setUp(self) -> None:
        super().setUp()
        self.now = datetime(2026, 9, 27, 5, 0, tzinfo=UTC)
        self.pages = {
            # Each event's own page, looked up once for its description.
            "https://www.almacafe.co.za/alma-event/tiana": ld_page(
                {"@type": "Event", "name": "x", "startDate": "2026-10-02T18:30:00+00:00",
                 "url": "https://www.almacafe.co.za/alma-event/x/", "description": "A night of jazz."}),
            "https://www.almacafe.co.za/alma-event/folk": ld_page(
                {"@type": "Event", "name": "y", "startDate": "2026-10-03T18:00:00+00:00",
                 "url": "https://www.almacafe.co.za/alma-event/y/", "description": "Songs around a fire."}),
            "https://www.almacafe.co.za/": ld_page(
                alma_event("Tiana Amari Quartet", "2026-10-02T18:30:00+00:00", "tiana"),
                alma_event("Folk evening", "2026-10-03T18:00:00+00:00", "folk")),
            "https://www.kalkbaytheatre.co.za/": tribe_payload(("Jazz at the church", "2026-10-01 17:00:00")),
        }
        ev.http_get = FakeWeb(self.pages)
        self.watch = self.add_watch(["almacafe", "kalkbaytheatre"],
                                    [{"name": "jazz", "keywords": ["jazz", "quartet"]}],
                                    next_run_at=self.now)

    def test_a_scheduled_run_stores_everything_and_digests_the_matches(self):
        result = ev.run_watch(self.db, self.watch, "schedule", self.now)
        self.assertEqual((result["status"], result["fetched"], result["new"]), ("ok", 3, 3))
        self.assertIsNotNone(result["digest_id"])
        payload = json.loads(self.db.query("SELECT payload FROM digests")[0]["payload"])
        # The folk evening is stored but is not jazz.
        self.assertEqual(sorted(e["title"] for e in payload["events"]),
                         ["Jazz at the church", "Tiana Amari Quartet"])
        self.assertEqual(payload["counts"]["by_interest"], {"jazz": 2})
        # A new event with no description was looked up on its own page, once.
        stored = self.db.query("SELECT description FROM events WHERE title = 'Tiana Amari Quartet'")
        self.assertEqual(stored[0]["description"], "A night of jazz.")

    def test_nothing_new_means_no_scheduled_digest_but_an_asked_for_one_still_comes(self):
        ev.run_watch(self.db, self.watch, "schedule", self.now)
        watch = self.db.watch(self.watch["id"])
        quiet = ev.run_watch(self.db, watch, "schedule", self.now + timedelta(days=1))
        self.assertEqual((quiet["new"], quiet["digest_id"]), (0, None))
        asked = ev.run_watch(self.db, self.db.watch(self.watch["id"]), "on-demand", self.now + timedelta(days=1))
        self.assertIsNotNone(asked["digest_id"])
        self.assertEqual(json.loads(self.db.query("SELECT payload FROM digests ORDER BY id DESC")[0]["payload"])
                         ["counts"]["new"], 0)

    def test_new_means_first_seen_since_the_last_digest(self):
        ev.run_watch(self.db, self.watch, "schedule", self.now)
        self.pages["https://www.kalkbaytheatre.co.za/"] = tribe_payload(
            ("Jazz at the church", "2026-10-01 17:00:00"), ("Late jazz trio", "2026-10-04 18:00:00"))
        result = ev.run_watch(self.db, self.db.watch(self.watch["id"]), "schedule", self.now + timedelta(days=1))
        self.assertEqual((result["new"], result["matched_new"]), (1, 1))
        payload = json.loads(self.db.query("SELECT payload FROM digests ORDER BY id DESC")[0]["payload"])
        self.assertEqual([e["title"] for e in payload["events"]], ["Late jazz trio"])

    def test_one_source_down_is_a_partial_run_not_a_failed_one(self):
        self.pages["https://www.kalkbaytheatre.co.za/"] = RuntimeError("www.kalkbaytheatre.co.za answered HTTP 503")
        result = ev.run_watch(self.db, self.watch, "schedule", self.now)
        self.assertEqual(result["status"], "partial")
        self.assertIn("kalkbaytheatre", result["errors"][0])
        self.assertEqual(result["fetched"], 2)

    def test_the_same_show_on_two_listings_is_counted_once(self):
        ev.run_watch(self.db, self.watch, "schedule", self.now)
        duplicate = ev.Event("capetownetc", "Tiana Amari Quartet!", ev.parse_when("2026-10-02T20:30:00+02:00")[0],
                             "https://www.capetownetc.com/whats-on-single/tiana/")
        self.db.upsert_events([duplicate], self.now)
        watch = dict(self.db.watch(self.watch["id"]), sources=["almacafe", "kalkbaytheatre", "capetownetc"])
        summary = ev.summarise(self.db, watch, now=self.now, days_ahead=30, only_new=False)
        self.assertEqual([e.title for e in summary.events].count("Tiana Amari Quartet"), 1)
        self.assertEqual(summary.counts.total, 2)

    def test_keywords_match_whole_words(self):
        row = {"title": "Playing with fire", "description": "", "venue": "", "categories": []}
        self.assertEqual(ev.matching_interests(row, [{"name": "theatre", "keywords": ["play"]}]), [])
        row["title"] = "Two plays in one night"
        self.assertEqual(ev.matching_interests(row, [{"name": "theatre", "keywords": ["play"]}]), ["theatre"])


class Courier(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = digests.DigestState(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def log(db_id: str, *ids: int, watch_of=None) -> dict:
        return {"db_id": db_id, "digests": [
            {"id": i, "watch_id": (watch_of or {}).get(i, 1), "trigger": "schedule", "created_at": "",
             "summary": {"cut_at": f"2026-10-0{min(i, 9)}T05:00:00+00:00"}} for i in ids]}

    def test_each_digest_is_delivered_once_across_polls_and_restarts(self):
        posted = []
        courier = digests.Courier(lambda: self.log("a", 1, 2), posted.append, self.state, interval=0)
        self.assertEqual(courier.poll_once(), 2)
        self.assertEqual(courier.poll_once(), 0)
        restarted = digests.Courier(lambda: self.log("a", 1, 2, 3), posted.append,
                                    digests.DigestState(self.tmp.name), interval=0)
        self.assertEqual(restarted.poll_once(), 1)
        self.assertEqual([d["id"] for d in posted], [1, 2, 3])
        self.assertEqual(self.state.load()["unread"], 3)

    def test_coming_back_to_several_digests_is_one_catch_up_per_watch(self):
        posted, asked = [], []

        def catch_up(watch_id, since):
            asked.append((watch_id, since))
            return {"watch_name": "Jazz", "counts": {"new": 5}, "events": []}

        log = self.log("a", 1, 2, 3, 4, watch_of={4: 2})
        digests.Courier(lambda: log, posted.append, self.state, interval=0, catch_up=catch_up).poll_once()
        # Watch 1 had three waiting: one aggregate from the server, since the
        # beginning because nothing was ever delivered. Watch 2 had one: as is.
        self.assertEqual(asked, [(1, digests.EPOCH)])
        self.assertEqual([(d["id"], d["trigger"]) for d in posted], [(3, "reconnect"), (4, "schedule")])
        self.assertEqual(posted[0]["summary"]["covers"], 3)
        state = self.state.load()
        self.assertEqual((state["cursors"], state["combined"], state["unread"]), ({"1": 3, "2": 4}, 3, 2))

    def test_a_catch_up_starts_where_the_last_delivered_digest_ended(self):
        self.state.update(db_id="a", cursors={"1": 4}, cuts={"1": "2026-10-04T05:00:00+00:00"})
        asked = []
        catch_up = lambda watch_id, since: asked.append(since) or {"counts": {"new": 1}, "events": []}  # noqa: E731
        digests.Courier(lambda: self.log("a", 3, 4, 5, 6), lambda d: None, self.state,
                        interval=0, catch_up=catch_up).poll_once()
        self.assertEqual(asked, ["2026-10-04T05:00:00+00:00"])
        self.assertEqual(self.state.load()["cuts"]["1"], "2026-10-06T05:00:00+00:00")

    def test_a_watch_deleted_meanwhile_is_represented_by_its_newest_digest(self):
        posted = []
        digests.Courier(lambda: self.log("a", 1, 2), posted.append, self.state, interval=0,
                        catch_up=lambda watch_id, since: None).poll_once()
        self.assertEqual([(d["id"], d["trigger"]) for d in posted], [(2, "schedule")])

    def test_one_watch_failing_does_not_hold_back_or_repeat_another(self):
        posted = []

        def deliver(digest):
            if digest["watch_id"] == 2:
                raise RuntimeError("model down")
            posted.append(digest["id"])

        courier = digests.Courier(lambda: self.log("a", 1, 2, watch_of={2: 2}), deliver, self.state, interval=0)
        self.assertEqual(courier.poll_once(), 1)
        self.assertEqual(self.state.load()["cursors"], {"1": 1})
        courier.poll_once()
        self.assertEqual(posted, [1])  # watch 1 is not posted twice while watch 2 keeps failing

    def test_a_recreated_database_resets_the_cursors(self):
        self.state.update(db_id="old", cursor=40, cursors={"1": 40})
        posted = []
        digests.Courier(lambda: self.log("new", 1), posted.append, self.state, interval=0).poll_once()
        self.assertEqual([d["id"] for d in posted], [1])
        self.assertEqual((self.state.load()["db_id"], self.state.load()["cursors"]), ("new", {"1": 1}))

    def test_a_failed_delivery_is_retried_on_the_next_tick(self):
        calls = {"n": 0}

        def flaky(digest):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("disk full")

        courier = digests.Courier(lambda: self.log("a", 1), flaky, self.state, interval=0)
        self.assertEqual(courier.poll_once(), 0)
        self.assertIn("disk full", self.state.load()["last_error"])
        self.assertEqual(courier.poll_once(), 1)
        self.assertEqual(self.state.load()["cursor"], 1)

    def test_a_server_that_is_down_is_a_state_not_a_crash(self):
        def down():
            raise RuntimeError("initialize: could not reach the server")
        self.assertEqual(digests.Courier(down, print, self.state, interval=0).poll_once(), 0)
        self.assertIn("could not reach", self.state.load()["last_error"])

    def test_the_template_carries_the_same_numbers_and_links(self):
        text = digests.fallback_text({
            "watch_name": "Jazz", "counts": {"new": 1, "this_weekend": 1, "by_interest": {"jazz": 1}},
            "events": [{"title": "Quartet", "when": "Fri 2 Oct, 18:30", "venue": "Alma",
                        "url": "https://a.test/q", "description": "Jazz."}],
            "omitted": 2, "errors": ["luma: HTTP 503"]}, "schedule", "HTTP 401")
        for part in ("1 new", "jazz: 1", "Quartet - Fri 2 Oct, 18:30, Alma", "https://a.test/q",
                     "2 more", "luma: HTTP 503", "HTTP 401"):
            self.assertIn(part, text)


class Serving(unittest.TestCase):
    """How the server may be exposed - the part that makes it safe to put on a VPS."""

    GOOD = {"EVENTS_HOST": "0.0.0.0", "EVENTS_PUBLIC_HOST": "events.example.com",
            "EVENTS_TOKEN": "x" * 40, "EVENTS_SSL_CERTFILE": "/c.pem", "EVENTS_SSL_KEYFILE": "/k.pem"}

    def test_localhost_needs_nothing(self):
        config = ev.server_config({})
        self.assertEqual((config.local, config.url), (True, "http://127.0.0.1:8788/mcp"))

    def test_a_public_address_needs_a_token_tls_and_a_host_name(self):
        for missing in ("EVENTS_TOKEN", "EVENTS_PUBLIC_HOST", "EVENTS_SSL_CERTFILE", "EVENTS_SSL_KEYFILE"):
            env = {k: v for k, v in self.GOOD.items() if k != missing}
            with self.assertRaises(SystemExit) as refused:
                ev.server_config(env)
            self.assertIn(missing, str(refused.exception))
        with self.assertRaises(SystemExit):
            ev.server_config({**self.GOOD, "EVENTS_TOKEN": "short"})
        self.assertEqual(ev.server_config(self.GOOD).url, "https://events.example.com:8788/mcp")

    def test_the_token_is_checked_on_every_request(self):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.testclient import TestClient

        inner = Starlette(routes=[Route("/mcp", lambda request: PlainTextResponse("ok"), methods=["POST"])])
        client = TestClient(ev.BearerAuth(inner, "s" * 40))
        self.assertEqual(client.post("/mcp").status_code, 401)
        self.assertEqual(client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        ok = client.post("/mcp", headers={"Authorization": "Bearer " + "s" * 40})
        self.assertEqual((ok.status_code, ok.text), (200, "ok"))

    def test_a_public_app_is_wrapped_in_the_token_check(self):
        app = ev.build_app(ev.server_config(self.GOOD))
        self.assertIsInstance(app, ev.BearerAuth)


class StoredDigests(unittest.TestCase):
    def test_a_digest_is_replayed_behind_a_line_that_says_nobody_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            conversations = store_module.ConversationStore(tmp)
            conversations.append_digest("digests", "Two new jazz nights.", digest={"id": 1})
            conversations.append_turn("digests", "tell me more", "Sure.")
            history = conversations.load("digests").history()
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user", "assistant"])
        self.assertEqual(history[0]["content"], store_module.DIGEST_PROMPT_STANDIN)
        self.assertEqual(history[1]["content"], "Two new jazz nights.")


if __name__ == "__main__":
    unittest.main()
