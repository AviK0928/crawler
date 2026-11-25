"""Turns whatever the user pastes (a careers-page URL, a job-board URL, or just a
company name) into a source entry the crawler understands."""
import re
from urllib.parse import urlparse

from . import generic, more_sources, phenom, sources, talentbrew, xmlfeed
from .http import NotFound, assert_public_url

# job-board URL patterns, both as pasted and as embedded in a company's own careers page
PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?!embed)([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("keka", re.compile(r"(?<![\w.-])([\w-]+)\.keka\.com/careers", re.I)),
    ("workday", re.compile(r"(https?://[\w-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[\w-]+(?:\?[\w=&%.-]+)?)", re.I)),
    ("recruitee", re.compile(r"(?<![\w.-])([a-z0-9][\w-]*)\.recruitee\.com", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/api/v1/widget/accounts/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!api\b|j/)([\w-]+)", re.I)),
    ("breezy", re.compile(r"(?<![\w.-])([a-z0-9][\w-]*)\.breezy\.hr", re.I)),
    ("oracle", re.compile(r"(https?://[\w.-]+\.oraclecloud\.com/hcmUI/CandidateExperience/[\w-]+/sites/[\w-]+)", re.I)),
]
NOT_SLUGS = {"www", "app", "api", "careers", "jobs"}
IGNORE_SLUGS = {"embed", "v1", "jobs", "api", "js", "en", "en-us", "www", "cdn"}


def _safe_public_url(url):
    assert_public_url(url)


def find_boards(text):
    """All (ats, id) job boards referenced in a URL or an HTML page, in order, de-duplicated."""
    found = []
    for ats, pat in PATTERNS:
        for m in pat.finditer(text):
            ident = m.group(1).rstrip("/.")
            if ats not in ("workday", "oracle") and (ident.lower() in IGNORE_SLUGS or ident.lower() in NOT_SLUGS):
                continue
            if (ats, ident) not in found and not any(a == ats and i.lower() == ident.lower() for a, i in found):
                found.append((ats, ident))
    return found


def _count(client, ats, ident):
    """Returns number of postings if the board exists, else raises."""
    nothing = lambda t, l: False
    if ats == "smartrecruiters":
        page = client.get_json(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings?limit=1")
        if not page.get("totalFound"):
            raise NotFound(ident)
        return page["totalFound"]
    if ats == "oracle":
        return more_sources.oracle_count(client, ident)
    if ats == "keka":
        found = sources.FETCHERS["keka"](client, ident, nothing)
        if not found.scanned:
            raise NotFound(ident)
        return found.scanned
    if ats == "workday":
        host, tenant, site = sources.parse_workday_url(ident)
        page = client.post_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                {"appliedFacets": sources.workday_url_facets(ident), "limit": 1, "offset": 0,
                                 "searchText": ""}, headers={"Accept-Language": "en-US"})
        return page.get("total", 0)
    return len(sources.FETCHERS[ats](client, ident, nothing))


def _name_guesses(name):
    base = name.lower().strip()
    slugs = [re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "-", base).strip("-"),
             re.sub(r"[^a-z0-9]", "", base.split()[0]) if base.split() else ""]
    sr = [re.sub(r"[^A-Za-z0-9]", "", name.title()), name.replace(" ", "")]
    return [s for s in dict.fromkeys(slugs) if s], [s for s in dict.fromkeys(sr + slugs) if s]


def _pretty(ident):
    return re.sub(r"[-_]+", " ", ident).title() if not ident.startswith("http") else urlparse(ident).netloc.split(".")[0].title()


def resolve(text, client):
    """Returns {"name","ats","id","jobs"} or raises ValueError with a friendly message."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Paste a careers page link or a company name")

    if "amazon.jobs" in text.lower() or text.lower() in ("amazon", "amazon india"):
        return {"name": "Amazon", "ats": "amazon", "id": {"base_query": "software development engineer", "country": "IND"}, "jobs": None}

    is_url = bool(re.match(r"^(https?://)?[\w-]+(\.[\w-]+)+(/|$)", text)) and " " not in text
    if is_url:
        url = text if "://" in text else "https://" + text
        boards = find_boards(url)
        if not boards:
            # a company's own careers page: look for an embedded job board in its HTML
            _safe_public_url(url)
            try:
                html = client.get_text(url)
                boards = find_boards(html)
            except Exception:
                html, boards = "", []
        else:
            html = ""
        for ats, ident in boards:
            try:
                n = _count(client, ats, ident)
                return {"name": _pretty(ident), "ats": ats, "id": ident, "jobs": n}
            except Exception:
                continue
        # no known job board: try Eightfold, an XML job feed, Phenom, TalentBrew, the Jibe feed, then the sitemap + Google-for-Jobs data
        _safe_public_url(url)
        ef = more_sources.probe_eightfold(client, url, html)
        if ef:
            ident, n = ef
            return {"name": ident["domain"].split(".")[0].title(), "ats": "eightfold", "id": ident, "jobs": n}
        feed = xmlfeed.probe(client, url, html)
        if feed:
            feed_url, n = feed
            host = [p for p in urlparse(feed_url).netloc.split(".") if p not in ("www", "jobs", "careers", "career", "com", "in", "co", "io", "org", "net")]
            return {"name": (host[0] if host else feed_url).title(), "ats": "xmlfeed", "id": feed_url, "jobs": n}
        ph = phenom.probe(client, url, html)
        if ph:
            base, n = ph
            host = [p for p in urlparse(base).netloc.split(".") if p not in ("www", "jobs", "careers", "career", "com", "in", "co", "io", "org", "net")]
            return {"name": (host[0] if host else base).title(), "ats": "phenom", "id": base, "jobs": n}
        tb = talentbrew.probe(client, url)
        if tb:
            origin, n = tb
            host = [p for p in urlparse(origin).netloc.split(".") if p not in ("www", "jobs", "careers", "career", "com", "in", "co", "io", "org", "net")]
            return {"name": (host[0] if host else origin).title(), "ats": "talentbrew", "id": origin, "jobs": n}
        found = generic.probe(client, url)
        if found:
            ats, ident, n = found
            host = [p for p in urlparse(ident).netloc.split(".") if p not in ("www", "jobs", "careers", "career", "com", "in", "co", "io", "org", "net")]
            return {"name": (host[0] if host else ident).title(), "ats": ats, "id": ident, "jobs": n}
        # last resort: guess from the domain, e.g. https://razorpay.com/jobs -> "razorpay"
        host = urlparse(url).hostname or ""
        parts = [p for p in host.split(".") if p not in ("www", "careers", "career", "jobs", "com", "in", "co", "io", "org", "net")]
        if parts and not boards:
            try:
                return resolve(parts[0], client)
            except ValueError:
                pass
        raise ValueError("Couldn't read jobs from that site. It isn't on a supported job board, has no public "
                         "job feed, and doesn't publish job pages in its sitemap. Try pasting the link of a single "
                         "job from the site, or the company name.")

    slugs, sr_slugs = _name_guesses(text)
    for ats in ("greenhouse", "lever", "ashby", "workable", "recruitee"):
        for s in slugs:
            try:
                n = _count(client, ats, s)
                if n:
                    return {"name": text.strip().title(), "ats": ats, "id": s, "jobs": n}
            except Exception:
                pass
    for s in sr_slugs:
        try:
            return {"name": text.strip().title(), "ats": "smartrecruiters", "id": s, "jobs": _count(client, "smartrecruiters", s)}
        except Exception:
            pass
    raise ValueError(f"Couldn't find “{text}” on Greenhouse, Lever, Ashby or SmartRecruiters. "
                     "Paste their careers page link instead (Workday links work too).")
