# F1 Monitor

A locally hosted Formula 1 dashboard built with Python, [Dash](https://dash.plotly.com/) and the [OpenF1](https://openf1.org) API. It covers 2023 onwards and needs no account or API key.

## Run it

Needs Python 3.10 or later. On Windows:

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Then open <http://127.0.0.1:8050>. The server listens on this machine only.

## What it shows

Pick a season, Grand Prix and session at the top of the page. The header counts down to the next session.

| Tab | Contents |
| --- | --- |
| **Championship** | Title fight by round, driver standings with wins and podiums, constructors' table, calendar with winners |
| **Session** (race or sprint) | Winner, fastest lap and biggest gain, lap-by-lap positions with Safety Car and red-flag markers, classification, race control, tyre strategy, race pace, places gained, pit stops, track conditions |
| **Session** (practice or qualifying) | Classification with Q1–Q3 times, gap to fastest, lap time spread, tyre usage, track conditions, race control |
| **Telemetry** | Two drivers' best laps compared: speed, throttle, brake, running gap, sector times and a track map of who was faster where |

## Good to know

- **During a live session:** OpenF1's free access pauses while a session is running, so its data appears shortly after the session ends.
- **Rate limit:** OpenF1 allows 30 requests a minute. Responses are cached in `.cache/`, and finished sessions are kept permanently, so only the first view of a session makes requests. Delete `.cache/` to reset.
- **Telemetry precision:** car data is sampled at about 4 Hz, so treat small gaps on the trace as approximate. The lap and sector times are exact.

## Implementation notes

- `f1dash/api.py` uses [pyopenf1](https://pypi.org/project/pyopenf1/)'s `BaseHTTPClient` for rate limiting and retries, not its typed endpoint methods. As of 0.1.3 those raise validation errors on real data: null positions for retired drivers, list-valued qualifying times and null lap segments.
- `app.py` calls `truststore.inject_into_ssl()` so TLS is verified against the operating system's certificates. Without it, Python rejects connections on machines where antivirus or a proxy re-signs HTTPS.
- The app is served by Waitress because Flask's development server truncated Dash's JavaScript bundles on Windows.

## Tests

```bash
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```

## Disclaimer

This is an unofficial project and isn't associated with Formula 1 companies. F1 and related marks are trademarks of Formula One Licensing B.V.
