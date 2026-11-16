# crawler: Java SDE Job Finder

Finds **entry-level Java / backend SDE openings in India** straight from company job
boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, amazon.jobs). It scores
each opening for fit and lets you download the results as **XLSX or CSV** from a simple web page.

Paste a company's careers link into the page and it works out which job board the company uses.

There are three free ways to run it. You don't need to install anything on your own machine.

| | How | Good for |
|---|---|---|
| **Render** | Hosted web page with its own URL | Using it from any device |
| **Colab** | Open `colab.ipynb` in Google Colab | Quick runs, no account setup |
| **GitHub Actions** | Runs every morning on its own | A daily XLSX without opening anything |

---

## 1. Render (hosted web page, free)

1. Push this repo to GitHub.
2. Go to [render.com](https://render.com), sign in with GitHub, then **New + → Blueprint** and pick this repo.
   It reads `render.yaml` and sets everything up. Click **Apply**.
3. After a few minutes you get a URL like `https://java-job-finder.onrender.com`.
4. *(Recommended)* In the service's **Environment** settings, set `APP_PASSWORD`, so that only you
   can use it. The browser will ask for it; any username works.

The free plan sleeps after 15 idle minutes, so the first visit takes about a minute to wake it up.
The server stores nothing permanently. Your added companies, filters, "NEW" markers and
applied/skipped statuses are saved in **your browser**.

## 2. Google Colab

Open `colab.ipynb` in Colab (on GitHub, click the file, then **Open in Colab**, or use
`https://colab.research.google.com/github/AviK0928/crawler/blob/main/colab.ipynb`).
Run all cells. Cell 2 opens the same web page; cell 3 just downloads `jobs.xlsx`.

**Private repo?** Add your PAT in Colab's 🔑 **Secrets** panel under the name `GH_TOKEN`
(fine-grained token, read-only **Contents** permission on this repo only). Never paste it into a cell.

## 3. GitHub Actions (automatic daily crawl)

`.github/workflows/daily-crawl.yml` runs every day at 9:00 IST using `companies.json`.
To get the file: **Actions → daily crawl → latest run → Artifacts → java-sde-jobs**.
You can also run it any time with the **Run workflow** button.

---

## Adding companies

**In the web page:** paste any of these into "Add a company":
- a job-board link: `https://jobs.lever.co/razorpay`, `https://boards.greenhouse.io/stripe`, `https://jobs.ashbyhq.com/zepto`
- a Workday link: `https://walmart.wd5.myworkdayjobs.com/en-US/WalmartExternal`
- a company's own careers page, if it embeds one of the above: `https://razorpay.com/jobs/`
- a single job's link (it works out the board from it)
- or just a name: `Postman`

Companies added in the page are saved in that browser. To keep them for good (and for
the daily GitHub crawl), click **Copy my added sources (JSON)** and paste the lines into
`companies.json` on GitHub (✏️ edit button → commit).

**`companies.json` format:**
```json
{"name": "Stripe",  "ats": "greenhouse", "id": "stripe"},
{"name": "Walmart", "ats": "workday",    "id": "https://walmart.wd5.myworkdayjobs.com/WalmartExternal"},
{"name": "Amazon",  "ats": "amazon",     "id": {"base_query": "java", "country": "IND"}}
```
Any company that fails appears under "sources failed" after a crawl. PhonePe and Postman use
Greenhouse but have its public job feed switched off, so they can't be crawled. Rippling, Zepto and
Upstox use their own sites.

**Workday and "429 Too Many Requests":** Workday limits how fast one server can ask for jobs.
The crawler visits Workday companies one at a time, waits between requests, retries 429s with longer
pauses and keeps partial results. Workday may still block cloud servers at busy times. If it does,
run the crawl again later, or use the GitHub Actions daily crawl, which runs from different IPs.

**Not supported:** companies with fully custom career sites (Google, Microsoft, Flipkart…), and
LinkedIn/Naukri, which ban automated scraping.

## How jobs are scored

Titles with Senior / Staff / Lead / Manager / II / III / SDE-2 are dropped, as are jobs outside your locations.

**Experience filter (default: 0–1 years).** The crawler reads every experience requirement in the
title and description ("3+ years of experience", "Experience: 2-4 years", "two years'…", "(3-5 yrs)")
and uses the **strictest** one. A posting saying "3+ years of experience … 1+ year with AWS" counts as
3 years and is dropped. Ranges count by their lower end ("1-3 years" = 1). Jobs that never state a
number are kept and labelled *experience not stated*. Tick **hide jobs that don't say how much
experience they need** to drop those too.

The rest get points:

| Signal | Points |
|---|---|
| Entry-level title (SDE-1, new grad, associate, junior…) | +35 (+15 if only in description) |
| Java in title / Java, Spring, JVM in description | +30 / +20 |
| Asks for 0–1 years experience | +25 |
| Stack overlap (Kafka, Postgres, REST, microservices, AWS…) | up to +15 |
| Backend in title | +10 |

The **Why** column shows what matched. Defaults are in `config.json`; the page lets you change them per browser.

## Development

```bash
pip install -r requirements.txt
python app.py                                   # http://localhost:8000
python -m unittest discover -s tests -t .       # 34 tests, no network needed
```
