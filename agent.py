"""Daily job agent: search -> filter -> score & tailor (Claude) -> build resume -> apply -> audit.

Commands:
  python agent.py run            daily pipeline (schedule this)
  python agent.py apply          open prepared applications in a browser, prefilled, for you to submit
  python agent.py report         print today's audit
  python agent.py check-sources  verify your company slugs return jobs
  python agent.py preview        build a PDF from your master resume (no API call)
  python agent.py add-company URL   point at a company's careers page; adds it if supported
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

import sources
import tailor
from render_resume import render

ROOT = Path(__file__).resolve().parent
DATA, OUT, AUDIT, LOGS = ROOT / "data", ROOT / "output" / "resumes", ROOT / "audit", ROOT / "logs"
STATE_FILE = DATA / "jobs.json"
AUTO_SOURCES = {"greenhouse", "lever", "ashby"}
TODAY = dt.date.today().isoformat()
MONTH = TODAY[:7]


# ---------- helpers ----------
def log(msg):
    LOGS.mkdir(exist_ok=True)
    with open(LOGS / "agent.log", "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_resume():
    return json.loads((ROOT / "resume_master.json").read_text(encoding="utf-8"))


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"jobs": {}, "spend": {}}


def save_state(state):
    DATA.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def slug(text, n=40):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:n]


def set_status(rec, status, note=""):
    rec.update(status=status, note=note, updated=TODAY)


# ---------- audit ----------
def write_audit(state, cfg, run_stats=None):
    AUDIT.mkdir(exist_ok=True)
    recs = sorted(state["jobs"].values(), key=lambda r: (r.get("updated", ""), r.get("score") or 0), reverse=True)
    cols = ["updated", "status", "score", "company", "title", "location", "source", "note",
            "resume", "apply_url", "cost_usd"]
    with open(AUDIT / "applications.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs:
            if r["status"] != "LOW_FIT":
                w.writerow(r)

    today = [r for r in recs if r.get("updated") == TODAY]
    by = lambda s: [r for r in today if r["status"] == s]
    spent_month = state["spend"].get(MONTH, 0.0)
    lines = [f"# Job agent - {TODAY}", ""]
    if run_stats:
        lines.append(f"Found {run_stats['found']} postings, {run_stats['passed']} passed filters, "
                     f"{run_stats['scored']} new scored. Cost today ${run_stats['cost']:.3f} "
                     f"(month ${spent_month:.2f} of ${cfg['ai']['monthly_budget_usd']}).")
        lines.append("")
    for status, title in [("APPLIED", "Applied"), ("CHECK", "Check your email (submit not confirmed)"),
                          ("READY", "Ready for you - run `python agent.py apply`"),
                          ("NEEDS_YOU", "Needs your input")]:
        rows = by(status)
        if rows:
            lines.append(f"## {title} ({len(rows)})")
            for r in rows:
                extra = f" - {r['note']}" if r.get("note") and status != "READY" else ""
                lines.append(f"- {r['company']} | {r['title']} | score {r.get('score')}{extra}")
            lines.append("")
    lines.append(f"Skipped as low fit today: {len(by('LOW_FIT'))}. Errors: {len(by('ERROR'))}.")
    (AUDIT / "daily").mkdir(exist_ok=True)
    path = AUDIT / "daily" / f"{TODAY}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------- pipeline ----------
def score_and_tailor(jobs, cfg, resume, state):
    import anthropic
    client = anthropic.Anthropic()
    system = tailor.build_system(resume, cfg["ai"]["min_score"])
    budget = cfg["ai"]["monthly_budget_usd"]
    scored, cost_today = 0, 0.0

    for job in jobs[: cfg["ai"]["max_jobs_scored_per_run"]]:
        if state["spend"].get(MONTH, 0.0) >= budget:
            log(f"monthly budget ${budget} reached; stopping AI calls")
            break
        rec = state["jobs"].setdefault(job["id"], {
            k: job[k] for k in ("id", "source", "company", "title", "location", "url", "apply_url")})
        rec.update(found=rec.get("found", TODAY), attempts=rec.get("attempts", 0) + 1)
        try:
            sources.hydrate(job)
            text, usage = tailor.call_claude(client, cfg, system, job)
            cost = tailor.cost_usd(usage, cfg)
            state["spend"][MONTH] = state["spend"].get(MONTH, 0.0) + cost
            cost_today += cost
            scored += 1
            rec.update(cost_usd=round(cost, 5), tokens_in=usage["in"] + usage["cache_read"] + usage["cache_write"],
                       tokens_out=usage["out"])
            plan = tailor.parse_plan(text)
            rec["score"] = int(plan["s"])
            if plan["s"] < cfg["ai"]["min_score"]:
                set_status(rec, "LOW_FIT")
            else:
                plan, notes = tailor.validate_plan(plan, resume)
                folder = OUT / f"{TODAY}_{slug(job['company'], 25)}_{slug(job['title'])}"
                folder.mkdir(parents=True, exist_ok=True)
                pdf = folder / f"{slug(resume['name'])}_Resume.pdf"
                pages = render(resume, plan, str(pdf))
                (folder / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
                rec["resume"] = str(pdf.relative_to(ROOT))
                note = "; ".join(notes + ([f"resume is {pages} pages"] if pages > 1 else []))
                set_status(rec, "READY", plan.get("r", "") + (f" ({note})" if note else ""))
        except Exception as e:
            log(f"tailor failed for {job['id']}: {e}")
            set_status(rec, "ERROR", str(e)[:200])
        save_state(state)
    return scored, cost_today


def auto_apply(cfg, state):
    applied_today = sum(1 for r in state["jobs"].values() if r["status"] == "APPLIED" and r.get("updated") == TODAY)
    queue = sorted((r for r in state["jobs"].values() if r["status"] == "READY" and r["source"] in AUTO_SOURCES),
                   key=lambda r: r.get("score") or 0, reverse=True)
    queue = queue[: max(0, cfg["apply"]["max_per_day"] - applied_today)]
    if not queue:
        return
    from apply_bot import Applier
    with Applier(cfg, headless=cfg["apply"].get("headless", True)) as bot:
        for rec in queue:
            try:
                status, note = bot.auto(rec, str(ROOT / rec["resume"]))
            except Exception as e:
                status, note = "NEEDS_YOU", f"browser error: {str(e)[:120]}"
                log(f"auto-apply failed for {rec['id']}: {e}")
            set_status(rec, status, note)
            save_state(state)


def cmd_run(args):
    cfg, resume, state = load_cfg(), load_resume(), load_state()
    log("run started")
    found = sources.fetch_all(cfg, log)
    passed = sources.prefilter(found, cfg)
    fresh = [j for j in passed if j["id"] not in state["jobs"]
             or (state["jobs"][j["id"]]["status"] == "ERROR" and state["jobs"][j["id"]].get("attempts", 0) < 3)]
    scored, cost = score_and_tailor(fresh, cfg, resume, state) if fresh else (0, 0.0)
    if cfg["apply"]["mode"] == "auto":
        auto_apply(cfg, state)
    save_state(state)
    path = write_audit(state, cfg, {"found": len(found), "passed": len(passed), "scored": scored, "cost": cost})
    today = [r for r in state["jobs"].values() if r.get("updated") == TODAY]
    count = lambda s: sum(r["status"] == s for r in today)
    print(f"{TODAY}: applied {count('APPLIED')}, ready {count('READY')}, needs you {count('NEEDS_YOU')}, "
          f"low fit {count('LOW_FIT')}, cost ${cost:.3f}. Audit: {path.relative_to(ROOT)}")
    log(f"run finished: scored {scored}, cost ${cost:.4f}")


def cmd_apply(args):
    cfg, state = load_cfg(), load_state()
    queue = sorted((r for r in state["jobs"].values() if r["status"] in ("READY", "NEEDS_YOU")),
                   key=lambda r: r.get("score") or 0, reverse=True)
    if not queue:
        print("Nothing waiting. Run `python agent.py run` first.")
        return
    print(f"{len(queue)} applications waiting. Press Ctrl+C any time to stop; progress is saved.")
    from apply_bot import Applier
    with Applier(cfg, headless=False) as bot:
        for rec in queue:
            try:
                status, note = bot.assist(rec, str(ROOT / rec["resume"]))
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"  Could not open form: {e}")
                continue
            if status:
                set_status(rec, status, note)
                save_state(state)
    write_audit(state, cfg)


def cmd_report(args):
    state, cfg = load_state(), load_cfg()
    print((write_audit(state, cfg)).read_text(encoding="utf-8"))


def cmd_check_sources(args):
    cfg = load_cfg()
    for source, fetch in sources.FETCHERS.items():
        for s in cfg["sources"].get(source) or []:
            try:
                jobs = fetch(s)
                print(f"OK   {source}:{s}  {len(jobs)} jobs, {len(sources.prefilter(jobs, cfg))} match your filters")
            except Exception as e:
                print(f"FAIL {source}:{s}  {str(e)[:100]}")
    if cfg["sources"].get("remotive"):
        try:
            jobs = sources.fetch_remotive()
            print(f"OK   remotive  {len(jobs)} jobs, {len(sources.prefilter(jobs, cfg))} match your filters")
        except Exception as e:
            print(f"FAIL remotive  {str(e)[:100]}")


def add_slug_to_config(ats, slug):
    path = ROOT / "config.yaml"
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"^(  {ats}:\s*\[)(.*?)(\])", text, flags=re.M)
    if not m:
        return False
    items = [i.strip() for i in m.group(2).split(",") if i.strip()]
    if slug in items:
        return True
    items.append(slug)
    text = text[:m.start()] + m.group(1) + ", ".join(items) + m.group(3) + text[m.end():]
    path.write_text(text, encoding="utf-8")
    return True


def cmd_add_company(args):
    cfg = load_cfg()
    try:
        found, others = sources.inspect_careers_page(args.url)
    except Exception as e:
        sys.exit(f"Could not open {args.url}: {e}")
    if not found:
        if others:
            print(f"This company uses {', '.join(others)}, which the agent doesn't support "
                  "(it needs an account login or blocks automation). Apply there by hand with "
                  "a tailored PDF from output/resumes/.")
        else:
            print("Couldn't find a supported job board on that page. Try the page that lists "
                  "the open jobs, or open one job and paste that URL.")
        return
    for ats, slug in found:
        try:
            jobs = sources.FETCHERS[ats](slug)
        except Exception as e:
            print(f"Found {ats}:{slug} but its feed didn't load ({str(e)[:80]}); not added.")
            continue
        add_slug_to_config(ats, slug)
        print(f"Added {ats}:{slug}: {len(jobs)} open jobs, "
              f"{len(sources.prefilter(jobs, cfg))} match your filters right now.")


def cmd_preview(args):
    resume = load_resume()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT.parent / "master_resume_preview.pdf"
    render(resume, {"sum": resume["summary"], "b": [], "sk": [], "k": []}, str(path))
    print(f"Wrote {path.relative_to(ROOT)}")


def main():
    load_env()
    p = argparse.ArgumentParser(description="Daily job application agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "apply", "report", "check-sources", "preview"):
        sub.add_parser(name)
    sub.add_parser("add-company").add_argument("url")
    args = p.parse_args()
    if args.cmd == "run" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Put it in a .env file next to agent.py (see README).")
    {"run": cmd_run, "apply": cmd_apply, "report": cmd_report,
     "check-sources": cmd_check_sources, "preview": cmd_preview, "add-company": cmd_add_company}[args.cmd](args)


if __name__ == "__main__":
    main()
