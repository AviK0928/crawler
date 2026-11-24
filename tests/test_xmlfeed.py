"""Tests for the XML job-feed fallback (e.g. careers.criteo.com), network faked."""
import unittest
from unittest import mock

from jobcrawler import xmlfeed
from jobcrawler.http import NotFound

PAGE = "https://careers.criteo.com/en/jobs/"
FEED = PAGE + "xml/?rss=true"


def job(title, city, country, ref, desc, remote="Hybrid Remote"):
    return f"""<job>
    <title><![CDATA[{title}]]></title>
    <date><![CDATA[Fri, 20 Nov 2026 00:00:00 GMT]]></date>
    <requisitionid><![CDATA[{ref}]]></requisitionid>
    <referencenumber><![CDATA[{ref}]]></referencenumber>
    <url><![CDATA[https://careers.criteo.com/en/jobs/{ref}/x/]]></url>
    <company><![CDATA[Criteo]]></company>
    <city><![CDATA[{city}]]></city><state><![CDATA[]]></state><country><![CDATA[{country}]]></country>
    <description><![CDATA[<h3>What You'll Do:</h3><p>{desc} &amp; more</p>]]></description>
    <jobtype><![CDATA[Permanent - Full Time]]></jobtype>
    <remotetype><![CDATA[{remote}]]></remotetype>
  </job>"""


XML = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<source>
  <publisher>Criteo</publisher>
  <publisherUrl>https://www.criteo.com/</publisherUrl>
  <lastBuildDate>Sat, 21 Nov 2026 21:34:42 GMT</lastBuildDate>
  {job("Software Development Engineer (Java)", "Gurgaon", "India", "r30001", "0-1 years, Java, Spring Boot")}
  {job("Senior AI Engineer", "Gurgaon", "India", "r20323", "6+ years")}
  {job("Software Engineer", "Paris", "France", "r30002", "Java")}
  {job("Backend Engineer", "", "", "r30003", "Java", remote="Fully Remote")}
</source>"""


class FakeClient:
    def __init__(self, pages):
        self.pages, self.fetched = pages, []

    def get_text(self, url, **kw):
        self.fetched.append(url)
        if url in self.pages:
            return self.pages[url]
        raise NotFound(url)


def keep_india(title, loc):
    return "senior" not in title.lower() and (loc is None or "india" in loc.lower() or "remote" in loc.lower())


class XmlFeed(unittest.TestCase):
    def setUp(self):
        p = mock.patch("jobcrawler.xmlfeed.assert_public_url", lambda u: None)
        p.start(); self.addCleanup(p.stop)

    def test_parse(self):
        jobs = xmlfeed.to_jobs(*xmlfeed.parse_feed(XML), FEED)
        self.assertEqual(len(jobs), 4)
        j = jobs[0]
        self.assertEqual((j.company, j.job_id, j.location, j.posted),
                         ("Criteo", "r30001", "Gurgaon, India", "2026-11-20"))
        self.assertEqual(j.url, "https://careers.criteo.com/en/jobs/r30001/x/")
        self.assertIn("Spring Boot & more", j.description)
        self.assertNotIn("<p>", j.description)
        self.assertEqual(jobs[3].location, "Remote")

    def test_fetch_filters(self):
        c = FakeClient({FEED: XML})
        titles = [j.title for j in xmlfeed.xmlfeed(c, FEED, keep_india)]
        self.assertEqual(titles, ["Software Development Engineer (Java)", "Backend Engineer"])

    def test_probe_finds_feed_from_careers_url(self):
        c = FakeClient({FEED: XML})
        self.assertEqual(xmlfeed.probe(c, PAGE), (FEED, 4))
        self.assertEqual(xmlfeed.probe(c, "https://careers.criteo.com/en/jobs"), (FEED, 4))

    def test_probe_uses_link_tag(self):
        other = "https://careers.example.com/feeds/all-jobs.xml"
        html = f'<link rel="alternate" type="application/rss+xml" href="/feeds/all-jobs.xml">'
        c = FakeClient({other: XML})
        self.assertEqual(xmlfeed.probe(c, "https://careers.example.com/", html), (other, 4))

    def test_probe_ignores_links_to_other_sites(self):
        html = '<link rel="alternate" type="application/rss+xml" href="https://evil.example.org/x.xml">'
        c = FakeClient({})
        self.assertIsNone(xmlfeed.probe(c, "https://careers.example.com/", html))
        self.assertFalse(any("evil" in u for u in c.fetched))

    def test_not_a_feed(self):
        self.assertIsNone(xmlfeed.parse_feed("<html><body>hi</body></html>"))
        self.assertIsNone(xmlfeed.parse_feed('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'))
        self.assertIsNone(xmlfeed.parse_feed("not xml"))
        with self.assertRaises(NotFound):
            xmlfeed.xmlfeed(FakeClient({FEED: "<rss/>"}), FEED, keep_india)

    def test_private_address_refused(self):
        mock.patch.stopall()
        c = FakeClient({})
        with self.assertRaises(ValueError):
            xmlfeed.xmlfeed(c, "http://169.254.169.254/jobs.xml", keep_india)
        self.assertEqual(c.fetched, [])


class Resolve(unittest.TestCase):
    def test_resolve_criteo_careers_page(self):
        from jobcrawler import resolve
        c = FakeClient({PAGE: "<html><body>Jobs</body></html>", FEED: XML})
        with mock.patch.object(resolve, "_safe_public_url", lambda u: None), \
             mock.patch("jobcrawler.more_sources.probe_eightfold", lambda *a, **k: None):
            e = resolve.resolve(PAGE, c)
        self.assertEqual((e["ats"], e["id"], e["jobs"], e["name"]), ("xmlfeed", FEED, 4, "Criteo"))


if __name__ == "__main__":
    unittest.main()
