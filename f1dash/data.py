"""Turns OpenF1 responses into the frames the dashboard draws."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .api import client

FIRST_SEASON = 2023  # OpenF1 coverage starts here
SETTLED_AFTER = pd.Timedelta(hours=3)  # session data is treated as final after this
MINI_SECTORS = 24
BEST_LAP_WINDOW = 1.07  # the sporting 107% rule, used to drop in/out and SC laps


def now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def format_lap(seconds: float | None) -> str:
    if seconds is None or pd.isna(seconds):
        return "–"
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f}"


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}"


def _dates(df: pd.DataFrame, *cols: str) -> pd.DataFrame:
    for col in cols:
        if col in df:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
    return df


# --- Calendar ---------------------------------------------------------------


def meetings(year: int) -> pd.DataFrame:
    df = pd.DataFrame(client.get("meetings", ttl=3600, year=year))
    if df.empty:
        return df
    df = _dates(df, "date_start", "date_end")
    held = {s["meeting_key"] for s in client.get("sessions", ttl=3600, year=year)
            if not s.get("is_cancelled")}
    df = df[df["meeting_key"].isin(held)]  # cancelled GPs keep no round number
    df = df[~df["meeting_name"].str.contains("Testing", case=False, na=False)]
    df = df.sort_values("date_start").reset_index(drop=True)
    df["round"] = df.index + 1
    return df


def sessions(year: int) -> pd.DataFrame:
    df = pd.DataFrame(client.get("sessions", ttl=3600, year=year))
    if df.empty:
        return df
    df = _dates(df, "date_start", "date_end")
    if "is_cancelled" in df:
        df = df[~df["is_cancelled"].fillna(False).astype(bool)]
    return df[df["meeting_key"].isin(meetings(year)["meeting_key"])].sort_values("date_start")


def session_info(session_key: int) -> dict:
    rows = client.get("sessions", ttl=3600, session_key=session_key)
    if not rows:
        raise LookupError(f"Unknown session {session_key}")
    return rows[0]


def _ttl(info: dict) -> float | None:
    ended = pd.Timestamp(info["date_end"])
    return None if now() - ended > SETTLED_AFTER else 60


def default_selection(year: int) -> tuple[int | None, int | None]:
    """Most recent finished session, as (meeting_key, session_key)."""
    df = sessions(year)
    if df.empty:
        return None, None
    done = df[df["date_end"] < now()]
    row = done.iloc[-1] if not done.empty else df.iloc[0]
    return int(row["meeting_key"]), int(row["session_key"])


@dataclass
class NextUp:
    live: dict | None
    upcoming: dict | None


def next_up(year: int) -> NextUp:
    df = sessions(year)
    if df.empty:
        return NextUp(None, None)
    names = meetings(year).set_index("meeting_key")["meeting_name"]
    df = df.assign(meeting_name=df["meeting_key"].map(names))
    t = now()
    live = df[(df["date_start"] <= t) & (df["date_end"] >= t)]
    ahead = df[df["date_start"] > t]
    return NextUp(
        live.iloc[0].to_dict() if not live.empty else None,
        ahead.iloc[0].to_dict() if not ahead.empty else None,
    )


# --- Season -----------------------------------------------------------------


@dataclass
class Season:
    drivers: pd.DataFrame = field(default_factory=pd.DataFrame)  # standings
    teams: pd.DataFrame = field(default_factory=pd.DataFrame)
    progress: pd.DataFrame = field(default_factory=pd.DataFrame)  # points after each GP
    calendar: pd.DataFrame = field(default_factory=pd.DataFrame)
    rounds_done: int = 0
    rounds_total: int = 0


def tidy_drivers(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["name_acronym"] = df["name_acronym"].fillna(df["driver_number"].astype(str))
    df["full_name"] = df["full_name"].fillna(df["name_acronym"]).str.title()
    df["team_name"] = df["team_name"].fillna("–")
    df["team_colour"] = "#" + df["team_colour"].fillna("898781").astype(str)
    return df


def _people(span: dict, ttl: float | None) -> pd.DataFrame:
    """Latest known identity per driver number across a season."""
    df = pd.DataFrame(client.get_filtered("drivers", span, ttl))
    if df.empty:
        return df
    df = df.sort_values("session_key").groupby("driver_number").last().reset_index()
    return tidy_drivers(df)


def season(year: int) -> Season:
    meets, sess = meetings(year), sessions(year)
    if meets.empty or sess.empty:
        return Season()
    ttl = None if year < now().year else 600
    races = sess[sess["session_name"] == "Race"]
    finished = races[races["date_end"] < now()]
    span = {"session_key>=": int(sess["session_key"].min()),
            "session_key<=": int(sess["session_key"].max())}

    people = _people(span, ttl)
    champ = pd.DataFrame(client.get_filtered("championship_drivers", span, ttl))
    teams = pd.DataFrame(client.get_filtered("championship_teams", span, ttl))
    podiums = pd.DataFrame(client.get_filtered("session_result", {**span, "position<=": 3}, ttl))
    if not podiums.empty:
        podiums = podiums[podiums["session_key"].isin(races["session_key"])]

    # Session keys aren't strictly per-season, so a key range can catch strays.
    champ = champ[champ["session_key"].isin(sess["session_key"])] if not champ.empty else champ
    teams = teams[teams["session_key"].isin(sess["session_key"])] if not teams.empty else teams

    out = Season(rounds_done=len(finished), rounds_total=len(meets))
    out.calendar = _calendar(meets, podiums, people)
    if champ.empty or finished.empty:
        return out

    order = sess["session_key"].tolist()
    latest = max(champ["session_key"].unique(), key=order.index)
    table = champ[champ["session_key"] == latest].sort_values("position_current")
    table = table.merge(people, on="driver_number", how="left", suffixes=("", "_p"))
    table["wins"] = table["podiums"] = 0
    if not podiums.empty:
        wins = podiums[podiums["position"] == 1].groupby("driver_number").size()
        table["wins"] = table["driver_number"].map(wins).fillna(0).astype(int)
        table["podiums"] = (table["driver_number"]
                            .map(podiums.groupby("driver_number").size())
                            .fillna(0).astype(int))
    out.drivers = table.reset_index(drop=True)

    if not teams.empty:
        out.teams = (teams[teams["session_key"] == latest]
                     .sort_values("position_current").reset_index(drop=True))

    labels = races.merge(meets[["meeting_key", "round"]], on="meeting_key")
    labels = labels.rename(columns={"circuit_short_name": "circuit"})
    prog = champ.merge(labels[["session_key", "round", "circuit"]], on="session_key")
    prog = prog.merge(people[["driver_number", "name_acronym"]], on="driver_number", how="left")
    out.progress = prog.sort_values(["round", "driver_number"]).reset_index(drop=True)
    return out


def _calendar(meets: pd.DataFrame, podiums: pd.DataFrame, people: pd.DataFrame) -> pd.DataFrame:
    cal = meets[["round", "meeting_key", "meeting_name", "location",
                 "date_start", "date_end"]].copy()
    winners = pd.Series(dtype=object)
    if not podiums.empty and not people.empty:
        first = podiums[podiums["position"] == 1]
        first = first.merge(people[["driver_number", "full_name"]], on="driver_number")
        winners = first.set_index("meeting_key")["full_name"]
    cal["winner"] = cal["meeting_key"].map(winners).fillna("–")
    t = now()
    cal["status"] = np.where(cal["date_end"] < t, "Done",
                             np.where(cal["date_start"] <= t, "This weekend", "Upcoming"))
    return cal


# --- Session ----------------------------------------------------------------


@dataclass
class Session:
    info: dict
    drivers: pd.DataFrame
    results: pd.DataFrame
    laps: pd.DataFrame
    stints: pd.DataFrame
    pits: pd.DataFrame
    control: pd.DataFrame
    weather: pd.DataFrame
    positions: pd.DataFrame
    overtakes: pd.DataFrame

    @property
    def is_race(self) -> bool:
        return self.info["session_name"] in ("Race", "Sprint")

    @property
    def is_qualifying(self) -> bool:
        name = self.info["session_name"]
        return "Qualifying" in name or "Shootout" in name


def load_session(session_key: int) -> Session:
    info = session_info(session_key)
    ttl = _ttl(info)

    def fetch(endpoint: str) -> pd.DataFrame:
        return pd.DataFrame(client.get(endpoint, ttl=ttl, session_key=session_key))

    drivers = fetch("drivers")
    drivers = tidy_drivers(drivers) if not drivers.empty else drivers
    is_race = info["session_name"] in ("Race", "Sprint")
    empty = pd.DataFrame()
    positions = _dates(fetch("position"), "date") if is_race else empty
    return Session(
        info=info,
        drivers=drivers,
        results=classify(fetch("session_result"), drivers, positions),
        laps=_dates(fetch("laps"), "date_start"),
        stints=fetch("stints"),
        pits=fetch("pit") if is_race else empty,
        control=_dates(fetch("race_control"), "date"),
        weather=clean_weather(_dates(fetch("weather"), "date")),
        positions=positions,
        overtakes=fetch("overtakes") if is_race else empty,
    )


def clean_weather(weather: pd.DataFrame) -> pd.DataFrame:
    """Drop sensor dropouts, which arrive as a track temperature of exactly 0."""
    if weather.empty:
        return weather
    return weather[weather["track_temperature"] > 0]


def _best_time(value) -> float:
    """Qualifying gives [Q1, Q2, Q3]; the last set time is the one that counts."""
    if isinstance(value, list):
        times = [v for v in value if v is not None]
        return times[-1] if times else np.nan
    return np.nan if value is None else value


def classify(results: pd.DataFrame, drivers: pd.DataFrame,
             positions: pd.DataFrame) -> pd.DataFrame:
    """Classification with grid slot, status and best/total time per driver."""
    if results.empty or drivers.empty:
        return pd.DataFrame()
    df = results.merge(drivers, on="driver_number", how="left", suffixes=("", "_d"))
    df["time"] = df["duration"].map(_best_time).astype(float)
    df["status"] = np.select(
        [df["dsq"].fillna(False).astype(bool), df["dns"].fillna(False).astype(bool),
         df["dnf"].fillna(False).astype(bool)],
        ["DSQ", "DNS", "DNF"], default="Finished")
    if not positions.empty:
        # The first timing-screen order is the true grid, penalties included.
        grid = positions.sort_values("date").groupby("driver_number")["position"].first()
        df["grid"] = df["driver_number"].map(grid)
        df["gained"] = df["grid"] - df["position"]
    df = df.sort_values(["position", "number_of_laps"], ascending=[True, False],
                        na_position="last")
    return df.reset_index(drop=True)


def gap_text(row: pd.Series, leader_time: float) -> str:
    """The Time/Gap column: winner's time, then gaps, then retirement status."""
    if row["status"] != "Finished":
        return row["status"]
    if row["position"] == 1:
        return format_duration(row["time"]) if row["time"] > 300 else format_lap(row["time"])
    gap = row.get("gap_to_leader")
    if isinstance(gap, str):
        return gap
    if isinstance(gap, (int, float)) and not pd.isna(gap):
        return f"+{gap:.3f}"
    return f"+{row['time'] - leader_time:.3f}" if pd.notna(row["time"]) else "–"


def positions_by_lap(laps: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Track position at the end of each lap, with lap 0 as the grid."""
    if laps.empty or positions.empty:
        return pd.DataFrame()
    df = laps[["driver_number", "lap_number", "date_start", "lap_duration"]].copy()
    df = df.sort_values(["driver_number", "lap_number"])
    next_start = df.groupby("driver_number")["date_start"].shift(-1)
    computed = df["date_start"] + pd.to_timedelta(df["lap_duration"], unit="s")
    df["lap_end"] = next_start.fillna(computed)
    df = df.dropna(subset=["lap_end"]).sort_values("lap_end")
    track = positions[["driver_number", "date", "position"]].sort_values("date")
    merged = pd.merge_asof(df, track, left_on="lap_end", right_on="date", by="driver_number")
    merged = merged[["driver_number", "lap_number", "position"]].dropna()
    grid = track.groupby("driver_number")["position"].first().reset_index().assign(lap_number=0)
    both = pd.concat([grid, merged])
    return both.sort_values(["driver_number", "lap_number"]).reset_index(drop=True)


def clean_laps(laps: pd.DataFrame) -> pd.DataFrame:
    """Representative laps: timed, not out-laps, within 107% of the best among them."""
    if laps.empty:
        return laps
    df = laps[laps["lap_duration"].notna() & ~laps["is_pit_out_lap"].fillna(False).astype(bool)]
    if df.empty:
        return df
    return df[df["lap_duration"] <= df["lap_duration"].min() * BEST_LAP_WINDOW]


def neutralisations(control: pd.DataFrame) -> pd.DataFrame:
    """Lap of each Safety Car, VSC and red-flag start, for marking race charts."""
    if control.empty:
        return pd.DataFrame(columns=["lap_number", "label"])
    msg = control["message"].fillna("")
    label = np.select(
        [msg.str.contains("VSC DEPLOYED|VIRTUAL SAFETY CAR DEPLOYED"),
         msg.str.contains("SAFETY CAR DEPLOYED"),
         # flag is often null, and a substring match would hit "CHEQUERED FLAG"
         control["flag"].eq("RED") | msg.str.startswith("RED FLAG")],
        ["VSC", "SC", "Red flag"], default="")
    out = control.assign(label=label)
    return out[(out["label"] != "") & out["lap_number"].notna()][["lap_number", "label"]]


NOTABLE = ("PENALTY", "INVESTIGATION", "NOTED", "DISQUALIFIED", "RETIRED", "STOPPED",
           "RED FLAG", "CHEQUERED", "RAIN")


def notable_messages(control: pd.DataFrame) -> pd.DataFrame:
    """Race control minus the routine traffic (blue flags, track limits, clears)."""
    if control.empty:
        return control
    msg = control["message"].fillna("")
    keep = (control["category"].eq("SafetyCar") | control["flag"].eq("RED")
            | msg.str.contains("|".join(NOTABLE)))
    keep &= ~msg.str.contains("TRACK LIMITS|BLUE FLAG")
    return control[keep].sort_values("date")


# --- Telemetry --------------------------------------------------------------


def best_lap(session: Session, driver: int) -> pd.Series | None:
    if session.laps.empty:
        return None
    mine = clean_laps(session.laps[session.laps["driver_number"] == driver])
    mine = mine.dropna(subset=["date_start"]) if not mine.empty else mine
    return None if mine.empty else mine.loc[mine["lap_duration"].idxmin()]


def _window(lap: pd.Series) -> dict:
    start = lap["date_start"]
    end = start + pd.Timedelta(seconds=float(lap["lap_duration"]))
    fmt = "%Y-%m-%dT%H:%M:%S.%f"
    return {"date>=": start.strftime(fmt), "date<=": end.strftime(fmt)}


def _lap_samples(endpoint: str, session: Session, driver: int, lap: pd.Series) -> pd.DataFrame:
    params = {"session_key": session.info["session_key"], "driver_number": driver,
              **_window(lap)}
    df = _dates(pd.DataFrame(client.get_filtered(endpoint, params, _ttl(session.info))), "date")
    if len(df) < 20:
        return pd.DataFrame()
    df = df.sort_values("date").reset_index(drop=True)
    df["t"] = (df["date"] - lap["date_start"]).dt.total_seconds()
    return df


def lap_trace(session: Session, driver: int, lap: pd.Series) -> pd.DataFrame:
    """Car data for one lap, with time and distance measured from the lap start."""
    df = _lap_samples("car_data", session, driver, lap)
    if not df.empty:
        df["distance"] = (df["speed"] / 3.6 * df["t"].diff().fillna(0)).cumsum()
    return df


@dataclass
class Comparison:
    frame: pd.DataFrame  # common distance grid: speed/throttle/brake per driver + delta
    track: pd.DataFrame  # x, y, segment, faster
    lap_a: pd.Series
    lap_b: pd.Series


def compare(session: Session, a: int, b: int) -> Comparison | None:
    """Align two drivers' best laps. ``delta`` > 0 means B is behind A at that point."""
    lap_a, lap_b = best_lap(session, a), best_lap(session, b)
    if lap_a is None or lap_b is None:
        return None
    trace_a, trace_b = lap_trace(session, a, lap_a), lap_trace(session, b, lap_b)
    if trace_a.empty or trace_b.empty:
        return None

    # Both laps cover the same track, so align on lap fraction rather than raw
    # distance; integrated speed drifts a little at the ~4 Hz sample rate.
    fraction = np.linspace(0, 1, 500)
    frame = pd.DataFrame({"distance": fraction * trace_a["distance"].iloc[-1]})
    clock = {}
    for tag, trace, lap in (("a", trace_a, lap_a), ("b", trace_b, lap_b)):
        progress = trace["distance"] / trace["distance"].iloc[-1]
        for col in ("speed", "throttle", "brake"):
            frame[f"{col}_{tag}"] = np.interp(fraction, progress, trace[col])
        seconds = np.interp(fraction, progress, trace["t"])
        clock[tag] = (seconds - seconds[0]) * lap["lap_duration"] / (seconds[-1] - seconds[0])
    frame["delta"] = clock["b"] - clock["a"]
    track = _track(session, a, lap_a, clock, fraction)
    return Comparison(frame, track, lap_a, lap_b)


def _track(session: Session, driver: int, lap: pd.Series, clock: dict,
           fraction: np.ndarray) -> pd.DataFrame:
    """Track outline split into mini-sectors, each tagged with the faster driver."""
    df = _lap_samples("location", session, driver, lap)
    if df.empty:
        return df
    df["fraction"] = np.interp(df["t"], clock["a"], fraction)
    df["segment"] = np.minimum((df["fraction"] * MINI_SECTORS).astype(int), MINI_SECTORS - 1)
    edges = np.linspace(0, 1, MINI_SECTORS + 1)
    time_a = np.diff(np.interp(edges, fraction, clock["a"]))
    time_b = np.diff(np.interp(edges, fraction, clock["b"]))
    df["faster"] = np.where(time_a[df["segment"]] <= time_b[df["segment"]], "a", "b")
    return df[["x", "y", "segment", "faster"]]
