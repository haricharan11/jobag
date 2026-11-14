"""One Claude call per job: score the fit and return a tiny JSON tailoring plan.

Token design: the model reads a lot (resume + full job description) but writes very
little. Rejected jobs cost ~10 output tokens ({"s":42}). Accepted jobs return bullet
IDs, keyword list and a short summary; the PDF itself is built locally for free.
"""
import json
import re

SYSTEM_TEMPLATE = """You are a resume-tailoring engine. You read a candidate's master resume and one job posting and output a tiny JSON plan. A separate program builds the resume from your plan, so never write resume content except the summary.

CANDIDATE MASTER RESUME (JSON; experience bullets have ids):
{resume}

CANDIDATE CONSTRAINTS: based in Bangalore, India; about 5 years of experience. A role is only reachable if it is located in India or is remote and open to candidates in India.

SCORING — "s" is 0-100, the realistic chance this candidate gets an interview:
- 85-100: core stack matches (React/TypeScript frontend or full-stack), seniority matches (roughly 3-7 years), location reachable.
- 70-84: good match with one or two gaps that a hiring manager would likely overlook.
- 40-69: notable gaps (different primary framework, wrong seniority, unclear location).
- 0-39: hard mismatch: requires far more experience (8+ years, staff/principal level), a primary stack the candidate lacks (e.g. Angular-only, Vue-only, native mobile, data/ML), or location not reachable from India.

OUTPUT RULES — STRICT:
- Output one line of minified JSON and nothing else. No markdown, no prose.
- If s < {min_score}: output exactly {{"s":N}} and stop.
- Otherwise output {{"s":N,"r":"...","sum":"...","b":[...],"k":[...],"sk":[...]}} where:
  r   = reason for the score, max 10 words.
  sum = rewritten professional summary, 45-65 words, emphasising what this job asks for. Use ONLY facts present in the master resume. Never add numbers, employers, tools, certifications or claims that are not in it.
  b   = experience bullet ids to include, most relevant first within each role: 7-9 ids starting with "a", then 2-3 ids starting with "b".
  k   = up to 8 skill terms that appear in BOTH the job posting and the resume, spelled as in the resume.
  sk  = the skill category names from the resume, most relevant to this job first."""


class TailorError(Exception):
    pass


def build_system(resume, min_score):
    compact = json.dumps(resume, ensure_ascii=False, separators=(",", ":"))
    return SYSTEM_TEMPLATE.format(resume=compact, min_score=min_score)


def job_message(job, max_chars):
    desc = job["description"][:max_chars]
    return (f"JOB POSTING\nCompany: {job['company']}\nTitle: {job['title']}\n"
            f"Location: {job['location']}\n\n{desc}")


def call_claude(client, cfg, system, job):
    ai = cfg["ai"]
    resp = client.messages.create(
        model=ai["model"],
        max_tokens=ai["max_output_tokens"],
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": job_message(job, ai["max_jd_chars"])}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    u = resp.usage
    usage = {
        "in": u.input_tokens,
        "out": u.output_tokens,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
    }
    return text, usage


def cost_usd(usage, cfg):
    pin, pout = cfg["ai"]["price_per_mtok_input"], cfg["ai"]["price_per_mtok_output"]
    inp = usage["in"] + usage["cache_write"] * 1.25 + usage["cache_read"] * 0.1
    return (inp * pin + usage["out"] * pout) / 1_000_000


def parse_plan(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise TailorError(f"no JSON in model output: {text[:80]!r}")
    try:
        plan = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise TailorError(f"bad JSON from model: {e}") from e
    if not isinstance(plan.get("s"), (int, float)):
        raise TailorError("model output missing score")
    return plan


def _numbers(text):
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def validate_plan(plan, resume):
    """Repair or drop anything that doesn't match the master resume."""
    roles = [r for exp in resume["experience"] for r in exp["roles"]]
    valid_ids = {b["id"] for r in roles for b in r["bullets"]}
    master_text = json.dumps(resume, ensure_ascii=False)
    notes = []

    ids = [i for i in plan.get("b", []) if i in valid_ids]
    ids = list(dict.fromkeys(ids))  # dedupe, keep order
    for r in roles:  # every role keeps at least its first bullets
        if not any(i.startswith(r["key"]) for i in ids):
            ids += [b["id"] for b in r["bullets"][:3]]
    plan["b"] = ids

    names = [c["name"] for c in resume["skills"]]
    order = [n for n in plan.get("sk", []) if n in names]
    plan["sk"] = list(dict.fromkeys(order)) + [n for n in names if n not in order]

    plan["k"] = [k for k in plan.get("k", []) if isinstance(k, str) and k.lower() in master_text.lower()][:8]

    summary = (plan.get("sum") or "").strip()
    invented = _numbers(summary) - _numbers(master_text)
    words = len(summary.split())
    if not summary or invented or words < 25 or words > 90:
        notes.append(f"summary replaced with master (invented numbers: {sorted(invented)})" if invented
                     else "summary replaced with master (length)")
        summary = resume["summary"]
    plan["sum"] = summary
    return plan, notes
