# F1 Monitor

A locally hosted Formula 1 dashboard built with Python, [Dash](https://dash.plotly.com/) and the [OpenF1](https://openf1.org) API. It covers 2023 onwards and needs no account or API key, except for following a session live.

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
| **Track** | Circuit map with every car's position, the running order, start lights, red and yellow flags with yellow sectors marked on the map, Safety Car and VSC status, and race control messages. Replay any finished session at up to 10× speed, or follow a session live |
| **Telemetry** | Two drivers' best laps compared: speed, throttle, brake, running gap, sector times and a track map of who was faster where |

## Good to know

- **During a live session:** OpenF1 counts data as live from 30 minutes before a session until 30 minutes after it ends. Free data for that session opens once that window closes.
- **Rate limit:** OpenF1 allows 30 requests a minute. Responses are cached in `.cache/`, and finished sessions are kept permanently, so only the first view of a session makes requests. Track replay is the exception: it streams car positions in 2-minute chunks and keeps recent ones in memory only. Delete `.cache/` to reset.
- **Telemetry precision:** car data is sampled at about 4 Hz, so treat small gaps on the trace as approximate. The lap and sector times are exact.
- **Start lights:** only the moment of lights-out is recorded. The lights coming on one by one in replay are reconstructed at the regulation one-second steps, with a fixed hold, and only the first start of a race or sprint is shown, not standing restarts. Live mode can only show lights-out, about 3 seconds late.
- **Flags in live mode:** status is built from messages received while connected, so a flag already out when you connect won't show until it changes.

## Following a session live

Replay is free. Live car positions need an [OpenF1 sponsor account](https://openf1.org) (€9.90 a month at the time of writing) and arrive about 3 seconds behind real time.

Set your account details as environment variables before starting the app. In PowerShell:

```powershell
$env:OPENF1_USERNAME = "you@example.com"
$env:OPENF1_PASSWORD = "your-openf1-password"
.venv\Scripts\python app.py
```

A **Watch live** button then appears on the Track tab from 30 minutes before a session until 30 minutes after it. The credentials stay on the server and are never sent to the browser.

Driver names come from the circuit's most recent finished session, because the live session's own data isn't free yet. New drivers show as their car number until then.

## Implementation notes

- `f1dash/api.py` uses [pyopenf1](https://pypi.org/project/pyopenf1/)'s `BaseHTTPClient` for rate limiting and retries, not its typed endpoint methods. As of 0.1.3 those raise validation errors on real data: null positions for retired drivers, list-valued qualifying times and null lap segments.
- `app.py` calls `truststore.inject_into_ssl()` so TLS is verified against the operating system's certificates. Without it, Python rejects connections on machines where antivirus or a proxy re-signs HTTPS.
- The app is served by Waitress because Flask's development server truncated Dash's JavaScript bundles on Windows.
- The circuit outline and marshal sectors come from the MultiViewer circuit data that OpenF1 links to from each meeting. If that's unavailable, the outline falls back to a fast lap's trace and yellow sectors are named but not drawn.

## Tests

```bash
.venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest
```

## Disclaimer

This is an unofficial project and isn't associated with Formula 1 companies. F1 and related marks are trademarks of Formula One Licensing B.V.
