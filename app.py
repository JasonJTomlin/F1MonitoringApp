"""F1 Monitor: a local dashboard on the OpenF1 API. Run with ``python app.py``."""

from __future__ import annotations

import logging

import truststore

# Must run before anything opens a connection. Verifies TLS against the OS
# trust store, so machines where antivirus or a corporate proxy re-signs HTTPS
# keep working with verification on.
truststore.inject_into_ssl()

import pandas as pd  # noqa: E402
from dash import Dash, Input, Output, State, dcc, html, no_update  # noqa: E402
from flask import jsonify  # noqa: E402
from pyopenf1.exceptions import PyOpenF1Error, RateLimitError  # noqa: E402
from waitress import serve  # noqa: E402

from f1dash import data, live, track, views  # noqa: E402
from f1dash.api import LiveSessionLocked  # noqa: E402

HOST, PORT = "127.0.0.1", 8050  # loopback only: not reachable from the network
log = logging.getLogger("f1monitor")

app = Dash(__name__, title="F1 Monitor", update_title=None)


def _guarded(build, *args):
    """Turn data failures into a readable panel instead of a dead callback."""
    try:
        return build(*args)
    except LiveSessionLocked:
        return views.message("Session in progress",
                             "OpenF1's free data opens 30 minutes after a session ends, "
                             "so this view will fill in then.")
    except RateLimitError:
        return views.message("Rate limit reached",
                             "OpenF1 allows 30 requests a minute. Wait a minute and "
                             "reselect. Loaded data is cached, so this rarely repeats.")
    except (PyOpenF1Error, OSError, TimeoutError) as exc:
        log.warning("OpenF1 request failed: %s", exc)
        return views.message("Couldn't reach OpenF1",
                             "Check your connection and try again.")
    except Exception:
        log.exception("Failed to build view %s", getattr(build, "__name__", build))
        return views.message("Something went wrong",
                             "This view couldn't be drawn. The server log has the details.")


def _loading(child) -> dcc.Loading:
    # Keep the previous render visible and dimmed, so the page doesn't jump.
    return dcc.Loading(child, delay_show=250, type="dot", color="#3987e5",
                       overlay_style={"visibility": "visible", "opacity": 0.45})


this_year = data.now().year
app.layout = html.Div(className="page", children=[
    html.Header(className="top", children=[
        html.H1("F1 Monitor"),
        html.Div(id="next-up", className="next-up"),
    ]),
    html.Div(className="filters", children=[
        html.Label(["Season", dcc.Dropdown(
            id="season", clearable=False, value=this_year,
            options=[{"label": str(y), "value": y}
                     for y in range(this_year, data.FIRST_SEASON - 1, -1)])]),
        html.Label(["Grand Prix", dcc.Dropdown(id="meeting", clearable=False)], className="grow"),
        html.Label(["Session", dcc.Dropdown(id="session", clearable=False)]),
    ]),
    dcc.Tabs(id="tab", value="season", className="tabs", children=[
        dcc.Tab(label="Championship", value="season", children=_loading(html.Div(id="season-view"))),
        dcc.Tab(label="Session", value="session", children=_loading(html.Div(id="session-view"))),
        dcc.Tab(label="Track", value="track", children=[
            html.Div(id="track-root"),
            html.P(id="track-hint", className="card-note"),
        ]),
        dcc.Tab(label="Telemetry", value="telemetry", children=[
            html.Div(className="filters inner", children=[
                html.Label(["Driver A", dcc.Dropdown(id="driver-a", clearable=False)],
                           className="grow"),
                html.Label(["Driver B", dcc.Dropdown(id="driver-b", clearable=False)],
                           className="grow"),
            ]),
            _loading(html.Div(id="telemetry-view")),
        ]),
    ]),
    html.Footer([
        "Data from ", html.A("OpenF1", href="https://openf1.org", target="_blank"),
        ". Unofficial; not associated with Formula 1.",
    ]),
    dcc.Interval(id="tick", interval=30_000),
    dcc.Store(id="track-sink"),
])

app.clientside_callback(
    """(session, tab) => {
        window.f1track.load(session, tab);
        return window.dash_clientside.no_update;
    }""",
    Output("track-sink", "data"), Input("session", "value"), Input("tab", "value"),
)


def _json_guarded(build, *args):
    """Track API responses: JSON on success, a readable ``error`` otherwise."""
    try:
        return jsonify(build(*args))
    except LiveSessionLocked:
        return jsonify(error="Car positions for this session open 30 minutes after it ends."), 423
    except RateLimitError:
        return jsonify(error="OpenF1 rate limit reached. Retrying shortly."), 429
    except (PyOpenF1Error, OSError, TimeoutError, LookupError) as exc:
        log.warning("Track data failed: %s", exc)
        return jsonify(error="Car positions aren't available for this session."), 502


def _replay_meta(session_key):
    meta = track.replay_meta(session_key)
    if meta is None:
        raise LookupError("No lap or location data")
    return meta


def _live_meta():
    meta = track.live_meta()
    if meta is None:
        raise LookupError("No live session or circuit outline")
    return meta


def _track_status():
    in_window = track.live_session(data.sessions(this_year), data.now()) is not None
    configured = live.credentials() is not None
    hint = "" if configured else (
        "Replay is free for any finished session. Following a session live needs an OpenF1 "
        "sponsor account: see the README.")
    return {"canWatchLive": in_window and configured, "hint": hint}


def _live_snapshot():
    live.feed.start()
    return live.feed.snapshot()


@app.server.get("/api/track/meta/<int:session_key>")
def track_meta(session_key):
    return _json_guarded(_replay_meta, session_key)


@app.server.get("/api/track/chunk/<int:session_key>/<int:index>")
def track_chunk(session_key, index):
    return _json_guarded(track.replay_chunk, session_key, index)


@app.server.get("/api/track/status")
def track_status():
    return _json_guarded(_track_status)


@app.server.get("/api/track/live/meta")
def track_live_meta():
    return _json_guarded(_live_meta)


@app.server.get("/api/track/live")
def track_live():
    return _json_guarded(_live_snapshot)


@app.callback(Output("meeting", "options"), Output("meeting", "value"), Input("season", "value"))
def pick_season(year):
    try:
        meets = data.meetings(year)
        default, _ = data.default_selection(year)
    except Exception:
        log.exception("Could not load the %s calendar", year)
        return [], None
    if meets.empty:
        return [], None
    started = meets["date_start"] <= data.now()
    options = [{"label": f"R{m['round']} · {m['meeting_name']}", "value": int(m["meeting_key"]),
                "disabled": not ok} for (_, m), ok in zip(meets.iterrows(), started)]
    return options, default


@app.callback(Output("session", "options"), Output("session", "value"),
              Input("meeting", "value"), State("season", "value"))
def pick_meeting(meeting, year):
    if meeting is None:
        return [], None
    try:
        sess = data.sessions(year)
    except Exception:
        log.exception("Could not load %s sessions", year)
        return [], None
    sess = sess[sess["meeting_key"] == meeting]
    t = data.now()
    options = [{"label": r["session_name"], "value": int(r["session_key"]),
                "disabled": r["date_start"] > t} for _, r in sess.iterrows()]
    done = sess[sess["date_end"] < t]
    pick = done if not done.empty else sess
    return options, int(pick.iloc[-1 if not done.empty else 0]["session_key"])


@app.callback(Output("driver-a", "options"), Output("driver-b", "options"),
              Output("driver-a", "value"), Output("driver-b", "value"),
              Input("session", "value"), Input("tab", "value"))
def pick_drivers(session_key, tab):
    if session_key is None or tab != "telemetry":
        return no_update, no_update, no_update, no_update
    try:
        options, first, second = views.driver_options(session_key)
    except Exception as exc:
        log.warning("Driver list unavailable: %s", exc)
        return [], [], None, None
    return options, options, first, second


@app.callback(Output("season-view", "children"), Input("season", "value"), Input("tab", "value"))
def show_season(year, tab):
    return _guarded(views.season_view, year) if tab == "season" else no_update


@app.callback(Output("session-view", "children"), Input("session", "value"), Input("tab", "value"))
def show_session(session_key, tab):
    if tab != "session" or session_key is None:
        return no_update
    return _guarded(views.session_view, session_key)


@app.callback(Output("telemetry-view", "children"), Input("driver-a", "value"),
              Input("driver-b", "value"), Input("tab", "value"), State("session", "value"))
def show_telemetry(a, b, tab, session_key):
    if tab != "telemetry" or None in (a, b, session_key):
        return no_update
    if a == b:
        return views.message("Pick two drivers", "Choose a different driver for A and B.")
    return _guarded(views.telemetry_view, session_key, a, b)


def _countdown(start: pd.Timestamp) -> str:
    total = int((start - data.now()).total_seconds() // 60)
    days, rest = divmod(total, 1440)
    hours, minutes = divmod(rest, 60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


@app.callback(Output("next-up", "children"), Input("tick", "n_intervals"))
def show_next_up(_):
    try:
        status = data.next_up(this_year)
    except Exception as exc:
        log.warning("Schedule unavailable: %s", exc)
        return ""
    parts = []
    if status.live:
        parts.append(html.Span(f"● Live now: {status.live['meeting_name']} · "
                               f"{status.live['session_name']}", className="live"))
    if status.upcoming:
        nxt = status.upcoming
        local = nxt["date_start"].to_pydatetime().astimezone()
        parts.append(html.Span([
            "Next: ", html.Strong(f"{nxt['meeting_name']} · {nxt['session_name']}"),
            f" in {_countdown(nxt['date_start'])} ({local:%a %H:%M} your time)",
        ]))
    return parts


if __name__ == "__main__":
    print(f"F1 Monitor running at http://{HOST}:{PORT}")
    # Waitress, not app.run(): Flask's dev server truncated Dash's large JS
    # bundles on Windows, leaving the page stuck on "Loading...".
    serve(app.server, host=HOST, port=PORT, threads=8)
