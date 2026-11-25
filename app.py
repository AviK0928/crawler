"""Web UI: add job-board links, run a crawl, download XLSX/CSV.

Crawls run in a background thread and the page polls for progress, so a slow
crawl never hits a request timeout. The server keeps no permanent state: your
added links, settings, "seen" jobs and applied/skipped marks live in your
browser (localStorage), so Render's free tier wiping its disk doesn't matter.
"""
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from jobcrawler.engine import entry_key, run_crawl
from jobcrawler.export import to_csv, to_xlsx
from jobcrawler.http import HttpClient
from jobcrawler.resolve import resolve

ROOT = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

PASSWORD = os.environ.get("APP_PASSWORD", "")
MAX_SOURCES = 150
MAX_RUNNING = 2
RUNS = {}            # run_id -> dict(status, log, started, matches, errors, scanned)
RUNS_LOCK = threading.Lock()


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return json.load(f)


@app.before_request
def basic_auth():
    """Optional: set APP_PASSWORD on Render so strangers can't use your crawler."""
    if not PASSWORD or request.path == "/healthz":
        return
    auth = request.authorization
    if not auth or not secrets.compare_digest(auth.password or "", PASSWORD):
        return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="jobs"'})


@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "static"), "index.html")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/defaults")
def defaults():
    cfg = load("config.json")
    companies = load("companies.json")
    for c in companies:
        c["key"] = entry_key(c)
    return jsonify(companies=companies, settings={k: cfg[k] for k in
                   ("locations", "max_years_experience", "require_java", "include_internships",
                    "require_stated_experience", "min_score")})


@app.post("/api/resolve")
def api_resolve():
    text = (request.get_json(silent=True) or {}).get("input", "")
    try:
        entry = resolve(text[:500], HttpClient(min_interval=0.2, timeout=12, retries=1))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"Lookup failed ({type(e).__name__}). Try again or paste a single job's link."), 400
    entry["key"] = entry_key(entry)
    return jsonify(entry)


def _settings(user):
    cfg = load("config.json")
    user = user or {}
    if isinstance(user.get("locations"), list):
        cfg["locations"] = [str(x).strip() for x in user["locations"] if str(x).strip()][:60]
    for k, cast in (("max_years_experience", int), ("min_score", int)):
        if k in user:
            try:
                cfg[k] = max(0, min(30, cast(user[k]))) if k == "max_years_experience" else max(0, cast(user[k]))
            except (TypeError, ValueError):
                pass
    for k in ("require_java", "include_internships", "require_stated_experience"):
        if k in user:
            cfg[k] = bool(user[k])
    return cfg


def _valid_entry(e):
    return (isinstance(e, dict) and e.get("ats") in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "amazon",
                                                    "jibe", "sitemap", "xmlfeed", "talentbrew", "phenom", "recruitee", "workable", "breezy",
                                                    "oracle", "eightfold", "workable_search",
                                                    "keka", "himalayas", "hn_hiring")
            and (isinstance(e.get("id"), str) and 0 < len(e["id"]) < 300 or isinstance(e.get("id"), dict)))


@app.post("/api/crawl")
def api_crawl():
    body = request.get_json(silent=True) or {}
    entries = [e for e in body.get("sources", []) if _valid_entry(e)][:MAX_SOURCES]
    if not entries:
        return jsonify(error="No sources selected"), 400
    with RUNS_LOCK:
        if sum(r["status"] == "running" for r in RUNS.values()) >= MAX_RUNNING:
            return jsonify(error="A crawl is already running, wait for it to finish"), 429
        # keep memory small: drop old finished runs
        for rid in sorted(RUNS, key=lambda r: RUNS[r]["started"])[:-5]:
            if RUNS[rid]["status"] != "running":
                RUNS.pop(rid)
        run_id = uuid.uuid4().hex[:12]
        run = RUNS[run_id] = dict(status="running", log=[], started=time.time(), matches=[], errors=[], scanned=0)
    cfg = _settings(body.get("settings"))

    def work():
        try:
            m, err, n = run_crawl(entries, cfg, log=lambda s: run["log"].append(s))
            run.update(matches=m, errors=err, scanned=n, status="done")
        except Exception as ex:
            run["log"].append(f"Crawl failed: {ex}")
            run["status"] = "failed"

    threading.Thread(target=work, daemon=True).start()
    return jsonify(id=run_id)


def _get_run(run_id):
    run = RUNS.get(run_id)
    if not run:
        abort(404, "This crawl expired (the free server restarts when idle). Run it again.")
    return run


@app.get("/api/crawl/<run_id>")
def api_status(run_id):
    run = _get_run(run_id)
    since = request.args.get("since", default=0, type=int)
    out = dict(status=run["status"], log=run["log"][since:], log_len=len(run["log"]),
               elapsed=int(time.time() - run["started"]))
    if run["status"] == "done":
        out.update(scanned=run["scanned"], errors=run["errors"], jobs=[dict(
            key=j.key, score=j.score, company=j.company, title=j.title, location=j.location,
            years_min=j.years_min, posted=j.posted, reasons=j.reasons, source=j.source, url=j.url)
            for j in run["matches"]])
    return jsonify(out)


@app.route("/api/crawl/<run_id>/download.<fmt>", methods=["GET", "POST"])
def api_download(run_id, fmt):
    run = _get_run(run_id)
    if run["status"] != "done":
        abort(409, "Crawl not finished yet")
    jobs = run["matches"]
    # the page can post the keys you've hidden (applied/skipped) so the file matches the screen
    hide = set((request.get_json(silent=True) or {}).get("hide", [])) if request.method == "POST" else set()
    jobs = [j for j in jobs if j.key not in hide]
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    if fmt == "xlsx":
        data, mime = to_xlsx(jobs, run["errors"]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif fmt == "csv":
        data, mime = to_csv(jobs), "text/csv"
    else:
        abort(404)
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="java-sde-jobs_{stamp}.{fmt}"'})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
