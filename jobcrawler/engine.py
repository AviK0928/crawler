"""Runs a crawl over a list of source entries and returns scored matches.

A source entry looks like one of:
  {"name": "Stripe",  "ats": "greenhouse",      "id": "stripe"}
  {"name": "Walmart", "ats": "workday",         "id": "https://walmart.wd504.myworkdayjobs.com/WalmartExternal"}
  {"name": "Amazon",  "ats": "amazon",          "id": {"base_query": "java", "country": "IND"}}
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

from . import scoring, sources
from .boards import AGGREGATORS as _BOARD_AGGREGATORS
from .more_sources import AGGREGATORS as _MORE_AGGREGATORS

AGGREGATORS = _MORE_AGGREGATORS | _BOARD_AGGREGATORS
from .http import CrawlTimeout, HttpClient, NotFound, RateLimited


def entry_label(e):
    ident = e["id"] if isinstance(e["id"], str) else (e["id"].get("base_query") or e["id"].get("domain")
                                                      or e["id"].get("query") or e["id"].get("what")
                                                      or e["id"].get("thread") or "")
    return f'{e.get("name") or ident} ({e["ats"]})'


def entry_key(e):
    return e["ats"] + ":" + (e["id"] if isinstance(e["id"], str) else json.dumps(e["id"], sort_keys=True))


def run_crawl(entries, cfg, log=print, client=None):
    """Returns (matches sorted by score, errors [(label, msg)], scanned_count)."""
    client = client or HttpClient(min_interval=cfg.get("min_seconds_between_requests_per_host", 0.5),
                                  host_intervals={"myworkdayjobs.com": cfg.get("workday_seconds_between_requests", 1.5),
                                                  "himalayas.app": cfg.get("himalayas_seconds_between_requests", 3)})

    def keep(title, location):
        return scoring.title_ok(title, cfg) and (location is None or scoring.location_ok(location, cfg))

    def fetch(e):
        fn = sources.FETCHERS[e["ats"]]
        if e["ats"] == "workday":
            return fn(client, e["id"], keep, searches=tuple(cfg.get("workday_searches", ["software engineer"])),
                      country=cfg.get("workday_country", "India"))
        if e["ats"] == "oracle":
            return fn(client, e["id"], keep, searches=tuple(cfg.get("workday_searches", ["software engineer"])))
        if e["ats"] == "eightfold":
            return fn(client, e["id"], keep, location=cfg.get("eightfold_location", "India"),
                      searches=tuple(cfg.get("workday_searches", ["software engineer"])))
        if e["ats"] == "phenom":
            return fn(client, e["id"], keep, country_filter=cfg.get("workday_country", "India"))
        if e["ats"] == "smartrecruiters":
            return fn(client, e["id"], keep, country=cfg.get("smartrecruiters_country", "in"))
        return fn(client, e["id"], keep)

    entries = [e for e in entries if e.get("ats") in sources.FETCHERS and e.get("id")]
    raw, errors = [], []
    budget = cfg.get("max_crawl_seconds", 240)
    client.deadline = time.monotonic() + budget
    log(f"Crawling {len(entries)} sources (time limit {budget}s)…")
    retry_later = []          # Workday sites that hit a rate limit: tried once more at the end

    def record(e, run, retrying=False):
        label = entry_label(e) + (" (retry)" if retrying else "")
        try:
            got = run()
            if e["ats"] not in AGGREGATORS:   # aggregators keep each job's own company
                for j in got:
                    j.company = e.get("name") or j.company
            raw.extend(got)
            scanned = getattr(got, "scanned", None)
            log(f"✓ {label}: " + (f"{scanned} postings scanned, {len(got)} relevant" if scanned is not None
                                  else f"{len(got)} postings"))
        except NotFound:
            errors.append((label, "board not found (wrong slug or company moved job sites)"))
            log(f"✗ {label}: not found")
        except RateLimited as ex:
            if e["ats"] == "workday" and not retrying:
                retry_later.append((e, str(ex)))
                log(f"… {label}: Workday rate limit, will retry at the end")
                return
            msg = str(ex) if "skipped" in str(ex) else "rate-limited (HTTP 429) by the job site; try again later"
            errors.append((label, msg))
            log(f"✗ {label}: {msg}")
        except CrawlTimeout:
            errors.append((label, f"timed out (crawl limit {budget}s)"))
            log(f"✗ {label}: timed out")
        except Exception as ex:  # one broken board shouldn't kill the run
            errors.append((label, f"{type(ex).__name__}: {ex}"[:300]))
            log(f"✗ {label}: {type(ex).__name__}: {ex}"[:300])

    pool = ThreadPoolExecutor(max_workers=cfg.get("workers", 8))
    futs = {pool.submit(fetch, e): e for e in entries}
    try:
        for fut in as_completed(futs, timeout=budget + 15):
            record(futs[fut], fut.result)
    except FuturesTimeout:
        pass
    for fut, e in futs.items():
        if not fut.done():
            errors.append((entry_label(e), f"timed out (crawl limit {budget}s)"))
            log(f"✗ {entry_label(e)}: timed out")
    pool.shutdown(wait=False, cancel_futures=True)

    # Workday's rate limit usually clears within a minute: wait once, then retry those sites one by one
    if retry_later:
        wait = cfg.get("workday_retry_wait_seconds", 45)
        left = client.deadline - time.monotonic()
        if wait and wait > 0 and left > wait + 30:
            log(f"Waiting {wait}s for Workday's rate limit to clear, then retrying {len(retry_later)} site(s)…")
            try:
                client._sleep(wait)
            except CrawlTimeout:
                pass
            client.blocked = {b for b in client.blocked if "myworkdayjobs.com" not in b}
            for e, _ in retry_later:
                record(e, lambda e=e: fetch(e), retrying=True)
        else:
            for e, why in retry_later:
                msg = why if "skipped" in why else "rate-limited (HTTP 429) by Workday; try again later"
                errors.append((entry_label(e), msg))
                log(f"✗ {entry_label(e)}: {msg}")

    seen, matches = set(), []
    for j in raw:
        if j.key in seen:
            continue
        seen.add(j.key)
        if scoring.score(j, cfg):
            matches.append(j)
    matches.sort(key=lambda j: -j.score)
    log(f"Done: scanned {len(seen)} postings → {len(matches)} matches.")
    return matches, errors, len(seen)
