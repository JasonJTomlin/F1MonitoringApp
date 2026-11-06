"""Pit windows and the live pit flag, using row shapes from real sessions."""

import pandas as pd

from f1dash import live, pits

START = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")
END = START + pd.Timedelta(hours=1)


def _at(seconds):
    return START + pd.Timedelta(seconds=seconds)


def _pit(seconds, lane, lap=5, driver=1):
    return {"driver_number": driver, "date": _at(seconds), "lane_duration": lane, "lap_number": lap}


def _lap(number, seconds, duration, driver=1, out=False):
    return {"driver_number": driver, "lap_number": number, "date_start": _at(seconds),
            "lap_duration": duration, "is_pit_out_lap": out}


LAPS = pd.DataFrame([_lap(1, 100, 90.0), _lap(2, 190, 90.0), _lap(3, 280, 90.0)])


def test_timed_stop_runs_from_entry_to_exit():
    table = pd.DataFrame([_pit(250, 25.0)])
    assert pits.timeline(table, LAPS, START, END, is_race=True) == {1: [[225.0, 250.0]]}


def test_first_exit_of_a_session_counts_from_the_start():
    table = pd.DataFrame([_pit(100, None, lap=1)])
    no_earlier_laps = pd.DataFrame([_lap(1, 100, 90.0)])
    assert pits.timeline(table, no_earlier_laps, START, END, is_race=True)[1] == [[0, 100.0]]


def test_garage_wait_is_kept_when_the_out_lap_starts_with_the_pit_row():
    # Real qualifying shape: untimed in-lap, then a row stamped as the out-lap begins.
    laps = pd.DataFrame([_lap(1, 150, None, out=True), _lap(2, 247, 100.0), _lap(3, 348, None),
                         _lap(4, 948, 700.0, out=True), _lap(5, 1046, 78.0)])
    table = pd.DataFrame([_pit(948.2, None, lap=4)])
    spans = pits.timeline(table, laps, START, END, is_race=False)[1]
    entered, left = spans[1]
    assert 348 < entered < 600 and left == 948.2


def test_wait_before_the_first_run_has_no_row_of_its_own():
    laps = pd.DataFrame([_lap(1, 150, None, out=True), _lap(2, 247, 100.0)])
    other_session = pd.DataFrame([_pit(-11854, None, lap=1)])
    assert pits.timeline(other_session, laps, START, END, is_race=False)[1][0] == [0, 150.0]


def test_rows_from_later_sessions_are_ignored():
    table = pd.DataFrame([_pit(9000, 25.0)])
    assert pits.timeline(table, LAPS, START, END, is_race=True) == {}


def test_pit_rows_for_a_car_not_in_the_session_are_ignored():
    table = pd.DataFrame([_pit(-500, None, driver=236)])
    assert 236 not in pits.timeline(table, LAPS, START, END, False, [1])


def test_car_that_never_runs_stays_in_the_garage_outside_races():
    empty = pd.DataFrame()
    assert pits.timeline(empty, LAPS, START, END, False, [1, 27])[27] == [[0, 3600.0]]
    assert 27 not in pits.timeline(empty, LAPS, START, END, True, [1, 27])


def test_exits_before_the_replay_are_ignored():
    table = pd.DataFrame([_pit(-3000, None, lap=1)])
    assert pits.timeline(table, LAPS, START, END, is_race=True) == {}


def test_practice_cars_end_the_session_in_the_garage():
    table = pd.DataFrame([_pit(100, None, lap=1)])
    spans = pits.timeline(table, LAPS, START, END, is_race=False)[1]
    assert spans[-1] == [370.0, 3600.0]  # lap 3 ends at 370 s


def test_races_do_not_assume_a_final_pit_visit():
    table = pd.DataFrame([_pit(250, 25.0)])
    assert len(pits.timeline(table, LAPS, START, END, is_race=True)[1]) == 1


def test_untimed_in_lap_uses_an_estimate():
    laps = pd.DataFrame([_lap(1, 100, 80.0), _lap(2, 180, 80.0), _lap(3, 260, None)])
    spans = pits.timeline(pd.DataFrame([_pit(100, None, lap=1)]), laps, START, END, is_race=False)[1]
    assert spans[-1] == [360.0, 3600.0]  # 260 s + median lap of 80 s x 1.25


def test_live_flag_stays_up_until_the_car_is_on_a_flying_lap():
    feed = live.LiveFeed()
    base = {"session_key": 1, "driver_number": 44}
    feed.handle("v1/pit", {**base, "lane_duration": None})
    assert feed.snapshot()["pit"] == [44]
    feed.handle("v1/laps", {**base, "lap_number": 6, "is_pit_out_lap": True})
    assert feed.snapshot()["pit"] == [44]
    feed.handle("v1/laps", {**base, "lap_number": 7, "is_pit_out_lap": False})
    assert feed.snapshot()["pit"] == []


def test_live_flag_shows_briefly_when_only_the_exit_is_reported(monkeypatch):
    feed = live.LiveFeed()
    clock = [100.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    feed.handle("v1/pit", {"session_key": 1, "driver_number": 44, "lane_duration": 23.1})
    assert feed.snapshot()["pit"] == [44]
    clock[0] += live.PIT_FLAG_SECONDS + 1
    assert feed.snapshot()["pit"] == []
