"""Careers sites built on Phenom (e.g. careers.united.com/us/en/search-results).

Phenom pages load their job lists from a search service on the same site:
    POST https://<site>/widgets   {"ddoKey": "refineSearch", "selected_fields": {"country": ["India"]}, ...}
which returns {"refineSearch": {"totalHits": N, "data": {"jobs": [{jobId, title, location, ...}]}}}.
We ask it for one country's jobs, filter by title, then open only the relevant job pages for the
description. If the search service refuses, we fall back to the site's sitemap of job pages.
"""
import json
import re
from urllib.parse import urlparse

from .generic import job_urls_from_sitemaps, parse_jobposting
from .http import NotFound, assert_public_url
from .models import Found, Job, html_to_text

_LOCALE = re.compile(r"^/([a-z]{2})/([a-z]{2})(/|$)", re.I)
_REFNUM = re.compile(r'"refNum"\s*:\s*"([^"]+)"')
_DDO = re.compile(r"phApp\.ddo\s*=\s*(\{.*?\});\s*phApp\.", re.S)
_JOB_LINK = re.compile(r"/[a-z]{2}/[a-z]{2}/job/([^/?#]+)(?:/([^/?#]+))?/?$", re.I)
MARKERS = ("phenompeople", "phApp.ddo", "phenom")


def base_of(url):
    """'https://careers.united.com/us/en/search-results' -> ('https://careers.united.com', 'us', 'en')"""
    u = urlparse(url if "://" in url else "https://" + url)
    origin = f"{u.scheme or 'https'}://{u.netloc}"
    m = _LOCALE.match(u.path or "")
    country, lang = (m.group(1).lower(), m.group(2).lower()) if m else ("us", "en")
    return origin, country, lang


def _payload(country, lang, ref, keywords, offset, size, country_filter):
    body = {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country, "pageName": "search-results",
            "ddoKey": "refineSearch", "sortBy": "Most recent", "subsearch": "", "from": offset, "jobs": True,
            "counts": True, "all_fields": ["category", "country", "state", "city", "type"], "size": size,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page1",
            "siteType": "external", "keywords": keywords, "global": True,
            "selected_fields": {"country": [country_filter]} if country_filter else {}, "locationData": {}}
    if ref:
        body["refNum"] = ref
    return body


def search(client, site, country_filter="India", keywords="", page_size=100, max_jobs=2000):
    """All postings from the site's search service (one country), as raw dicts. Raises if unavailable."""
    origin, country, lang = base_of(site)
    ref = None
    try:
        page = client.get_text(f"{origin}/{country}/{lang}/search-results", max_bytes=3_000_000, retries=1)
        m = _REFNUM.search(page)
        ref = m.group(1) if m else None
    except Exception:
        pass
    jobs, offset, total = [], 0, None
    while offset < max_jobs:
        data = client.post_json(f"{origin}/widgets", _payload(country, lang, ref, keywords, offset, page_size,
                                                             country_filter), retries=2)
        rs = (data or {}).get("refineSearch") or {}
        got = ((rs.get("data") or {}).get("jobs")) or []
        if total is None:
            total = rs.get("totalHits") or rs.get("hits") or 0
            if not got and "data" not in rs:
                raise NotFound(f"no Phenom search service on {origin}")
        jobs.extend(got)
        offset += len(got)
        if not got or offset >= total:
            break
    return jobs


def _location(j):
    loc = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
    multi = j.get("multi_location") or j.get("multiLocation") or []
    extra = [m if isinstance(m, str) else (m or {}).get("location", "") for m in multi]
    return " / ".join(dict.fromkeys(l for l in [loc] + extra if l))


def job_url(origin, country, lang, j):
    if j.get("jobUrl"):
        return j["jobUrl"]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", j.get("title", "")).strip("-")
    return f"{origin}/{country}/{lang}/job/{j.get('jobId') or j.get('jobSeqNo')}/{slug}"


def page_details(html, url):
    """(title, location, description, posted) from a Phenom job page, or None."""
    parsed = parse_jobposting(html, url)
    if parsed:
        title, loc, desc, posted, _ = parsed
        return title, loc, desc, posted
    m = _DDO.search(html or "")
    if m:
        try:
            jd = (json.loads(m.group(1)).get("jobDetail") or {}).get("data", {}).get("job") or {}
        except ValueError:
            jd = {}
        if jd:
            return (jd.get("title", ""), _location(jd), html_to_text(jd.get("description", "")),
                    str(jd.get("postedDate") or "")[:10])
    return None


def phenom(client, site, keep, country_filter="India", max_pages=150):
    origin, country, lang = base_of(site)
    assert_public_url(origin)
    host = urlparse(origin).netloc.split(".")
    company = host[-2] if len(host) >= 2 else origin
    out = Found()
    try:
        raw = search(client, site, country_filter)
    except Exception:
        raw = None
    if raw is not None:
        out.scanned = len(raw)
        for j in raw:
            title, loc = j.get("title", ""), _location(j)
            if not keep(title, loc or None):
                continue
            url = job_url(origin, country, lang, j)
            desc = html_to_text(j.get("descriptionTeaser") or j.get("description") or "")
            if len(out) < max_pages:
                try:
                    d = page_details(client.get_text(url, max_bytes=2_000_000, retries=2), url)
                    if d and d[2]:
                        desc = d[2]
                except Exception:
                    pass          # keep the teaser text
            out.append(Job(source="phenom", company=company, job_id=str(j.get("jobId") or j.get("jobSeqNo") or url),
                           title=title, location=loc, url=url, description=desc,
                           posted=str(j.get("postedDate") or j.get("dateCreated") or "")[:10]))
        return out
    # fallback: the sitemap's job pages, filtered by the title in the link
    links = []
    for u in job_urls_from_sitemaps(client, origin, limit=20000):
        m = _JOB_LINK.search(urlparse(u).path)
        if m:
            links.append((u, m.group(1), re.sub(r"[-_]+", " ", m.group(2) or "")))
    if not links:
        raise NotFound(f"no Phenom job search or job sitemap on {origin}")
    out.scanned = len(links)
    for u, ident, slug_title in links:
        if slug_title and not keep(slug_title, None):
            continue
        if len(out) >= max_pages:
            break
        try:
            d = page_details(client.get_text(u, max_bytes=2_000_000, retries=2), u)
        except Exception:
            continue
        if not d or not keep(d[0], d[1] or None):
            continue
        out.append(Job(source="phenom", company=company, job_id=ident, title=d[0], location=d[1],
                       url=u, description=d[2], posted=d[3]))
    return out


def probe(client, site, html=""):
    """(site base URL, job count) if this is a Phenom careers site, else None."""
    origin, country, lang = base_of(site)
    if html and not any(m in html for m in MARKERS) and "/search-results" not in site:
        return None
    try:
        n = len(search(client, site, "India", max_jobs=100))
        return f"{origin}/{country}/{lang}", n
    except Exception:
        pass
    links = [u for u in job_urls_from_sitemaps(client, origin, limit=20000) if _JOB_LINK.search(urlparse(u).path)]
    if links and (not html or any(m in html for m in MARKERS)):
        return f"{origin}/{country}/{lang}", len(links)
    return None
