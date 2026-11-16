"""Tiny polite HTTP client: retries, backoff, per-host rate limiting, cookies. Stdlib only."""
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 java-job-crawler/2.1")


class NotFound(Exception):
    pass


class RateLimited(Exception):
    """Still getting HTTP 429 after all retries."""


class HttpClient:
    def __init__(self, min_interval=0.5, timeout=20, retries=3, host_intervals=None):
        self.min_interval = min_interval
        self.host_intervals = host_intervals or {}   # e.g. {"myworkdayjobs.com": 1.5}
        self.timeout = timeout
        self.retries = retries
        self._last = {}
        self._lock = threading.Lock()
        # cookies: some boards (Workday) set a session cookie on the first page view
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _interval(self, host):
        for suffix, iv in self.host_intervals.items():
            if host.endswith(suffix):
                return iv
        return self.min_interval

    def _wait_turn(self, host):
        with self._lock:
            now = time.monotonic()
            next_ok = self._last.get(host, 0) + self._interval(host)
            delay = max(0.0, next_ok - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)

    def _request(self, url, data=None, accept="application/json", max_bytes=None, headers=None, retries=None):
        host = urlparse(url).netloc
        hdrs = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"}
        hdrs.update(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
        retries = self.retries if retries is None else retries
        last_err = None
        for attempt in range(retries):
            self._wait_turn(host)
            req = urllib.request.Request(url, data=body, headers=hdrs)
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    return resp.read(max_bytes) if max_bytes else resp.read()
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    raise NotFound(url)
                if e.code == 429:
                    last_err = RateLimited(f"HTTP 429 Too Many Requests from {host}")
                    ra = e.headers.get("Retry-After") if e.headers else None
                    if attempt < retries - 1:
                        time.sleep(min(float(ra), 60) if ra and ra.isdigit() else min(60, 5 * 2 ** attempt))
                    continue
                if e.code in (500, 502, 503, 504):
                    last_err = e
                    if attempt < retries - 1:
                        time.sleep(2 ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        raise last_err

    def get_json(self, url, headers=None, retries=None):
        return json.loads(self._request(url, headers=headers, retries=retries).decode("utf-8"))

    def post_json(self, url, data, headers=None, retries=None):
        return json.loads(self._request(url, data=data, headers=headers, retries=retries).decode("utf-8"))

    def get_text(self, url, max_bytes=3_000_000, headers=None, retries=None):
        return self._request(url, accept="text/html,*/*", max_bytes=max_bytes,
                             headers=headers, retries=retries).decode("utf-8", "replace")
