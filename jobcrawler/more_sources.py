"""More job systems with public, no-login job feeds.

recruitee  https://{slug}.recruitee.com/api/offers/
workable   https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
breezy     https://{slug}.breezy.hr/json
oracle     Oracle Recruiting Cloud (e.g. JPMorgan):  https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions
eightfold  Eightfold career sites (e.g. Microsoft, Qualcomm):  https://{site}/api/apply/v2/jobs?domain=...
"""
import re
import urllib.error
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from .http import NotFound, assert_public_url
from .models import Found, Job, html_to_text

SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


def _slug(s):
    if not isinstance(s, str) or not SLUG.match(s):
        raise ValueError(f"invalid board name {s!r}")
    return s


def _join(*parts):
    return ", ".join(str(p) for p in parts if p)


# ------------------------------------------------------------------ Recruitee

def recruitee(client, slug, keep):
    data = client.get_json(f"https://{_slug(slug)}.recruitee.com/api/offers/")
    return parse_recruitee(data, slug)


def parse_recruitee(data, slug):
    out = []
    for o in data.get("offers", []):
        locs = [_join(l.get("city"), l.get("state"), l.get("country")) for l in (o.get("locations") or [])]
        loc = " / ".join(l for l in locs if l) or o.get("location") or _join(o.get("city"), o.get("country"))
        if o.get("remote"):
            loc = (loc + " (Remote)").strip()
        out.append(Job(source="recruitee", company=slug, job_id=str(o.get("id") or o.get("slug")),
                       title=o.get("title", ""), location=loc,
                       url=o.get("careers_url") or f"https://{slug}.recruitee.com/o/{o.get('slug', '')}",
                       description=html_to_text((o.get("description") or "") + " " + (o.get("requirements") or "")),
                       posted=str(o.get("published_at") or "")[:10]))
    return out


# ------------------------------------------------------------------ Workable

def workable(client, slug, keep):
    data = client.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{_slug(slug)}?details=true")
    return parse_workable(data, slug)


def parse_workable(data, slug):
    out = []
    for j in data.get("jobs", []):
        locs = [_join(l.get("city"), l.get("region"), l.get("country")) for l in (j.get("locations") or [])
                if not l.get("hidden")]
        loc = " / ".join(l for l in locs if l) or _join(j.get("city"), j.get("state"), j.get("country"))
        if j.get("telecommuting"):
            loc = (loc + " (Remote)").strip()
        code = j.get("shortcode") or j.get("code") or ""
        out.append(Job(source="workable", company=data.get("name") or slug, job_id=str(code),
                       title=j.get("title", ""), location=loc,
                       url=j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/j/{code}/",
                       description=html_to_text(j.get("description", "")) + " " + str(j.get("experience") or ""),
                       posted=str(j.get("published_on") or j.get("created_at") or "")[:10]))
    return out


# ------------------------------------------------------------------ Breezy HR

def breezy(client, slug, keep):
    data = client.get_json(f"https://{_slug(slug)}.breezy.hr/json")
    return parse_breezy(data, slug)


def parse_breezy(data, slug):
    out = []
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        country = loc.get("country")
        country = country.get("name") if isinstance(country, dict) else country
        state = loc.get("state")
        state = state.get("name") if isinstance(state, dict) else state
        loc_s = loc.get("name") or _join(loc.get("city"), state, country)
        if loc.get("is_remote"):
            loc_s += " (Remote)"
        out.append(Job(source="breezy", company=slug, job_id=str(j.get("id") or j.get("friendly_id")),
                       title=j.get("name", ""), location=loc_s,
                       url=j.get("url") or f"https://{slug}.breezy.hr/p/{j.get('friendly_id', '')}",
                       description=html_to_text(j.get("description", "")),
                       posted=str(j.get("published_date") or "")[:10]))
    return out


# ------------------------------------------------------------------ Oracle Recruiting Cloud

ORACLE_URL = re.compile(r"(https?://[\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/([\w-]+)/sites/([\w-]+)", re.I)


def parse_oracle_url(url):
    m = ORACLE_URL.search(url or "")
    if not m:
        raise ValueError("Oracle careers link should look like https://xxx.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001")
    return m.group(1), m.group(2), m.group(3)


def oracle(client, url, keep, searches=("software engineer", "java"), max_per_search=300):
    origin, lang, site = parse_oracle_url(url)
    assert_public_url(origin)
    api = f"{origin}/hcmRestApi/resources/latest"
    seen, out = set(), Found()
    for q in searches:
        offset = 0
        while offset < max_per_search:
            finder = f'findReqs;siteNumber={site},limit=25,offset={offset},keyword="{q}",sortBy=POSTING_DATES_DESC'
            data = client.get_json(f"{api}/recruitingCEJobRequisitions?onlyData=true"
                                   f"&expand=requisitionList.secondaryLocations&finder={quote(finder, safe='=,;')}")
            items = data.get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            total = items[0].get("TotalJobsCount") or 0
            for r in reqs:
                rid = str(r.get("Id", ""))
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                out.scanned += 1
                locs = [r.get("PrimaryLocation", "")] + [s.get("Name", "") for s in (r.get("secondaryLocations") or [])]
                loc = " / ".join(l for l in locs if l)
                if not keep(r.get("Title", ""), loc):
                    continue
                job = Job(source="oracle", company=urlparse(origin).netloc.split(".")[0], job_id=rid,
                          title=r.get("Title", ""), location=loc,
                          url=f"{origin}/hcmUI/CandidateExperience/{lang}/sites/{site}/job/{rid}",
                          description=html_to_text(r.get("ShortDescriptionStr", "")),
                          posted=str(r.get("PostedDate") or "")[:10])
                try:
                    f2 = quote(f'ById;Id="{rid}",siteNumber={site}', safe='=,;')
                    d = (client.get_json(f"{api}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder={f2}",
                                         retries=2).get("items") or [{}])[0]
                    job.description = html_to_text(" ".join(str(d.get(k) or "") for k in (
                        "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))) or job.description
                except Exception:
                    pass
                out.append(job)
            offset += len(reqs)
            if not reqs or offset >= total:
                break
    return out


def oracle_count(client, url):
    origin, _, site = parse_oracle_url(url)
    assert_public_url(origin)
    finder = quote(f"findReqs;siteNumber={site},limit=1", safe="=,;")
    data = client.get_json(f"{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder={finder}",
                           retries=1)
    items = data.get("items")
    if not isinstance(items, list):
        raise NotFound(url)
    return (items[0] if items else {}).get("TotalJobsCount", 0)


# ------------------------------------------------------------------ Eightfold

def eightfold(client, ident, keep, location="India", searches=("software engineer", "java"), max_per_search=300):
    """ident = {"url": "https://apply.careers.microsoft.com", "domain": "microsoft.com"}"""
    origin = "https://" + urlparse(ident["url"] if "://" in ident["url"] else "https://" + ident["url"]).netloc
    domain = ident["domain"]
    if not re.match(r"^[\w.-]+$", domain or ""):
        raise ValueError("invalid Eightfold domain")
    assert_public_url(origin)
    seen, out = set(), Found()
    for q in searches:
        start = 0
        while start < max_per_search:
            data = client.get_json(f"{origin}/api/apply/v2/jobs?domain={quote(domain)}&start={start}&num=10"
                                   f"&location={quote(location)}&query={quote(q)}&sort_by=timestamp")
            positions = data.get("positions") or []
            for p in positions:
                pid = str(p.get("id", ""))
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.scanned += 1
                loc = " / ".join(p.get("locations") or []) or p.get("location", "")
                if not keep(p.get("name", ""), loc):
                    continue
                ts = p.get("t_create")
                job = Job(source="eightfold", company=domain.split(".")[0], job_id=pid, title=p.get("name", ""),
                          location=loc,
                          url=p.get("canonicalPositionUrl") or f"{origin}/careers/job/{pid}",
                          description=html_to_text(p.get("job_description", "")),
                          posted=datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else "")
                if not job.description:
                    try:
                        d = client.get_json(f"{origin}/api/apply/v2/jobs/{pid}?domain={quote(domain)}", retries=2)
                        job.description = html_to_text(d.get("job_description", ""))
                    except Exception:
                        pass
                out.append(job)
            start += len(positions)
            if not positions or start >= (data.get("count") or 0):
                break
    return out


EIGHTFOLD_DOMAIN = re.compile(r"""(?:[?&]domain=|["']domain["']\s*:\s*["'])([a-z0-9][\w.-]*\.[a-z]{2,})""", re.I)


def probe_eightfold(client, url, html=""):
    """Eightfold sites: *.eightfold.ai, or a page whose HTML mentions eightfold + a domain parameter."""
    host = urlparse(url).netloc.lower()
    if not (host.endswith("eightfold.ai") or "eightfold" in (html or "").lower()):
        return None
    m = EIGHTFOLD_DOMAIN.search(url) or EIGHTFOLD_DOMAIN.search(html or "")
    domain = m.group(1) if m else (host.split(".")[0] + ".com")
    origin = "https://" + host
    try:
        data = client.get_json(f"{origin}/api/apply/v2/jobs?domain={quote(domain)}&start=0&num=1", retries=1)
    except Exception:
        return None
    if isinstance(data, dict) and isinstance(data.get("positions"), list):
        return {"url": origin, "domain": domain}, data.get("count")
    return None


# ------------------------------------------------------------------ aggregators (many companies per source)
# These return jobs from many employers, so each job keeps its own company name.

AGGREGATORS = {"workable_search", "adzuna"}


def workable_search(client, ident, keep, max_pages=10):
    """Workable's own cross-company job search (jobs.workable.com): thousands of startups/SMBs.
    ident = {"query": "java", "location": "India"}"""
    q, loc = ident.get("query", ""), ident.get("location", "India")
    out, token = Found(), None
    for _ in range(max_pages):
        url = f"https://jobs.workable.com/api/v1/jobs?query={quote(q)}&location={quote(loc)}"
        if token:
            url += f"&pageToken={quote(token)}"
        data = client.get_json(url)
        jobs = data.get("jobs") or []
        for j in parse_workable_search(jobs):
            out.scanned += 1
            if keep(j.title, j.location or None):
                out.append(j)
        token = data.get("nextPageToken")
        if not jobs or not token:
            break
    return out


def parse_workable_search(items):
    out = []
    for j in items:
        comp = j.get("company") or {}
        company = (comp.get("title") or comp.get("name")) if isinstance(comp, dict) else str(comp)
        locs = j.get("locations")
        if isinstance(locs, list) and locs and isinstance(locs[0], str):
            loc = " / ".join(locs)
        else:
            l = j.get("location") or {}
            loc = (_join(l.get("city"), l.get("subregion") or l.get("region"), l.get("countryName") or l.get("country"))
                   if isinstance(l, dict) else str(l or ""))
        desc = " ".join(str(j.get(k) or "") for k in ("description", "requirementsSection", "requirements"))
        out.append(Job(source="workable_search", company=company or "Workable company", job_id=str(j.get("id", "")),
                       title=j.get("title", ""), location=loc,
                       url=j.get("url") or f"https://jobs.workable.com/view/{j.get('id', '')}",
                       description=html_to_text(desc), posted=str(j.get("created") or "")[:10]))
    return out


def adzuna(client, ident, keep, app_id=None, app_key=None, max_pages=3, max_days_old=30):
    """Adzuna job aggregator, India site. Needs a free key from developer.adzuna.com.
    ident = {"what": "java developer"}"""
    import os
    app_id = app_id or os.environ.get("ADZUNA_APP_ID", "")
    app_key = app_key or os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        raise ValueError("Adzuna key not set: add ADZUNA_APP_ID and ADZUNA_APP_KEY (free at developer.adzuna.com) "
                         "in Render > Environment, or untick Adzuna")
    out = Found()
    for page in range(1, max_pages + 1):
        try:
            data = client.get_json(
                f"https://api.adzuna.com/v1/api/jobs/in/search/{page}?app_id={quote(app_id)}&app_key={quote(app_key)}"
                f"&results_per_page=50&what={quote(ident.get('what', 'java developer'))}&category=it-jobs"
                f"&max_days_old={max_days_old}&sort_by=date&content-type=application/json")
        except NotFound:
            raise NotFound("Adzuna search") from None      # the URL contains the key: never show it
        except urllib.error.HTTPError as ex:
            raise RuntimeError(f"Adzuna returned HTTP {ex.code}" + (" (check your app_id/app_key)" if ex.code in (401, 403) else "")) from None
        results = data.get("results") or []
        for j in parse_adzuna(results):
            out.scanned += 1
            if keep(j.title, j.location or None):
                out.append(j)
        if len(results) < 50:
            break
    return out


def parse_adzuna(results):
    out = []
    for r in results:
        comp = (r.get("company") or {}).get("display_name", "")
        loc = (r.get("location") or {}).get("display_name", "")
        area = (r.get("location") or {}).get("area") or []
        if area and "India" not in loc:
            loc = ", ".join([loc] + [a for a in area if a not in loc]) if loc else ", ".join(area)
        out.append(Job(source="adzuna", company=comp or "Unknown", job_id=str(r.get("id", "")),
                       title=html_to_text(r.get("title", "")), location=loc, url=r.get("redirect_url", ""),
                       description=html_to_text(r.get("description", "")), posted=str(r.get("created") or "")[:10]))
    return out
