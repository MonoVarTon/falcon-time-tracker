**English** · [Русский](README.md)

# Time Tracker

A web application for tracking employees' working time, built as a take-home assignment for the Software Developer position at Falcon AI.

It is designed around the real environment of Falcon AI's clients — **a US freight dispatch office**. The demo data contains dispatchers, safety, billing and a driver recruiter, and the working day follows the office clock (Chicago by default), even when an employee works remotely from another country.

| Assignment requirement | Where it is implemented |
|---|---|
| Add employees | "Add employee" form · `POST /api/employees` |
| Start and stop a timer with a task description | "Start work" / "Finish" · `…/timer/start`, `…/timer/stop` |
| Entries and total time per employee for a period | "Report for a period" · `GET /api/report` |
| Data persists after a restart | SQLite file; covered by `test_employee_survives_restart`, `test_data_survives_app_restart` |
| Edge cases: second timer start, empty report | `test_second_start_is_rejected`, `test_empty_report`, `test_empty_report_without_employees` |

## Getting started

Requires Python 3.10 or newer (tested on Windows with Python 3.12 and on Linux with Python 3.10).

```
git clone https://github.com/MonoVarTon/falcon-time-tracker.git
```

**Windows (Command Prompt):**
```
cd falcon-time-tracker
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app
```

**macOS / Linux:**
```
cd falcon-time-tracker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app
```

Open **http://localhost:8000**. API documentation: **http://localhost:8000/docs**.

The separate `.venv` environment keeps the project's library versions from changing the system-wide Python.

### Running it with an AI agent

If you have an AI agent with terminal access (Claude Code, Cursor, Antigravity and similar), you can skip the manual setup — give it this prompt in an empty folder:

```
Download https://github.com/MonoVarTon/falcon-time-tracker and run it locally on my machine.

1. Clone the repository into the current folder and read README.en.md in full.
2. Detect my OS (Windows or macOS/Linux) and follow the "Getting started" section
   for it: a separate .venv inside the project folder, install from requirements.txt.
3. Run the tests: python -m pytest -q. Show me the summary line ("… passed").
4. Fill a demo database as described in "Demo data in one command" (a separate demo.db file)
   and start the server with that database.
5. Give me the link http://localhost:8000 and briefly tell me what to look at:
   the "Needs attention" filter, the "Break"/"Lunch" buttons, the report from Monday to today.

Boundaries: work only inside the project folder, install nothing globally, do not change the code.
No keys, passwords or credentials are needed. If a step fails, stop and show the full error;
do not try to "fix" it.
```

The prompt points to the README instead of repeating the commands, so there is a single set of instructions that cannot drift apart.

### Demo data in one command

A fictional dispatch office: 5 employees and a week of their work, with breaks and lunches. It is convenient to explore on a separate database without touching the real one:

```
set DATABASE_URL=sqlite:///./demo.db        (Windows)
export DATABASE_URL=sqlite:///./demo.db     (macOS / Linux)
python -m app.seed
python -m uvicorn app.main:app
```

The command refuses to write into a database that already has employees, so demo data never mixes with real data (to add it anyway: `python -m app.seed --append`).

"Today" in the demo is built from the current moment on the company clock. If it is night in Chicago, the demo dispatchers are on a night shift — which is normal for dispatch offices.

The demo deliberately includes an employee with a **forgotten timer**: they appear at the top of the list, marked "Needs attention".

### Configuration

| Variable | Default | What it sets |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./timetracker.db` | database file |
| `COMPANY_TIMEZONE` | `America/Chicago` | office time zone that defines the working day (an IANA name, e.g. `America/New_York`) |

## Features

**Employee:** "Start work" with a task → "Break" / "Lunch" → "Back to work" (the same task continues) → "Finish". The large clock shows **"worked today"** — the total work time for the day; it stops during a break, and the break has its own clock.

**Manager:**
- header filters: all / needs attention / working / paused / not started; search by name;
- **"Needs attention" comes first:** a timer has been running for more than 10 hours or has stopped automatically;
- **a forgotten timer stops automatically after 12 hours** — exactly at "start + 12 h", marked "please confirm the time";
- **workday extension** (+2/+4/+6 h, up to 24 h) recorded with the name of the approving manager;
- **end-time correction with a hint:** "usually leaves at 17:10 — average over 4 days"; a corrected entry is marked "✎ corrected" with a reason, so the report shows which hours are exact and which are restored.

**Period report:** each employee's work time, breaks and lunch shown separately, the list of entries and a **day timeline** — bars for work, breaks and lunch on a shared hour scale. Work that crosses midnight is split correctly between days.

## Architecture

```
app/
  config.py    company time zone and day boundaries
  db.py        SQLite connection and upgrade of an existing database
  models.py    tables: employees and time entries
  schemas.py   what the API accepts and returns (input validation)
  services.py  all business rules: timer, breaks, report, auto-stop, correction
  main.py      thin API routes: accept request → call a rule → respond
  seed.py      demo data
static/
  index.html   the page: HTML, CSS and JavaScript, no bundler
  timeline.js  day-timeline calculation (pure functions, covered by tests)
tests/         112 pytest checks; one of them runs 10 JavaScript checks
docs/
  decisions.md every decision with its "why" (in Russian)
  ai-log.md    AI error log: what went wrong, how it was found, how it was fixed (in Russian)
```

Key decisions (the full list with reasons is in [`docs/decisions.md`](docs/decisions.md)):

- **A timer is running while its entry has no end time.** There is no separate flag, so the data cannot contradict itself.
- **One running timer per employee** is enforced twice: by a rule in the code (a clear 409 error) and by a partial unique index in the database — in case of two simultaneous clicks.
- **All times are stored in UTC.** SQLite does not store time zones, so there is a custom `UTCDateTime` column type; naive times without a zone are rejected.
- **The working day follows the company clock.** A remote dispatcher works on US hours; a 17:00–02:00 Chicago shift is one company workday. On daylight-saving days the day lasts 23 or 25 hours, and this is handled.
- **The current time is a `get_now` dependency.** Tests "move the clock" and check durations exactly, without waiting.
- **Lossless database upgrade:** new columns are added to an existing database at startup (`ADDED_COLUMNS` in `db.py`). Verified on a real database created by the first version.
- **The page renders data only through `textContent`** — a name containing HTML tags is shown as text and never executed.

## Tests

```
python -m pytest -q
```

The tests cover every assignment feature and the edge cases: second start, empty report, work across midnight, daylight-saving day, breaks, auto-stop, extension, correction, application restart and upgrade of an old database. Tests run on a temporary database and never touch the real one.

The tests were mutation-checked: a bug was deliberately introduced into the code to see whether any test caught it. Twice none did — and that is exactly how the missing tests were found (see the log below).

The day-timeline checks are written in JavaScript and run from pytest when Node.js is installed; otherwise they are skipped.

## AI tools and models

| Tool | Model | Used for |
|---|---|---|
| Claude (app, agent mode with access to the project folder) | Claude Opus 5.5 (Anthropic) | design, code, tests, running checks, checking the page in a browser (Playwright), researching the vacancy and the company |
| Claude skills | frontend-design, dataviz (colour-palette validator), a Python/FastAPI skill adapted from the open-source ECC repository (MIT, security-reviewed) | UI rules, colour distinguishability checks, code and testing rules |
| Antigravity | Gemini 3.8 Flash (Google) | **independent code review by a second model**: Gemini only read the code and wrote a report; Claude verified every finding by reproducing it |

How the work was split: **the human** set the tasks, tested the application by hand and made the decisions; **the AI** wrote code test-first, ran the checks, took screenshots and kept a log of its own mistakes. Every decision is recorded with its reason so it can be explained and changed.

### Example prompts

Real prompts from the project (meaning preserved, translated from Russian):

1. Kick-off: "First look at my existing work: what can be reused — stack, similar apps. Then propose a skeleton and a plan; code only after my 'go'. The code must be understandable to me: explain every decision."
2. A bug found by hand: "When you press lunch or break, the work time on the timer resets instead of continuing. The report may count it, but the employee can't see how much they've worked in total." → the large clock became "worked today".
3. A product idea: "We need the timer to switch off automatically at 12 hours, and if an employee stays for overtime — approval from a manager. And a correction with a hint: over the last 7 days they left at such-and-such time — so we use the average." → auto-stop, extension, hint.
4. Grounding in the client: "Look at the link again and understand what Falcon is: where it is based and what it does." → company research, a dispatch-office demo, office time zone.
5. Scale: "Such people should pop up at the top of the list — in case there are hundreds of employees." → "Needs attention" and filters.
6. Keeping scope: "Aren't we wandering off the assignment? Maybe go back to a simpler form and describe the scheme in the README?" → features frozen; further development described below.

## Bugs found and fixed

All cases are real; the full log is in [`docs/ai-log.md`](docs/ai-log.md). The most telling ones:

| What the AI got wrong | How it was found | How it was fixed |
|---|---|---|
| Stored time with the standard `DateTime(timezone=True)`. SQLite does not store the zone: "12:00 Minsk time" came back as "12:00" with no zone — reports would have silently shifted by 3 hours | a test saved and re-read a time | custom `UTCDateTime` type; times without a zone are an error |
| A comment inside the page script contained the text `</script>` — the browser treated it as the end of the script and the buttons silently stopped working | API tests were green; only a run in a real browser caught it | comment rewritten, a script-integrity test added |
| The large clock showed the current segment and reset after lunch | **found by the human** during manual testing — the tests checked the calculation, not the meaning of the screen | "worked today" clock |
| The AI's own check required "no database file in the folder" — and failed on the owner's real database; deleting it "because the test said so" would have destroyed data | looked at the file before deleting | check rewritten; database untouched (sha256 before and after matched) |
| Tests silently created the production database when importing the app | noticed the file after a manual run, reproduced it | tests set a temporary database before importing the app + a dedicated check |
| The "usually leaves at 17:00" hint was computed as "midnight + minutes": on a daylight-saving day (a 25-hour day) it would have said 16:00 | deliberately broke the calculation — **not a single test failed** | wall-clock time; a test added specifically for 1 November |
| The "break" colour was chosen by eye and almost merged with "work", including for people with colour-vision deficiency | palette validator before rendering | colour replaced, all distinguishability checks passed |
| After correcting the last "needs attention" employee, the filter stayed on with an empty list and no way out | browser scenario run | fallback to "All" before rendering |
| The leave-time hint crashed with a 500 error when leaving at 23:59:45 and did not work for 17:00–02:00 night shifts | **second-model review** (Gemini 3.8 Flash) | hint computed per shift, leave time counted from the shift's start day |
| The Minsk time-zone check passed even when the zone was ignored (15:00 UTC = 18:00 Minsk — coincidence) | **second-model review** | the check also verifies local time in the explanation |

Lesson from the log: green tests are not proof. Half of the bugs were caught not by tests but by a browser run, manual testing by a human, deliberate code breaking and **a second model**.

### Independent review by a second model

Gemini 3.8 Flash read the code without write access and raised 10 findings. Each was verified by reproduction:

| | Outcome |
|---|---|
| Confirmed and fixed (each with a check that failed on the old code) | 6: hint crash near midnight, night shifts, a "hollow" time-zone check, a lost extension, an unbounded report period, outdated documentation |
| Fixed as minor | 2: the "now" marker on the server clock; guarding the extension form at the 24 h limit (practically unreachable) |
| Deliberately left as is | 2: WAL mode for SQLite (Python's default lock wait is 5 seconds, not 0 as the reviewer stated) and batch loading of the employee list — both described in "Roadmap" |
| Clarified | the extension race does not exceed 24 h as claimed — the real problem was that a second approval silently overwrote the first |

The review found two bugs that both the tests and the mutation checks had missed. Verifying the fixes found two more — in Claude's own code and test.

## Known limitations (by design)

- **No login or roles** — the assignment explicitly excludes them. So "manager approval" for an extension is a recorded name, not a role check, and anyone who opens the page can correct time. The reason for each correction is stored, so it is visible who changed what and why.
- **One time zone per company.** A design for employees in different zones is below.
- **Filters and search run in the browser** — instant, but sized for hundreds of employees, not tens of thousands.
- **Driver hours are not tracked.** US truck drivers have their own federal hours rules and electronic logs (Hours of Service, ELD) — a separate regulated domain. This app is for the dispatch office; doing driver logs halfway is worse than not doing them.

## Roadmap

1. **Day boundaries per employee's time zone, display in the viewer's time zone.** Worked time does not depend on zones (everything is stored in UTC); the zone only affects where "Monday" ends. If days followed the viewer's clock, a manager in Chicago and an accountant in New York would see different numbers for the same date. The right way: the zone lives in the employee's profile (taken from their browser), their days follow their zone, and times on screen follow the viewer's clock. Day calculation is already isolated in one function (`company_day_bounds`); only the source of the zone changes.
2. **Login and roles:** employees see themselves, managers see their team and approve extensions, accountants see reports.
3. **Overtime:** flag weeks over 40 hours — the basis for overtime pay in the US.
4. **Export:** a CSV table for accounting and a weekly timesheet for the employee's and manager's signatures (PDF).
5. **AI bookkeeper:** at the end of a period it collects the hours, checks anomalies (forgotten and uncorrected timers, days without lunch, sudden jumps in hours), writes an explanation and routes the timesheet for signatures: employee → manager → accountant. Money and taxes are calculated by ordinary code under fixed rules, not by the model; the final signature belongs to a human.
6. **Badges and turnstiles:** card swipes are facts, not guesses. Entry and exit times from the turnstile suggest the start and end of the day and solve the "forgot to press" problem. Risks: presence is not work, tailgating, personal data.
7. **Integration with company systems:** Falcon AI's TMS and HR bot — an employee is created once, and the timesheet goes where payroll happens.
8. **Scale:** PostgreSQL instead of SQLite (or WAL mode if SQLite stays), server-side search and pagination, loading all employees' state in one request instead of two per employee.
9. **Dependencies:** Starlette warns that the test client is moving from `httpx` to `httpx2` — update with the next FastAPI upgrade.
