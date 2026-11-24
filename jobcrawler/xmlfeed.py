"""Fallback for careers sites that publish an XML job feed (the "Indeed XML" format).

Many custom careers sites (e.g. careers.criteo.com) aren't on a known job board and don't list
job pages in their sitemap, but do publish every open job in one XML file for Indeed and other
aggregators. Shape:

    <source>
      <publisher>Criteo</publisher>
      <job>
        <title>...</title> <date>Fri, 20 Nov 2026 00:00:00 GMT</date>
        <referencenumber>r21355</referencenumber> <url>https://...</url>
        <company>Criteo</company> <city>Gurgaon</city> <state/> <country>India</country>
        <description><![CDATA[<h3>...</h3>]]></description>
      </job>
      ...
    </source>
"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from .http import NotFound, assert_public_url
from .models import Job, html_to_text

# where sites usually put the feed, relative to the pasted careers URL / the site root
FEED_SUFFIXES = ("xml/?rss=true", "xml/", "feed/", "rss/")
FEED_PATHS = ("/jobs/xml/?rss=true", "/en/jobs/xml/?rss=true", "/jobs/feed/", "/jobs.xml",
              "/feed/jobs.xml", "/jobs/rss/")
_ALT_LINK = re.compile(r"<link[^>]+(?:rss|atom|xml)[^>]*>", re.I)
_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


def _local(tag):
    return tag.split("}")[-1].lower()


def _text(el, *names):
    for child in el:
        if _local(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _date(s):
    if not s:
        return ""
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return ""


def parse_feed(xml_text):
    """Returns the list of <job> elements, or None if this isn't a job feed."""
    try:
        root = ET.fromstring(xml_text.strip().encode())
    except (ET.ParseError, ValueError):
        return None
    jobs = [el for el in root.iter() if _local(el.tag) == "job"]
    if not jobs or not any(_text(j, "title") for j in jobs[:5]):
        return None
    return root, jobs


def _company(root, feed_url):
    name = _text(root, "publisher")
    if name:
        return name
    host = [p for p in urlparse(feed_url).netloc.split(".") if p not in ("www", "careers", "jobs")]
    return host[0].title() if host else feed_url


def to_jobs(root, jobs, feed_url):
    company = _company(root, feed_url)
    out = []
    for j in jobs:
        title = html_to_text(_text(j, "title"))
        if not title:
            continue
        loc = ", ".join(x for x in (_text(j, "city"), _text(j, "state"), _text(j, "country")) if x)
        remote = _text(j, "remotetype")
        if remote and "remote" in remote.lower() and "hybrid" not in remote.lower():
            loc = f"{loc} / Remote" if loc else "Remote"
        url = _text(j, "url", "link", "applyurl")
        ident = _text(j, "referencenumber", "requisitionid", "apijobid", "id") or url or title
        out.append(Job(source="xmlfeed", company=_text(j, "company") or company, job_id=ident,
                       title=title, location=loc, url=urljoin(feed_url, url) if url else feed_url,
                       description=html_to_text(_text(j, "description")),
                       posted=_date(_text(j, "date", "posted", "pubdate"))))
    return out


def xmlfeed(client, feed_url, keep):
    assert_public_url(feed_url)
    parsed = parse_feed(client.get_text(feed_url, max_bytes=20_000_000, retries=2))
    if not parsed:
        raise NotFound(f"no job feed at {feed_url}")
    return [j for j in to_jobs(*parsed, feed_url) if keep(j.title, j.location or None)]


def candidate_feeds(page_url, html=""):
    """Feed URLs to try for a careers page: ones the page links to, then common locations."""
    found = []
    for tag in _ALT_LINK.findall(html or ""):
        m = _HREF.search(tag)
        if m:
            found.append(urljoin(page_url, m.group(1)))
    base = page_url if page_url.endswith("/") else page_url + "/"
    found += [base + s for s in FEED_SUFFIXES]
    u = urlparse(page_url)
    found += [f"{u.scheme}://{u.netloc}{p}" for p in FEED_PATHS]
    return [f for f in dict.fromkeys(found) if urlparse(f).netloc == u.netloc]


def probe(client, page_url, html=""):
    """Returns (feed_url, number_of_jobs) for the first working feed, or None."""
    for feed in candidate_feeds(page_url, html):
        try:
            parsed = parse_feed(client.get_text(feed, max_bytes=20_000_000, retries=1))
        except Exception:
            continue
        if parsed:
            return feed, len(parsed[1])
    return None
