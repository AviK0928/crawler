"""More job sources:

keka        One company's Keka Hire careers page (<company>.keka.com/careers), used by many Indian
            startups and mid-size companies. The careers page loads its list from a public JSON feed:
                GET https://<company>.keka.com/careers/api/jobs/default/active
            (Keka's robots.txt allows /careers/.)
himalayas   Himalayas' free public remote-jobs API, searched for jobs open to India
            (India-only or worldwide). Himalayas' terms: credit Himalayas, use its apply link,
            don't republish the jobs elsewhere.
hn_hiring   The monthly Hacker News "Ask HN: Who is hiring?" thread, read through HN's official
            search API (hn.algolia.com). Posts are free text, so title/location are best guesses.
"""
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse

from .http import NotFound
from .models import Found, Job, html_to_text

AGGREGATORS = {"himalayas", "hn_hiring"}

# ---------------------------------------------------------------- Keka

_KEKA_HOST = re.compile(r"^(?:https?://)?([\w-]+)\.keka\.com", re.I)
_KEKA_UUID = re.compile(r"/ats/documents/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/", re.I)


def keka_host(ident):
    """'hr', 'hr.keka.com', 'https://hr.keka.com/careers/jobdetails/1' -> 'hr.keka.com'"""
    m = _KEKA_HOST.match(ident.strip())
    slug = m.group(1) if m else re.sub(r"[^\w-]", "", ident.strip().lower())
    if not slug or slug == "www":
        raise ValueError(f"not a Keka careers site: {ident}")
    return f"{slug.lower()}.keka.com"


def _keka_jobs(client, host):
    try:
        data = client.get_json(f"https://{host}/careers/api/jobs/default/active", retries=2)
        if isinstance(data, list):
            return data
    except NotFound:
        pass
    # older portals: the feed is keyed on an id that appears in the careers page
    html = client.get_text(f"https://{host}/careers/", max_bytes=2_000_000, retries=2)
    m = _KEKA_UUID.search(html or "")
    if not m:
        raise NotFound(f"no Keka careers feed on {host}")
    data = client.get_json(f"https://{host}/careers/api/embedjobs/default/active/{m.group(1)}", retries=2)
    if not isinstance(data, list):
        raise NotFound(f"no Keka careers feed on {host}")
    return data


def parse_keka(items, host):
    out = []
    for j in items:
        locs = []
        for l in j.get("jobLocations") or []:
            locs.append(", ".join(x for x in (l.get("city") or l.get("name"), l.get("state"),
                                              l.get("countryName")) if x))
        exp = str(j.get("experience") or "").strip()
        desc = html_to_text(j.get("description") or j.get("excerpt") or "")
        if exp:   # Keka gives experience as e.g. "2-4"; put it where the experience check reads it
            desc = f"Experience: {exp} years. {desc}"
        out.append(Job(source="keka", company=host.split(".")[0], job_id=str(j.get("id", "")),
                       title=(j.get("title") or "").strip(), location=" / ".join(dict.fromkeys(l for l in locs if l)),
                       url=f"https://{host}/careers/jobdetails/{j.get('id', '')}", description=desc,
                       posted=str(j.get("publishedOn") or "")[:10]))
    return out


def keka(client, ident, keep):
    host = keka_host(ident)
    items = _keka_jobs(client, host)
    out = Found(scanned=len(items))
    out.extend(j for j in parse_keka(items, host) if keep(j.title, j.location or None))
    return out


# ---------------------------------------------------------------- Himalayas

def _himalayas_location(restrictions):
    if not restrictions:
        return "Remote (worldwide, open to India)"
    return "Remote (" + ", ".join(str(r) for r in restrictions) + ")"


def parse_himalayas(items):
    out = []
    for j in items:
        ts = j.get("pubDate")
        posted = datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if isinstance(ts, (int, float)) else ""
        seniority = j.get("seniority") or []
        seniority = ", ".join(seniority) if isinstance(seniority, list) else str(seniority)
        desc = html_to_text(j.get("description") or j.get("excerpt") or "")
        if seniority:
            desc = f"Seniority: {seniority}. {desc}"
        out.append(Job(source="himalayas", company=f"{j.get('companyName') or 'Unknown'} (via Himalayas)",
                       job_id=str(j.get("guid") or j.get("applicationLink") or j.get("title")),
                       title=(j.get("title") or "").strip(), location=_himalayas_location(j.get("locationRestrictions")),
                       url=j.get("applicationLink") or j.get("guid") or "https://himalayas.app/jobs",
                       description=desc, posted=posted))
    return out


def himalayas(client, ident, keep, max_pages=5):
    """ident = {"query": "java", "country": "IN"} -> remote jobs open to India, newest first."""
    q, country = ident.get("query", "software engineer"), ident.get("country", "IN")
    out, seen = Found(), set()
    for page in range(1, max_pages + 1):
        params = {"q": q, "country": country, "sort": "recent", "page": page}
        data = client.get_json("https://himalayas.app/jobs/api/search?" + urlencode(params), retries=2)
        items = data.get("jobs") or []
        for j in parse_himalayas(items):
            if j.job_id in seen:
                continue
            seen.add(j.job_id)
            out.scanned += 1
            if keep(j.title, j.location):
                out.append(j)
        if not items or out.scanned >= (data.get("totalCount") or 0):
            break
    return out


# ---------------------------------------------------------------- Hacker News "Who is hiring?"

HN_API = "https://hn.algolia.com/api/v1"
_ROLE = re.compile(r"engineer|developer|\bsde\b|programmer|software|backend|back-end|full[ -]?stack|java", re.I)


def latest_hiring_thread(client):
    data = client.get_json(f"{HN_API}/search_by_date?tags=story,author_whoishiring&hitsPerPage=10", retries=2)
    for h in data.get("hits") or []:
        if re.match(r"Ask HN: Who is hiring\?", h.get("title") or "", re.I):
            return h["objectID"], (h.get("created_at") or "")[:10]
    raise NotFound("couldn't find this month's Who is hiring? thread")


def parse_hn_post(item):
    """(company, [candidate titles], first line, full text) from one top-level comment."""
    html = item.get("text") or ""
    first = html_to_text(re.split(r"<p>", html, maxsplit=1)[0])
    parts = [p.strip() for p in re.split(r"\s+\|\s+|\s*\|\s*", first) if p.strip()]
    company = re.sub(r"\s*\(.*?\)\s*$", "", parts[0])[:80] if parts else ""
    titles = []
    for p in parts[1:]:
        if _ROLE.search(p):
            titles += [t.strip() for t in re.split(r",|;|/| and ", p) if _ROLE.search(t)]
    return company, titles, first, html_to_text(html)


def hn_hiring(client, ident, keep):
    """ident = {"thread": "latest"}. Location comes from the post's first line (e.g. 'Acme | Backend Engineer | Bengaluru')."""
    thread, posted = latest_hiring_thread(client) if ident.get("thread", "latest") == "latest" else (ident["thread"], "")
    data = client.get_json(f"{HN_API}/items/{thread}", retries=2)
    out = Found()
    for c in data.get("children") or []:
        if not c.get("text") or not c.get("author"):
            continue      # deleted / flagged
        out.scanned += 1
        company, titles, first, text = parse_hn_post(c)
        for t in dict.fromkeys(titles):
            if keep(t, first):
                out.append(Job(source="hn_hiring", company=f"{company or 'Unknown'} (HN Who is hiring)",
                               job_id=f"{c.get('id')}:{t.lower()}", title=t, location=first[:200],
                               url=f"https://news.ycombinator.com/item?id={c.get('id')}", description=text,
                               posted=(c.get("created_at") or posted)[:10]))
                break     # one match per post is enough
    return out
