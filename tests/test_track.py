"""Track tab data: replay frames, the live window and the MQTT message handler."""

import pandas as pd

from f1dash import live, track

START = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")


def _row(seconds, number, x, y):
    moment = START + pd.Timedelta(seconds=seconds)
    return {"date": moment.isoformat(), "driver_number": number, "x": x, "y": y}


def test_frames_share_one_time_axis_and_skip_no_fix_rows():
    rows = [_row(1.0, 1, 100, 200), _row(1.0, 3, 300, 400),
            _row(1.25, 1, 110, 210), _row(1.25, 3, 0, 0)]
    frames = track.frames_from_rows(rows, START)
    assert frames["t"] == [1.0, 1.25]
    assert frames["cars"][1] == {"x": [100, 110], "y": [200, 210]}
    assert frames["cars"][3] == {"x": [300, 300], "y": [400, 400]}  # brief gap bridged


def test_frames_show_a_car_as_missing_once_its_feed_stops():
    rows = [_row(i * 0.25, 1, 100 + i, 200) for i in range(6)] + [_row(0, 3, 300, 400)]
    car = track.frames_from_rows(rows, START)["cars"][3]
    assert car["x"][:3] == [300, 300, 300]
    assert car["x"][3:] == [None, None, None]


def test_frames_handle_empty_responses():
    assert track.frames_from_rows([], START) == {"t": [], "cars": {}}


def test_live_window_extends_30_minutes_either_side():
    sessions = pd.DataFrame({
        "session_name": ["Race"],
        "date_start": [START],
        "date_end": [START + pd.Timedelta(hours=2)],
    })
    minutes = pd.Timedelta(minutes=1)
    assert track.live_session(sessions, START - 29 * minutes) is not None
    assert track.live_session(sessions, START + 149 * minutes) is not None
    assert track.live_session(sessions, START - 31 * minutes) is None
    assert track.live_session(sessions, START + 151 * minutes) is None


def test_feed_keeps_latest_state_and_ignores_no_fix():
    feed = live.LiveFeed()
    feed.handle("v1/location", {"session_key": 1, "driver_number": 44, "x": 10, "y": 20})
    feed.handle("v1/location", {"session_key": 1, "driver_number": 44, "x": 0, "y": 0})
    feed.handle("v1/position", {"session_key": 1, "driver_number": 44, "position": 2})
    feed.handle("v1/race_control", {"session_key": 1, "message": "SAFETY CAR DEPLOYED"})
    snapshot = feed.snapshot()
    assert snapshot["cars"] == {44: [10, 20]}
    assert snapshot["order"] == {44: 2}
    assert snapshot["message"] == "SAFETY CAR DEPLOYED"


def test_feed_clears_state_when_a_new_session_starts():
    feed = live.LiveFeed()
    feed.handle("v1/location", {"session_key": 1, "driver_number": 44, "x": 10, "y": 20})
    feed.handle("v1/location", {"session_key": 2, "driver_number": 3, "x": 5, "y": 6})
    snapshot = feed.snapshot()
    assert snapshot["sessionKey"] == 2
    assert snapshot["cars"] == {3: [5, 6]}


def test_credentials_need_both_values(monkeypatch):
    monkeypatch.setenv("OPENF1_USERNAME", "someone")
    monkeypatch.delenv("OPENF1_PASSWORD", raising=False)
    assert live.credentials() is None
