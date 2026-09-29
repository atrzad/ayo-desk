"""Polite HTTP for online metadata: per-service rate limits, retries, and a 30-day disk cache.

Safe to use from worker threads (one lock per host limiter, one lock for the cache).
"""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__

USER_AGENT = f"AyoMusica/{__version__} (https://github.com/atrzad/ayo-desk)"
CACHE_SECONDS = 30 * 24 * 3600
# Seconds between requests to each host (MusicBrainz asks for at most 1/s; Deezer allows 50 per 5 s).
INTERVALS = {"api.deezer.com": 0.17, "musicbrainz.org": 1.05, "coverartarchive.org": 1.0}
RETRY_STATUS = {429, 500, 502, 503, 504}


class NetError(Exception):
    """A request that failed for a reason worth telling the user (offline, blocked, server error)."""


class Offline(NetError):
    pass


class Limiter:
    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = self.next - now
            self.next = max(now, self.next) + self.interval
        if delay > 0:
            time.sleep(delay)


class Cache:
    def __init__(self, path=None):
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ayo-desk"
        path = Path(path) if path else base / "http-cache.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.execute("CREATE TABLE IF NOT EXISTS responses (url TEXT PRIMARY KEY, fetched REAL NOT NULL, "
                        "body BLOB NOT NULL)")

    def get(self, url, max_age=CACHE_SECONDS):
        with self.lock:
            row = self.db.execute("SELECT fetched, body FROM responses WHERE url=?", (url,)).fetchone()
        if row and time.time() - row[0] < max_age:
            return row[1]
        return None

    def put(self, url, body):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO responses(url, fetched, body) VALUES(?,?,?)",
                            (url, time.time(), body))

    def close(self):
        with self.lock:
            self.db.close()


class Http:
    def __init__(self, cache=None, opener=None, sleep=time.sleep):
        self.cache = cache
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.limiters = {}
        self.lock = threading.Lock()

    def _limiter(self, host):
        with self.lock:
            if host not in self.limiters:
                self.limiters[host] = Limiter(INTERVALS.get(host, 0.25))
            return self.limiters[host]

    def get(self, url, params=None, cache=True, binary=False, timeout=15, attempts=3):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        if cache and self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                return cached if binary else json.loads(cached)
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in range(attempts):
            self._limiter(host).wait()
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with self.opener(request, timeout=timeout) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                if exc.code in RETRY_STATUS and attempt + 1 < attempts:
                    retry = exc.headers.get("Retry-After") if exc.headers else None
                    self.sleep(float(retry) if retry and retry.isdigit() else 2 ** attempt * 1.5)
                    continue
                raise NetError(f"{host} respondeu {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                if attempt + 1 < attempts:
                    self.sleep(2 ** attempt)
                    continue
                raise Offline(f"Sem conexão com {host}") from exc
            if not binary:
                data = json.loads(body)
                # Deezer reports its own quota errors inside a 200 response.
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict) and error.get("code") == 4 and attempt + 1 < attempts:
                    self.sleep(5)
                    continue
                if cache and self.cache is not None and not error:
                    self.cache.put(url, body)
                return data
            if cache and self.cache is not None:
                self.cache.put(url, body)
            return body
        raise NetError(f"{host} não respondeu")
