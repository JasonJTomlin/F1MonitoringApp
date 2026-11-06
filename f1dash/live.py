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

from .status import TrackStatus

TOKEN_URL = "https://api.openf1.org/token"
BROKER, PORT = "mqtt.openf1.org", 8883
TOPICS = ("v1/location", "v1/position", "v1/race_control")
FRESH_SECONDS = 120  # the feed can replay an old SESSION STARTED stamped lap 1

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
