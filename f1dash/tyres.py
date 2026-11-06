"""Which tyre each car is on over time, shared by replay and the live feed.

A stint's ``tyre_age_at_start`` already counts earlier running on that set, and
reused sets come back as new stints, so age is simply that plus laps in the stint.
"""

from __future__ import annotations

import pandas as pd

LETTERS = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}
UNKNOWN = "?"


def letter(compound: str | None) -> str:
    return LETTERS.get((compound or "").upper(), UNKNOWN)


def laps_on_set(start_age: int | None, lap: int, lap_start: int) -> int | None:
    """Laps completed on the set while driving ``lap``, or None if the age is unknown."""
    if start_age is None or pd.isna(start_age):
        return None
    return int(start_age) + max(int(lap) - int(lap_start), 0)


def timeline(stints: pd.DataFrame, laps: pd.DataFrame, start: pd.Timestamp,
             from_grid: bool = False) -> dict[int, list[list]]:
    """{driver: [[seconds, letter, laps on set], ...]}, one entry per lap started.

    ``from_grid`` carries the first tyre back to the start of the replay, because
    lap 1 only begins at lights-out and cars sit on the grid on those tyres first.
    """
    if stints.empty or laps.empty:
        return {}
    timed = laps.dropna(subset=["date_start"])
    seconds = (timed["date_start"] - start).dt.total_seconds().clip(lower=0).round(2)
    timed = timed.assign(seconds=seconds)
    out: dict[int, list[list]] = {}
    for number, group in stints.groupby("driver_number"):
        mine = timed[timed["driver_number"] == number]
        events = []
        for stint in group.sort_values("stint_number").to_dict("records"):
            on_stint = mine[mine["lap_number"].between(stint["lap_start"], stint["lap_end"])]
            for row in on_stint.itertuples():
                events.append([row.seconds, letter(stint["compound"]),
                               laps_on_set(stint["tyre_age_at_start"], row.lap_number,
                                           stint["lap_start"])])
        events.sort(key=lambda event: event[0])
        if events:
            if from_grid:
                events[0][0] = 0
            out[int(number)] = events
    return out
