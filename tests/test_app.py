"""Tests for Workday, link resolving, the crawl engine, exports and the web app,
with all network calls faked."""
import io
import json
import time
import unittest
from unittest import mock

from openpyxl import load_workbook

import app as webapp
from jobcrawler import engine, resolve, sources
from jobcrawler.export import to_csv, to_xlsx
from jobcrawler.http import HttpClient, NotFound

GH_STRIPE = {"jobs": [
    {"id": 1, "title": "Software Engineer I, Backend", "absolute_url": "https://stripe.com/jobs/1",
     "location": {"name": "Bengaluru, India"}, "updated_at": "2026-11-14T00:00:00Z",
     "content": "&lt;p&gt;0-2 years experience. Java, Spring Boot, Kafka, PostgreSQL&lt;/p&gt;"},
    {"id": 2, "title": "Senior Software Engineer", "absolute_url": "https://stripe.com/jobs/2",
     "location": {"name": "Bengaluru, India"}, "content": "Java"},
    {"id": 3, "title": "Software Engineer", "absolute_url": "https://stripe.com/jobs/3",
     "location": {"name": "Seattle, WA"}, "content": "Java"},
    {"id": 4, "title": "Software Engineer", "absolute_url": "https://stripe.com/jobs/4",
     "location": {"name": "Hyderabad, India"}, "content": "4+ years of experience with Go"}]}

WD_LIST = {"total": 3, "jobPostings": [
    {"title": "Software Engineer", "externalPath": "/job/Bangalore/Software-Engineer_R-100",
     "locationsText": "Bangalore, India", "postedOn": "Posted 2 Days Ago"},
    {"title": "Software Engineer III", "externalPath": "/job/Bangalore/SE-III_R-101",
     "locationsText": "Bangalore, India"},
    {"title": "Java Developer", "externalPath": "/job/Multi/Java-Developer_R-102",
     "locationsText": "2 Locations"}]}
WD_DETAIL = {
    "/job/Bangalore/Software-Engineer_R-100": {"jobPostingInfo": {
        "jobDescription": "<p>0-1 years experience. Java, Spring Boot, microservices</p>",
        "location": "Bangalore, India", "externalUrl": "https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal/job/R-100"}},
    "/job/Multi/Java-Developer_R-102": {"jobPostingInfo": {
        "jobDescription": "<p>1+ years of experience in Java</p>",
        "location": "Chennai, India", "additionalLocations": ["Austin, TX"]}},
}


class FakeClient(HttpClient):
    """Routes URLs to canned JSON. Anything unknown is a 404."""
    def __init__(self):
        super().__init__(min_interval=0)
        self.calls = []

    def get_json(self, url, **kw):
        self.calls.append(("GET", url))
        if "greenhouse.io/v1/boards/stripe/" in url:
            return GH_STRIPE
        if "api.lever.co/v0/postings/razorpay" in url:
            return [{"id": "x", "text": "SDE 1", "hostedUrl": "https://jobs.lever.co/razorpay/x",
                     "categories": {"location": "Bangalore"}, "descriptionPlain": "Java 0-1 years experience"}]
        if "amazon.jobs" in url:
            if "offset=0" in url:
                return {"hits": 1, "jobs": [{"id_icims": "77", "title": "Software Development Engineer",
                        "normalized_location": "Bengaluru, KA, IND", "job_path": "/en/jobs/77",
                        "basic_qualifications": "1+ years of non-internship professional software development experience; Java"}]}
            return {"hits": 1, "jobs": []}
        if "myworkdayjobs.com/wday/cxs/walmart/WalmartExternal/job/" in url:
            path = url.split("/WalmartExternal", 1)[1]
            if path in WD_DETAIL:
                return WD_DETAIL[path]
        raise NotFound(url)

    def post_json(self, url, data, **kw):
        self.calls.append(("POST", url))
        if url == "https://walmart.wd5.myworkdayjobs.com/wday/cxs/walmart/WalmartExternal/jobs":
            if data["offset"] == 0:
                return WD_LIST if data["limit"] > 1 else {"total": 1234, "jobPostings": []}
            return {"total": 0, "jobPostings": []}
        raise NotFound(url)

    def get_text(self, url, max_bytes=0, **kw):
        self.calls.append(("PAGE", url))
        if "razorpay.com/jobs" in url:
            return '<html><a href="https://jobs.lever.co/razorpay/abc">Apply</a></html>'
        if "example-company.com/careers" in url:
            return '<script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe"></script>'
        return "<html>nothing here</html>"


CFG = json.load(open(webapp.os.path.join(webapp.ROOT, "config.json")))


class Workday(unittest.TestCase):
    def test_url_parsing(self):
        self.assertEqual(sources.parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x"),
                         ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"))
        self.assertEqual(sources.parse_workday_url("walmart.wd5.myworkdayjobs.com/WalmartExternal"),
                         ("walmart.wd5.myworkdayjobs.com", "walmart", "WalmartExternal"))
        with self.assertRaises(ValueError):
            sources.parse_workday_url("https://nvidia.wd5.myworkdayjobs.com/en-US/")

    def test_fetch_filters_and_multi_location(self):
        c = FakeClient()
        keep = lambda t, l: engine.scoring.title_ok(t, CFG) and (l is None or engine.scoring.location_ok(l, CFG))
        jobs = sources.workday(c, "https://walmart.wd5.myworkdayjobs.com/WalmartExternal", keep, searches=("software",))
        self.assertEqual(sorted(j.title for j in jobs), ["Java Developer", "Software Engineer"])
        multi = next(j for j in jobs if j.title == "Java Developer")
        self.assertEqual(multi.location, "Chennai, India / Austin, TX")   # filled in from detail
        self.assertFalse(any("SE-III" in u for _, u in c.calls))          # never fetched the senior job


class Robustness(unittest.TestCase):
    def test_workday_keeps_partial_results_when_rate_limited(self):
        from jobcrawler.http import RateLimited
        c = FakeClient()
        orig = c.post_json
        def flaky(url, data, **kw):
            if data["searchText"] == "java":
                raise RateLimited("429")
            return orig(url, data, **kw)
        c.post_json = flaky
        keep = lambda t, l: engine.scoring.title_ok(t, CFG) and (l is None or engine.scoring.location_ok(l, CFG))
        jobs = sources.workday(c, "https://walmart.wd5.myworkdayjobs.com/WalmartExternal", keep,
                               searches=("software engineer", "java"))
        self.assertEqual(len(jobs), 2)   # first search's results survive the 429 on the second

    def test_rate_limit_reported_cleanly(self):
        from jobcrawler.http import RateLimited
        c = FakeClient()
        def blocked(url, data, **kw):
            raise RateLimited("429")
        c.post_json = blocked
        _, errors, _ = engine.run_crawl([{"name": "NVIDIA", "ats": "workday",
                                          "id": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"}],
                                        CFG, log=lambda m: None, client=c)
        self.assertIn("rate-limited", errors[0][1])

    def test_smartrecruiters_country_filter(self):
        c = FakeClient()
        c.get_json = lambda url, **kw: (c.calls.append(url), {"totalFound": 0, "content": []})[1]
        sources.smartrecruiters(c, "Visa", lambda t, l: True, country="in")
        self.assertIn("&country=in", c.calls[0])

    def test_http_client_retries_429_then_raises(self):
        import urllib.error
        from jobcrawler.http import RateLimited
        hc = HttpClient(min_interval=0, retries=3)
        n = {"calls": 0}
        def opener(req, timeout):
            n["calls"] += 1
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "0"}, None)
        hc._opener.open = opener
        with self.assertRaises(RateLimited):
            hc.get_json("https://example.com/x")
        self.assertEqual(n["calls"], 3)


class Resolve(unittest.TestCase):
    def setUp(self):
        self.c = FakeClient()
        # pretend every hostname is public so the SSRF guard doesn't need DNS
        p = mock.patch.object(resolve, "_safe_public_url", lambda u: None)
        p.start(); self.addCleanup(p.stop)

    def test_find_boards(self):
        self.assertEqual(resolve.find_boards("https://boards.greenhouse.io/embed/job_board?for=acme&b=x"), [("greenhouse", "acme")])
        self.assertEqual(resolve.find_boards("https://job-boards.greenhouse.io/acme/jobs/123"), [("greenhouse", "acme")])
        self.assertEqual(resolve.find_boards("https://jobs.lever.co/cred/abc-123"), [("lever", "cred")])
        self.assertEqual(resolve.find_boards("https://jobs.ashbyhq.com/zepto/abc"), [("ashby", "zepto")])
        self.assertEqual(resolve.find_boards("https://jobs.smartrecruiters.com/Visa/7444"), [("smartrecruiters", "Visa")])
        self.assertEqual(resolve.find_boards("https://paypal.wd1.myworkdayjobs.com/en-US/jobs/job/x"),
                         [("workday", "https://paypal.wd1.myworkdayjobs.com/en-US/jobs")])

    def test_direct_board_link(self):
        e = resolve.resolve("https://boards.greenhouse.io/stripe", self.c)
        self.assertEqual((e["ats"], e["id"], e["jobs"]), ("greenhouse", "stripe", 4))

    def test_job_link_resolves_to_board(self):
        e = resolve.resolve("https://jobs.lever.co/razorpay/some-job-id", self.c)
        self.assertEqual((e["ats"], e["id"]), ("lever", "razorpay"))

    def test_company_careers_page_with_embedded_board(self):
        e = resolve.resolve("https://razorpay.com/jobs/", self.c)
        self.assertEqual((e["ats"], e["id"]), ("lever", "razorpay"))
        e = resolve.resolve("https://www.example-company.com/careers", self.c)
        self.assertEqual((e["ats"], e["id"]), ("greenhouse", "stripe"))

    def test_workday_link(self):
        e = resolve.resolve("https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal/job/Bangalore/X_R-1", self.c)
        self.assertEqual(e["ats"], "workday")
        self.assertEqual(e["jobs"], 1234)

    def test_company_name(self):
        self.assertEqual(resolve.resolve("Stripe", self.c)["id"], "stripe")
        self.assertEqual(resolve.resolve("amazon", self.c)["ats"], "amazon")

    def test_unknown(self):
        with self.assertRaises(ValueError):
            resolve.resolve("https://nothing-here.org/careers", self.c)
        with self.assertRaises(ValueError):
            resolve.resolve("Totally Unknown Co", self.c)

    def test_ssrf_guard(self):
        mock.patch.stopall()
        for bad in ("http://127.0.0.1/admin", "http://localhost:8000/", "http://169.254.169.254/latest", "file:///etc/passwd"):
            with self.assertRaises(ValueError, msg=bad):
                resolve._safe_public_url(bad)


class EngineAndExport(unittest.TestCase):
    def test_run_crawl(self):
        entries = [{"name": "Stripe", "ats": "greenhouse", "id": "stripe"},
                   {"name": "Amazon", "ats": "amazon", "id": {"base_query": "java", "country": "IND"}},
                   {"name": "Walmart", "ats": "workday", "id": "https://walmart.wd5.myworkdayjobs.com/WalmartExternal"},
                   {"name": "Ghost", "ats": "lever", "id": "doesnotexist"}]
        logs = []
        matches, errors, scanned = engine.run_crawl(entries, CFG, log=logs.append, client=FakeClient())
        titles = [(j.company, j.title) for j in matches]
        self.assertIn(("Stripe", "Software Engineer I, Backend"), titles)
        self.assertIn(("Amazon", "Software Development Engineer"), titles)
        self.assertIn(("Walmart", "Software Engineer"), titles)
        self.assertNotIn(("Stripe", "Senior Software Engineer"), titles)
        self.assertEqual(matches[0].title, "Software Engineer I, Backend")      # sorted by score
        self.assertEqual([e[0] for e in errors], ["Ghost (lever)"])

        x = to_xlsx(matches, errors)
        wb = load_workbook(io.BytesIO(x))
        ws = wb["Jobs"]
        self.assertEqual(ws["C1"].value, "Title")
        self.assertEqual(ws.max_row, len(matches) + 1)
        self.assertTrue(ws["C2"].hyperlink.target.startswith("https://"))
        self.assertIn("Sources that failed", wb.sheetnames)
        csv_text = to_csv(matches).decode("utf-8-sig")
        self.assertTrue(csv_text.startswith("Score,Company,Title"))


class WebApp(unittest.TestCase):
    def setUp(self):
        webapp.RUNS.clear()
        self.client = webapp.app.test_client()
        p1 = mock.patch.object(webapp, "HttpClient", lambda **kw: FakeClient())
        p2 = mock.patch("jobcrawler.engine.HttpClient", lambda **kw: FakeClient())
        p3 = mock.patch.object(resolve, "_safe_public_url", lambda u: None)
        for p in (p1, p2, p3):
            p.start(); self.addCleanup(p.stop)

    def test_index_and_defaults(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Java SDE Job Finder", r.data)
        r.close()
        d = self.client.get("/api/defaults").get_json()
        self.assertGreater(len(d["companies"]), 10)
        self.assertTrue(all("key" in c for c in d["companies"]))
        self.assertIn("india", d["settings"]["locations"])

    def test_resolve_endpoint(self):
        r = self.client.post("/api/resolve", json={"input": "https://jobs.lever.co/razorpay"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.get_json()["key"], "lever:razorpay")
        r = self.client.post("/api/resolve", json={"input": ""})
        self.assertEqual(r.status_code, 400)

    def test_crawl_poll_download(self):
        body = {"sources": [{"name": "Stripe", "ats": "greenhouse", "id": "stripe"},
                            {"name": "Razorpay", "ats": "lever", "id": "razorpay"},
                            {"bogus": True}],
                "settings": {"max_years_experience": 2, "require_java": True, "min_score": 10}}
        rid = self.client.post("/api/crawl", json=body).get_json()["id"]
        for _ in range(100):
            st = self.client.get(f"/api/crawl/{rid}").get_json()
            if st["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(st["status"], "done", st)
        self.assertEqual({j["company"] for j in st["jobs"]}, {"Stripe", "Razorpay"})

        r = self.client.get(f"/api/crawl/{rid}/download.xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(load_workbook(io.BytesIO(r.data))["Jobs"].max_row, 3)

        hide = [st["jobs"][0]["key"]]
        r = self.client.post(f"/api/crawl/{rid}/download.csv", json={"hide": hide})
        self.assertEqual(r.data.decode("utf-8-sig").strip().count("\n"), 1)   # header + 1 row

    def test_bad_requests(self):
        self.assertEqual(self.client.post("/api/crawl", json={"sources": []}).status_code, 400)
        self.assertEqual(self.client.get("/api/crawl/nope").status_code, 404)

    def test_password(self):
        with mock.patch.object(webapp, "PASSWORD", "s3cret"):
            self.assertEqual(self.client.get("/api/defaults").status_code, 401)
            self.assertEqual(self.client.get("/healthz").status_code, 200)
            import base64
            h = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
            self.assertEqual(self.client.get("/api/defaults", headers=h).status_code, 200)


if __name__ == "__main__":
    unittest.main()
