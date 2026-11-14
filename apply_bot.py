"""Fill application forms with Playwright. Zero AI tokens are used here.

auto():   fills, and submits only if every required field is answered.
assist(): fills, then you review and press Submit yourself in the visible browser.
"""
import re

DESCRIBE_JS = r"""
() => Array.from(document.querySelectorAll('input, textarea, select')).map((el, i) => {
  let label = '';
  if (el.labels && el.labels.length) label = el.labels[0].innerText;
  const lb = el.getAttribute('aria-labelledby');
  if (!label && lb) { const l = document.getElementById(lb.split(' ')[0]); if (l) label = l.innerText; }
  if (!label) label = el.getAttribute('aria-label') || el.placeholder || '';
  if (!label) {
    const wrap = el.closest('.field, .application-question, .form-group, li, fieldset');
    const l = wrap && wrap.querySelector('label, legend, .application-label, .text');
    if (l) label = l.innerText;
  }
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  label = (label || '').replace(/\s+/g, ' ').trim().slice(0, 160);
  const role = (el.getAttribute('role') || '').toLowerCase();
  let value = el.value || '';
  if (role === 'combobox' && !value) {
    const ctl = el.closest('[class*="control"], [class*="container"]') || el.parentElement;
    const sel = ctl && ctl.querySelector('[class*="single-value"], [class*="multi-value"]');
    if (sel) value = sel.innerText.trim();
  }
  return {
    i, tag: el.tagName.toLowerCase(), type: (el.type || '').toLowerCase(),
    name: (el.name || '').toLowerCase(), id: (el.id || '').toLowerCase(), label,
    required: el.required || el.getAttribute('aria-required') === 'true' || /\*$/.test(label),
    visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none',
    value, role, checked: !!el.checked,
    opt: ((el.labels && el.labels.length ? el.labels[0].innerText : '') ||
          (el.nextSibling && el.nextSibling.textContent) || el.value || '').trim().slice(0, 60)
  };
})
"""

# Exact name/id attributes used by common ATS forms.
ATTR_MAP = {
    "first_name": "first_name", "last_name": "last_name", "name": "full_name",
    "email": "email", "phone": "phone", "org": "current_company",
    "urls[linkedin]": "linkedin", "location": "location",
}
# Label patterns -> profile keys (checked in order).
LABEL_MAP = [
    (r"first\s*name|given name", "first_name"),
    (r"last\s*name|surname|family name", "last_name"),
    (r"^(full\s*)?name\b|^your name", "full_name"),
    (r"\be-?mail\b", "email"),
    (r"phone|mobile", "phone"),
    (r"linkedin", "linkedin"),
    (r"^(current\s+)?location|^city\b", "location"),
    (r"^country\b", "country"),
    (r"current (company|employer|organi[sz]ation)", "current_company"),
]
TEXT_TYPES = {"", "text", "email", "tel", "url", "search", "number"}
CONFIRM_RE = re.compile(r"thank you for (applying|your application)|application (has been |was )?"
                        r"(received|submitted)|we('ve| have) received your application|"
                        r"successfully submitted", re.I)


def _profile_key(f):
    for attr in (f["name"], f["id"]):
        if attr in ATTR_MAP:
            return ATTR_MAP[attr]
    lab = f["label"].lower()
    for pat, key in LABEL_MAP:
        if re.search(pat, lab):
            return key
    return None


YEARS_RE = re.compile(r"\b(years?|yrs)\b.*\bexperience\b|\bexperience\b.*\b(years?|yrs)\b", re.I)
SKILL_HINT_RE = re.compile(r"\b(using|with|in|of)\s+(?!the\b|total\b|overall\b|professional\b|relevant\b|industry\b)[a-z]", re.I)


def _years_answer(label, years):
    """Only answer years questions for skills you've listed; never reuse 'total' for another skill."""
    lab = label.lower()
    for skill, n in years.items():
        if skill != "total" and re.search(r"(?<![a-z])" + re.escape(skill.lower()) + r"(?![a-z])", lab):
            return str(n)
    stripped = re.sub(r"\b(do you have|have you)\b", "", lab)
    if SKILL_HINT_RE.search(stripped.split("experience", 1)[-1]):
        return None  # asks about a specific skill we have no number for
    return str(years["total"]) if "total" in years else None


def _answer(f, answers, years=None):
    lab = f["label"].lower()
    if years and YEARS_RE.search(lab):
        return _years_answer(lab, years)
    for key, val in answers.items():
        if val and key.lower() in lab:
            return str(val)
    return None


def fill_form(page, cfg, resume_path):
    fields = page.evaluate(DESCRIBE_JS)
    loc = page.locator("input, textarea, select")
    filled = {"resume": False, "count": 0}

    files = [f for f in fields if f["type"] == "file"]
    target = next((f for f in files if re.search(r"resume|cv", f["label"] + f["name"] + f["id"], re.I)),
                  files[0] if files else None)
    if target:
        try:
            loc.nth(target["i"]).set_input_files(resume_path)
            filled["resume"] = True
        except Exception:
            pass

    for f in fields:
        if not f["visible"] or f["value"] or f["type"] in ("file", "hidden", "submit", "checkbox", "radio"):
            continue
        value = None
        if f["tag"] == "input" and f["type"] in TEXT_TYPES:
            key = _profile_key(f)
            value = cfg["profile"].get(key) if key else None
        if value is None and (f["tag"] in ("textarea", "select") or f["type"] in TEXT_TYPES):
            value = _answer(f, cfg.get("answers") or {}, cfg.get("experience_years"))
        if not value:
            continue
        try:
            el = loc.nth(f["i"])
            if f["role"] == "combobox":
                _pick_combobox(page, el, value)
            elif f["tag"] == "select":
                try:
                    el.select_option(label=value)
                except Exception:
                    el.select_option(value=value)
            else:
                el.fill(value)
            filled["count"] += 1
        except Exception:
            pass
    # Yes/No style radio questions answered from config "answers"
    groups = {}
    for f in fields:
        if f["type"] == "radio" and f["name"]:
            groups.setdefault(f["name"], []).append(f)
    for opts in groups.values():
        if any(o["checked"] for o in opts):
            continue
        ans = _answer(opts[0], cfg.get("answers") or {}, cfg.get("experience_years"))
        pick = next((o for o in opts if ans and o["opt"].lower().startswith(ans.lower())), None)
        if pick:
            try:
                loc.nth(pick["i"]).check()
                filled["count"] += 1
            except Exception:
                pass
    return filled


def _pick_combobox(page, el, value):
    """Searchable dropdowns (react-select etc.): type, then click the best matching option."""
    el.click()
    el.fill(value)
    page.wait_for_timeout(1500)
    opts = page.get_by_role("option")
    n = opts.count()
    if not n:
        el.press("Enter")
        return
    texts = [opts.nth(k).inner_text().strip().lower() for k in range(min(n, 15))]
    v = value.lower()
    k = next((k for k, t in enumerate(texts) if t == v), None)
    if k is None:
        k = next((k for k, t in enumerate(texts) if t.startswith(v) or v.split(",")[0] in t), 0)
    opts.nth(k).click()
    page.wait_for_timeout(300)


def missing_required(page):
    fields = page.evaluate(DESCRIBE_JS)
    missing, groups = [], {}
    for f in fields:
        if not f["required"] or f["type"] in ("hidden", "submit"):
            continue
        if f["type"] in ("radio", "checkbox"):
            g = groups.setdefault(f["name"] or f["id"], {"label": f["label"], "checked": False})
            g["checked"] |= f["checked"]
        elif f["type"] == "file":
            continue  # handled by the resume check
        elif f["visible"] and not f["value"]:
            missing.append(f["label"] or f["name"] or "unlabelled field")
    missing += [g["label"] or name for name, g in groups.items() if not g["checked"]]
    return list(dict.fromkeys(missing))


def _submit_button(page):
    for sel in ["button[type=submit]", "input[type=submit]", "#btn-submit"]:
        b = page.locator(sel)
        if b.count() and b.first.is_visible():
            return b.first
    b = page.get_by_role("button", name=re.compile(r"submit|apply", re.I))
    return b.first if b.count() else None


class Applier:
    def __init__(self, cfg, headless=True):
        self.cfg, self.headless = cfg, headless

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=self.headless)
        return self

    def __exit__(self, *exc):
        self.browser.close()
        self._pw.stop()

    def _open(self, job, resume_path):
        page = self.browser.new_page()
        page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        filled = fill_form(page, self.cfg, resume_path)
        return page, filled, missing_required(page)

    def auto(self, job, resume_path):
        page, filled, missing = self._open(job, resume_path)
        try:
            if not filled["resume"]:
                return "NEEDS_YOU", "no resume upload field found"
            if missing:
                return "NEEDS_YOU", "unanswered: " + "; ".join(missing[:4])
            button = _submit_button(page)
            if not button:
                return "NEEDS_YOU", "no submit button found"
            button.click()
            page.wait_for_timeout(8000)
            if CONFIRM_RE.search(page.inner_text("body")):
                return "APPLIED", "auto-submitted, confirmation seen"
            return "CHECK", "clicked submit but saw no confirmation; check email before re-applying"
        finally:
            page.close()

    def assist(self, job, resume_path):
        page, filled, missing = self._open(job, resume_path)
        print(f"\n> {job['company']} - {job['title']}  (score {job.get('score')})")
        if not filled["resume"]:
            print(f"  Upload your resume manually: {resume_path}")
        if missing:
            print("  Still needed: " + "; ".join(missing[:8]))
        print("  Review the form in the browser window and submit it there.")
        ans = input("  y = I submitted it, s = skip this job, Enter = later: ").strip().lower()
        page.close()
        return {"y": ("APPLIED", "submitted by you (assist mode)"),
                "s": ("SKIPPED", "skipped by you")}.get(ans, (None, None))
