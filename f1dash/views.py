"""Builds each tab's content from the data layer."""

from __future__ import annotations

import pandas as pd
from dash import dcc, html

from . import charts, data


def kpi(label: str, value: str, sub: str = "") -> html.Div:
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(value, className="kpi-value"),
        html.Div(sub, className="kpi-sub"),
    ])


def kpi_row(*tiles: html.Div) -> html.Div:
    return html.Div(tiles, className="kpi-row")


def table(frame: pd.DataFrame, numeric: tuple[str, ...] = ()) -> html.Table:
    """Plain table. A ``_swatch`` column renders as a team colour chip beside Team."""
    columns = [c for c in frame.columns if not c.startswith("_")]
    head = html.Thead(html.Tr([html.Th(c, className="num" if c in numeric else None)
                               for c in columns]))
    rows = []
    for record in frame.to_dict("records"):
        cells = []
        for col in columns:
            value = record[col]
            content = "–" if value is None or value != value else value
            if col == "Team" and "_swatch" in record:
                content = [html.Span(className="swatch",
                                     style={"background": record["_swatch"]}), content]
            cells.append(html.Td(content, className="num" if col in numeric else None))
        rows.append(html.Tr(cells))
    return html.Table([head, html.Tbody(rows)], className="data-table")


def card(title: str, *body, note: str = "", wide: bool = False) -> html.Section:
    header = [html.H3(title)]
    if note:
        header.append(html.P(note, className="card-note"))
    return html.Section([*header, *body], className="card wide" if wide else "card")


def chart(title: str, fig, note: str = "", wide: bool = False,
          twin: pd.DataFrame | None = None, numeric: tuple[str, ...] = ()) -> html.Section:
    """A chart card; ``twin`` adds a collapsible table of the same values."""
    # Responsive graphs take their height from the container, so set it there.
    body = [dcc.Graph(figure=fig, style={"height": fig.layout.height},
                      config={"displayModeBar": False, "responsive": True})]
    if twin is not None:
        body.append(html.Details([html.Summary("View as table"),
                                  html.Div(table(twin, numeric), className="table-scroll")]))
    return card(title, *body, note=note, wide=wide)


def message(title: str, text: str) -> html.Div:
    return html.Div([html.H3(title), html.P(text)], className="message")


def grid(*cards) -> html.Div:
    return html.Div([c for c in cards if c is not None], className="grid")


# --- Season -----------------------------------------------------------------


def season_view(year: int) -> html.Div:
    s = data.season(year)
    if s.drivers.empty:
        body = [message(f"No standings for {year}",
                        "OpenF1 has not published championship tables for this season.")]
        if not s.calendar.empty:
            body.append(grid(_calendar_card(s.calendar)))
        return html.Div(body)

    leader, second = s.drivers.iloc[0], s.drivers.iloc[1]
    gap = leader["points_current"] - second["points_current"]
    tiles = [
        kpi("Drivers' leader", leader["full_name"],
            f"{leader['points_current']:.0f} pts · {leader['wins']} wins"),
        kpi("Lead over P2", f"{gap:.0f} pts", f"to {second['full_name']}"),
    ]
    if not s.teams.empty:
        team = s.teams.iloc[0]
        tiles.append(kpi("Constructors' leader", team["team_name"],
                         f"{team['points_current']:.0f} pts"))
    tiles.append(kpi("Season progress", f"{s.rounds_done} / {s.rounds_total}", "Grands Prix run"))

    top = s.drivers["driver_number"].head(5).tolist()
    standings = pd.DataFrame({
        "Pos": s.drivers["position_current"],
        "Driver": s.drivers["full_name"],
        "Team": s.drivers["team_name"],
        "_swatch": s.drivers["team_colour"],
        "Pts": s.drivers["points_current"].map("{:.0f}".format),
        "Wins": s.drivers["wins"],
        "Podiums": s.drivers["podiums"],
    })
    progress = s.progress.pivot_table(index="name_acronym", columns="round",
                                      values="points_current").reindex(
        s.drivers["name_acronym"]).reset_index().rename(columns={"name_acronym": "Driver"})
    progress.columns = [str(c) for c in progress.columns]

    teams_card = None
    if not s.teams.empty:
        teams_card = chart("Constructors' championship", charts.ranked_bars(
            s.teams["team_name"].tolist(), s.teams["points_current"].tolist(),
            hover="%{y}: %{x:.0f} pts"))
    return html.Div([
        kpi_row(*tiles),
        grid(
            chart("Drivers' championship", charts.championship_progress(s.progress, top),
                  note="Points after each Grand Prix. Top five highlighted.", wide=True,
                  twin=progress),
            card("Driver standings", html.Div(table(standings, ("Pos", "Pts", "Wins", "Podiums")),
                                              className="table-scroll tall")),
            teams_card,
            _calendar_card(s.calendar),
        ),
    ])


def _calendar_card(cal: pd.DataFrame) -> html.Section:
    frame = pd.DataFrame({
        "Rd": cal["round"],
        "Grand Prix": cal["meeting_name"],
        "Date": cal["date_end"].dt.strftime("%d %b"),
        "Winner": cal["winner"],
        "Status": cal["status"],
    })
    return card("Calendar", html.Div(table(frame, ("Rd",)), className="table-scroll tall"),
                wide=True)


# --- Session ----------------------------------------------------------------


def session_view(session_key: int) -> html.Div:
    s = data.load_session(session_key)
    if s.results.empty and s.laps.empty:
        return message("No data yet", "OpenF1 has nothing for this session. It may not have "
                                      "run, or results may still be on their way.")
    if s.is_race and not s.results.empty:
        return html.Div([_race_kpis(s), _race_grid(s)])
    return html.Div([_timed_kpis(s), _timed_grid(s)])


def _fastest_lap(s: data.Session) -> tuple[str, str]:
    if s.laps.empty or s.laps["lap_duration"].notna().sum() == 0:
        return "–", ""
    best = s.laps.loc[s.laps["lap_duration"].idxmin()]
    who = s.drivers.set_index("driver_number")["name_acronym"].get(best["driver_number"], "")
    return data.format_lap(best["lap_duration"]), f"{who} · lap {int(best['lap_number'])}"


def _conditions_kpi(s: data.Session) -> html.Div:
    if s.weather.empty:
        return kpi("Conditions", "–")
    track = s.weather["track_temperature"]
    wet = s.weather["rainfall"].fillna(0).gt(0).any()
    return kpi("Track temp", f"{track.min():.0f}–{track.max():.0f}°C",
               "Rain during session" if wet else "Dry")


def _race_kpis(s: data.Session) -> html.Div:
    winner = s.results.iloc[0]
    fastest, fastest_by = _fastest_lap(s)
    stopped = s.results[s.results["status"].isin(["DNF", "DNS", "DSQ"])]
    tiles = [
        kpi("Winner", winner["full_name"], winner["team_name"]),
        kpi("Fastest lap", fastest, fastest_by),
    ]
    if "gained" in s.results and s.results["gained"].notna().any():
        mover = s.results.loc[s.results["gained"].idxmax()]
        tiles.append(kpi("Biggest gain", f"{mover['gained']:+.0f}",
                         f"{mover['name_acronym']} · P{mover['grid']:.0f} to "
                         f"P{mover['position']:.0f}"))
    tiles += [
        kpi("Overtakes", str(len(s.overtakes)), f"{len(stopped)} not classified"),
        _conditions_kpi(s),
    ]
    return kpi_row(*tiles)


def _classification(s: data.Session) -> pd.DataFrame:
    r = s.results
    leader_time = r["time"].iloc[0]
    frame = pd.DataFrame({
        "Pos": r["position"].map(lambda p: "–" if pd.isna(p) else f"{p:.0f}"),
        "Driver": r["full_name"],
        "Team": r["team_name"],
        "_swatch": r["team_colour"],
    })
    if "grid" in r:
        frame["Grid"] = r["grid"].map(lambda g: "–" if pd.isna(g) else f"{g:.0f}")
    frame["Laps"] = r["number_of_laps"]
    frame["Time / gap"] = r.apply(data.gap_text, axis=1, leader_time=leader_time)
    if "points" in r:
        frame["Pts"] = r["points"].fillna(0).map("{:.0f}".format)
    return frame


def _race_grid(s: data.Session) -> html.Div:
    marks = data.neutralisations(s.control)
    by_lap = data.positions_by_lap(s.laps, s.positions)
    classification = card(
        "Classification",
        html.Div(table(_classification(s), ("Pos", "Grid", "Laps", "Time / gap", "Pts")),
                 className="table-scroll tall"))

    cards = [classification, _control_card(s)]
    if not by_lap.empty:
        cards.insert(0, chart("Race positions", charts.race_positions(by_lap, s.results, marks),
                              note="Top five finishers highlighted. "
                                   "Vertical rules mark Safety Car, VSC and red-flag calls.",
                              wide=True))
    if not s.stints.empty:
        cards.append(chart("Tyre strategy", charts.tyre_strategy(s.stints, s.results),
                           note="S soft · M medium · H hard · I intermediate · W wet.",
                           wide=True, twin=_stint_table(s), numeric=("From lap", "To lap")))
    if not s.laps.empty:
        cards.append(chart("Race pace", charts.lap_spread(s.laps, s.results),
                           note="Lap times by driver in finishing order. Excludes out-laps and "
                                "laps outside 107% of the best, such as Safety Car laps.",
                           wide=True))
    if "gained" in s.results and s.results["gained"].notna().any():
        cards.append(chart("Places gained", charts.places_gained(s.results),
                           note="Grid slot minus finishing position. Retirements excluded."))
    cards.append(_pit_card(s))
    cards.append(_weather_card(s, wide=True))
    return grid(*cards)


def _stint_table(s: data.Session) -> pd.DataFrame:
    names = s.results.set_index("driver_number")["name_acronym"]
    df = s.stints[s.stints["driver_number"].isin(names.index)].sort_values(
        ["driver_number", "stint_number"])
    return pd.DataFrame({"Driver": df["driver_number"].map(names),
                         "Compound": df["compound"].fillna("Unknown").str.title(),
                         "From lap": df["lap_start"], "To lap": df["lap_end"]})


def _pit_card(s: data.Session) -> html.Section | None:
    if s.pits.empty or "lane_duration" not in s.pits:
        return None
    stops = s.pits.dropna(subset=["lane_duration"]).nsmallest(10, "lane_duration")
    if stops.empty:
        return None
    names = s.drivers.set_index("driver_number")["name_acronym"]
    labels = [f"{names.get(r.driver_number, r.driver_number)} · L{r.lap_number}"
              for r in stops.itertuples()]
    fig = charts.ranked_bars(labels, stops["lane_duration"].tolist(), height_per=26,
                             value_format="{:.1f}s", hover="%{y}: %{x:.1f}s")
    return chart("Fastest pit stops", fig,
                 note=f"Time in the pit lane. {len(s.pits)} stops in total.")


def _weather_card(s: data.Session, wide: bool = False) -> html.Section | None:
    if s.weather.empty:
        return None
    return chart("Track conditions", charts.track_conditions(s.weather),
                 note="Times in UTC.", wide=wide)


def _control_card(s: data.Session) -> html.Section | None:
    notable = data.notable_messages(s.control)
    if notable.empty:
        return None
    items = []
    for row in notable.itertuples():
        lap = f"L{row.lap_number:.0f}" if pd.notna(row.lap_number) else row.date.strftime("%H:%M")
        items.append(html.Li([html.Span(lap, className="feed-lap"), row.message]))
    return card("Race control", html.Ul(items, className="feed"),
                note="Routine blue-flag and track-limit messages hidden.")


def _timed_kpis(s: data.Session) -> html.Div:
    fastest, fastest_by = _fastest_lap(s)
    tiles = []
    if not s.results.empty:
        first = s.results.iloc[0]
        label = "Pole" if s.is_qualifying else "Fastest"
        tiles.append(kpi(label, first["full_name"], first["team_name"]))
    tiles.append(kpi("Best lap", fastest, fastest_by))
    if not s.laps.empty:
        tiles.append(kpi("Laps set", f"{s.laps['lap_duration'].notna().sum():,}",
                         f"{s.laps['driver_number'].nunique()} drivers"))
    tiles.append(_conditions_kpi(s))
    return kpi_row(*tiles)


def _timed_table(s: data.Session) -> pd.DataFrame:
    r = s.results
    frame = pd.DataFrame({
        "Pos": r["position"].map(lambda p: "–" if pd.isna(p) else f"{p:.0f}"),
        "Driver": r["full_name"],
        "Team": r["team_name"],
        "_swatch": r["team_colour"],
    })
    if r["duration"].map(lambda v: isinstance(v, list)).any():
        for i, part in enumerate(("Q1", "Q2", "Q3")):
            frame[part] = r["duration"].map(
                lambda v, i=i: data.format_lap(v[i]) if isinstance(v, list) and len(v) > i
                else "–")
    else:
        frame["Best"] = r["time"].map(data.format_lap)
        frame["Laps"] = r["number_of_laps"]
    return frame


def _timed_grid(s: data.Session) -> html.Div:
    cards = []
    if not s.results.empty:
        t = _timed_table(s)
        cards.append(card("Classification", html.Div(
            table(t, ("Pos", "Q1", "Q2", "Q3", "Best", "Laps")), className="table-scroll tall")))
        timed = s.results.dropna(subset=["time"])
        if len(timed) > 1:
            gaps = timed["time"] - timed["time"].min()
            note = ("Each driver's final set time against the fastest. Eliminated drivers' "
                    "times come from earlier, usually slower, track conditions."
                    if s.is_qualifying else "Best lap against the session's fastest.")
            cards.append(chart("Gap to fastest", charts.ranked_bars(
                timed["name_acronym"].tolist(), gaps.tolist(), height_per=24,
                value_format="+{:.3f}", hover="%{y}: +%{x:.3f}s"), note=note))
    if not s.laps.empty and not s.results.empty:
        cards.append(chart("Lap time spread", charts.lap_spread(s.laps, s.results),
                           note="Excludes out-laps and laps outside 107% of the best.", wide=True))
    if not s.stints.empty and not s.results.empty:
        cards.append(chart("Tyre usage", charts.tyre_strategy(s.stints, s.results),
                           note="S soft · M medium · H hard · I intermediate · W wet. "
                                "Bars show each driver's lap count on each set.",
                           wide=True))
    cards.append(_weather_card(s))
    cards.append(_control_card(s))
    return grid(*cards)


# --- Telemetry --------------------------------------------------------------


def driver_options(session_key: int) -> tuple[list[dict], int | None, int | None]:
    s = data.load_session(session_key)
    source = s.results if not s.results.empty else s.drivers
    if source.empty:
        return [], None, None
    options = [{"label": f"{r.name_acronym} · {r.full_name}", "value": int(r.driver_number)}
               for r in source.itertuples()]
    second = options[1]["value"] if len(options) > 1 else None
    return options, options[0]["value"], second


def telemetry_view(session_key: int, a: int, b: int) -> html.Div:
    s = data.load_session(session_key)
    names = s.drivers.set_index("driver_number")["name_acronym"]
    name_a, name_b = names.get(a, str(a)), names.get(b, str(b))
    result = data.compare(s, a, b)
    if result is None:
        return message("No comparable laps",
                       f"Telemetry for {name_a} and {name_b} isn't available in this session. "
                       "Each driver needs a clean timed lap.")
    diff = result.lap_b["lap_duration"] - result.lap_a["lap_duration"]
    ahead, behind = (name_a, name_b) if diff >= 0 else (name_b, name_a)
    sectors = pd.DataFrame({
        "Sector": ["S1", "S2", "S3", "Lap"],
        name_a: [*(data.format_lap(result.lap_a[f"duration_sector_{i}"]) for i in (1, 2, 3)),
                 data.format_lap(result.lap_a["lap_duration"])],
        name_b: [*(data.format_lap(result.lap_b[f"duration_sector_{i}"]) for i in (1, 2, 3)),
                 data.format_lap(result.lap_b["lap_duration"])],
    })
    tiles = kpi_row(
        kpi(f"{name_a} best", data.format_lap(result.lap_a["lap_duration"]),
            f"Lap {int(result.lap_a['lap_number'])}"),
        kpi(f"{name_b} best", data.format_lap(result.lap_b["lap_duration"]),
            f"Lap {int(result.lap_b['lap_number'])}"),
        kpi("Gap", f"{abs(diff):.3f}s", f"{ahead} ahead of {behind}"),
    )
    cards = [
        chart("Best-lap telemetry", charts.telemetry(result, name_a, name_b), wide=True,
              note=f"Each driver's best clean lap, aligned by track position. Gap above zero "
                   f"means {name_b} is behind {name_a}. Car data is sampled at about 4 Hz, so "
                   "treat small gaps as approximate."),
    ]
    if not result.track.empty:
        cards.append(chart("Track dominance", charts.track_map(result.track, name_a, name_b),
                           note=f"Faster driver in each of {data.MINI_SECTORS} mini-sectors."))
    cards.append(card("Sector times", table(sectors, (name_a, name_b))))
    return html.Div([tiles, grid(*cards)])
