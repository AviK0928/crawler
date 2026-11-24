"""Tests for Phenom careers sites (e.g. careers.united.com), network faked."""
import json
import unittest
from unittest import mock

from jobcrawler import engine, phenom, resolve, scoring
from jobcrawler.http import HttpClient, NotFound
from tests.test_app import CFG

STRICT = {**CFG, "max_years_experience": 1}
O = "https://careers.united.com"
SEARCH_PAGE = '<html><script>phApp.ddo = {}; var x = {"refNum":"UAIUAUS","siteType":"external"};</script>' \
              '<link href="https://cdn.phenompeople.com/x.css"></html>'
JOBS = [
    {"jobId": "GGN00002700", "title": "Developer I", "location": "Gurugram, Haryana, India",
     "postedDate": "2026-11-20T00:00:00.000+0000", "descriptionTeaser": "Develop software..."},
    {"jobId": "GGN00002701", "title": "Senior Developer - Information Technology", "location": "Gurugram, Haryana, India"},
    {"jobId": "GGN00002702", "title": "Associate Analyst - Operations", "location": "Gurugram, Haryana, India"},
]
DETAIL = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Developer I", "datePosted": "2026-11-20",
          "description": "<p>0-1 years of experience. Java, Spring Boot, REST APIs.</p>",
          "jobLocation": {"address": {"addressLocality": "Gurugram", "addressCountry": "India"}}}


class C(HttpClient):
    def __init__(self, widgets=True):
        super().__init__(min_interval=0)
        self.widgets, self.posts, self.fetched = widgets, [], []

    def post_json(self, url, data, **kw):
        self.posts.append((url, data))
        if not self.widgets or url != O + "/widgets":
            raise NotFound(url)
        page = JOBS[data["from"]:data["from"] + data["size"]]
        return {"refineSearch": {"status": 200, "totalHits": len(JOBS), "data": {"jobs": page}}}

    def get_json(self, url, **kw):
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.fetched.append(url)
        if url == O + "/us/en/search-results":
            return SEARCH_PAGE
        if url.startswith(O + "/us/en/job/GGN00002700"):
            return f'<script type="application/ld+json">{json.dumps(DETAIL)}</script>'
        if url == O + "/sitemap.xml":
            return f"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>{O}/us/en/search-results</loc></url>
              <url><loc>{O}/us/en/job/GGN00002700/Developer-I</loc></url>
              <url><loc>{O}/us/en/job/WHQ00027007/Sr-IT-Manager-Supply-Chain</loc></url></urlset>"""
        raise NotFound(url)


KEEP = lambda t, l: scoring.title_ok(t, CFG) and (l is None or scoring.location_ok(l, CFG))


class Phenom(unittest.TestCase):
    def setUp(self):
        for t in ("jobcrawler.phenom.assert_public_url", "jobcrawler.resolve._safe_public_url",
                  "jobcrawler.xmlfeed.assert_public_url", "jobcrawler.generic.assert_public_url",
                  "jobcrawler.talentbrew.assert_public_url"):
            p = mock.patch(t, lambda u: None)
            p.start(); self.addCleanup(p.stop)

    def test_base(self):
        self.assertEqual(phenom.base_of("https://careers.united.com/us/en/search-results"), (O, "us", "en"))
        self.assertEqual(phenom.base_of("careers.united.com"), (O, "us", "en"))

    def test_search_service(self):
        c = C()
        jobs = phenom.phenom(c, O + "/us/en", KEEP)
        self.assertEqual([j.title for j in jobs], ["Developer I"])
        j = jobs[0]
        self.assertEqual((j.job_id, j.location, j.posted), ("GGN00002700", "Gurugram, Haryana, India", "2026-11-20"))
        self.assertIn("Spring Boot", j.description)
        self.assertEqual(j.url, O + "/us/en/job/GGN00002700/Developer-I")
        body = c.posts[0][1]
        self.assertEqual((body["refNum"], body["selected_fields"], body["lang"]), ("UAIUAUS", {"country": ["India"]}, "en_us"))
        self.assertEqual(jobs.scanned, 3)

    def test_sitemap_fallback(self):
        jobs = phenom.phenom(C(widgets=False), O + "/us/en", KEEP)
        self.assertEqual([j.title for j in jobs], ["Developer I"])

    def test_resolve_search_results_link(self):
        with mock.patch("jobcrawler.more_sources.probe_eightfold", lambda *a, **k: None):
            e = resolve.resolve(O + "/us/en/search-results", C())
        self.assertEqual((e["ats"], e["id"], e["jobs"]), ("phenom", O + "/us/en", 3))

    def test_engine_strict(self):
        matches, errors, _ = engine.run_crawl([{"name": "United Airlines", "ats": "phenom", "id": O + "/us/en"}],
                                              STRICT, log=lambda m: None, client=C())
        self.assertEqual(errors, [])
        self.assertEqual([(j.company, j.title) for j in matches], [("United Airlines", "Developer I")])


if __name__ == "__main__":
    unittest.main()
