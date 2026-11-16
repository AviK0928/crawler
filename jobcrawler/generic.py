"""Fallbacks for careers sites that aren't on Greenhouse/Lever/Ashby/SmartRecruiters/Workday.

1. jibe:    sites built on Google's Jibe careers front-end (often backed by iCIMS) expose
            a public JSON feed at  https://<site>/api/jobs
2. sitemap: works on almost any careers site. Sites publish a sitemap of their job pages and
            embed schema.org "JobPosting" data in each page so Google for Jobs can list them.
            We read the sitemap, keep URLs whose slug looks like a relevant title, then read the
            JobPosting data from those pages.
"""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin, urlparse

from .http import NotFound, assert_public_url
from .models import Found, Job, html_to_text

# ---------------------------------------------------------------- Jibe


def origin_of(url):
    u = urlparse(url if "://" in url else "https://" + url)
    return f"{u.scheme or 'https'}://{u.netloc}"


def jibe(client, site, keep, location="India", max_pages=30):
    origin = origin_of(site)
    assert_public_url(origin)
    out, page, total = [], 1, None
    while page <= max_pages:
        data = client.get_json(f"{origin}/api/jobs?page={page}&limit=100&location={quote(location)}"
                               f"&sortBy=posted_date&descending=true")
        items = data.get("jobs") or []
        if total is None:
            total = data.get("totalCount") or data.get("count") or 0
        out.extend(parse_jibe(items, origin))
        if not items or len(out) >= total:
            break
        page += 1
    return out


def parse_jibe(items, origin):
    out = []
    host = urlparse(origin).netloc.split(".")
    company = host[-2] if len(host) >= 2 else origin
    for it in items:
        d = it.get("data", it)
        loc = d.get("location_name") or d.get("full_location") or ", ".join(
            x for x in (d.get("city"), d.get("state"), d.get("country")) if x)
        slug = d.get("slug") or d.get("req_id") or ""
        out.append(Job(
            source="jibe", company=company, job_id=str(d.get("req_id") or slug),
            title=d.get("title", ""), location=loc,
            url=d.get("canonical_url") or (f"{origin}/jobs/{slug}" if slug else d.get("apply_url", "")),
            description=html_to_text(" ".join(str(d.get(k) or "") for k in
                                              ("description", "qualifications", "responsibilities"))),
            posted=str(d.get("posted_date") or "")[:10],
        ))
    return out


def looks_like_jibe(data):
    return isinstance(data, dict) and isinstance(data.get("jobs"), list) and ("totalCount" in data or "count" in data)


# ---------------------------------------------------------------- sitemap + JobPosting

JOB_PATH = re.compile(r"/(job|jobs|position|positions|opening|openings|vacancy|vacancies|"
                      r"career|careers|requisition|requisitions|job-detail|jobdetail)s?/[^?#]*[a-z0-9]", re.I)
NOT_JOB = re.compile(r"/(search|category|categories|location|locations|team|teams|department|"
                     r"benefits|faq|blog|events?)(/|$)", re.I)
_LD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)


def _fetch_xml(client, url):
    if url.lower().endswith(".gz"):
        return None
    try:
        return ET.fromstring(client.get_text(url, max_bytes=8_000_000, retries=2).strip().encode())
    except Exception:
        return None


def _locs(root):
    return [e.text.strip() for e in root.iter() if e.tag.split("}")[-1] == "loc" and e.text]


def _same_site(url, origin):
    a, b = urlparse(url).netloc.lower(), urlparse(origin).netloc.lower()
    base = lambda h: ".".join(h.split(".")[-2:])
    return a == b or base(a) == base(b)


def job_urls_from_sitemaps(client, origin, limit=5000):
    """Job-page URLs listed in the site's sitemap(s)."""
    sitemaps = []
    try:
        robots = client.get_text(origin + "/robots.txt", max_bytes=200_000, retries=1)
        sitemaps = [l.split(":", 1)[1].strip() for l in robots.splitlines() if l.lower().startswith("sitemap:")]
    except Exception:
        pass
    sitemaps += [origin + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/jobs/sitemap.xml", "/sitemap-jobs.xml")]
    seen_maps, pages, queue = set(), [], list(dict.fromkeys(sitemaps))
    while queue and len(seen_maps) < 25 and len(pages) < limit:
        sm = queue.pop(0)
        if sm in seen_maps or not _same_site(sm, origin):
            continue
        seen_maps.add(sm)
        root = _fetch_xml(client, sm)
        if root is None:
            continue
        locs = _locs(root)
        if root.tag.split("}")[-1] == "sitemapindex":
            # look at job-ish child sitemaps first
            locs.sort(key=lambda u: 0 if re.search(r"job|career|position|posting", u, re.I) else 1)
            queue = locs[:20] + queue
        else:
            pages.extend(u for u in locs if _same_site(u, origin) and JOB_PATH.search(urlparse(u).path)
                         and not NOT_JOB.search(urlparse(u).path))
    return list(dict.fromkeys(pages))[:limit]


def slug_text(url):
    """'.../job/Bengaluru-Software-Engineer-I-Java/12345/' -> 'Bengaluru Software Engineer I Java 12345'"""
    path = urlparse(url).path
    return re.sub(r"[-_/+.%]+|\b20\d\d\b", " ", re.sub(r"%20", " ", path)).strip()


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_jobposting(html, url):
    """Returns (title, location, description, posted, id) from schema.org JobPosting JSON-LD, or None."""
    for block in _LD.findall(html or ""):
        try:
            data = json.loads(block.strip(), strict=False)
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                locs = []
                jl = node.get("jobLocation") or []
                for place in (jl if isinstance(jl, list) else [jl]):
                    addr = (place or {}).get("address") or {}
                    if isinstance(addr, str):
                        locs.append(addr); continue
                    country = addr.get("addressCountry")
                    if isinstance(country, dict):
                        country = country.get("name")
                    locs.append(", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x))
                if node.get("jobLocationType") == "TELECOMMUTE":
                    locs.append("Remote")
                ident = node.get("identifier")
                if isinstance(ident, dict):
                    ident = ident.get("value")
                return (html_to_text(str(node.get("title", ""))), " / ".join(l for l in locs if l),
                        html_to_text(str(node.get("description", ""))), str(node.get("datePosted", ""))[:10],
                        str(ident or url))
    return None


def sitemap(client, site, keep, max_pages=300):
    origin = origin_of(site)
    assert_public_url(origin)
    urls = job_urls_from_sitemaps(client, origin)
    if not urls:
        raise NotFound(f"no job sitemap on {origin}")
    texts = [slug_text(u) for u in urls]
    wordy = sum(bool(re.search(r"[a-z]{4,}.*[a-z]{4,}", t, re.I)) for t in texts) / max(1, len(texts))
    if wordy > 0.5:   # URLs carry the job title: only open pages whose title could match
        urls = [u for u, t in zip(urls, texts) if keep(t, None)]
    host = urlparse(origin).netloc.split(".")
    company = host[-2] if len(host) >= 2 else origin
    out = Found(scanned=len(texts))
    for u in urls[:max_pages]:
        try:
            parsed = parse_jobposting(client.get_text(u, max_bytes=2_000_000, retries=2), u)
        except Exception:
            continue
        if not parsed:
            continue
        title, loc, desc, posted, ident = parsed
        if not keep(title, loc or None):
            continue
        out.append(Job(source="sitemap", company=company, job_id=ident, title=title,
                       location=loc, url=u, description=desc, posted=posted))
    return out


def probe(client, site):
    """Which fallback (if any) works for this careers site? Returns (ats, id, jobs) or None."""
    origin = origin_of(site)
    try:
        data = client.get_json(f"{origin}/api/jobs?limit=1", retries=1)
        if looks_like_jibe(data):
            return "jibe", origin, data.get("totalCount") or data.get("count")
    except Exception:
        pass
    urls = job_urls_from_sitemaps(client, origin, limit=5000)
    for u in urls[:3]:
        try:
            if parse_jobposting(client.get_text(u, max_bytes=2_000_000, retries=1), u):
                return "sitemap", origin, len(urls)
        except Exception:
            continue
    return None
