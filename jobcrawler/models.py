import html
import re
from dataclasses import dataclass, field

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(s)          # Greenhouse double-escapes its HTML
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    return _WS.sub(" ", s).strip()


@dataclass
class Job:
    source: str          # greenhouse | lever | ashby | smartrecruiters | amazon
    company: str
    job_id: str
    title: str
    location: str
    url: str
    description: str = ""
    posted: str = ""
    # filled in by scoring
    score: int = 0
    years_min: int | None = None
    reasons: list = field(default_factory=list)
    is_new: bool = False

    @property
    def key(self):
        return f"{self.source}:{self.company}:{self.job_id}"


class Found(list):
    """Jobs kept by a fetcher that pre-filters, plus how many postings it looked at."""
    def __init__(self, items=(), scanned=0):
        super().__init__(items)
        self.scanned = scanned
