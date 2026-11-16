"""Decides whether a job is an entry-level Java/backend SDE role in the right place,
and scores how good a fit it is. All knobs live in config.json."""
import re

TITLE_ROLE = re.compile(
    r"\b(software|backend|back[\s-]end|java|sde|swe|developer|programmer|"
    r"engineer|platform|full[\s-]?stack|application)\b", re.I)

TITLE_NON_SWE = re.compile(
    r"\b(sales|account|marketing|recruit|hr|finance|legal|support engineer|"
    r"customer|solutions? consultant|design(er)?|data analyst|qa analyst|"
    r"hardware|mechanical|electrical|civil|test technician|technical writer)\b", re.I)

TITLE_SENIOR = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|vice president|"
    r"architect|distinguished|fellow|expert|specialist ii)\b", re.I)

# "SDE II", "Software Engineer 3", "SDE-2", "Engineer IV", "L5"
TITLE_LEVEL_UP = re.compile(
    r"(sde|swe|engineer|developer)[\s\-,]*(ii|iii|iv|v|[2-6])\b|\bl[5-9]\b", re.I)

TITLE_INTERN = re.compile(r"\b(intern|internship|co-?op|apprentice|trainee)\b", re.I)

ENTRY_SIGNALS = re.compile(
    r"\b(new[\s-]?grad(uate)?s?|graduate|entry[\s-]?level|junior|jr\.?|associate|fresher|"
    r"early[\s-]career|university|campus|class of 20\d\d|20(25|26|27) grad|"
    r"sde[\s\-]*(1|i)\b|engineer[\s\-]*(1|i)\b|developer[\s\-]*(1|i)\b)", re.I)

JAVA_STRONG = re.compile(r"\b(java|spring[\s-]?boot|spring|jvm|hibernate|j2ee|jakarta ee)\b", re.I)
JAVA_ADJ = re.compile(
    r"\b(kotlin|microservices?|rest(ful)? apis?|postgres(ql)?|sql|kafka|aws|"
    r"distributed systems|backend|object[\s-]oriented|data structures)\b", re.I)

# "3+ years", "2-4 years", "minimum of 5 years", "5 or more years"
YEARS = re.compile(
    r"(?:minimum (?:of )?|at least |)(\d{1,2})\s*(?:\+|(?:-|–|to)\s*\d{1,2}|or more)?\s*\+?\s*"
    r"(?:years?|yrs?)(?:'|’)?\s*(?:of\s+)?(?:[\w-]+\s+){0,5}?(?:experience|exp\b)", re.I)


def min_years(text):
    """Smallest 'N years experience' requirement mentioned, ignoring absurd values."""
    vals = [int(m.group(1)) for m in YEARS.finditer(text or "")]
    vals = [v for v in vals if v <= 20]
    return min(vals) if vals else None


def location_ok(location, cfg):
    loc = (location or "").lower()
    if not cfg["locations"]:
        return True
    # word boundaries so "india" doesn't match "Indianapolis" and "ind" doesn't match "Indiana"
    if any(re.search(rf"\b{re.escape(term.lower())}\b", loc) for term in cfg["locations"]):
        return True
    if cfg.get("allow_remote_anywhere") and "remote" in loc:
        return True
    return False


def title_ok(title, cfg):
    if not TITLE_ROLE.search(title) or TITLE_NON_SWE.search(title):
        return False
    if TITLE_SENIOR.search(title) or TITLE_LEVEL_UP.search(title):
        return False
    if TITLE_INTERN.search(title) and not cfg.get("include_internships"):
        return False
    return True


def prefilter(title, location, cfg):
    return title_ok(title, cfg) and location_ok(location, cfg)


def score(job, cfg):
    """Returns True if the job should be kept; fills job.score / reasons / years_min."""
    if not prefilter(job.title, job.location, cfg):
        return False

    text = f"{job.title}\n{job.description}"
    reasons, s = [], 0

    yrs = min_years(job.description)
    job.years_min = yrs
    if yrs is not None and yrs > cfg["max_years_experience"]:
        return False
    if yrs is not None and yrs <= 1:
        s += 25; reasons.append(f"min {yrs} yrs exp")
    elif yrs == 2:
        s += 10; reasons.append("min 2 yrs exp")

    if ENTRY_SIGNALS.search(job.title):
        s += 35; reasons.append("entry-level title")
    elif ENTRY_SIGNALS.search(job.description):
        s += 15; reasons.append("entry-level wording")

    java_hits = {m.group(0).lower() for m in JAVA_STRONG.finditer(text)}
    if "java" in job.title.lower():
        s += 30; reasons.append("Java in title")
    elif java_hits:
        s += 20; reasons.append("mentions " + ", ".join(sorted(java_hits)[:3]))
    elif cfg.get("require_java"):
        return False

    adj = {m.group(0).lower() for m in JAVA_ADJ.finditer(text)}
    if adj:
        s += min(15, 3 * len(adj)); reasons.append(f"{len(adj)} stack matches")

    if re.search(r"\b(backend|back-end|server[\s-]side)\b", job.title, re.I):
        s += 10; reasons.append("backend title")

    if not job.description:
        reasons.append("no description fetched")

    job.score, job.reasons = s, reasons
    return s >= cfg["min_score"]
