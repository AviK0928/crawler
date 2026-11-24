"""Tests for Radancy TalentBrew careers sites (e.g. careers.unitedhealthgroup.com), network faked."""
import json
import unittest
from unittest import mock

from jobcrawler import engine, resolve, scoring, talentbrew
from jobcrawler.http import HttpClient, NotFound
from tests.test_app import CFG

STRICT = {**CFG, "max_years_experience": 1}
SITE = "https://careers.unitedhealthgroup.com"
J = SITE + "/job"
SITEMAP = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{SITE}</loc></url>
  <url><loc>{SITE}/category/technology-jobs/34088/81405/1</loc></url>
  <url><loc>{SITE}/location/hyderabad-telangana-india-jobs/34088/1269750-1254788-1269843/4</loc></url>
  <url><loc>{J}/hyderabad/software-engineer/34088/100</loc></url>
  <url><loc>{J}/gurgaon/associate-software-engineer/34088/101</loc></url>
  <url><loc>{J}/hyderabad/senior-software-engineer/34088/102</loc></url>
  <url><loc>{J}/eden-prairie/software-engineer/34088/103</loc></url>
  <url><loc>{J}/noida/registered-nurse/34088/104</loc></url>
  <url><loc>{J}/bengaluru/software-engineer/34088/105</loc></url>
</urlset>"""


def ld_page(title, city, desc):
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "datePosted": "2026-11-18",
          "description": f"<p>{desc}</p>", "identifier": {"value": "2362879"},
          "jobLocation": {"@type": "Place", "address": {"addressLocality": city, "addressRegion": "Telangana",
                                                          "addressCountry": "IN"}}}
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>x</body></html>'


PLAIN = """<html><head><script>var x=1;</script></head><body><nav>Search jobs</nav>
<h1 class="job-title">Associate Software Engineer</h1>
<p><b>Primary location:</b> Gurgaon, Haryana, India</p><p><b>Date posted:</b> 11/20/2026</p>
<div><p>0-1 years of experience. Java, Spring Boot, REST APIs, SQL.</p></div><footer>(c) UHG</footer></body></html>"""

PAGES = {
    "/job/hyderabad/software-engineer/34088/100": ld_page("Software Engineer", "Hyderabad",
                                                          "0-1 years of experience in Java and Spring Boot"),
    "/job/gurgaon/associate-software-engineer/34088/101": PLAIN,
    "/job/eden-prairie/software-engineer/34088/103": ld_page("Software Engineer", "Eden Prairie", "Java"),
}


class C(HttpClient):
    def __init__(self):
        super().__init__(min_interval=0)
        self.fetched = []

    def get_json(self, url, **kw):
        raise NotFound(url)

    def post_json(self, url, data, **kw):
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.fetched.append(url)
        if not url.startswith(SITE):
            raise NotFound(url)
        path = url[len(SITE):]
        if path in ("/sitemap.xml",):
            return SITEMAP
        if path in PAGES:
            return PAGES[path]
        if path in ("", "/"):
            return '<html><script src="https://tbcdn.talentbrew.com/company/34088/js/x.js"></script></html>'
        raise NotFound(url)   # robots.txt, other sitemaps, closed job 105


KEEP = lambda t, l: scoring.title_ok(t, CFG) and (l is None or scoring.location_ok(l, CFG))


class TalentBrew(unittest.TestCase):
    def setUp(self):
        for target in ("jobcrawler.talentbrew.assert_public_url", "jobcrawler.resolve._safe_public_url",
                       "jobcrawler.xmlfeed.assert_public_url", "jobcrawler.generic.assert_public_url"):
            p = mock.patch(target, lambda u: None)
            p.start(); self.addCleanup(p.stop)

    def test_job_links(self):
        links = talentbrew.job_links([f"{J}/hyderabad/software-engineer/34088/100",
                                      f"{SITE}/category/technology-jobs/34088/81405/1"])
        self.assertEqual(links, [(f"{J}/hyderabad/software-engineer/34088/100", "hyderabad", "software engineer", "100")])

    def test_only_opens_india_relevant_pages(self):
        c = C()
        jobs = talentbrew.talentbrew(c, SITE, KEEP)
        opened = [u for u in c.fetched if "/job/" in u]
        self.assertEqual(sorted(u.rsplit("/", 1)[1] for u in opened), ["100", "101", "105"])  # no US, senior, nurse
        self.assertEqual(jobs.scanned, 6)
        self.assertEqual(sorted((j.job_id, j.title) for j in jobs),
                         [("100", "Software Engineer"), ("101", "Associate Software Engineer")])

    def test_page_without_structured_data(self):
        title, loc, desc, posted = talentbrew.parse_page(PLAIN, "u", "gurgaon")
        self.assertEqual((title, loc, posted), ("Associate Software Engineer", "Gurgaon, Haryana, India", "2026-11-20"))
        self.assertIn("Spring Boot", desc)
        self.assertNotIn("var x", desc)
        self.assertNotIn("Search jobs", desc)

    def test_page_with_structured_data(self):
        title, loc, desc, posted = talentbrew.parse_page(PAGES["/job/hyderabad/software-engineer/34088/100"], "u", "hyderabad")
        self.assertEqual((title, posted), ("Software Engineer", "2026-11-18"))
        self.assertIn("Hyderabad", loc)

    def test_resolve(self):
        with mock.patch("jobcrawler.more_sources.probe_eightfold", lambda *a, **k: None):
            e = resolve.resolve(SITE, C())
        self.assertEqual((e["ats"], e["id"], e["jobs"]), ("talentbrew", SITE, 6))

    def test_engine_strict(self):
        matches, errors, _ = engine.run_crawl([{"name": "UnitedHealth Group / Optum", "ats": "talentbrew", "id": SITE}],
                                              STRICT, log=lambda m: None, client=C())
        self.assertEqual(errors, [])
        self.assertEqual(sorted(j.title for j in matches), ["Associate Software Engineer", "Software Engineer"])
        self.assertTrue(all(j.company == "UnitedHealth Group / Optum" for j in matches))


if __name__ == "__main__":
    unittest.main()
