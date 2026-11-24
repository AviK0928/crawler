"""Recruitee, Workable, Breezy, Oracle Recruiting Cloud and Eightfold, with network faked."""
import unittest
from unittest import mock
from urllib.parse import unquote

from jobcrawler import engine, more_sources as ms, resolve, scoring
from jobcrawler.http import HttpClient, NotFound
from tests.test_app import CFG

STRICT = {**CFG, "max_years_experience": 1}
KEEP = lambda t, l: scoring.title_ok(t, CFG) and (l is None or scoring.location_ok(l, CFG))

RECRUITEE = {"offers": [
    {"id": 11, "slug": "java-developer", "title": "Java Developer", "city": "Pune", "country": "India",
     "careers_url": "https://acme.recruitee.com/o/java-developer", "published_at": "2026-11-01 10:00:00 UTC",
     "description": "<p>Spring Boot microservices</p>", "requirements": "<ul><li>0-1 years of experience</li></ul>",
     "locations": [{"city": "Pune", "state": "Maharashtra", "country": "India"}]},
    {"id": 12, "slug": "senior-dev", "title": "Senior Developer", "location": "Amsterdam, Netherlands"}]}

WORKABLE = {"name": "Acme Labs", "jobs": [
    {"title": "Software Engineer I (Backend)", "shortcode": "ABC123", "url": "https://apply.workable.com/acme/j/ABC123/",
     "published_on": "2026-11-05", "telecommuting": False, "experience": "Entry level",
     "description": "<p>Java, Kafka. 0-1 years experience.</p>",
     "locations": [{"country": "India", "countryCode": "IN", "city": "Bengaluru", "region": "Karnataka", "hidden": False}]}]}

BREEZY = [{"id": "x1", "friendly_id": "be-dev", "name": "Backend Developer (Java)", "url": "https://acme.breezy.hr/p/be-dev",
           "published_date": "2026-11-02T00:00:00Z",
           "location": {"city": "Hyderabad", "country": {"name": "India", "id": "IN"}, "is_remote": False}}]

ORACLE_LIST = {"items": [{"TotalJobsCount": 2, "requisitionList": [
    {"Id": "210600001", "Title": "Software Engineer I - Java", "PostedDate": "2026-11-10",
     "PrimaryLocation": "Bengaluru, Karnataka, India", "ShortDescriptionStr": "Java engineer",
     "secondaryLocations": [{"Name": "Hyderabad, Telangana, India"}]},
    {"Id": "210600002", "Title": "Software Engineer III", "PrimaryLocation": "Mumbai, India"}]}]}
ORACLE_DETAIL = {"items": [{"ExternalDescriptionStr": "<p>Build Java/Spring services.</p>",
                            "ExternalQualificationsStr": "<p>0-1 years of experience</p>"}]}

EF_LIST = {"count": 2, "positions": [
    {"id": 1970001, "name": "Software Engineer", "locations": ["Hyderabad, Telangana, India"],
     "t_create": 1762800000, "canonicalPositionUrl": "https://jobs.careers.microsoft.com/global/en/job/1970001"},
    {"id": 1970002, "name": "Principal Software Engineer", "locations": ["Bangalore, Karnataka, India"]}]}
EF_DETAIL = {"job_description": "<p>Java or C#. 0-1 years of experience. New grad welcome.</p>"}


class Fake(HttpClient):
    def __init__(self):
        super().__init__(min_interval=0)
        self.urls = []

    def get_json(self, url, **kw):
        self.urls.append(url)
        u = unquote(url)
        if u.startswith("https://acme.recruitee.com/api/offers/"):
            return RECRUITEE
        if u.startswith("https://apply.workable.com/api/v1/widget/accounts/acme"):
            return WORKABLE
        if u.startswith("https://acme.breezy.hr/json"):
            return BREEZY
        if u.startswith("https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?"):
            if "offset=0" in u and 'keyword="software' in u:
                return ORACLE_LIST
            if "limit=1" in u:
                return {"items": [{"TotalJobsCount": 4321}]}
            return {"items": [{"TotalJobsCount": 2, "requisitionList": []}]}
        if u.startswith("https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"):
            return ORACLE_DETAIL
        if u.startswith("https://apply.careers.microsoft.com/api/apply/v2/jobs/1970001"):
            return EF_DETAIL
        if u.startswith("https://apply.careers.microsoft.com/api/apply/v2/jobs?"):
            if "start=0" in u and "query=software" in u:
                return EF_LIST
            if "num=1" in u:
                return {"count": 999, "positions": []}
            return {"count": 2, "positions": []}
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.urls.append(url)
        if url.startswith("https://careers.acme-recruitee-custom.com"):
            return '<a href="https://acme.recruitee.com/o/java-developer">Jobs</a>'
        if url.startswith("https://apply.careers.microsoft.com"):
            return '<script>window.config = {"domain": "microsoft.com", "provider": "eightfold"}</script>'
        raise NotFound(url)


class Base(unittest.TestCase):
    def setUp(self):
        self.c = Fake()
        for t in ("jobcrawler.more_sources.assert_public_url", "jobcrawler.resolve._safe_public_url",
                  "jobcrawler.generic.assert_public_url"):
            p = mock.patch(t, lambda u: None); p.start(); self.addCleanup(p.stop)


class Parsing(Base):
    def test_recruitee(self):
        jobs = ms.recruitee(self.c, "acme", KEEP)
        self.assertEqual(jobs[0].location, "Pune, Maharashtra, India")
        self.assertIn("0-1 years", jobs[0].description)
        self.assertEqual(jobs[0].posted, "2026-11-01")

    def test_workable(self):
        [j] = ms.workable(self.c, "acme", KEEP)
        self.assertEqual((j.company, j.job_id, j.location), ("Acme Labs", "ABC123", "Bengaluru, Karnataka, India"))
        self.assertTrue(scoring.score(j, STRICT))

    def test_breezy(self):
        [j] = ms.breezy(self.c, "acme", KEEP)
        self.assertEqual(j.location, "Hyderabad, India")

    def test_oracle(self):
        jobs = ms.oracle(self.c, "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs",
                         KEEP, searches=("software engineer",))
        self.assertEqual(jobs.scanned, 2)
        [j] = jobs                                         # Engineer III filtered by title
        self.assertEqual(j.url, "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210600001")
        self.assertIn("0-1 years", j.description)          # came from the detail call
        self.assertIn("Hyderabad", j.location)
        self.assertTrue(scoring.score(j, STRICT))

    def test_eightfold(self):
        jobs = ms.eightfold(self.c, {"url": "https://apply.careers.microsoft.com", "domain": "microsoft.com"},
                            KEEP, searches=("software engineer",))
        [j] = jobs                                         # Principal filtered
        self.assertEqual((j.company, j.posted), ("microsoft", "2025-11-10"))
        self.assertIn("New grad", j.description)
        self.assertTrue(scoring.score(j, STRICT))

    def test_bad_slugs_rejected(self):
        for bad in ("evil.com/#", "x@169.254.169.254", "../x", ""):
            with self.assertRaises(ValueError):
                ms.recruitee(self.c, bad, KEEP)
        with self.assertRaises(ValueError):
            ms.eightfold(self.c, {"url": "https://a.com", "domain": "x/../y?"}, KEEP)


class Resolving(Base):
    def test_links(self):
        cases = {
            "https://acme.recruitee.com/o/java-developer": ("recruitee", "acme"),
            "https://apply.workable.com/acme/j/ABC123/": ("workable", "acme"),
            "https://acme.breezy.hr/p/be-dev": ("breezy", "acme"),
            "https://careers.acme-recruitee-custom.com/": ("recruitee", "acme"),     # embedded on own domain
        }
        for link, want in cases.items():
            e = resolve.resolve(link, self.c)
            self.assertEqual((e["ats"], e["id"]), want, link)

    def test_oracle_link(self):
        e = resolve.resolve("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210600001", self.c)
        self.assertEqual(e["ats"], "oracle")
        self.assertEqual(e["jobs"], 4321)

    def test_eightfold_site(self):
        e = resolve.resolve("https://apply.careers.microsoft.com/careers?hl=en", self.c)
        self.assertEqual((e["ats"], e["id"]), ("eightfold", {"url": "https://apply.careers.microsoft.com", "domain": "microsoft.com"}))


class EngineRun(Base):
    def test_all_new_sources_together(self):
        entries = [
            {"name": "Acme R", "ats": "recruitee", "id": "acme"},
            {"name": "Acme W", "ats": "workable", "id": "acme"},
            {"name": "Acme B", "ats": "breezy", "id": "acme"},
            {"name": "JPMorgan Chase", "ats": "oracle", "id": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"},
            {"name": "Microsoft", "ats": "eightfold", "id": {"url": "https://apply.careers.microsoft.com", "domain": "microsoft.com"}},
        ]
        logs = []
        matches, errors, _ = engine.run_crawl(entries, STRICT, log=logs.append, client=self.c)
        self.assertEqual(errors, [])
        self.assertEqual({j.company for j in matches}, {"Acme R", "Acme W", "Acme B", "JPMorgan Chase", "Microsoft"})
        strict_stated = engine.run_crawl(entries, {**STRICT, "require_stated_experience": True}, log=lambda m: None, client=self.c)[0]
        self.assertNotIn("Acme B", {j.company for j in strict_stated})
        # Breezy job has no experience info -> kept only when unstated experience allowed (it is, by default)
        self.assertTrue(any("Microsoft (eightfold)" in l and "scanned" in l for l in logs))


if __name__ == "__main__":
    unittest.main()


class Aggregators(Base):
    WS = {"nextPageToken": "p2", "jobs": [
        {"id": "u-1", "title": "Junior Java Developer", "company": {"title": "Tiny Startup Pvt Ltd"},
         "locations": ["Pune, Maharashtra, India"], "created": "2026-11-14T08:00:00Z",
         "url": "https://jobs.workable.com/view/u-1", "description": "<p>0-1 years experience, Spring Boot</p>"},
        {"id": "u-2", "title": "Senior Java Developer", "company": {"title": "Other Co"},
         "location": {"city": "Noida", "subregion": "UP", "countryName": "India"}}]}
    WS2 = {"jobs": [{"id": "u-3", "title": "Software Engineer", "company": {"title": "SmallCo"},
                     "location": {"city": "Kochi", "countryName": "India"}, "description": "Java, 1 year of experience"}]}
    def setUp(self):
        super().setUp()
        outer = self
        orig = self.c.get_json
        def gj(url, **kw):
            self.c.urls.append(url)
            if url.startswith("https://jobs.workable.com/api/v1/jobs"):
                return outer.WS2 if "pageToken=p2" in url else outer.WS
            return orig(url, **kw)
        self.c.get_json = gj

    def test_workable_search_pages_and_companies(self):
        jobs = ms.workable_search(self.c, {"query": "java", "location": "India"}, KEEP)
        self.assertEqual(jobs.scanned, 3)
        self.assertEqual([(j.company, j.title) for j in jobs],
                         [("Tiny Startup Pvt Ltd", "Junior Java Developer"), ("SmallCo", "Software Engineer")])
        self.assertEqual(jobs[1].location, "Kochi, India")

    def test_engine_keeps_each_jobs_company_for_aggregators(self):
        matches, errors, _ = engine.run_crawl(
            [{"name": "Workable search: java", "ats": "workable_search", "id": {"query": "java", "location": "India"}}],
            STRICT, log=lambda m: None, client=self.c)
        self.assertEqual(errors, [])
        self.assertEqual(sorted({j.company for j in matches}), ["SmallCo", "Tiny Startup Pvt Ltd"])
