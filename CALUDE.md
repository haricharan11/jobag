# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Windows setup (per README; Python 3.11+):
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

CLI entry point is `agent.py` (there is no build step, test suite, or linter configured):
```
python agent.py run            # full daily pipeline; makes Claude API calls
python agent.py preview        # render master resume to output/master_resume_preview.pdf (no API call)
python agent.py check-sources  # verify every configured company feed returns jobs
python agent.py add-company <careers-page-URL>   # detect ATS and append slug to config.yaml
python agent.py apply          # open READY/NEEDS_YOU applications in a visible browser for manual submit
python agent.py report         # print today's audit markdown
```

Testing a single change: there are no tests. Use `preview` to check resume layout offline, and `check-sources` to check fetchers against live feeds. Running `run` spends real API money (capped by `monthly_budget_usd`), so prefer `check-sources` or a stubbed call when iterating.

`run_daily.bat` / `run_daily.sh` wrap `agent.py run` for Task Scheduler/cron and append to `logs/daily.log`.

## Architecture

The pipeline in `agent.py:cmd_run` is: fetch -> free prefilter -> one LLM call per job -> local PDF build -> optional auto-submit -> audit. Each stage is a separate module:

- **sources.py**: pulls public JSON feeds (Greenhouse, Lever, Ashby, SmartRecruiters; Remotive is off by default). Every source is normalised to the same job dict shape via `_job()`. `prefilter()` applies title include/exclude word-boundary regexes, location substring match, and `max_age_days`, all before any AI spend. `hydrate()` fetches full descriptions for SmartRecruiters, whose list feed omits them. `detect_ats()` powers `add-company` by regex-matching ATS URLs in a careers page.
- **tailor.py**: the only module that talks to Claude. The model is asked for either `{"s":N}` (rejected, ~10 output tokens) or a small JSON plan selecting existing resume bullet ids, skill ordering, keywords, and a summary. `validate_plan()` is the guardrail: it drops unknown bullet ids, restores bullets for any role left empty, and replaces the summary with the master summary if it contains numbers absent from `resume_master.json` or has the wrong length. The model never writes new bullets, so keep it that way when editing the prompt.
- **render_resume.py**: builds a text-based one-page A4 PDF with reportlab. It retries with smaller fonts and then fewer bullets until pypdf reports one page.
- **apply_bot.py**: Playwright form filler, with no AI calls. Field matching goes through `_profile_key()` (exact `name`/`id` attrs, then label regexes) and then `_answer()` (`config.yaml` `answers` by substring, plus `experience_years`, which deliberately refuses to reuse the total for an unlisted skill). `auto()` submits only when there is a resume field, no unanswered required fields, and a confirmation message is seen afterwards. `assist()` leaves the browser open for the user.
- **agent.py**: orchestration, state, and audit. `main()` loads `.env` into `os.environ` by hand (no python-dotenv).

### State and statuses

All job state lives in `data/jobs.json`, keyed by `source:slug:jobid`, and is saved after every job, so a crash is recoverable. Statuses: `READY` (PDF built, awaiting apply), `APPLIED`, `CHECK` (submit clicked, no confirmation seen), `NEEDS_YOU` (form question unanswered or no upload field), `LOW_FIT` (below `ai.min_score`; hidden from the CSV), `ERROR` (retried on later runs up to 3 attempts), `SKIPPED`. Monthly spend is tracked in `state["spend"]["YYYY-MM"]`; once it reaches `monthly_budget_usd`, scoring stops for the rest of the month.

Outputs are written under `output/resumes/<date>_<company>_<title>/` (PDF plus `plan.json`), `audit/daily/YYYY-MM-DD.md`, and `audit/applications.csv`. `data/`, `output/`, `audit/`, `logs/`, and `.env` are gitignored and hold personal data; do not commit them.

### Config and data

- `config.yaml` is the single place for behaviour: profile, form answers, search filters, company slugs under `sources`, model and cost settings, and `apply.mode` (`review` submits nothing automatically; `auto` submits only for greenhouse/lever/ashby, capped by `max_per_day`).
- `resume_master.json` is the only source of resume facts. Experience bullets have stable `id`s (`a…`/`b…`) that the plan references, so never renumber existing ids. New bullets get new ids.
- Secrets: `ANTHROPIC_API_KEY` comes from `.env` (copied from `.env.example`).

### Things that are easy to get wrong

- `TODAY` and `MONTH` are computed at import time, so they are fixed for the life of a process.
- Lever gives `createdAt` as epoch milliseconds; `_date()` handles that. Other sources give ISO strings.
- Remotive jobs have `source="remotive"`, which is not in `AUTO_SOURCES`, so they are never auto-submitted (by design).
- `add_slug_to_config()` edits `config.yaml` with a regex on the `  <ats>: [ ... ]` line, so keep the flow-list format on one line.
