"""Careers sites built on Radancy TalentBrew (e.g. careers.unitedhealthgroup.com).

Their job links carry the city and the title:
    https://careers.unitedhealthgroup.com/job/hyderabad/software-engineer/34088/96231860176
                                              city      title             company  job id
and the site's sitemap lists every open job. Big employers have thousands of US jobs, so instead of
opening pages at random we pick India jobs with a relevant title straight from the links, then open
only those pages for the title, location, date and description.
"""
import re
from urllib.parse import urlparse

from .generic import job_urls_from_sitemaps, origin_of, parse_jobposting
from .http import NotFound, assert_public_url
from .models import Found, Job, html_to_text

JOB_LINK = re.compile(r"/job/([^/?#]+)/([^/?#]+)/(\d+)/(\d+)/?$")
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_DROP = re.compile(r"<(script|style|noscript|header|footer|nav)[^>]*>.*?</\1>", re.S | re.I)
_DATE = re.compile(r"Date posted:?\s*</?[^>]*>?\s*(\d{2})/(\d{2})/(\d{4})", re.I)
_LOC = re.compile(r"(?:Primary location|Location):?\s*(?:</[^>]+>\s*)*(?:<[^>]+>\s*)*([^<]{3,80})", re.I)


def _words(slug):
    return re.sub(r"[-_+%]+", " ", slug).strip()


def job_links(urls):
    """[(url, city text, title text, job id)] for TalentBrew-style job links."""
    out = []
    for u in urls:
        m = JOB_LINK.search(urlparse(u).path)
        if m:
            out.append((u, _words(m.group(1)), _words(m.group(2)), m.group(4)))
    return out


def parse_page(html, url, city):
    """(title, location, description, posted) from a job page: JobPosting data if present, else the HTML."""
    parsed = parse_jobposting(html, url)
    if parsed:
        title, loc, desc, posted, _ = parsed
        return title, loc or city.title(), desc, posted
    h1 = _H1.search(html or "")
    title = html_to_text(h1.group(1)) if h1 else ""
    loc_m = _LOC.search(html or "")
    loc = html_to_text(loc_m.group(1)) if loc_m else city.title()
    d = _DATE.search(html or "")
    posted = f"{d.group(3)}-{d.group(1)}-{d.group(2)}" if d else ""
    body = _DROP.sub(" ", html or "")
    return title, loc, html_to_text(body)[:20000], posted


def talentbrew(client, site, keep, max_pages=150):
    origin = origin_of(site)
    assert_public_url(origin)
    links = job_links(job_urls_from_sitemaps(client, origin, limit=60000))
    if not links:
        raise NotFound(f"no TalentBrew job links in the sitemap of {origin}")
    picked = [l for l in links if keep(l[2], l[1])]          # title and city from the link itself
    host = urlparse(origin).netloc.split(".")
    company = host[-2] if len(host) >= 2 else origin
    out = Found(scanned=len(links))
    for url, city, slug_title, job_id in picked[:max_pages]:
        try:
            html = client.get_text(url, max_bytes=2_000_000, retries=2)
        except NotFound:
            continue          # job closed since the sitemap was built
        title, loc, desc, posted = parse_page(html, url, city)
        title = title or slug_title.title()
        if not keep(title, loc or None):
            continue
        out.append(Job(source="talentbrew", company=company, job_id=job_id, title=title, location=loc,
                       url=url, description=desc, posted=posted))
    return out


def looks_like_talentbrew(html):
    html = html or ""
    return "tbcdn.talentbrew.com" in html or "radancy" in html.lower() or bool(
        re.search(r'href="[^"]*/job/[^"/]+/[^"/]+/\d+/\d+"', html))


def probe(client, site):
    """(origin, number of job links) if the site's sitemap lists TalentBrew-style job links."""
    origin = origin_of(site)
    links = job_links(job_urls_from_sitemaps(client, origin, limit=60000))
    return (origin, len(links)) if links else None
