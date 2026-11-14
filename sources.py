"""Fetch jobs from official public job-board APIs and pre-filter them for free."""
import datetime as dt
import html
import re

import requests

HEADERS = {"User-Agent": "personal-job-agent/1.0"}
TIMEOUT = 30


def clean_html(text):
    t = html.unescape(text or "")  # Greenhouse double-encodes its HTML
    t = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "- ", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\xa0]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t)
    return t.strip()


def _date(value):
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):  # Lever: epoch milliseconds
            return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).date().isoformat()
        return str(value)[:10]
    except Exception:
        return None


def _job(source, slug, jid, company, title, location, url, apply_url, desc, posted):
    return {
        "id": f"{source}:{slug}:{jid}",
        "source": source,
        "slug": slug,
        "company": company or slug,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "apply_url": apply_url,
        "description": desc,
        "posted": posted,
    }


def _get_json(urls, params=None):
    """Try each regional host in turn (US first, then EU); return the first good JSON."""
    last = None
    for url in urls:
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.ok:
                return r.json(), url
            last = requests.HTTPError(f"{r.status_code} from {url}")
        except requests.RequestException as e:
            last = e
    raise last


def fetch_greenhouse(slug):
    data, used = _get_json([f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                            f"https://boards-api.eu.greenhouse.io/v1/boards/{slug}/jobs"],
                           {"content": "true"})
    host = "job-boards.eu.greenhouse.io" if ".eu." in used else "job-boards.greenhouse.io"
    out = []
    for j in data.get("jobs", []):
        own = j.get("absolute_url") or ""
        apply_url = own if "greenhouse.io" in own else f"https://{host}/{slug}/jobs/{j['id']}"
        out.append(_job(
            "greenhouse", slug, j["id"], j.get("company_name") or slug, j.get("title"),
            (j.get("location") or {}).get("name"), own or apply_url,
            apply_url,
            clean_html(j.get("content")), _date(j.get("first_published") or j.get("updated_at"))))
    return out


def fetch_lever(slug):
    data, _ = _get_json([f"https://api.lever.co/v0/postings/{slug}",
                         f"https://api.eu.lever.co/v0/postings/{slug}"], {"mode": "json"})
    out = []
    for j in data:
        parts = [j.get("descriptionPlain") or clean_html(j.get("description"))]
        for block in j.get("lists", []):
            parts.append(f"{block.get('text', '')}:\n{clean_html(block.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        if j.get("workplaceType") == "remote":
            loc = f"{loc} (Remote)"
        out.append(_job(
            "lever", slug, j["id"], slug, j.get("text"), loc, j.get("hostedUrl"),
            j.get("applyUrl") or f"{j.get('hostedUrl')}/apply",
            "\n".join(p for p in parts if p), _date(j.get("createdAt"))))
    return out


def fetch_ashby(slug):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = f"{loc} (Remote)"
        out.append(_job(
            "ashby", slug, j["id"], slug, j.get("title"), loc, j.get("jobUrl"),
            j.get("applyUrl") or f"{j.get('jobUrl')}/application",
            j.get("descriptionPlain") or clean_html(j.get("descriptionHtml")),
            _date(j.get("publishedAt"))))
    return out


def fetch_smartrecruiters(slug):
    out = []
    for offset in range(0, 500, 100):
        r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                         params={"limit": 100, "offset": offset}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            country = "India" if (loc.get("country") or "").lower() == "in" else loc.get("country")
            where = ", ".join(x for x in (loc.get("city"), loc.get("region"), country) if x)
            if loc.get("remote"):
                where += " (Remote)"
            url = f"https://jobs.smartrecruiters.com/{slug}/{j['id']}"
            job = _job("smartrecruiters", slug, j["id"], (j.get("company") or {}).get("name") or slug,
                       j.get("name"), where,
                       url, url, "", _date(j.get("releasedDate")))
            job["detail_url"] = j.get("ref") or f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{j['id']}"
            out.append(job)
        if offset + 100 >= data.get("totalFound", 0):
            break
    return out


def hydrate(job):
    """Load the full description for sources whose list feed omits it (SmartRecruiters)."""
    if job.get("description") or not job.get("detail_url"):
        return job
    r = requests.get(job["detail_url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    sections = (d.get("jobAd") or {}).get("sections") or {}
    parts = [clean_html((sections.get(k) or {}).get("text"))
             for k in ("jobDescription", "qualifications", "additionalInformation", "companyDescription")]
    job["description"] = "\n".join(p for p in parts if p)
    job["apply_url"] = d.get("applyUrl") or job["apply_url"]
    return job


def fetch_remotive(search_terms=("react", "frontend")):
    out, seen = [], set()
    for term in search_terms:
        r = requests.get("https://remotive.com/api/remote-jobs",
                         params={"search": term, "limit": 100}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            out.append(_job(
                "remotive", "remotive", j["id"], j.get("company_name"), j.get("title"),
                f"{j.get('candidate_required_location', '')} (Remote)", j.get("url"), j.get("url"),
                clean_html(j.get("description")), _date(j.get("publication_date"))))
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
            "smartrecruiters": fetch_smartrecruiters}


def fetch_all(cfg, log):
    jobs = []
    for source, fetch in FETCHERS.items():
        for slug in cfg["sources"].get(source) or []:
            try:
                found = fetch(slug)
                jobs.extend(found)
            except Exception as e:  # one bad slug must not stop the run
                log(f"source {source}:{slug} failed: {e}")
    if cfg["sources"].get("remotive"):
        try:
            jobs.extend(fetch_remotive())
        except Exception as e:
            log(f"source remotive failed: {e}")
    return jobs


def _word_re(terms):
    return re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t.lower()) for t in terms) + r")(?![a-z0-9])")


def prefilter(jobs, cfg):
    """Free filtering by title, location and age. Returns jobs worth scoring."""
    s = cfg["search"]
    inc, exc = _word_re(s["title_include"]), _word_re(s["title_exclude"])
    locs = [l.lower() for l in s["locations"]]
    cutoff = (dt.date.today() - dt.timedelta(days=s.get("max_age_days", 30))).isoformat()
    keep = []
    for j in jobs:
        title = j["title"].lower()
        if not inc.search(title) or exc.search(title):
            continue
        loc = j["location"].lower()
        if loc and not any(l in loc for l in locs):
            continue
        if j["posted"] and j["posted"] < cutoff:
            continue
        keep.append(j)
    return keep


# ---------- company careers pages ----------
ATS_PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("greenhouse", r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)([A-Za-z0-9_-]+)"),
    ("lever", r"api\.(?:eu\.)?lever\.co/v0/postings/([A-Za-z0-9_-]+)"),
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_-]+)"),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
]
UNSUPPORTED = {
    "Workday": r"myworkdayjobs\.com|wd\d+\.myworkday", "SuccessFactors": r"successfactors|sapsf",
    "iCIMS": r"icims\.com", "Taleo": r"taleo\.net", "Keka": r"\.keka\.com", "Freshteam": r"freshteam\.com",
    "Workable": r"workable\.com", "Darwinbox": r"darwinbox", "Zoho Recruit": r"zohorecruit",
}


def detect_ats(text):
    """Find which supported job board a careers page uses. Returns ([(ats, slug)], [unsupported names])."""
    found = []
    for ats, pat in ATS_PATTERNS:
        for slug in re.findall(pat, text, flags=re.I):
            slug = slug.strip("/")
            if ats != "smartrecruiters":  # SmartRecruiters company ids keep their case
                slug = slug.lower()
            if slug and (ats, slug) not in found and slug not in ("jobs", "v0", "v1", "embed"):
                found.append((ats, slug))
    others = [name for name, pat in UNSUPPORTED.items() if re.search(pat, text, re.I)]
    return found, others


def inspect_careers_page(url):
    found, others = detect_ats(url)
    if not found:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        found, more = detect_ats(r.url + "\n" + r.text)
        others += [o for o in more if o not in others]
    return found, others
