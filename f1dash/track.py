"""Data for the Track tab: the circuit outline and car positions over time."""

from __future__ import annotations

from functools import lru_cache

import pandas as pd

from . import data
from .api import client

CHUNK_SECONDS = 120  # about 1 MB from OpenF1, and one request per 2 minutes at 1x
LIVE_MARGIN = pd.Timedelta(minutes=30)  # OpenF1 treats this side of a session as live
MAX_GAP_FRAMES = 2  # bridge brief dropouts, but let a stopped feed show as missing


def _identities(drivers: pd.DataFrame) -> dict:
    if drivers.empty:
        return {}
    return {int(r.driver_number): {"code": r.name_acronym, "colour": r.team_colour}
            for r in drivers.itertuples()}


def _seconds(moments: pd.Series, start: pd.Timestamp) -> pd.Series:
    return (moments - start).dt.total_seconds().clip(lower=0).round(2)


def frames_from_rows(rows: list[dict], start: pd.Timestamp) -> dict:
    """Pack location rows into one shared time axis with per-car x/y arrays."""
    if not rows:
        return {"t": [], "cars": {}}
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True, format="ISO8601")
    df = df[(df["x"] != 0) | (df["y"] != 0)]  # (0, 0) means no fix
    if df.empty:
        return {"t": [], "cars": {}}
    # Cars share timestamps to within a sample, so snap to a 0.25 s grid.
    df["slot"] = df["date"].dt.round("250ms")
    x = df.pivot_table(index="slot", columns="driver_number", values="x", aggfunc="last")
    y = df.pivot_table(index="slot", columns="driver_number", values="y", aggfunc="last")
    x = x.ffill(limit=MAX_GAP_FRAMES)
    y = y.ffill(limit=MAX_GAP_FRAMES)

    def values(series: pd.Series) -> list:
        return [None if pd.isna(v) else int(v) for v in series]

    return {
        "t": _seconds(x.index.to_series(), start).tolist(),
        "cars": {int(n): {"x": values(x[n]), "y": values(y[n])} for n in x.columns},
    }


def outline(session: data.Session) -> list[list[int]]:
    """Trace of the fastest usable lap, tried in order in case one lacks location data."""
    laps = data.clean_laps(session.laps)
    if laps.empty:
        return []
    for _, lap in laps.dropna(subset=["date_start"]).nsmallest(3, "lap_duration").iterrows():
        samples = data._lap_samples("location", session, int(lap["driver_number"]), lap)
        if not samples.empty:
            samples = samples[(samples["x"] != 0) | (samples["y"] != 0)]
            return samples[["x", "y"]].astype(int).values.tolist()
    return []


@lru_cache(maxsize=8)
def _bounds(session_key: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    laps = data.load_session(session_key).laps
    if laps.empty:
        raise LookupError("No laps in this session")
    ends = laps["date_start"] + pd.to_timedelta(laps["lap_duration"], unit="s")
    return laps["date_start"].min() - pd.Timedelta(seconds=10), ends.max() + pd.Timedelta(seconds=30)


def replay_meta(session_key: int) -> dict | None:
    """Everything the player needs up front; car positions then stream in chunks."""
    session = data.load_session(session_key)
    if session.laps.empty or session.laps["date_start"].isna().all():
        return None
    start, end = _bounds(session_key)
    shape = outline(session)
    if not shape:
        return None

    order = pd.DataFrame(client.get("position", ttl=data._ttl(session.info),
                                    session_key=session_key))
    events = []
    if not order.empty:
        order["date"] = pd.to_datetime(order["date"], utc=True, format="ISO8601")
        order = order.sort_values("date")
        events = [[t, int(n), int(p)] for t, n, p in zip(
            _seconds(order["date"], start), order["driver_number"], order["position"])]

    notes = data.notable_messages(session.control)
    messages = []
    if not notes.empty:
        messages = [[t, m] for t, m in zip(_seconds(notes["date"], start), notes["message"])]

    laps = []
    if session.is_race:
        firsts = session.laps.dropna(subset=["date_start"]).groupby("lap_number")["date_start"].min()
        laps = [[t, int(n)] for t, n in zip(_seconds(firsts, start), firsts.index)]

    return {
        "title": session.info["session_name"],
        "duration": round((end - start).total_seconds()),
        "chunkSeconds": CHUNK_SECONDS,
        "outline": shape,
        "drivers": _identities(session.drivers),
        "order": events,
        "messages": messages,
        "laps": laps,
    }


@lru_cache(maxsize=24)
def replay_chunk(session_key: int, index: int) -> dict:
    start, _ = _bounds(session_key)
    begin = start + pd.Timedelta(seconds=index * CHUNK_SECONDS)
    stop = begin + pd.Timedelta(seconds=CHUNK_SECONDS)
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    # Not stored on disk: a whole race would come to tens of megabytes.
    rows = client.get_filtered(
        "location",
        {"session_key": session_key, "date>=": begin.strftime(fmt), "date<": stop.strftime(fmt)},
        ttl=None, store=False)
    return frames_from_rows(rows, start)


def live_session(sessions: pd.DataFrame, moment: pd.Timestamp) -> dict | None:
    """The session OpenF1 currently counts as live, if any."""
    if sessions.empty:
        return None
    window = sessions[(sessions["date_start"] - LIVE_MARGIN <= moment)
                      & (sessions["date_end"] + LIVE_MARGIN >= moment)]
    return window.iloc[-1].to_dict() if not window.empty else None


def live_meta() -> dict | None:
    """Outline and names for the live session, borrowed from a free finished session.

    Live-window REST data isn't free, so this uses the circuit's most recent
    session that is already historical, this year or last.
    """
    year = data.now().year
    current = live_session(data.sessions(year), data.now())
    if current is None:
        return None
    for season in (year, year - 1):
        past = data.sessions(season)
        if past.empty:
            continue
        past = past[(past["circuit_key"] == current["circuit_key"])
                    & (past["date_end"] + LIVE_MARGIN < data.now())]
        for key in reversed(past["session_key"].tolist()):
            source = data.load_session(int(key))
            shape = outline(source)
            if shape:
                return {
                    "title": f"{current['session_name']} · {current['location']}",
                    "outline": shape,
                    "drivers": _identities(source.drivers),
                }
    return None
