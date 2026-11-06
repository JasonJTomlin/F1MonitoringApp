"""Tyre timeline and live tyre state, using stint shapes from real sessions."""

import pandas as pd

from f1dash import live, tyres

START = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")


def _stint(number, lap_start, lap_end, compound, age, driver=1):
    return {"driver_number": driver, "stint_number": number, "lap_start": lap_start,
            "lap_end": lap_end, "compound": compound, "tyre_age_at_start": age}


def _laps(count, driver=1):
    return pd.DataFrame([
        {"driver_number": driver, "lap_number": n,
         "date_start": START + pd.Timedelta(seconds=100 * n)} for n in range(1, count + 1)
    ])


def test_age_counts_earlier_running_and_laps_in_the_stint():
    stints = pd.DataFrame([_stint(1, 1, 3, "SOFT", 0), _stint(2, 4, 5, "SOFT", 3),
                           _stint(3, 6, 6, "MEDIUM", 0)])
    events = tyres.timeline(stints, _laps(6), START)[1]
    assert [(e[1], e[2]) for e in events] == [
        ("S", 0), ("S", 1), ("S", 2), ("S", 3), ("S", 4), ("M", 0)]
    assert events[0][0] == 100.0


def test_race_carries_the_first_tyre_back_to_the_grid():
    stints = pd.DataFrame([_stint(1, 1, 2, "MEDIUM", 1)])
    events = tyres.timeline(stints, _laps(2), START, from_grid=True)[1]
    assert events[0] == [0, "M", 1]
    assert events[1][0] == 200.0


def test_unknown_compounds_and_missing_data():
    assert tyres.letter("UNKNOWN") == "?"
    assert tyres.letter("TEST_UNKNOWN") == "?"
    assert tyres.letter(None) == "?"
    assert tyres.letter("INTERMEDIATE") == "I"
    assert tyres.laps_on_set(None, 5, 1) is None
    assert tyres.timeline(pd.DataFrame(), _laps(2), START) == {}


def test_laps_without_a_timestamp_are_skipped():
    laps = _laps(2)
    laps["date_start"] = laps["date_start"].astype(object)
    laps.loc[0, "date_start"] = None
    laps["date_start"] = pd.to_datetime(laps["date_start"], utc=True)
    events = tyres.timeline(pd.DataFrame([_stint(1, 1, 2, "HARD", 0)]), laps, START)[1]
    assert [e[2] for e in events] == [1]


def test_live_feed_ages_the_current_stint_as_laps_arrive():
    feed = live.LiveFeed()
    base = {"session_key": 1, "driver_number": 44}
    feed.handle("v1/stints", {**base, "stint_number": 1, "lap_start": 1, "compound": "SOFT",
                              "tyre_age_at_start": 2})
    assert feed.snapshot()["tyres"] == {44: ["S", 2]}
    feed.handle("v1/laps", {**base, "lap_number": 4})
    assert feed.snapshot()["tyres"] == {44: ["S", 5]}


def test_live_feed_keeps_the_newest_stint_when_an_old_one_is_updated():
    feed = live.LiveFeed()
    base = {"session_key": 1, "driver_number": 44}
    feed.handle("v1/stints", {**base, "stint_number": 2, "lap_start": 5, "compound": "HARD",
                              "tyre_age_at_start": 0})
    feed.handle("v1/stints", {**base, "stint_number": 1, "lap_start": 1, "compound": "SOFT",
                              "tyre_age_at_start": 0})
    assert feed.snapshot()["tyres"][44][0] == "H"
