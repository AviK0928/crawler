"""Adapters for public job-board APIs.

All of these are the official, unauthenticated JSON feeds that the companies'
own career pages are built on (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, amazon.jobs). No login, no HTML scraping, no ToS-grey sites like LinkedIn.

Each fetcher returns a list[Job]. `keep(title, location)` is a cheap
pre-filter so we only pull full descriptions for plausible jobs where the API
needs one extra request per posting.
"""
import re
import threading
from urllib.parse import quote, urlencode, urlparse

from .http import NotFound
from .models import Job, html_to_text


def greenhouse(client, slug, keep):
    data = client.get_json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs?content=true")
    return parse_greenhouse(data, slug)


def parse_greenhouse(data, slug):
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            source="greenhouse", company=slug, job_id=str(j["id"]),
            title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            description=html_to_text(j.get("content", "")),
            posted=(j.get("updated_at") or "")[:10],
        ))
    return out


def lever(client, slug, keep):
    try:
        data = client.get_json(f"https://api.lever.co/v0/postings/{quote(slug)}?mode=json")
    except NotFound:
        data = client.get_json(f"https://api.eu.lever.co/v0/postings/{quote(slug)}?mode=json")
    return parse_lever(data, slug)


def parse_lever(data, slug):
    from datetime import datetime, timezone
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []):
            parts.append(lst.get("text", ""))
            parts.append(html_to_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        created = j.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, timezone.utc).strftime("%Y-%m-%d") if created else ""
        out.append(Job(
            source="lever", company=slug, job_id=j["id"],
            title=j.get("text", ""),
            location=" / ".join(l for l in locs if l),
            url=j.get("hostedUrl", ""),
            description=" ".join(p for p in parts if p),
            posted=posted,
        ))
    return out


def ashby(client, slug, keep):
    data = client.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}")
    return parse_ashby(data, slug)


def parse_ashby(data, slug):
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        if j.get("isRemote"):
            locs.append("Remote")
        out.append(Job(
            source="ashby", company=slug, job_id=j.get("id", ""),
            title=j.get("title", ""),
            location=" / ".join(l for l in locs if l),
            url=j.get("jobUrl", ""),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
            posted=(j.get("publishedAt") or "")[:10],
        ))
    return out


def smartrecruiters(client, slug, keep, country="in"):
    """`country` (ISO code, e.g. "in") filters on SmartRecruiters' side; big companies have 1000s of postings."""
    out, offset = [], 0
    cq = f"&country={quote(country)}" if country else ""
    while True:
        page = client.get_json(
            f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings?limit=100&offset={offset}{cq}")
        content = page.get("content", [])
        for p in content:
            job = parse_smartrecruiters_summary(p, slug)
            if keep(job.title, job.location):
                try:
                    detail = client.get_json(
                        f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings/{p['id']}")
                    job.description = parse_smartrecruiters_detail(detail)
                except NotFound:
                    pass
                out.append(job)
        offset += len(content)
        if not content or offset >= page.get("totalFound", 0) or offset >= 2000:
            return out


def parse_smartrecruiters_summary(p, slug):
    loc = p.get("location") or {}
    country = (loc.get("country") or "").lower()
    country = {"in": "India", "us": "USA", "gb": "UK"}.get(country, country.upper())
    loc_s = ", ".join(x for x in (loc.get("city"), loc.get("region"), country) if x)
    if loc.get("remote"):
        loc_s += " (Remote)"
    return Job(
        source="smartrecruiters", company=slug, job_id=str(p["id"]),
        title=p.get("name", ""), location=loc_s,
        url=f"https://jobs.smartrecruiters.com/{slug}/{p['id']}",
        posted=(p.get("releasedDate") or "")[:10],
    )


def parse_smartrecruiters_detail(d):
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    return " ".join(html_to_text((sections.get(k) or {}).get("text", ""))
                    for k in ("jobDescription", "qualifications", "additionalInformation"))


def amazon(client, query, keep):
    """`query` is a dict of amazon.jobs search params, e.g. {"base_query": "java", "loc_query": "India"}."""
    out, offset = [], 0
    while True:
        params = {"result_limit": 100, "offset": offset, "sort": "recent", **query}
        data = client.get_json("https://www.amazon.jobs/en/search.json?" + urlencode(params, doseq=True))
        jobs = data.get("jobs", [])
        out.extend(parse_amazon(data))
        offset += len(jobs)
        if not jobs or offset >= data.get("hits", 0) or offset >= 1000:
            return out


def parse_amazon(data):
    out = []
    for j in data.get("jobs", []):
        desc = " ".join(html_to_text(j.get(k, "")) for k in
                        ("description", "basic_qualifications", "preferred_qualifications"))
        out.append(Job(
            source="amazon", company="amazon", job_id=str(j.get("id_icims") or j.get("id")),
            title=j.get("title", ""),
            location=j.get("normalized_location") or j.get("location", ""),
            url="https://www.amazon.jobs" + j.get("job_path", ""),
            description=desc,
            posted=j.get("posted_date", ""),
        ))
    return out


_WD_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_WD_MULTI = re.compile(r"^\d+ Locations?$", re.I)


def parse_workday_url(url):
    """https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/... -> (host, tenant, site)"""
    u = urlparse(url if "://" in url else "https://" + url)
    host = u.netloc.lower()
    if "myworkdayjobs.com" not in host:
        raise ValueError("not a Workday URL")
    tenant = host.split(".")[0]
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[0] == "wday":           # already an API URL: /wday/cxs/{tenant}/{site}/...
        return host, parts[2], parts[3]
    parts = [p for p in parts if not _WD_LOCALE.match(p)]
    if not parts:
        raise ValueError("Workday URL needs the career-site part, e.g. .../en-US/ExternalCareers")
    return host, tenant, parts[0]


# Workday rate-limits aggressively, so all Workday companies are crawled one at a time
WORKDAY_LOCK = threading.Lock()


def workday(client, url, keep, searches=("software engineer", "java"), max_per_search=200):
    host, tenant, site = parse_workday_url(url)
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    headers = {"Origin": f"https://{host}", "Referer": f"https://{host}/{site}"}
    seen, out = set(), []
    with WORKDAY_LOCK:
        try:  # open the career site once like a browser would, to pick up its session cookie
            client.get_text(f"https://{host}/{site}", max_bytes=200_000, retries=1)
        except Exception:
            pass
        for q in searches:
            offset, total = 0, None
            while True:
                try:
                    page = client.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset,
                                                            "searchText": q}, headers=headers, retries=5)
                except Exception:
                    if out:          # keep what we already have instead of losing the whole company
                        return out
                    raise
                posts = page.get("jobPostings", [])
                if total is None:                 # Workday only reports total on the first page
                    total = page.get("total", 0)
                for p in posts:
                    path = p.get("externalPath", "")
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    loc = p.get("locationsText", "")
                    multi = bool(_WD_MULTI.match(loc))
                    if not keep(p.get("title", ""), None if multi else loc):
                        continue
                    job = Job(source="workday", company=tenant, job_id=path.rsplit("_", 1)[-1] or path,
                              title=p.get("title", ""), location=loc,
                              url=f"https://{host}/{site}{path}", posted=p.get("postedOn", ""))
                    try:
                        info = client.get_json(f"{api}{path}", headers=headers, retries=2).get("jobPostingInfo", {})
                        job.description = html_to_text(info.get("jobDescription", ""))
                        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                        if multi or not job.location:
                            job.location = " / ".join(l for l in locs if l)
                        job.url = info.get("externalUrl") or job.url
                    except Exception:
                        pass         # keep the job even if its description couldn't be fetched
                    if multi and job.location == loc:
                        continue     # couldn't learn where a multi-location job is
                    out.append(job)
                offset += len(posts)
                if not posts or offset >= total or offset >= max_per_search:
                    break
    return out


FETCHERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "amazon": amazon,
}
