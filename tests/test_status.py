"""Track status rules, using message shapes from real race control data."""

import pandas as pd

from f1dash import live, status, track


def _flag(flag, scope, sector=None, message=""):
    return {"category": "Flag", "flag": flag, "scope": scope, "sector": sector, "message": message}


def _text(message):
    return {"category": "Other", "flag": None, "scope": None, "sector": None, "message": message}


def _run(*messages):
    state = status.TrackStatus()
    for message in messages:
        state.apply(message)
    return state.snapshot()


def test_sector_yellows_set_and_clear_individually():
    snapshot = _run(_flag("DOUBLE YELLOW", "Sector", 3.0), _flag("YELLOW", "Sector", 2.0),
                    _flag("CLEAR", "Sector", 2.0))
    assert snapshot["sectors"] == {"3": "DOUBLE YELLOW"}


def test_track_clear_removes_all_yellows():
    snapshot = _run(_flag("YELLOW", "Sector", 2.0), _flag("YELLOW", "Track"),
                    _flag("CLEAR", "Track", message="TRACK CLEAR"))
    assert snapshot["sectors"] == {}
    assert snapshot["trackYellow"] is None


def test_safety_car_lasts_until_track_clear_after_in_this_lap():
    deployed = _text("SAFETY CAR DEPLOYED")
    assert _run(deployed)["safetyCar"] == "SC"
    # A TRACK CLEAR while the car is still out must not end it.
    assert _run(deployed, _flag("CLEAR", "Track"))["safetyCar"] == "SC"
    coming_in = _run(deployed, _text("SAFETY CAR IN THIS LAP"))
    assert coming_in["safetyCar"] == "SC" and coming_in["ending"] is True
    assert _run(deployed, _text("SAFETY CAR IN THIS LAP"),
                _flag("CLEAR", "Track"))["safetyCar"] is None


def test_vsc_ending_then_track_clear():
    assert _run(_text("VSC DEPLOYED"))["safetyCar"] == "VSC"
    assert _run(_text("VSC DEPLOYED"), _text("VSC ENDING"))["ending"] is True
    assert _run(_text("VSC DEPLOYED"), _text("VSC ENDING"),
                _flag("CLEAR", "Track"))["safetyCar"] is None


def test_red_flag_replaces_safety_car_and_lifts_on_session_started():
    red = _text("RED FLAG - RACE SUSPENDED")  # real messages leave the flag field null
    stopped = _run(_text("SAFETY CAR DEPLOYED"), red)
    assert stopped["red"] is True and stopped["safetyCar"] is None
    assert _run(red, _flag("CLEAR", "Track"))["red"] is True
    assert _run(red, _text("SESSION STARTED"))["red"] is False


def test_chequered_flag_message_is_not_a_red_flag():
    assert _run({**_flag("CHEQUERED", "Track"), "message": "CHEQUERED FLAG"})["red"] is False


def test_timeline_merges_early_messages_and_skips_no_change_rows():
    start = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")
    control = pd.DataFrame([
        {**_flag("YELLOW", "Sector", 2.0), "date": start - pd.Timedelta(minutes=5)},
        {**_flag("CLEAR", "Sector", 2.0), "date": start - pd.Timedelta(minutes=4)},
        {**_text("NOTHING RELEVANT"), "date": start + pd.Timedelta(seconds=5)},
        {**_text("VSC DEPLOYED"), "date": start + pd.Timedelta(seconds=30)},
    ])
    changes = status.timeline(control, start)
    assert [t for t, _ in changes] == [0, 30.0]
    assert changes[0][1]["sectors"] == {}
    assert changes[1][1]["safetyCar"] == "VSC"


def _session_started(age_seconds, lap=1):
    moment = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=age_seconds)
    return {"session_key": 1, "message": "SESSION STARTED", "lap_number": lap,
            "date": moment.isoformat()}


def test_live_lights_out_needs_a_fresh_lap_one_session_start():
    feed = live.LiveFeed()
    feed.handle("v1/race_control", _session_started(7200))  # stale copy from before the race
    feed.handle("v1/race_control", _session_started(3, lap=46))  # restart after a red flag
    feed.handle("v1/race_control", _session_started(3, lap=None))  # practice or qualifying
    assert feed.snapshot()["lightsOutAgo"] is None
    feed.handle("v1/race_control", _session_started(3))
    assert feed.snapshot()["lightsOutAgo"] is not None


def test_live_feed_tracks_status_and_resets_it_for_a_new_session():
    feed = live.LiveFeed()
    feed.handle("v1/race_control", {"session_key": 1, "date": "2026-11-01T20:00:00+00:00",
                                    "message": "SAFETY CAR DEPLOYED"})
    assert feed.snapshot()["status"]["safetyCar"] == "SC"
    feed.handle("v1/location", {"session_key": 2, "driver_number": 3, "x": 5, "y": 6})
    assert feed.snapshot()["status"]["safetyCar"] is None


def test_sector_paths_cover_the_lap_and_cross_the_seam():
    path = [[i, 0] for i in range(10)]
    sectors = track.sector_paths(path, {1: [8, 0], 2: [2, 0], 3: [5, 0]})
    assert sectors[2] == [[2, 0], [3, 0], [4, 0], [5, 0]]
    assert sectors[3] == [[5, 0], [6, 0], [7, 0], [8, 0]]
    assert sectors[1] == [[8, 0], [9, 0], [0, 0], [1, 0], [2, 0]]
