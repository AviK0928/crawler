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

_WORDNUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}(?:\.\d)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)"
_RANGE_TAIL = r"\s*(?:\+|plus|(?:-|–|—|to)\s*" + _NUM + r"|or more|or above)?\s*\+?\s*"
_YRS = r"(?:years?|yrs?)(?:'|’)?"

# "3+ years of (professional software) experience", "2-4 yrs exp", "minimum of 5 years' experience"
YEARS_BEFORE = re.compile(
    r"(?:minimum (?:of )?|at least |)" + _NUM + _RANGE_TAIL + _YRS +
    r"\s*(?:of\s+)?(?:[\w/&()-]+\s+){0,6}?(?:experience|exp\b)", re.I)
# "experience of 3+ years", "Experience: 2-4 years", "experience (min 3 yrs)", "exp - 5 yrs"
YEARS_AFTER = re.compile(
    r"\b(?:experience|exp)\b\s*(?:of|:|-|–|\(|required|needed)?\s*(?:minimum|min\.?|at least)?\s*(?:of\s+)?"
    + _NUM + _RANGE_TAIL + _YRS, re.I)


def _num(tok):
    tok = tok.lower()
    return float(_WORDNUM[tok]) if tok in _WORDNUM else float(tok)


def required_years(text):
    """Experience the posting asks for, in years (None if it never says).

    Each mention counts by its lower bound ("2-4 years" -> 2) and the posting counts by its
    STRICTEST mention: "3+ years of experience ... 1+ year of AWS experience" -> 3.
    """
    vals = []
    for pat in (YEARS_BEFORE, YEARS_AFTER):
        for m in pat.finditer(text or ""):
            v = _num(m.group(1))
            if v <= 25:
                vals.append(v)
    if not vals:
        return None
    v = max(vals)
    return int(v) if v == int(v) else v


min_years = required_years   # old name

TITLE_YEARS = re.compile(_NUM + _RANGE_TAIL + _YRS, re.I)   # "Software Engineer (3-5 yrs)"


def title_years(title):
    vals = [_num(m.group(1)) for m in TITLE_YEARS.finditer(title or "")]
    return max(vals) if vals else None


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

    yrs = required_years(text)
    ty = title_years(job.title)
    if ty is not None and (yrs is None or ty > yrs):
        yrs = int(ty) if ty == int(ty) else ty
    job.years_min = yrs
    if yrs is not None and yrs > cfg["max_years_experience"]:
        return False
    if yrs is None and cfg.get("require_stated_experience"):
        return False
    if yrs is None:
        reasons.append("experience not stated")
    elif yrs <= 1:
        s += 25; reasons.append(f"asks {yrs} yrs exp")
    else:
        s += 5; reasons.append(f"asks {yrs} yrs exp")

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
