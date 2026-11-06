"""OpenF1 access: pyopenf1 transport plus an on-disk response cache.

pyopenf1's typed endpoint methods fail validation on real data (null
``position`` for retired drivers, list-valued qualifying times, null lap
segments), so this module calls its ``BaseHTTPClient`` directly. That keeps the
package's rate limiting, retries and typed errors while returning raw JSON.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pyopenf1.core.http_client import BaseHTTPClient
from pyopenf1.core.rate_limiter import RateLimiter
from pyopenf1.exceptions import AuthenticationError, NotFoundError

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "openf1"
EMPTY_TTL = 60  # an empty answer may just mean the data has not landed yet

logging.getLogger("pyopenf1").setLevel(logging.ERROR)  # 404 = "no data", not a fault


class LiveSessionLocked(Exception):
    """OpenF1 restricts free access while a session is running."""


class OpenF1:
    """Thread-safe synchronous facade; Dash callbacks run on many threads.

    The async client lives on one background event loop, so its connection
    pool and rate limiter are shared by every callback.
    """

    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        self._http = BaseHTTPClient(
            timeout=60.0,
            max_retries=7,  # backoff totals ~1 min, one full rate-limit window
            rate_limiter=RateLimiter(max_per_second=3, max_per_minute=30),
        )

    def get(self, endpoint: str, ttl: float | None = 60, **params: Any) -> list[dict]:
        """Fetch an endpoint. ``ttl=None`` caches forever (finished sessions)."""
        return self.get_filtered(endpoint, params, ttl)

    def get_filtered(
        self, endpoint: str, params: dict[str, Any], ttl: float | None = 60
    ) -> list[dict]:
        """Like ``get``, for comparison filters such as ``{"date>=": value}``."""
        key = hashlib.sha1(
            json.dumps([endpoint, sorted(params.items())], default=str).encode()
        ).hexdigest()
        path = self._cache_dir / f"{key}.json"
        with self._locks[key]:  # concurrent callbacks share one request
            cached = self._read(path, ttl)
            if cached is not None:
                return cached
            rows = self._fetch(endpoint, params)
            path.write_text(json.dumps(rows), encoding="utf-8")
            return rows

    @staticmethod
    def _read(path: Path, ttl: float | None) -> list[dict] | None:
        if not path.exists():
            return None
        rows = json.loads(path.read_text(encoding="utf-8"))
        limit = ttl if rows else EMPTY_TTL
        if limit is not None and time.time() - path.stat().st_mtime > limit:
            return None
        return rows

    def _fetch(self, endpoint: str, params: dict[str, Any]) -> list[dict]:
        # Built by hand: as encoded query *keys*, filters like "date>=" reach
        # OpenF1 as date%3E%3D and are ignored.
        query = "&".join(
            f"{key}{'' if key[-1] in '<>=' else '='}{quote(str(value), safe=':')}"
            for key, value in params.items()
        )
        call = self._http.request("GET", f"/{endpoint}?{query}", use_cache=False)
        try:
            return asyncio.run_coroutine_threadsafe(call, self._loop).result(timeout=180)
        except NotFoundError:
            return []
        except AuthenticationError as exc:
            raise LiveSessionLocked(str(exc)) from exc


client = OpenF1()
