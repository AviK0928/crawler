"""Keka, Himalayas and HN Who is hiring sources, network faked with the real response shapes."""
import unittest

from jobcrawler import boards, engine, resolve, scoring
from jobcrawler.http import HttpClient, NotFound
from tests.test_app import CFG

STRICT = {**CFG, "max_years_experience": 1}
KEEP = lambda t, l: scoring.title_ok(t, CFG) and (l is None or scoring.location_ok(l, CFG))


def keka_job(i, title, city, country, exp, desc="Java, Spring Boot, REST APIs."):
    return {"id": i, "title": title, "description": f"<div><p>{desc}</p></div>", "departmentName": "Engineering",
            "excerpt": desc[:40], "jobType": 2, "experience": exp, "jobNumber": f"ACME/{i}",
            "jobLocations": [{"id": 1, "name": city, "city": city, "state": "TS", "countryCode": "IN" if country == "India" else "RP",
                              "countryName": country}],
            "salaryRange": {"currency": "INR", "salaryPeriod": 0}, "publishedOn": "2026-11-24T05:14:19.903Z",
            "publishedSinceDays": 1, "skillNames": []}


KEKA = [keka_job(1, "Associate Software Engineer", "Hyderabad", "India", "0-1"),
        keka_job(2, "Software Engineer", "Hyderabad", "India", "2-4"),
        keka_job(3, "Software Engineer", "Manila", "Philippines", "0-1"),
        keka_job(4, "Senior Customer Success Manager", "Hyderabad", "India", "5-8")]

HIMALAYAS = {"updatedAt": 1794189851, "limit": 20, "totalCount": 3, "jobs": [
    {"title": "Junior Backend Developer (Java)", "companyName": "Acme", "locationRestrictions": ["India"],
     "seniority": ["Entry-level"], "employmentType": "Full Time", "pubDate": 1794189851,
     "description": "<p>0-1 years. Java, Spring Boot.</p>", "applicationLink": "https://himalayas.app/companies/acme/jobs/jbd?utm_source=api",
     "guid": "https://himalayas.app/companies/acme/jobs/jbd"},
    {"title": "Software Engineer, AI Coding Tasks (Contract)", "companyName": "Askable", "locationRestrictions": [],
     "seniority": ["Mid-level"], "employmentType": "Contractor", "pubDate": 1792198336, "description": "<p>Python</p>",
     "applicationLink": "https://himalayas.app/companies/askable/jobs/se?utm_source=api",
     "guid": "https://himalayas.app/companies/askable/jobs/se"},
    {"title": "Software Engineer", "companyName": "USCo", "locationRestrictions": ["United States"],
     "seniority": ["Mid-level"], "pubDate": 1792198336, "description": "",
     "applicationLink": "https://himalayas.app/companies/usco/jobs/se?utm_source=api", "guid": "g3"}]}

HN_SEARCH = {"hits": [{"title": "Ask HN: Who wants to be hired? (November 2026)", "objectID": "999"},
                      {"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "1000",
                       "created_at": "2026-11-02T16:00:00.000Z"}]}
HN_ITEM = {"id": 1000, "children": [
    {"id": 1, "author": "a", "created_at": "2026-11-02T17:00:00.000Z",
     "text": "Acme (YC W24) | Backend Engineer, Frontend Engineer | Bengaluru, India | Full-time<p>We use Java and Spring. New grads welcome.</p>"},
    {"id": 2, "author": "b", "text": "BigCo | Senior Software Engineer | Remote (US)<p>10+ years</p>"},
    {"id": 3, "author": "c", "text": "SalesCo | Account Executive | Bangalore<p>x</p>"},
    {"id": 4, "author": None, "text": None},
]}


class C(HttpClient):
    def __init__(self, keka_new=True):
        super().__init__(min_interval=0)
        self.keka_new, self.fetched = keka_new, []

    def get_json(self, url, **kw):
        self.fetched.append(url)
        if url == "https://acme.keka.com/careers/api/jobs/default/active":
            if self.keka_new:
                return KEKA
            raise NotFound(url)
        if url == "https://acme.keka.com/careers/api/embedjobs/default/active/0b1f2c3d-1111-2222-3333-444455556666":
            return KEKA
        if url.startswith("https://himalayas.app/jobs/api/search?"):
            return HIMALAYAS if "page=1" in url else {"jobs": [], "totalCount": 3}
        if url.startswith("https://hn.algolia.com/api/v1/search_by_date"):
            return HN_SEARCH
        if url == "https://hn.algolia.com/api/v1/items/1000":
            return HN_ITEM
        raise NotFound(url)

    def get_text(self, url, **kw):
        self.fetched.append(url)
        if url == "https://acme.keka.com/careers/":
            return '<link href="/ats/documents/0b1f2c3d-1111-2222-3333-444455556666/careerportal/x.css">'
        raise NotFound(url)

    def post_json(self, url, data, **kw):
        raise NotFound(url)


class Keka(unittest.TestCase):
    def test_host(self):
        for s in ("acme", "acme.keka.com", "https://acme.keka.com/careers/jobdetails/5"):
            self.assertEqual(boards.keka_host(s), "acme.keka.com")
        with self.assertRaises(ValueError):
            boards.keka_host("https://www.keka.com/careers")

    def test_fetch_and_filter(self):
        jobs = boards.keka(C(), "acme", KEEP)
        self.assertEqual(jobs.scanned, 4)
        self.assertEqual([j.job_id for j in jobs], ["1", "2"])        # Manila + non-SWE dropped
        j = jobs[0]
        self.assertEqual((j.location, j.url, j.posted), ("Hyderabad, TS, India",
                                                        "https://acme.keka.com/careers/jobdetails/1", "2026-11-24"))
        self.assertTrue(j.description.startswith("Experience: 0-1 years."))

    def test_strict_experience(self):
        jobs = boards.keka(C(), "acme", KEEP)
        self.assertEqual([j.title for j in jobs if scoring.score(j, STRICT)], ["Associate Software Engineer"])

    def test_older_portal_uuid(self):
        self.assertEqual(len(boards.keka(C(keka_new=False), "acme", KEEP)), 2)

    def test_resolve_link(self):
        self.assertEqual(resolve.find_boards("https://acme.keka.com/careers/jobdetails/12"), [("keka", "acme")])
        self.assertEqual(resolve.find_boards("https://www.keka.com/careers"), [])
        e = resolve.resolve("https://acme.keka.com/careers/", C())
        self.assertEqual((e["ats"], e["id"], e["jobs"]), ("keka", "acme", 4))


class Himalayas(unittest.TestCase):
    def test_search(self):
        c = C()
        jobs = boards.himalayas(c, {"query": "java", "country": "IN"}, KEEP)
        self.assertIn("q=java", c.fetched[0]); self.assertIn("country=IN", c.fetched[0])
        self.assertEqual(jobs.scanned, 3)
        self.assertEqual([j.company for j in jobs], ["Acme (via Himalayas)", "Askable (via Himalayas)"])
        self.assertEqual(jobs[0].location, "Remote (India)")
        self.assertEqual(jobs[1].location, "Remote (worldwide, open to India)")
        self.assertTrue(jobs[0].url.startswith("https://himalayas.app/companies/acme/jobs/"))  # Himalayas' apply link
        self.assertEqual(jobs[0].posted, "2026-11-09")


class HimalayasKeyAndLimits(unittest.TestCase):
    def test_sends_key_when_set(self):
        from unittest import mock
        seen = []
        class K(C):
            def get_json(self, url, headers=None, **kw):
                seen.append(headers)
                return super().get_json(url, **kw)
        with mock.patch.dict("os.environ", {"HIMALAYAS_API_KEY": "abc"}):
            boards.himalayas(K(), {"query": "java"}, KEEP)
        self.assertEqual(seen[0], {"Authorization": "Bearer abc"})
        seen.clear()
        with mock.patch.dict("os.environ", {"HIMALAYAS_API_KEY": ""}):
            boards.himalayas(K(), {"query": "java"}, KEEP)
        self.assertIsNone(seen[0])

    def test_keeps_first_page_when_rate_limited_later(self):
        from jobcrawler.http import RateLimited
        class L(C):
            def get_json(self, url, **kw):
                if "page=2" in url:
                    raise RateLimited("429")
                data = dict(HIMALAYAS, totalCount=99)
                return data
        jobs = boards.himalayas(L(), {"query": "java"}, KEEP)
        self.assertEqual(jobs.scanned, 3)

    def test_rate_limited_first_page_raises(self):
        from jobcrawler.http import RateLimited
        class R(C):
            def get_json(self, url, **kw):
                raise RateLimited("429")
        with self.assertRaises(RateLimited):
            boards.himalayas(R(), {"query": "java"}, KEEP)


class HNHiring(unittest.TestCase):
    def test_parse(self):
        company, titles, first, _ = boards.parse_hn_post(HN_ITEM["children"][0])
        self.assertEqual((company, titles), ("Acme", ["Backend Engineer", "Frontend Engineer"]))
        self.assertIn("Bengaluru", first)

    def test_thread(self):
        jobs = boards.hn_hiring(C(), {"thread": "latest"}, KEEP)
        self.assertEqual(jobs.scanned, 3)
        self.assertEqual([(j.company, j.title) for j in jobs], [("Acme (HN Who is hiring)", "Backend Engineer")])
        self.assertEqual(jobs[0].url, "https://news.ycombinator.com/item?id=1")


class Engine(unittest.TestCase):
    def test_all_three_keep_their_company_names(self):
        entries = [{"name": "Acme", "ats": "keka", "id": "acme"},
                   {"name": "Himalayas: java", "ats": "himalayas", "id": {"query": "java", "country": "IN"}},
                   {"name": "HN Who is hiring", "ats": "hn_hiring", "id": {"thread": "latest"}}]
        matches, errors, _ = engine.run_crawl(entries, CFG, log=lambda m: None, client=C())
        self.assertEqual(errors, [])
        companies = {j.company for j in matches}
        self.assertIn("Acme", companies)
        self.assertIn("Acme (via Himalayas)", companies)
        self.assertIn("Acme (HN Who is hiring)", companies)


if __name__ == "__main__":
    unittest.main()
