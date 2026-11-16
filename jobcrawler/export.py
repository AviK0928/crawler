import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

COLS = [("Score", 8), ("Company", 18), ("Title", 48), ("Location", 32), ("Min yrs", 8),
        ("Posted", 14), ("Why it matched", 50), ("Source", 14), ("Link", 60)]


def _row(j):
    return [j.score, j.company, j.title, j.location, "" if j.years_min is None else j.years_min,
            j.posted, "; ".join(j.reasons), j.source, j.url]


def to_csv(jobs):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c for c, _ in COLS])
    for j in jobs:
        w.writerow(_row(j))
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM so Excel opens UTF-8 correctly


def to_xlsx(jobs, errors=()):
    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"
    ws.append([c for c, _ in COLS])
    for i, (_, width) in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
        cell = ws.cell(row=1, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E79")
    for j in jobs:
        ws.append(_row(j))
        r = ws.max_row
        title = ws.cell(row=r, column=3)
        title.hyperlink = j.url
        title.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    if errors:
        es = wb.create_sheet("Sources that failed")
        es.append(["Source", "Error"])
        for name, msg in errors:
            es.append([name, msg])
        es.column_dimensions["A"].width, es.column_dimensions["B"].width = 40, 80
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
