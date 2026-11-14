# Job Agent

Every day it finds new jobs, skips poor fits for free, asks Claude to score each remaining job and plan a tailored resume, builds that resume as a one-page PDF locally, applies (or queues the application for you), and writes a short audit.

## How it keeps output tokens tiny

Claude reads a lot (your full resume and the full job description) but writes almost nothing. A rejected job gets a reply like `{"s":42}`, about 10 output tokens. An accepted job gets a short JSON plan: which of your existing bullets to use and in what order, which skills to bold, and a 45–65 word summary. That's usually 150–250 output tokens. The PDF, the form filling, and the audit all run on your computer and cost zero tokens.

Claude never writes new bullet points. It can only reorder and choose from your real ones. If the rewritten summary contains any number that isn't in your master resume, the agent discards it and uses your original summary.

## One-time setup (Windows)

1. Install Python 3.11+ from python.org (tick "Add Python to PATH").
2. Open Command Prompt in this folder and run:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Copy `.env.example` to `.env` and paste your API key from console.anthropic.com.
4. In the Console, set a monthly spend limit. The agent also has its own limit (`monthly_budget_usd` in `config.yaml`).
5. Open `config.yaml` and fill in notice period and salary answers under `answers`. Any question left blank sends that job to your NEEDS_YOU list rather than guessing. Years-of-experience questions are only answered for skills listed under `experience_years`, so a question like "years using Angular" is never answered with your React number.
6. Add the companies you want, straight from their careers pages:
   ```
   python agent.py add-company https://www.easyship.com/careers
   ```
   The agent looks at the page, works out which job board it runs on (Greenhouse, Lever, or Ashby), checks the feed works, and adds it to `config.yaml`. If the company uses Workday or another portal the agent can't handle, it tells you. Run `python agent.py check-sources` any time to re-check every company.
7. Run `python agent.py preview` and open `output/master_resume_preview.pdf` to check the layout.

On Mac or Linux, use `source .venv/bin/activate` instead of the activate line above. Everything else is the same.

## Daily use

| Command | What it does |
|---|---|
| `python agent.py run` | Full daily pipeline. This is what you schedule. |
| `python agent.py apply` | Opens each prepared application in a browser, already filled with your details and tailored resume. You check the form, click Submit, and type `y`. |
| `python agent.py report` | Prints today's audit. |

Your audit is in two places. `audit/daily/YYYY-MM-DD.md` is a short summary of each day, and `audit/applications.csv` is the full history (it opens in Excel). The statuses mean:

- **APPLIED**: submitted, either automatically with a confirmation page seen, or by you.
- **READY**: tailored resume built, waiting for you to run `apply`.
- **NEEDS_YOU**: the form asks something your config doesn't answer. The note says what.
- **CHECK**: the agent clicked submit but didn't see a confirmation. Check your email before applying again.
- **LOW_FIT**: scored below `min_score`. Not shown in the CSV.

## Schedule it

**Windows:** run this once (change the path to this folder):
```
schtasks /create /tn "JobAgent" /tr "\"C:\path\to\job_agent\run_daily.bat\"" /sc daily /st 09:00
```
Your PC needs to be on at that time. If it's asleep, open Task Scheduler and enable "Run task as soon as possible after a scheduled start is missed".

**Mac/Linux:** add this line with `crontab -e`:
```
0 9 * * * /path/to/job_agent/run_daily.sh
```

## Review mode vs. auto mode

`apply.mode` in `config.yaml` is `review` by default. In review mode, the daily run prepares everything and you spend a few minutes running `python agent.py apply` to submit. I recommend starting this way for the first week so you can see the tailored resumes before employers do.

When you're comfortable with them, switch to `auto`. In auto mode, the agent submits on its own, but only for Greenhouse, Lever, and Ashby forms where every required field could be filled and only up to `max_per_day` applications. Remote jobs from Remotive are never auto-submitted because they link to many different sites.

## Cost

Each job scored uses roughly 3,000–6,000 input tokens plus a few hundred output tokens at most. At Haiku-class prices (about $1 per million input tokens and $5 per million output tokens), that's under a cent per job. Scoring 40 jobs a day comes to roughly $0.10–0.30 per day. Check the current price of the model you set in `config.yaml` on Anthropic's pricing page, and update `price_per_mtok_input` and `price_per_mtok_output` so the cost column in your audit is accurate.

## Where it looks for jobs in India

The agent reads Indian companies' own career boards. Most Indian product companies don't build their careers page themselves. They run it on hiring software such as Greenhouse, Lever, Ashby, or SmartRecruiters, and those tools publish each company's open jobs as a free public feed. When you apply on Meesho's or Groww's careers page, you're already using one of these, even if the page looks Indian.

`config.yaml` starts with about 20 Indian companies, including Groww, Razorpay, Meesho, CRED, Paytm, Zeta, Swiggy, Freshworks, InMobi, Druva, and Atlan. Run `python agent.py check-sources` first: a company sometimes changes its board, and that command shows which entries work. Add more with `python agent.py add-company <careers page URL>`.

**Naukri, LinkedIn, Instahyre, foundit, and Indeed are not included.** They're where much of Indian hiring happens, but their terms forbid bots, and accounts get restricted for automated applying. A safe way to use them alongside this agent is to keep your profile current there, and when you find a role, apply on the company's own careers page if it has one, using a tailored PDF from `output/resumes/`.

**Companies on Workday, SuccessFactors, Keka, Darwinbox, or Zoho Recruit** (common at large IT services firms and GCCs) aren't supported. Those portals usually require an account per company. `add-company` tells you when a company uses one.

## Limits to know about

- Job sites change their forms. The filler was tested against a replica of a live Greenhouse form (including its searchable dropdowns) and against mock Lever forms, but not submitted to a real company. Expect some jobs to land in NEEDS_YOU, especially early on. CAPTCHAs will also stop auto-submit, which is intended.
- LinkedIn, Naukri, and Indeed aren't included. Their terms prohibit automated applications, and accounts get banned for it. Workday and other custom portals aren't supported either. For those sites, you can still use the tailored PDF from `output/resumes/` and apply by hand.
- Your original resume PDF is a scanned image with no text layer, so most applicant tracking systems can't read it. Use the PDFs this agent generates, even when you apply manually.
- To change your resume content, edit `resume_master.json`. Every bullet keeps an `id`; add new bullets with new ids (for example `a12`).
"# jobag" 
