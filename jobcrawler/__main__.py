"""Command-line entry point, used by the GitHub Actions daily crawl.

  python -m jobcrawler crawl                 # -> out/jobs.xlsx + out/jobs.csv
  python -m jobcrawler resolve "Razorpay"    # which job board does a company use?
"""
import argparse
import json
import os
import sys

from .engine import run_crawl
from .export import to_csv, to_xlsx
from .http import HttpClient
from .resolve import resolve

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser(prog="jobcrawler")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crawl")
    r = sub.add_parser("resolve")
    r.add_argument("text", nargs="+")
    args = p.parse_args()

    if args.cmd == "resolve":
        for t in args.text:
            try:
                print(t, "->", json.dumps(resolve(t, HttpClient(min_interval=0.3))))
            except ValueError as e:
                print(t, "->", e)
        return

    cfg = load("config.json")
    matches, errors, _ = run_crawl(load("companies.json"), cfg, log=lambda m: print(m, file=sys.stderr))
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    with open(os.path.join(ROOT, "out", "jobs.xlsx"), "wb") as f:
        f.write(to_xlsx(matches, errors))
    with open(os.path.join(ROOT, "out", "jobs.csv"), "wb") as f:
        f.write(to_csv(matches))
    for j in matches[:20]:
        print(f"{j.score:3d}  {j.company[:15]:15s} {j.title[:55]:55s} {j.location[:30]}")


if __name__ == "__main__":
    main()
