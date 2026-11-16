"""Tiny polite HTTP client: retries, backoff, per-host rate limiting. Stdlib only."""
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

USER_AGENT = "Mozilla/5.0 (compatible; java-job-crawler/2.0; personal job search)"


class NotFound(Exception):
    pass


class HttpClient:
    def __init__(self, min_interval=0.5, timeout=20, retries=3):
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = {}
        self._lock = threading.Lock()

    def _wait_turn(self, host):
        with self._lock:
            now = time.monotonic()
            next_ok = self._last.get(host, 0) + self.min_interval
            delay = max(0.0, next_ok - now)
            self._last[host] = now + delay
        if delay:
            time.sleep(delay)

    def _request(self, url, data=None, accept="application/json", max_bytes=None):
        host = urlparse(url).netloc
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        last_err = None
        for attempt in range(self.retries):
            self._wait_turn(host)
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read(max_bytes) if max_bytes else resp.read()
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    raise NotFound(url)
                if e.code in (429, 500, 502, 503, 504):
                    last_err = e
                    ra = e.headers.get("Retry-After") if e.headers else None
                    time.sleep(min(float(ra), 30) if ra and ra.isdigit() else 2 ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(2 ** attempt)
        raise last_err

    def get_json(self, url):
        return json.loads(self._request(url).decode("utf-8"))

    def post_json(self, url, data):
        return json.loads(self._request(url, data=data).decode("utf-8"))

    def get_text(self, url, max_bytes=3_000_000):
        return self._request(url, accept="text/html,*/*", max_bytes=max_bytes).decode("utf-8", "replace")
