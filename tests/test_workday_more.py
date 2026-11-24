"""Workday: country filter from facets, 422 handling, per-server rate limits and the retry pass."""
import io
import unittest
import urllib.error

from jobcrawler import engine, sources
from jobcrawler.http import HttpClient, NotFound, RateLimited
from tests.test_app import CFG

KEEP = lambda t, l: engine.scoring.title_ok(t, CFG) and (l is None or engine.scoring.location_ok(l, CFG))

FACETS = [
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "eng", "count": 900}]},
    {"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "descriptor": "Locations", "values": [
            {"descriptor": "United States of America", "id": "us-id", "count": 700},
            {"descriptor": "India", "id": "in-id", "count": 120}]},
        {"facetParameter": "locations", "descriptor": "Locations", "values": [
            {"descriptor": "Bangalore", "id": "blr", "count": 80}]}]},
]
ALL = {"total": 900, "facets": FACETS, "jobPostings": [
    {"title": "Software Engineer", "externalPath": "/job/Austin/SE_R-1", "locationsText": "Austin, TX"}]}
INDIA = {"total": 1, "jobPostings": [
    {"title": "Software Engineer", "externalPath": "/job/Bangalore/SE_R-2", "locationsText": "Bangalore, India"}]}


def http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


class C(HttpClient):
    def __init__(self):
        super().__init__(min_interval=0)
        self.posts, self.pages = [], []

    def post_json(self, url, data, **kw):
        self.posts.append((url, data))
        return INDIA if data["appliedFacets"] else ALL

    def get_json(self, url, **kw):
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.pages.append(url)
        return "<html></html>"


class CountryFilter(unittest.TestCase):
    def test_finds_country_in_nested_facets(self):
        self.assertEqual(sources.country_facets(FACETS), {"locationCountry": ["in-id"]})

    def test_falls_back_to_india_cities(self):
        f = [{"facetParameter": "locations", "values": [{"descriptor": "Bengaluru, KA", "id": "a"},
                                                         {"descriptor": "Hyderabad", "id": "b"},
                                                         {"descriptor": "Austin", "id": "c"}]}]
        self.assertEqual(sources.country_facets(f), {"locations": ["a", "b"]})
        self.assertEqual(sources.country_facets([]), {})
        self.assertEqual(sources.country_facets(None), {})

    def test_search_uses_filter(self):
        c = C()
        jobs = sources.workday(c, "https://acme.wd5.myworkdayjobs.com/Ext", KEEP, searches=("software", "java"))
        self.assertEqual([j.location for j in jobs], ["Bangalore, India"])
        self.assertEqual([p[1]["appliedFacets"] for p in c.posts],
                         [{}, {"locationCountry": ["in-id"]}, {"locationCountry": ["in-id"]}])
        self.assertEqual(jobs.scanned, 2)      # only India postings were paged through

    def test_country_off(self):
        c = C()
        sources.workday(c, "https://acme.wd5.myworkdayjobs.com/Ext", KEEP, searches=("software",), country="")
        self.assertTrue(all(p[1]["appliedFacets"] == {} for p in c.posts))


class Http422(unittest.TestCase):
    def test_retries_after_visiting_careers_page(self):
        c = C()
        tries = []
        def post(url, data, **kw):
            tries.append(1)
            if len(tries) == 1:
                raise http_error(422)
            return INDIA
        c.post_json = post
        jobs = sources.workday(c, "https://walmart.wd5.myworkdayjobs.com/WalmartExternal", KEEP, searches=("software",))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(c.pages, ["https://walmart.wd5.myworkdayjobs.com/WalmartExternal"])

    def test_clear_error_when_still_422(self):
        c = C()
        def post(url, data, **kw):
            raise http_error(422, b'{"errorCode":"INVALID_SITE"}')
        c.post_json = post
        with self.assertRaises(ValueError) as cm:
            sources.workday(c, "https://walmart.wd5.myworkdayjobs.com/WalmartExternal", KEEP, searches=("software",))
        self.assertIn("HTTP 422", str(cm.exception))
        self.assertIn("INVALID_SITE", str(cm.exception))


class RateLimits(unittest.TestCase):
    def test_block_on_one_server_does_not_skip_others(self):
        class B(C):
            def post_json(self, url, data, **kw):
                self.posts.append((url, data))
                if "wd12" in url:
                    raise RateLimited("429")
                return INDIA
        entries = [{"name": "Salesforce", "ats": "workday", "id": "https://salesforce.wd12.myworkdayjobs.com/Ext"},
                   {"name": "PayPal", "ats": "workday", "id": "https://paypal.wd1.myworkdayjobs.com/jobs"}]
        b = B()
        _, errors, _ = engine.run_crawl(entries, {**CFG, "workers": 1, "workday_retry_wait_seconds": 0},
                                        log=lambda m: None, client=b)
        self.assertEqual([e[0] for e in errors], ["Salesforce (workday)"])
        self.assertTrue(any("paypal.wd1" in u for u, _ in b.posts))     # PayPal was still crawled

    def test_retry_pass_recovers(self):
        class Flaky(C):
            blocked_for = 1
            def post_json(self, url, data, **kw):
                self.posts.append((url, data))
                if Flaky.blocked_for:
                    Flaky.blocked_for -= 1
                    raise RateLimited("429")
                return INDIA
        logs = []
        c = Flaky()
        _, errors, scanned = engine.run_crawl(
            [{"name": "Mastercard", "ats": "workday", "id": "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers"}],
            {**CFG, "workday_retry_wait_seconds": 0.01}, log=logs.append, client=c)
        self.assertEqual(errors, [])
        self.assertEqual(scanned, 1)
        self.assertTrue(any("(retry)" in m and m.startswith("✓") for m in logs), logs)


if __name__ == "__main__":
    unittest.main()
