"""Retirement timing, the practice clock and live retirement inference."""

import pandas as pd

from f1dash import data, live, track

START = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")


def _session(name, control=None, results=None, laps=None):
    empty = pd.DataFrame()
    return data.Session(
        info={"session_name": name, "session_key": 1,
              "date_end": (START + pd.Timedelta(hours=1)).isoformat()},
        drivers=empty, results=results if results is not None else empty,
        laps=laps if laps is not None else empty, stints=empty, pits=empty,
        control=control if control is not None else empty, weather=empty,
        positions=empty, overtakes=empty)


def _chequered(seconds):
    return pd.DataFrame([{"flag": "CHEQUERED", "date": START + pd.Timedelta(seconds=seconds)}])


def test_last_movement_ignores_time_spent_stationary():
    rows = [{"date": "2026-11-01T20:00:01+00:00", "speed": 120},
            {"date": "2026-11-01T20:00:02+00:00", "speed": 4},
            {"date": "2026-11-01T20:00:03+00:00", "speed": 0}]
    assert track.last_movement(rows) == pd.Timestamp("2026-11-01T20:00:02", tz="UTC")
    assert track.last_movement([{"date": "2026-11-01T20:00:03+00:00", "speed": 0}]) is None


def test_retirement_is_timed_from_when_the_car_stopped(monkeypatch):
    results = pd.DataFrame([{"driver_number": 18, "status": "DNF"},
                            {"driver_number": 3, "status": "Finished"},
                            {"driver_number": 7, "status": "DNS"}])
    laps = pd.DataFrame([{"driver_number": 18, "date_start": START + pd.Timedelta(seconds=100)},
                         {"driver_number": 3, "date_start": START + pd.Timedelta(seconds=100)}])
    rows = [{"date": (START + pd.Timedelta(seconds=148)).isoformat(), "speed": 30}]
    monkeypatch.setattr(track.client, "get_filtered", lambda *args, **kwargs: rows)
    out = track.retirements(_session("Race", results=results, laps=laps), START)
    assert out == {18: 148.0, 7: 0}


def test_practice_retirements_are_not_tracked():
    results = pd.DataFrame([{"driver_number": 18, "status": "DNF"}])
    assert track.retirements(_session("Practice 1", results=results), START) == {}


def test_practice_clock_uses_the_chequered_flag_over_the_schedule():
    extended = _session("Practice 1", control=_chequered(4740))  # Montreal ran 79 minutes
    assert track.practice_end(extended, START) == 4740.0
    assert track.practice_end(_session("Practice 2"), START) == 3600.0  # no flag recorded


def test_only_practice_gets_a_clock():
    assert track.practice_end(_session("Qualifying", control=_chequered(100)), START) is None
    assert track.practice_end(_session("Race", control=_chequered(100)), START) is None


def _feed_with_laps(clock, monkeypatch):
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    feed = live.LiveFeed()
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 18, "lap_number": 3})
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 1, "lap_number": 3})
    clock[0] += 200
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 1, "lap_number": 5})
    return feed


def test_live_car_far_behind_with_no_new_lap_counts_as_retired(monkeypatch):
    clock = [1000.0]
    feed = _feed_with_laps(clock, monkeypatch)
    assert feed.snapshot()["retired"] == [18]


def test_live_retired_car_recovers_if_it_rejoins(monkeypatch):
    clock = [1000.0]
    feed = _feed_with_laps(clock, monkeypatch)
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 18, "lap_number": 4})
    assert feed.snapshot()["retired"] == []


def test_live_slow_lap_alone_is_not_a_retirement(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    feed = live.LiveFeed()
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 18, "lap_number": 3})
    feed.handle("v1/laps", {"session_key": 1, "driver_number": 1, "lap_number": 4})
    clock[0] += 400  # one lap behind only
    assert feed.snapshot()["retired"] == []


def test_live_red_flag_retires_nobody(monkeypatch):
    clock = [1000.0]
    feed = _feed_with_laps(clock, monkeypatch)
    feed.handle("v1/race_control", {"session_key": 1, "date": START.isoformat(),
                                    "message": "RED FLAG - RACE SUSPENDED"})
    assert feed.snapshot()["retired"] == []
