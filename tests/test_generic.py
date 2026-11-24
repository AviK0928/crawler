"""Tests for the Jibe and sitemap/JobPosting fallbacks (e.g. jobs.keysight.com), network faked."""
import json
import unittest
from unittest import mock

from jobcrawler import engine, generic, resolve, scoring
from jobcrawler.http import HttpClient, NotFound
from tests.test_app import CFG

STRICT = {**CFG, "max_years_experience": 1}

JIBE = {"totalCount": 3, "count": 3, "jobs": [
    {"data": {"slug": "48001", "req_id": "48001", "title": "R&D Engineer 1, Software",
              "location_name": "Gurugram, Haryana, India", "posted_date": "2026-11-10T00:00:00+0000",
              "description": "<p>Fresher / 0-1 years of experience. Java, Spring Boot, REST APIs.</p>"}},
    {"data": {"slug": "48002", "req_id": "48002", "title": "R&D Engineer 4, Software",
              "location_name": "Gurugram, Haryana, India", "description": "8 years of experience"}},
    {"data": {"slug": "48003", "req_id": "48003", "title": "Software Engineer",
              "city": "Santa Rosa", "state": "CA", "country": "United States", "description": "Java"}}]}

SITE = "https://careers.acme-sitemap.com"
ROBOTS = "User-agent: *\nDisallow: /admin\nSitemap: https://careers.acme-sitemap.com/sitemap_index.xml\n"
INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://careers.acme-sitemap.com/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://careers.acme-sitemap.com/sitemap-jobs.xml</loc></sitemap>
  <sitemap><loc>http://169.254.169.254/latest/evil.xml</loc></sitemap>
</sitemapindex>"""
JOBS_SM = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.acme-sitemap.com/job/bengaluru/software-engineer-i-java/111/</loc></url>
  <url><loc>https://careers.acme-sitemap.com/job/pune/senior-software-engineer/222/</loc></url>
  <url><loc>https://careers.acme-sitemap.com/job/gurgaon/sales-manager/333/</loc></url>
  <url><loc>https://careers.acme-sitemap.com/job/austin/backend-developer/444/</loc></url>
  <url><loc>https://careers.acme-sitemap.com/jobs/search/</loc></url>
</urlset>"""
PAGES_SM = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.acme-sitemap.com/benefits</loc></url></urlset>"""


def page(title, city, country, desc, extra=""):
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": title},
        {"@type": "JobPosting", "title": title, "description": f"<p>{desc}</p>", "datePosted": "2026-11-12",
         "identifier": {"@type": "PropertyValue", "value": title[:3]},
         "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": city,
                                                        "addressCountry": {"@type": "Country", "name": country}}}]}]}
    return f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script>{extra}</head><body>x</body></html>'


PAGES = {
    "/job/bengaluru/software-engineer-i-java/111/": page("Software Engineer I (Java)", "Bengaluru", "India",
                                                         "0-1 years of experience with Java and Spring Boot"),
    "/job/pune/senior-software-engineer/222/": page("Senior Software Engineer", "Pune", "India", "6+ years"),
    "/job/austin/backend-developer/444/": page("Backend Developer", "Austin", "US", "Java, 0-1 years experience"),
}


class FakeSiteClient(HttpClient):
    def __init__(self):
        super().__init__(min_interval=0)
        self.fetched = []

    def get_json(self, url, **kw):
        self.fetched.append(url)
        if url.startswith("https://jobs.keysight.com/api/jobs"):
            return JIBE if "page=2" not in url else {"totalCount": 3, "jobs": []}
        raise NotFound(url)

    def post_json(self, url, data, **kw):
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.fetched.append(url)
        if "169.254" in url:
            raise AssertionError("fetched a private address!")
        if not url.startswith(SITE):
            raise NotFound(url)
        path = url[len(SITE):]
        if path == "/robots.txt":
            return ROBOTS
        if path == "/sitemap_index.xml":
            return INDEX
        if path == "/sitemap-jobs.xml":
            return JOBS_SM
        if path == "/sitemap-pages.xml":
            return PAGES_SM
        if path in PAGES:
            return PAGES[path]
        raise NotFound(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.c = FakeSiteClient()
        for target in ("jobcrawler.generic.assert_public_url", "jobcrawler.resolve._safe_public_url"):
            p = mock.patch(target, lambda u: None)
            p.start(); self.addCleanup(p.stop)

    def keep(self, cfg=CFG):
        return lambda t, l: scoring.title_ok(t, cfg) and (l is None or scoring.location_ok(l, cfg))


class Jibe(Base):
    def test_fetch_and_parse(self):
        jobs = generic.jibe(self.c, "https://jobs.keysight.com/", self.keep())
        self.assertEqual(len(jobs), 3)
        j = jobs[0]
        self.assertEqual((j.company, j.job_id, j.location), ("keysight", "48001", "Gurugram, Haryana, India"))
        self.assertEqual(j.url, "https://jobs.keysight.com/jobs/48001")
        self.assertIn("Spring Boot", j.description)
        self.assertEqual(jobs[2].location, "Santa Rosa, CA, United States")
        self.assertIn("location=India", self.c.fetched[0])

    def test_scoring_strict(self):
        jobs = generic.jibe(self.c, "https://jobs.keysight.com", self.keep())
        kept = [j.title for j in jobs if scoring.score(j, STRICT)]
        self.assertEqual(kept, ["R&D Engineer 1, Software"])   # Engineer 4 and US job dropped

    def test_resolve_keysight(self):
        e = resolve.resolve("https://jobs.keysight.com", self.c)
        self.assertEqual((e["ats"], e["id"], e["jobs"], e["name"]), ("jibe", "https://jobs.keysight.com", 3, "Keysight"))


class Sitemap(Base):
    def test_finds_job_urls_skips_other_hosts_and_non_jobs(self):
        urls = generic.job_urls_from_sitemaps(self.c, SITE)
        self.assertEqual(len(urls), 4)
        self.assertFalse(any("search" in u or "benefits" in u for u in urls))
        self.assertFalse(any("169.254" in u for u in self.c.fetched))

    def test_parse_jobposting(self):
        t, loc, desc, posted, ident = generic.parse_jobposting(PAGES["/job/bengaluru/software-engineer-i-java/111/"], "u")
        self.assertEqual((t, loc, posted), ("Software Engineer I (Java)", "Bengaluru, India", "2026-11-12"))
        self.assertIn("Spring Boot", desc)
        self.assertIsNone(generic.parse_jobposting("<html>no data</html>", "u"))
        self.assertIsNone(generic.parse_jobposting('<script type="application/ld+json">{broken</script>', "u"))

    def test_only_opens_pages_whose_slug_could_match(self):
        jobs = generic.sitemap(self.c, SITE, self.keep())
        opened = [u for u in self.c.fetched if "/job/" in u]
        self.assertTrue(any("/111/" in u for u in opened))
        self.assertFalse(any("/222/" in u or "/333/" in u for u in opened))   # senior + sales skipped by slug
        self.assertEqual([j.title for j in jobs], ["Software Engineer I (Java)"])  # Austin dropped by location

    def test_resolve_sitemap_site(self):
        e = resolve.resolve(SITE + "/", self.c)
        self.assertEqual((e["ats"], e["id"]), ("sitemap", SITE))

    def test_full_crawl_via_engine(self):
        entries = [{"name": "Acme", "ats": "sitemap", "id": SITE},
                   {"name": "Keysight", "ats": "jibe", "id": "https://jobs.keysight.com"}]
        matches, errors, _ = engine.run_crawl(entries, STRICT, log=lambda m: None, client=self.c)
        self.assertEqual(errors, [])
        self.assertEqual(sorted((j.company, j.title) for j in matches),
                         [("Acme", "Software Engineer I (Java)"), ("Keysight", "R&D Engineer 1, Software")])


class Safety(unittest.TestCase):
    def test_private_addresses_refused_before_any_fetch(self):
        c = FakeSiteClient()
        for bad in ("http://127.0.0.1", "http://169.254.169.254", "http://localhost:8000"):
            for ats in ("jibe", "sitemap"):
                _, errors, _ = engine.run_crawl([{"name": "x", "ats": ats, "id": bad}], CFG,
                                                log=lambda m: None, client=c)
                self.assertEqual(len(errors), 1, (ats, bad))
        self.assertEqual(c.fetched, [])


if __name__ == "__main__":
    unittest.main()


class TimeLimitsAndFailFast(unittest.TestCase):
    def test_workday_block_skips_remaining_workday_sites(self):
        from jobcrawler.http import RateLimited
        calls = []

        class C(FakeSiteClient):
            def get_text(self, url, **kw):
                raise NotFound(url)

            def post_json(self, url, data, **kw):
                calls.append(url)
                raise RateLimited("429")

        entries = [{"name": n, "ats": "workday", "id": f"https://{n}.wd5.myworkdayjobs.com/Ext"}
                   for n in ("walmart", "nvidia", "paypal", "adobe")]
        import time as _t
        t0 = _t.monotonic()
        _, errors, _ = engine.run_crawl(entries, {**CFG, "workday_retry_wait_seconds": 0}, log=lambda m: None, client=C())
        self.assertLess(_t.monotonic() - t0, 5)
        self.assertEqual(len(calls), 1)                         # only the first site was even tried
        self.assertEqual(sum("skipped" in m for _, m in errors), 3)

    def test_crawl_time_limit_returns_partial_results(self):
        class Slow(FakeSiteClient):
            def get_json(self, url, **kw):
                self._check_deadline()
                if "amazon" in url:
                    self._sleep(0.4)                            # a never-ending, slow source
                    return {"hits": 10**6, "jobs": [{"id_icims": url[-6:], "title": "x",
                                                      "normalized_location": "Pune, IND", "job_path": "/x"}]}
                return super().get_json(url, **kw)

        import time as _t
        entries = [{"name": "Keysight", "ats": "jibe", "id": "https://jobs.keysight.com"},
                   {"name": "Amazon", "ats": "amazon", "id": {"base_query": "java"}}]
        logs = []
        t0 = _t.monotonic()
        with mock.patch("jobcrawler.generic.assert_public_url", lambda u: None):
            matches, errors, _ = engine.run_crawl(entries, {**STRICT, "max_crawl_seconds": 1},
                                                  log=logs.append, client=Slow())
        self.assertLess(_t.monotonic() - t0, 4)
        self.assertEqual([j.title for j in matches], ["R&D Engineer 1, Software"])   # finished source kept
        self.assertEqual(errors, [("Amazon (amazon)", "timed out (crawl limit 1s)")])

    def test_scanned_vs_relevant_logged(self):
        class SR(FakeSiteClient):
            def get_json(self, url, **kw):
                if "smartrecruiters" in url and "/postings?" in url:
                    return {"totalFound": 57, "content": [
                        {"id": "1", "name": "Sr. SW Engineer", "location": {"city": "Bangalore", "country": "in"}}]}
                raise NotFound(url)
        logs = []
        engine.run_crawl([{"name": "Visa", "ats": "smartrecruiters", "id": "Visa"}], CFG, log=logs.append, client=SR())
        self.assertIn("✓ Visa (smartrecruiters): 57 postings scanned, 0 relevant", logs)
