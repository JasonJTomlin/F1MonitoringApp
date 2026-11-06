"""Real-time car positions over OpenF1's MQTT feed (paid sponsor tier).

Credentials come from OPENF1_USERNAME and OPENF1_PASSWORD and stay on the
server. Access tokens last an hour, so a fresh one is fetched on every reconnect.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time

import httpx
import paho.mqtt.client as mqtt
import pandas as pd

from . import tyres
from .status import TrackStatus

TOKEN_URL = "https://api.openf1.org/token"
BROKER, PORT = "mqtt.openf1.org", 8883
TOPICS = ("v1/location", "v1/position", "v1/race_control", "v1/stints", "v1/laps", "v1/pit")
FRESH_SECONDS = 120  # the feed can replay an old SESSION STARTED stamped lap 1
PIT_FLAG_SECONDS = 6  # keep a completed stop visible briefly in case it's the only message
# The feed has no retirement message, so a car counts as out once it stops
# completing laps while well behind. Backmarkers keep setting laps, so they pass.
RETIRED_AFTER_SECONDS = 180
RETIRED_LAPS_BEHIND = 2

log = logging.getLogger("f1monitor.live")


def credentials() -> tuple[str, str] | None:
    username = os.environ.get("OPENF1_USERNAME")
    password = os.environ.get("OPENF1_PASSWORD")
    return (username, password) if username and password else None


def fetch_token(username: str, password: str) -> str:
    response = httpx.post(TOKEN_URL, data={"username": username, "password": password},
                          timeout=20)
    response.raise_for_status()
    return response.json()["access_token"]


class LiveFeed:
    """Keeps the latest state of the running session, for the browser to poll."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: mqtt.Client | None = None
        self._connected = False
        self._error = ""
        self._reset(None)

    def _reset(self, session_key: int | None) -> None:
        self._session_key = session_key
        self._cars: dict[int, list[int]] = {}
        self._order: dict[int, int] = {}
        self._message = ""
        self._status = TrackStatus()
        self._lights_out_at: float | None = None
        self._stints: dict[int, dict] = {}  # each car's latest stint
        self._laps: dict[int, int] = {}
        self._pit_until: dict[int, float] = {}  # inf until a message says the car left
        self._last_lap_at: dict[int, float] = {}

    def _retired(self) -> list[int]:
        """Cars that look retired. A race-only reading: garage time is normal elsewhere."""
        if self._status.red or not self._laps:
            return []
        leader = max(self._laps.values())
        cutoff = time.monotonic() - RETIRED_AFTER_SECONDS
        return sorted(n for n, lap in self._laps.items()
                      if leader - lap >= RETIRED_LAPS_BEHIND and self._last_lap_at.get(n, 0) < cutoff)

    def _handle_pit(self, number: int, payload: dict) -> None:
        """A row with a lane time means the car has left; one without means it's in."""
        left = payload.get("lane_duration") is not None
        self._pit_until[number] = (time.monotonic() + PIT_FLAG_SECONDS if left
                                   else float("inf"))

    def _in_pit(self) -> list[int]:
        now = time.monotonic()
        return sorted(n for n, until in self._pit_until.items() if until > now)

    def _tyres(self) -> dict[int, list]:
        out = {}
        for number, stint in self._stints.items():
            lap_start = stint.get("lap_start") or 1
            lap = self._laps.get(number, lap_start)
            out[number] = [tyres.letter(stint.get("compound")),
                           tyres.laps_on_set(stint.get("tyre_age_at_start"), lap, lap_start)]
        return out

    def _is_race_start(self, payload: dict) -> bool:
        if payload.get("message") != "SESSION STARTED" or payload.get("lap_number") != 1:
            return False
        age = pd.Timestamp.now(tz="UTC") - pd.Timestamp(payload["date"])
        return age.total_seconds() < FRESH_SECONDS

    def handle(self, topic: str, payload: dict) -> None:
        """Apply one feed message. Messages from a new session clear the old state."""
        with self._lock:
            session_key = payload.get("session_key")
            if session_key != self._session_key:
                self._reset(session_key)
            number = payload.get("driver_number")
            if topic == "v1/location":
                x, y = payload.get("x"), payload.get("y")
                if number is not None and (x or y):  # (0, 0) means no fix
                    self._cars[number] = [x, y]
            elif topic == "v1/position" and number is not None:
                self._order[number] = payload.get("position")
            elif topic == "v1/stints" and number is not None:
                current = self._stints.get(number)
                if current is None or payload.get("stint_number", 0) >= current.get("stint_number", 0):
                    self._stints[number] = payload
            elif topic == "v1/laps" and number is not None:
                lap = payload.get("lap_number") or 0
                if lap > self._laps.get(number, 0):
                    self._last_lap_at[number] = time.monotonic()
                self._laps[number] = max(self._laps.get(number, 0), lap)
                if not payload.get("is_pit_out_lap"):
                    self._pit_until.pop(number, None)  # on a flying lap, whatever came before
            elif topic == "v1/pit" and number is not None:
                self._handle_pit(number, payload)
            elif topic == "v1/race_control":
                self._message = payload.get("message") or self._message
                self._status.apply(payload)
                if self._is_race_start(payload):
                    self._lights_out_at = time.monotonic()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "error": self._error,
                "sessionKey": self._session_key,
                "cars": dict(self._cars),
                "order": dict(self._order),
                "message": self._message,
                "status": self._status.snapshot(),
                "tyres": self._tyres(),
                "pit": self._in_pit(),
                "retired": self._retired(),
                "lightsOutAgo": (None if self._lights_out_at is None
                                 else round(time.monotonic() - self._lights_out_at, 1)),
            }

    def start(self) -> None:
        """Connect once. Later calls do nothing, so polling can call this freely."""
        with self._lock:
            if self._client is not None:
                return
            login = credentials()
            if login is None:
                return
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self._client = client
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.tls_set_context(ssl.create_default_context())
        client.reconnect_delay_set(min_delay=2, max_delay=30)
        try:
            self._refresh_token()
            client.connect_async(BROKER, PORT, keepalive=30)
            client.loop_start()
        except (httpx.HTTPError, OSError, KeyError) as exc:
            self._fail(f"Couldn't sign in to OpenF1: {type(exc).__name__}")
            with self._lock:
                self._client = None  # the next poll retries

    def _refresh_token(self) -> None:
        username, password = credentials()
        self._client.username_pw_set(username, fetch_token(username, password))

    def _fail(self, text: str) -> None:
        log.warning(text)
        with self._lock:
            self._error = text
            self._connected = False

    def _on_connect(self, client, _userdata, _flags, reason, _properties) -> None:
        if reason.is_failure:
            self._fail(f"OpenF1 refused the connection: {reason}")
            return
        with self._lock:
            self._connected, self._error = True, ""
        for topic in TOPICS:
            client.subscribe(topic)

    def _on_disconnect(self, _client, _userdata, _flags, reason, _properties) -> None:
        with self._lock:
            self._connected = False
        try:
            self._refresh_token()  # the old token may be why we were dropped
        except (httpx.HTTPError, OSError, KeyError) as exc:
            self._fail(f"Couldn't renew the OpenF1 token: {type(exc).__name__}")

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            self.handle(message.topic, json.loads(message.payload))
        except ValueError:
            log.warning("Ignored a malformed message on %s", message.topic)


feed = LiveFeed()
