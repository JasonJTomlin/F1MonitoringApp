"""Transform tests, using row shapes copied from real OpenF1 responses."""

import pandas as pd

from f1dash import data


def _drivers():
    return data.tidy_drivers(pd.DataFrame([
        {"driver_number": 3, "name_acronym": "VER", "full_name": "Max VERSTAPPEN",
         "team_name": "Red Bull Racing", "team_colour": "4781D7"},
        {"driver_number": 44, "name_acronym": "HAM", "full_name": "Lewis HAMILTON",
         "team_name": "Ferrari", "team_colour": None},
        {"driver_number": 41, "name_acronym": "LIN", "full_name": "Arvid LINDBLAD",
         "team_name": "Racing Bulls", "team_colour": "6C98FF"},
    ]))


def _result(number, position, duration, gap, dnf=False, laps=71):
    return {"driver_number": number, "position": position, "duration": duration,
            "gap_to_leader": gap, "dnf": dnf, "dns": False, "dsq": False,
            "number_of_laps": laps, "points": 0.0}


def test_format_lap():
    assert data.format_lap(79.268) == "1:19.268"
    assert data.format_lap(59.5) == "59.500"
    assert data.format_lap(None) == "–"


def test_retired_driver_with_null_position_sorts_last():
    results = pd.DataFrame([
        _result(41, None, None, None, dnf=True, laps=58),
        _result(44, 2, 8429.571, 0.799),
        _result(3, 1, 8428.772, 0),
    ])
    positions = pd.DataFrame({
        "driver_number": [44, 3, 41, 3],
        "position": [1, 5, 8, 1],
        "date": pd.to_datetime(["2026-11-01T19:09:00", "2026-11-01T19:09:00",
                                "2026-11-01T19:09:00", "2026-11-01T20:30:00"], utc=True),
    })
    df = data.classify(results, _drivers(), positions)
    assert df["name_acronym"].tolist() == ["VER", "HAM", "LIN"]
    assert df["status"].tolist() == ["Finished", "Finished", "DNF"]
    assert df.loc[0, "grid"] == 5 and df.loc[0, "gained"] == 4  # grid is the earliest reading
    assert pd.isna(df.loc[2, "gained"])
    assert data.gap_text(df.iloc[0], 8428.772) == "2:20:28"
    assert data.gap_text(df.iloc[1], 8428.772) == "+0.799"
    assert data.gap_text(df.iloc[2], 8428.772) == "DNF"


def test_qualifying_uses_last_set_time():
    results = pd.DataFrame([
        _result(44, 1, [77.341, 76.953, 76.514], [0.0, 0.225, 0.0]),
        _result(3, 2, [77.9, None, None], [0.5, None, None]),
    ])
    df = data.classify(results, _drivers(), pd.DataFrame())
    assert df["time"].tolist() == [76.514, 77.9]


def test_positions_by_lap_starts_from_grid_and_uses_position_at_lap_end():
    start = pd.Timestamp("2026-11-01T20:00:00", tz="UTC")
    laps = pd.DataFrame({
        "driver_number": [3, 3], "lap_number": [1, 2],
        "date_start": [start, start + pd.Timedelta(seconds=90)],
        "lap_duration": [90.0, 90.0],
    })
    positions = pd.DataFrame({
        "driver_number": [3, 3, 3], "position": [5, 3, 1],
        "date": [start - pd.Timedelta(minutes=50), start + pd.Timedelta(seconds=40),
                 start + pd.Timedelta(seconds=120)],
    })
    out = data.positions_by_lap(laps, positions)
    assert out["lap_number"].tolist() == [0, 1, 2]
    assert out["position"].tolist() == [5, 3, 1]


def test_clean_laps_drops_out_laps_and_safety_car_pace():
    laps = pd.DataFrame({
        "lap_duration": [80.0, 81.0, 120.0, 79.0, None],
        "is_pit_out_lap": [False, False, False, True, False],
    })
    assert data.clean_laps(laps)["lap_duration"].tolist() == [80.0, 81.0]


def test_red_flag_found_from_message_when_flag_field_is_null():
    control = pd.DataFrame({
        "message": ["VSC DEPLOYED", "SAFETY CAR DEPLOYED", "RED FLAG - RACE SUSPENDED", "CLEAR",
                    "CHEQUERED FLAG"],
        "flag": [None, None, None, "CLEAR", None],
        "lap_number": [7, 44, 45, 50, 71],
    })
    marks = data.neutralisations(control)
    assert marks["label"].tolist() == ["VSC", "SC", "Red flag"]


def test_weather_drops_zero_temperature_dropouts():
    weather = pd.DataFrame({"track_temperature": [44.3, 0.0, 41.0]})
    assert data.clean_weather(weather)["track_temperature"].tolist() == [44.3, 41.0]
