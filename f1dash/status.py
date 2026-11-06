"""Track status from race control messages, shared by replay and the live feed.

OpenF1 reports events, not state, so state comes from applying messages in
order. How each state ends was taken from real sessions: a red flag lifts on
SESSION STARTED, and a Safety Car or VSC ends on the TRACK CLEAR that follows
its "ending" message.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

YELLOWS = ("YELLOW", "DOUBLE YELLOW")


def _present(value: Any) -> bool:
    return value is not None and not pd.isna(value)


class TrackStatus:
    def __init__(self) -> None:
        self.red = False
        self.safety_car: str | None = None  # "SC" or "VSC"
        self.ending = False
        self.track_yellow: str | None = None
        self.sectors: dict[int, str] = {}

    def _end_safety_car(self) -> None:
        self.safety_car, self.ending = None, False

    def apply(self, message: dict) -> None:
        text = message.get("message") or ""
        flag = message.get("flag") if _present(message.get("flag")) else None
        scope = message.get("scope") if _present(message.get("scope")) else None
        sector = message.get("sector")

        if text.startswith("RED FLAG") or flag == "RED":
            self.red = True
            self._end_safety_car()
        elif text == "SESSION STARTED":
            self.red = False
        elif text == "SESSION FINISHED":
            self.__init__()
        elif text.startswith("VSC DEPLOYED"):
            self.safety_car, self.ending = "VSC", False
        elif text.startswith("SAFETY CAR DEPLOYED"):
            self.safety_car, self.ending = "SC", False
        elif text.startswith(("VSC ENDING", "SAFETY CAR IN THIS LAP")):
            self.ending = True
        elif flag == "CHEQUERED":
            self._end_safety_car()
        elif flag in YELLOWS:
            if scope == "Sector" and _present(sector):
                self.sectors[int(sector)] = flag
            elif scope == "Track":
                self.track_yellow = flag
        elif flag == "CLEAR":
            if scope == "Sector" and _present(sector):
                self.sectors.pop(int(sector), None)
            elif scope == "Track":
                self.sectors.clear()
                self.track_yellow = None
                if self.ending:
                    self._end_safety_car()

    def snapshot(self) -> dict:
        return {
            "red": self.red,
            "safetyCar": self.safety_car,
            "ending": self.ending,
            "trackYellow": self.track_yellow,
            "sectors": {str(n): self.sectors[n] for n in sorted(self.sectors)},
        }


def timeline(control: pd.DataFrame, start: pd.Timestamp) -> list[list]:
    """[seconds, status] at each change; anything before ``start`` lands on 0."""
    if control.empty:
        return []
    status = TrackStatus()
    changes: list[list] = []
    for record in control.sort_values("date", kind="stable").to_dict("records"):
        status.apply(record)
        current = status.snapshot()
        seconds = max(round((record["date"] - start).total_seconds(), 2), 0)
        if changes and changes[-1][1] == current:
            continue
        if changes and changes[-1][0] == seconds:
            changes[-1][1] = current
        else:
            changes.append([seconds, current])
    return changes
