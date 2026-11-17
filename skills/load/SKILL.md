---
name: load
description: Load test a website with up to 100 virtual users at the same time (Locust), on QA/staging only, and build a plain-language HTML report that says PASSED / FAILED / COULD NOT TEST and at how many users things got slow. Use when someone asks for a load test, stress test, performance test, "how many users can it handle", or to check if a site is slow under load.
---

# Load test (QA engineer style)

A load test sends the same requests a browser sends, from many virtual users at once,
without opening browsers. It answers: "With N users at the same time, is the site
still fast, and does anything fail?"

Paths: the toolkit folder is two levels above this skill's folder.
- Runner: `<toolkit>/scripts/load_test.py` (reads `load.yaml`, writes `reports/load/results.json`)
- Report: `<toolkit>/scripts/load_report.py` (writes `reports/load/load-report.html`)
- Settings template: `<toolkit>/templates/load.yaml` (every option explained in comments)
- Practice site: `<toolkit>/demo/load_demo_app.py` (on your own computer, gets slow on purpose)

## Rules - never break these

- **Only QA/staging.** The site must be in BOTH `allowed_hosts` and `load_test_hosts` of
  `qa-toolkit.yaml`. Without `load_test_hosts`, only localhost is allowed. Never add a
  production or public site you don't own. The runner refuses anything else.
- **At most 100 users.** The runner caps it; never try to get around it.
- **Tell the developers before every run bigger than a quick check** (when, how many
  users, how long). Ask the person to confirm they did - don't assume it.
- **Step up, never jump:** 1 user -> 5 -> 20 -> 50 -> 100. Ask before each bigger step.
  Stop at the first step that FAILS and report it - don't push a struggling server.
- **Secrets only in `.env`** (emails of test accounts, OTPs, passwords, tokens), written
  as `${NAME}` in `load.yaml`. Make sure `.env` is in `.gitignore`. Never print the values.
- Never load a page that changes data (submit, delete, buy, import, invite) unless the
  person asks for exactly that and it is a throw-away test account.

## Steps

1. **Check the setup.** `python3 -c "import locust"` - if missing:
   `python3 -m pip install --user locust`. `qa-toolkit.yaml` must exist; check
   `load_test_hosts` has the site (if not, show the person the lines to add and why).

2. **Make `load.yaml`.** Copy `<toolkit>/templates/load.yaml` to the project root if
   there is none. Fill `base_url`, start with `users: 5`, and the `limits`
   (default: 95% of answers within 2-3 s, at most 1% errors - ask if the team has its own).

3. **Find the real requests - never guess them.**
   - Pages: open the site and look at the links/URLs a user visits.
   - Login: find what the login form sends. Ways, best first: the app's API docs;
     the person's DevTools screenshots (Network tab: URL, method, field names - values
     covered); or the app's own public page code. Read-only - no login yet.
   - Note how the session is kept: a token in the reply (use `save:` and an
     `Authorization` header) or cookies (nothing extra - each virtual user keeps its own).

4. **Write the steps so a failure really counts as a failure.**
   - Many apps answer `200 OK` even when something went wrong. Add `fail_if_text`
     (e.g. `'re:"error":"([^"]*)'` - the report then quotes the app's own message)
     and `expect_text` (a word the good answer always has, e.g. the page heading).
   - Pages behind login: `follow_redirects: false` + `expect_status: 200`, so a jump
     back to the login page counts as "not logged in".
   - Check it once by hand: the page signed in = 200; signed out = sent to login.
     If both look the same, the test can't tell them apart - fix that first.
   - Forms sent as FormData (e.g. Next.js server actions): use `multipart:` with the
     fields in the same order the browser sends them.

5. **1-user check** (about 20 s): `python3 <toolkit>/scripts/load_test.py --users 1 --duration 20`.
   Every step must work with 0 failures. A step with 0 requests just wasn't reached yet.
   If the login fails, the report says COULD NOT TEST and quotes why - fix the account
   or `.env`, don't blame the server.

6. **Step up**, asking before each one and saving each run separately:
   `python3 <toolkit>/scripts/load_test.py --users 20 --duration 120 --out reports/load/results-20-users.json`
   `python3 <toolkit>/scripts/load_report.py --results reports/load/results-20-users.json --out reports/load/load-report-20-users.html`
   Longer runs for more users (50 users: about 3 minutes; 100: about 5).

7. **Explain the result** in plain words, not raw numbers:
   - the verdict (PASSED / FAILED / NOT SURE / COULD NOT TEST) and what it means
   - a small table per step: typical answer, 95% within, slowest - compared with the
     previous run (5 vs 20 users ...)
   - the slowest page, and "first slower / first errors at about N users" if the report says so
   - honest limits: e.g. all virtual users shared ONE account; no browser was opened,
     so page images/scripts and screen drawing time are not included
   - next step: the next user count, or what the developers should look at
   Show the report: `open reports/load/load-report.html`.

## Reading the verdicts

| Verdict | Meaning |
|---|---|
| PASSED | Every limit held for this many users |
| FAILED | Too slow or too many errors - the report says when it started |
| NOT SURE | It ran, but some step was never reached - run longer |
| COULD NOT TEST | No virtual user could log in, so the site was never loaded - not a speed result |
| NO RESULT | Nothing was sent - check `base_url` and the steps |

## Practice first

To learn or demo without touching a real server:
`python3 <toolkit>/demo/load_demo_app.py` (localhost:8800, login OTP `123456`), then run
the load test against `http://localhost:8800`. `--comfortable 5` makes it fail on purpose.
