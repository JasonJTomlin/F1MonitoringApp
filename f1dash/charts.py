"""Plotly figures. Dark only: every colour here is validated for the dark surface.

Colour rules: the top five carry validated categorical hues and everyone else
recedes to gray, because 20+ drivers cannot each have a distinguishable hue.
Tyre colours are F1's own convention; each segment also prints its compound
letter so identity never depends on colour alone.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import data

SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
GRAY = "#55554f"
SURFACE = "#1a1a19"
INK, INK_2, MUTED = "#f0efec", "#c3c2b7", "#898781"
GRID, AXIS = "#2c2c2a", "#383835"
GAIN, LOSS = "#3987e5", "#e66767"
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
COMPOUNDS = {"SOFT": "#e66767", "MEDIUM": "#fab219", "HARD": "#c3c2b7",
             "INTERMEDIATE": "#199e70", "WET": "#3987e5"}


def _style(fig: go.Figure, height: int, **layout) -> go.Figure:
    axis = dict(gridcolor=GRID, linecolor=AXIS, zeroline=False, tickcolor=AXIS,
                tickfont=dict(color=MUTED, size=11), title_font=dict(color=MUTED, size=12))
    fig.update_layout(
        height=height,
        margin=dict(l=52, r=20, t=12, b=40),
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font=dict(family=FONT, color=INK_2, size=12),
        hoverlabel=dict(bgcolor="#262624", bordercolor=AXIS, font=dict(color=INK, family=FONT)),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, font=dict(color=INK_2)),
        barcornerradius=4,
        **layout,
    )
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def colour_map(ranked_numbers: list[int]) -> dict[int, str]:
    """Hue by standing, fixed for the whole view so charts agree on who is who."""
    return dict(zip(ranked_numbers, SERIES))


def _ranked_lines(fig, frame, x, y, id_col, name_col, colours, hover) -> None:
    """Gray field first, then highlighted drivers on top."""
    field = [i for i in frame[id_col].unique() if i not in colours]
    for ident in [*field, *colours]:  # colours is in rank order, so the legend is too
        rows = frame[frame[id_col] == ident]
        if rows.empty:
            continue
        name = rows[name_col].iloc[0]
        highlighted = ident in colours
        fig.add_scatter(
            x=rows[x], y=rows[y], mode="lines", name=name, showlegend=highlighted,
            line=dict(color=colours.get(ident, GRAY), width=2 if highlighted else 1, shape="linear"),
            hovertemplate=f"<b>{name}</b> {hover}<extra></extra>",
        )


# --- Season -----------------------------------------------------------------


def championship_progress(progress: pd.DataFrame, top: list[int]) -> go.Figure:
    colours = colour_map(top)
    fig = go.Figure()
    _ranked_lines(fig, progress, "round", "points_current", "driver_number", "name_acronym",
                  colours, "· Round %{x}: %{y:.0f} pts")
    last_round = progress["round"].max()
    ends = progress[(progress["round"] == last_round) & progress["driver_number"].isin(top)]
    ends = ends.sort_values("points_current", ascending=False)
    placed: list[float] = []
    span = max(progress["points_current"].max(), 1)
    for row in ends.itertuples():
        # Nudge labels apart where two drivers finish on nearly the same points.
        y = row.points_current
        while any(abs(y - other) < span * 0.04 for other in placed):
            y -= span * 0.01
        placed.append(y)
        fig.add_annotation(x=last_round, y=y, text=f"{row.name_acronym} {row.points_current:.0f}",
                           xanchor="left", xshift=6, showarrow=False, font=dict(color=INK, size=11))
    ticks = progress.drop_duplicates("round")
    _style(fig, 380, hovermode="closest")
    fig.update_layout(margin=dict(r=74))
    fig.update_xaxes(tickvals=ticks["round"], ticktext=ticks["circuit"], tickangle=-45,
                     showgrid=False)
    fig.update_yaxes(title="Points", rangemode="tozero")
    return fig


def ranked_bars(labels: list[str], values: list[float], height_per: int = 30,
                value_format: str = "{:.0f}", hover: str = "%{y}: %{x}") -> go.Figure:
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=dict(color=SERIES[0]),
        text=[value_format.format(v) for v in values], textposition="outside",
        textfont=dict(color=INK_2), cliponaxis=False,
        hovertemplate=hover + "<extra></extra>",
    ))
    _style(fig, max(160, height_per * len(labels) + 40), bargap=0.35)
    fig.update_layout(margin=dict(l=110, r=56, t=8, b=8))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=INK_2, size=12))
    fig.update_xaxes(visible=False, rangemode="tozero")
    return fig


# --- Session ----------------------------------------------------------------


def _mark_neutralisations(fig: go.Figure, marks: pd.DataFrame) -> None:
    rows = marks.drop_duplicates(["lap_number", "label"]).sort_values("lap_number")
    laps = rows["lap_number"].tolist()
    for i, row in enumerate(rows.itertuples()):
        # Labels on neighbouring laps would collide, so splay them outwards.
        near_next = i + 1 < len(laps) and laps[i + 1] - row.lap_number < 4
        near_previous = i > 0 and row.lap_number - laps[i - 1] < 4
        anchor = "right" if near_next else "left" if near_previous else "center"
        fig.add_vline(x=row.lap_number, line=dict(color=AXIS, width=1))
        fig.add_annotation(x=row.lap_number, y=1, yref="paper", text=row.label, yanchor="bottom",
                           xanchor=anchor, showarrow=False, font=dict(color=MUTED, size=10))


def race_positions(by_lap: pd.DataFrame, results: pd.DataFrame,
                   marks: pd.DataFrame) -> go.Figure:
    names = results.set_index("driver_number")["name_acronym"]
    frame = by_lap[by_lap["driver_number"].isin(names.index)].copy()
    frame["name"] = frame["driver_number"].map(names)
    top = results["driver_number"].head(5).tolist()
    colours = colour_map(top)
    fig = go.Figure()
    _ranked_lines(fig, frame, "lap_number", "position", "driver_number", "name", colours,
                  "· Lap %{x}: P%{y}")
    ends = frame.sort_values("lap_number").groupby("driver_number").last()
    for number, row in ends.iterrows():
        fig.add_annotation(
            x=row["lap_number"], y=row["position"], text=row["name"], xanchor="left", xshift=5,
            showarrow=False,
            font=dict(color=INK if number in colours else MUTED, size=10))
    _mark_neutralisations(fig, marks)
    count = int(frame["position"].max())
    _style(fig, 520, hovermode="closest")
    fig.update_layout(margin=dict(r=46, t=34), legend=dict(y=1.07))
    fig.update_yaxes(autorange=False, range=[count + 0.5, 0.5], dtick=1, title="Position",
                     showgrid=False)
    fig.update_xaxes(title="Lap", showgrid=False)
    return fig


def tyre_strategy(stints: pd.DataFrame, results: pd.DataFrame) -> go.Figure:
    order = results["name_acronym"].tolist()
    names = results.set_index("driver_number")["name_acronym"]
    df = stints[stints["driver_number"].isin(names.index)].copy()
    df["name"] = df["driver_number"].map(names)
    df["compound"] = df["compound"].fillna("UNKNOWN").str.upper()
    df["laps"] = df["lap_end"] - df["lap_start"] + 1
    fig = go.Figure()
    for compound in [*COMPOUNDS, *sorted(set(df["compound"]) - set(COMPOUNDS))]:
        rows = df[df["compound"] == compound]
        if rows.empty:
            continue
        fig.add_bar(
            y=rows["name"], x=rows["laps"], base=rows["lap_start"] - 1, orientation="h",
            name=compound.title(),
            marker=dict(color=COMPOUNDS.get(compound, GRAY), line=dict(color=SURFACE, width=2)),
            text=[compound[0] if laps >= 4 else "" for laps in rows["laps"]],
            textposition="inside", insidetextanchor="middle",
            textfont=dict(color="#0b0b0b", size=10),
            customdata=rows[["lap_start", "lap_end", "tyre_age_at_start"]],
            hovertemplate=("<b>%{y}</b> " + compound.title()
                           + "<br>Laps %{customdata[0]}–%{customdata[1]}"
                           + "<br>Tyre age at start: %{customdata[2]}<extra></extra>"),
        )
    _style(fig, 26 * len(order) + 90, barmode="overlay", bargap=0.2)
    fig.update_layout(margin=dict(l=52, t=34), barcornerradius=2)
    fig.update_yaxes(categoryorder="array", categoryarray=order, autorange="reversed",
                     showgrid=False, tickfont=dict(color=INK_2, size=11), dtick=1)
    fig.update_xaxes(title="Lap", rangemode="tozero")
    return fig


def lap_spread(laps: pd.DataFrame, results: pd.DataFrame) -> go.Figure:
    """Pace by driver in finishing order; one hue because order already identifies."""
    names = results.set_index("driver_number")["name_acronym"]
    df = data.clean_laps(laps)
    df = df[df["driver_number"].isin(names.index)].assign(
        name=lambda d: d["driver_number"].map(names))
    fig = go.Figure(go.Box(
        x=df["name"], y=df["lap_duration"], boxpoints=False, whiskerwidth=0.4,
        line=dict(color=SERIES[0], width=1.5), fillcolor="rgba(57,135,229,0.25)",
        hoverinfo="y",
    ))
    _style(fig, 340)
    fig.update_xaxes(categoryorder="array", categoryarray=results["name_acronym"].tolist(),
                     showgrid=False, tickfont=dict(color=INK_2, size=11), dtick=1)
    fig.update_yaxes(title="Lap time (s)")
    return fig


def places_gained(results: pd.DataFrame) -> go.Figure:
    df = results.dropna(subset=["gained"]).sort_values("gained", ascending=False)
    fig = go.Figure(go.Bar(
        x=df["name_acronym"], y=df["gained"],
        marker=dict(color=[GAIN if g >= 0 else LOSS for g in df["gained"]]),
        customdata=df[["grid", "position"]],
        hovertemplate=("<b>%{x}</b> %{y:+d}<br>P%{customdata[0]} → P%{customdata[1]}"
                       "<extra></extra>"),
    ))
    _style(fig, 300, bargap=0.35)
    fig.update_xaxes(showgrid=False, tickfont=dict(color=INK_2, size=11), dtick=1)
    fig.update_yaxes(title="Places vs grid", zeroline=True, zerolinecolor=AXIS, dtick=2)
    return fig


def track_conditions(weather: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col, name, colour in (("track_temperature", "Track", SERIES[1]),
                              ("air_temperature", "Air", SERIES[0])):
        fig.add_scatter(x=weather["date"], y=weather[col], mode="lines", name=name,
                        line=dict(color=colour, width=2),
                        hovertemplate=name + " %{y:.1f}°C<extra></extra>")
    _style(fig, 240, hovermode="x")
    fig.update_yaxes(title="°C")
    fig.update_xaxes(showgrid=False, tickformat="%H:%M")
    return fig


# --- Telemetry --------------------------------------------------------------


def telemetry(comparison: data.Comparison, name_a: str, name_b: str) -> go.Figure:
    """Small multiples on one distance axis: separate scales, never a dual axis."""
    df = comparison.frame
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        row_heights=[0.46, 0.2, 0.12, 0.22])
    rows = ((1, "speed", "Speed (km/h)", " km/h"), (2, "throttle", "Throttle %", "%"),
            (3, "brake", "Brake", ""))
    for row, col, title, unit in rows:
        for tag, name, colour in (("a", name_a, SERIES[0]), ("b", name_b, SERIES[1])):
            fig.add_scatter(x=df["distance"], y=df[f"{col}_{tag}"], mode="lines", name=name,
                            legendgroup=name, showlegend=row == 1,
                            line=dict(color=colour, width=2 if row == 1 else 1.5),
                            hovertemplate=f"{name} %{{y:.0f}}{unit}<extra></extra>",
                            row=row, col=1)
        fig.update_yaxes(title=title, row=row, col=1)
    fig.add_scatter(x=df["distance"], y=df["delta"], mode="lines", name="Gap", showlegend=False,
                    line=dict(color=INK_2, width=2),
                    hovertemplate=f"{name_b} vs {name_a} %{{y:+.3f}}s<extra></extra>", row=4, col=1)
    _style(fig, 640, hovermode="x unified", hoversubplots="axis")
    fig.update_layout(margin=dict(t=34))
    fig.update_yaxes(title=f"{name_b} gap (s)", zeroline=True, zerolinecolor=AXIS, row=4, col=1)
    fig.update_yaxes(showticklabels=False, showgrid=False, row=3, col=1)
    fig.update_xaxes(title="Distance (m)", row=4, col=1)
    return fig


def track_map(track: pd.DataFrame, name_a: str, name_b: str) -> go.Figure:
    fig = go.Figure()
    for tag, name, colour in (("a", name_a, SERIES[0]), ("b", name_b, SERIES[1])):
        xs: list[float | None] = []
        ys: list[float | None] = []
        segments = track.groupby("segment")
        for segment, rows in segments:
            if rows["faster"].iloc[0] != tag:
                continue
            # Join to the next sector's first point so the outline has no gaps.
            following = track[track["segment"] == segment + 1].head(1)
            xs += [*rows["x"], *following["x"], None]
            ys += [*rows["y"], *following["y"], None]
        fig.add_scatter(x=xs, y=ys, mode="lines", name=f"{name} faster", hoverinfo="name",
                        line=dict(color=colour, width=5))
    _style(fig, 400)
    fig.update_layout(margin=dict(l=8, r=8, t=30, b=8))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False, scaleanchor="x", scaleratio=1)
    return fig
