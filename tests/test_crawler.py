import json
import os
import tempfile
import unittest

from jobcrawler import scoring, sources
from jobcrawler.models import Job

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))


def job(title, location="Bengaluru, India", desc=""):
    return Job("greenhouse", "acme", title, title, location, "https://x/" + title, desc)


class TitleFilter(unittest.TestCase):
    def test_keeps_entry_titles(self):
        for t in ["Software Engineer", "Software Development Engineer", "SDE-1", "SDE I",
                  "Backend Engineer (Java)", "Java Developer", "Software Engineer I",
                  "Associate Software Engineer", "New Grad Software Engineer 2026"]:
            self.assertTrue(scoring.title_ok(t, CFG), t)

    def test_drops_senior_and_levels(self):
        for t in ["Senior Software Engineer", "Sr. Java Developer", "Staff Engineer", "SDE II",
                  "Software Dev Engineer II", "SDE-2", "Software Engineer 3", "Engineering Manager",
                  "Principal Engineer", "Tech Lead - Backend", "Solutions Architect"]:
            self.assertFalse(scoring.title_ok(t, CFG), t)

    def test_drops_non_swe(self):
        for t in ["Account Executive", "Product Designer", "Recruiter, Engineering",
                  "Customer Success Engineer"]:
            self.assertFalse(scoring.title_ok(t, CFG), t)

    def test_internships_configurable(self):
        self.assertFalse(scoring.title_ok("Software Engineering Intern", CFG))
        self.assertTrue(scoring.title_ok("Software Engineering Intern", {**CFG, "include_internships": True}))


class Location(unittest.TestCase):
    def test_india(self):
        for l in ["Bengaluru, Karnataka, India", "Gurugram", "Remote - India", "Bengaluru, KA, IND",
                  "Pune, Maharashtra, India / Hyderabad"]:
            self.assertTrue(scoring.location_ok(l, CFG), l)

    def test_not_india(self):
        for l in ["San Francisco, CA", "Indianapolis, IN", "Remote - US", "Independence, KS", "London"]:
            self.assertFalse(scoring.location_ok(l, CFG), l)


class Years(unittest.TestCase):
    def test_parse(self):
        cases = {
            "3+ years of experience in Java": 3,
            "1-3 years of professional software development experience": 1,
            "Minimum of 5 years experience": 5,
            "0 to 2 years of hands-on experience": 0,
            "2+ yrs exp in backend": 2,
            "We are 10 years old. Experience with Java is a plus": None,
            "1+ years of non-internship professional software development experience": 1,
        }
        for text, want in cases.items():
            self.assertEqual(scoring.min_years(text), want, text)


class Scoring(unittest.TestCase):
    def test_great_match_scores_high(self):
        j = job("Software Engineer I - Backend",
                desc="0-2 years of experience. Java, Spring Boot, PostgreSQL, Kafka, REST APIs, microservices.")
        self.assertTrue(scoring.score(j, CFG))
        self.assertGreaterEqual(j.score, 90)

    def test_too_much_experience_dropped(self):
        j = job("Software Engineer", desc="5+ years of experience building Java services")
        self.assertFalse(scoring.score(j, CFG))

    def test_non_java_low_score_or_dropped(self):
        j = job("Software Engineer", desc="Build iOS apps in Swift.")
        self.assertFalse(scoring.score(j, CFG))

    def test_require_java(self):
        j = job("Software Engineer", desc="Python, Django, SQL, AWS, data structures")
        self.assertFalse(scoring.score(j, {**CFG, "require_java": True}))

    def test_wrong_location(self):
        j = job("Java Developer", location="Austin, TX", desc="Java")
        self.assertFalse(scoring.score(j, CFG))


class Parsers(unittest.TestCase):
    def test_greenhouse(self):
        data = {"jobs": [{"id": 42, "title": "Software Engineer", "absolute_url": "https://gh/42",
                          "location": {"name": "Bengaluru, India"}, "updated_at": "2026-11-10T00:00:00Z",
                          "content": "&lt;p&gt;Java &amp;amp; Spring&lt;/p&gt;&lt;ul&gt;&lt;li&gt;1+ years experience&lt;/li&gt;&lt;/ul&gt;"}]}
        [j] = sources.parse_greenhouse(data, "acme")
        self.assertEqual(j.key, "greenhouse:acme:42")
        self.assertIn("Java & Spring", j.description)
        self.assertNotIn("<", j.description)
        self.assertEqual(j.posted, "2026-11-10")

    def test_lever(self):
        data = [{"id": "abc", "text": "Backend Engineer", "hostedUrl": "https://lv/abc",
                 "categories": {"location": "Bangalore", "allLocations": ["Bangalore", "Pune"]},
                 "descriptionPlain": "Build services.", "createdAt": 1762732800000,
                 "lists": [{"text": "Requirements", "content": "<li>Java</li><li>0-1 years experience</li>"}]}]
        [j] = sources.parse_lever(data, "acme")
        self.assertEqual(j.location, "Bangalore / Pune")
        self.assertIn("Java", j.description)
        self.assertEqual(scoring.min_years(j.description), 0)

    def test_ashby(self):
        data = {"jobs": [{"id": "u1", "title": "SWE", "location": "Remote", "isRemote": True,
                          "secondaryLocations": [{"location": "Bengaluru"}], "jobUrl": "https://ab/u1",
                          "descriptionPlain": "Kotlin", "publishedAt": "2026-11-01T10:00:00Z"},
                         {"id": "u2", "title": "Hidden", "isListed": False}]}
        jobs = sources.parse_ashby(data, "acme")
        self.assertEqual(len(jobs), 1)
        self.assertIn("Bengaluru", jobs[0].location)

    def test_smartrecruiters(self):
        p = {"id": "999", "name": "Software Engineer", "releasedDate": "2026-11-12T00:00:00.000Z",
             "location": {"city": "Bengaluru", "region": "KA", "country": "in", "remote": False}}
        j = sources.parse_smartrecruiters_summary(p, "Visa")
        self.assertEqual(j.location, "Bengaluru, KA, India")
        self.assertTrue(scoring.location_ok(j.location, CFG))
        d = {"jobAd": {"sections": {"jobDescription": {"text": "<p>Java</p>"},
                                    "qualifications": {"text": "<p>1+ years experience</p>"}}}}
        self.assertIn("1+ years", sources.parse_smartrecruiters_detail(d))

    def test_amazon(self):
        data = {"hits": 1, "jobs": [{
            "id_icims": "3012345", "title": "Software Development Engineer",
            "normalized_location": "Bengaluru, KA, IND", "job_path": "/en/jobs/3012345/sde",
            "description": "Build scalable systems.",
            "basic_qualifications": "- 1+ years of non-internship professional software development experience<br/>- Experience programming with at least one software programming language such as Java",
            "posted_date": "November 10, 2026"}]}
        [j] = sources.parse_amazon(data)
        self.assertEqual(j.url, "https://www.amazon.jobs/en/jobs/3012345/sde")
        self.assertTrue(scoring.score(j, CFG))
        self.assertEqual(j.years_min, 1)


class FakeClient:
    def __init__(self, pages): self.pages, self.calls = pages, []
    def get_json(self, url):
        self.calls.append(url)
        for k, v in self.pages.items():
            if k in url:
                return v
        raise AssertionError("unexpected " + url)


class SmartRecruitersFetch(unittest.TestCase):
    def test_only_fetches_details_for_prefiltered(self):
        client = FakeClient({
            "postings?limit=100&offset=0": {"totalFound": 2, "content": [
                {"id": "1", "name": "Software Engineer", "location": {"city": "Pune", "country": "in"}},
                {"id": "2", "name": "Sales Manager", "location": {"city": "Pune", "country": "in"}}]},
            "postings/1": {"jobAd": {"sections": {"jobDescription": {"text": "Java"}}}},
        })
        out = sources.smartrecruiters(client, "Acme", lambda t, l: scoring.prefilter(t, l, CFG))
        self.assertEqual([j.job_id for j in out], ["1"])
        self.assertFalse(any("postings/2" in c for c in client.calls))


if __name__ == "__main__":
    unittest.main()
