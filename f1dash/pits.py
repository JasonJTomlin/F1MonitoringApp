"""When each car is in the pits, built from OpenF1's pit rows.

A pit row is written when a car leaves: ``date`` is the pit exit and
``lane_duration`` the time since entry, which agrees with car speed to about a
second. The gaps are filled from laps, so those entry times are estimates:

- rows with no lane time, usually long garage waits
- a car's wait before its first run, which often has no row in the session
- a car's last return to the pits, which has no row because it never leaves

Rows can also belong to other sessions of the weekend, so only rows inside the
replay count.
"""

from __future__ import annotations

import pandas as pd

IN_LAP_ALLOWANCE = 1.25  # untimed in-laps are slow; scale up the car's median lap
SAME_MOMENT = pd.Timedelta(seconds=5)  # an out-lap can start as the pit row is stamped


def _arrival(mine: pd.DataFrame, before: pd.Timestamp | None) -> pd.Timestamp | None:
    """Estimated return to the pits: the end of the last lap started before ``before``."""
    earlier = mine if before is None else mine[mine["date_start"] < before - SAME_MOMENT]
    if earlier.empty:
        return None
    lap = earlier.iloc[-1]
    median = mine["lap_duration"].median()
    typical = median * IN_LAP_ALLOWANCE if pd.notna(median) else 120.0
    seconds = lap["lap_duration"] if pd.notna(lap["lap_duration"]) else typical
    end = lap["date_start"] + pd.Timedelta(seconds=float(seconds))
    return end if before is None else min(end, before)


def _visits(rows: pd.DataFrame, mine: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
            is_race: bool) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    visits = []
    for row in rows.sort_values("date").to_dict("records"):
        leaving = row["date"]
        if not start < leaving < end:
            continue
        if pd.notna(row.get("lane_duration")):
            entering = leaving - pd.Timedelta(seconds=float(row["lane_duration"]))
        else:
            entering = _arrival(mine, leaving) or start
        visits.append((entering, leaving))

    if is_race or mine.empty:
        return visits  # cars start a race on the grid and finish it on track
    first = mine.iloc[0]
    if first["is_pit_out_lap"] and (not visits or visits[0][0] > first["date_start"]):
        visits.insert(0, (start, first["date_start"]))
    if not visits or mine.iloc[-1]["date_start"] >= visits[-1][1] - SAME_MOMENT:
        visits.append((_arrival(mine, None), end))
    return visits


def timeline(pits: pd.DataFrame, laps: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
             is_race: bool, drivers: list[int] | None = None) -> dict[int, list[list[float]]]:
    """{driver: [[seconds in, seconds out], ...]}, sorted, clipped to the replay.

    ``drivers`` lets a car that never left the garage in practice or qualifying
    show as in the pits for the whole session.
    """
    timed = (laps.dropna(subset=["date_start"]).sort_values("date_start")
             if not laps.empty else laps)
    no_rows = pits.iloc[0:0] if not pits.empty else pd.DataFrame(columns=["date"])
    out: dict[int, list[list[float]]] = {}
    numbers = set(drivers or [])  # pit rows alone don't count: they can be from other sessions
    if not timed.empty:
        numbers |= set(timed["driver_number"])
    for number in sorted(numbers):
        rows = pits[pits["driver_number"] == number] if not pits.empty else no_rows
        mine = timed[timed["driver_number"] == number] if not timed.empty else timed
        if mine.empty and not is_race:
            spans = [[0, round((end - start).total_seconds(), 2)]]
        else:
            spans = [[max(round((a - start).total_seconds(), 2), 0),
                      round((b - start).total_seconds(), 2)]
                     for a, b in _visits(rows, mine, start, end, is_race) if a is not None and b > a]
        if spans:
            out[int(number)] = spans
    return out
