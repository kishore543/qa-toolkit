# qa-toolkit

QA skills for Claude Code, for testers and developers. It takes a feature from a
requirement to a tested result, QA-engineer style:

**requirement → test plan → test cases → automated tests → plain-language report**

Every automated test is one test case from your sheet, named after its ID, and the report
shows every test case in words anyone can understand: what passed, what failed, and **why**.

## What's inside

| Command | What it does | What you get |
|---|---|---|
| `/qa-toolkit:plan` | Writes a test plan from a requirement, user story or page. Asks up to 5 questions instead of guessing. | `test-plans/<feature>-test-plan.md` |
| `/qa-toolkit:cases` | Writes test cases from the plan: normal use, wrong input, edge cases, security basics. Every case is linked to a requirement. | `test-cases/<feature>-test-cases.csv` + a coverage table |
| `/qa-toolkit:automate` | Automates the test cases from a CSV **or Excel** sheet as Playwright + Python (pytest) tests, runs them and builds the report. | `pages/`, `tests/`, `test_data/`, `reports/` |
| `/qa-toolkit:api` | API tests from an OpenAPI / Swagger description: valid data, no login / wrong token, missing or wrong fields, unknown id, answer shape. GET only unless you allow writes; deletes only what it created. Same report as the UI tests. | `test-cases/api/`, `reports/api/api-report.html` |
| `/qa-toolkit:heal` | When tests break because the screen changed, finds the element again and fixes the locator - only after you approve. Never hides a real bug. | Fixed locators + `reports/heal-log.md` |
| `/qa-toolkit:load` | Load test: up to **100 virtual users** at the same time on QA/staging, stepping up 1 → 5 → 20 → 50 → 100. Logs each user in once (email + OTP, password or token from `.env`). | `reports/load/load-report.html` |
| `/qa-toolkit:browsers` | Cross-browser: runs your existing tests in Chrome, Firefox, Safari (WebKit), Edge and imitated iPhone / Android screens. Re-tries browser-only failures so flaky tests are not called browser bugs. | `reports/browsers/browser-report.html` |
| `/qa-toolkit:a11y` | Accessibility (WCAG 2.1 / 2.2 AA): axe-core scan, a keyboard-only walk (focus visible, no trap, nothing left out) and a 320px phone-width check. Read-only. Each problem: severity, who it affects, WCAG rule, how to fix, screenshot. | `reports/a11y/a11y-report.html` |
| `/qa-toolkit:bug` | Bug reports from the results: only real app problems (failed tests that look like app bugs, browser-only failures, accessibility problems), each checked by hand first. Files the ones you pick in Jira - dry run first, screenshot attached, no duplicates - or exports a Jira CSV. | `reports/bugs/bugs.html`, Jira issues |
| `/qa-toolkit:release` | Go / no-go: combines every result (tests, browsers, accessibility, load, security, bugs filed) into one page - READY TO RELEASE / READY WITH RISKS / NOT READY - with blockers, risks, what was not tested, sign-off lines and a summary to paste into chat. | `reports/release/release-report.html` |
| `/qa-toolkit:security` | Safe security checks on QA/staging: HTTPS & headers, cookies, who-can-see-what, bad input (XSS/SQL), login rate limits. Each problem gets a severity and a fix. Not a full pen-test. | `reports/security/security-report.html` |

**The reports** (`reports/report.html` and `reports/report.xlsx`):
- a verdict at the top, e.g. *NOT READY - 1 possible bug in the app, 2 tests need updating*
- every failure says why: **possible bug in the app**, **test needs updating**,
  **test out of step with the sheet**, or **environment problem** - and what to do next
- a screenshot of the screen at the moment of each failure
- is every requirement tested? (coverage table)
- the Excel file has Summary, Problems and All test cases tabs, with filters

**The load report** (`reports/load/load-report.html`): *PASSED / FAILED / COULD NOT TEST*,
how many users, typical and slowest answers per page, charts of users vs. speed, and
"answers first became slow at about N users" when that happens.

**The cross-browser report** (`reports/browsers/browser-report.html`): *WORKS IN ALL BROWSERS / BROWSER PROBLEMS FOUND*,
a card per browser with its version and pass rate, every browser-specific failure with the screenshot
from that browser, and a test-case x browser table with an "only differences" filter.

**The accessibility report** (`reports/a11y/a11y-report.html`): *NO PROBLEMS FOUND / NEEDS FIXES / NOT ACCESSIBLE YET*,
problems grouped across pages (Critical / Serious / Moderate / Minor), who each one hurts (e.g. screen reader
or keyboard users), the WCAG rule, how to fix it, the failing element outlined in a screenshot, and the manual
checks automation cannot do.

**The release dashboard** (`reports/release/release-report.html`): one big verdict, a status strip per area,
the blockers and risks in plain words, each area's numbers with a link to its full report, Jira links for filed bugs,
sign-off lines and Print / PDF - plus `release-summary.md` to paste into Slack, Teams or email.

**The security report** (`reports/security/security-report.html`): *PASSED / NEEDS FIXES / NOT SAFE YET*,
each problem with a severity (High / Medium / Low), what it means for users and how to fix it,
and an honest list of what was **not** tested.

**The safety guard:** tests and the AI's browser may only open websites listed in your
project's `qa-toolkit.yaml`. Anything else - including production - is refused.

## Before you start (once per computer)

1. **Claude Code** installed and signed in.
2. **Access to this repo** - it is private: ask Kishore to add your GitHub account. Check that
   `git clone https://github.com/kishore543/qa-toolkit` works for you.
3. **Python 3.9+ and Node.js** (Node runs the Playwright browser for the AI).
4. The Python packages and the browser:
   ```bash
   python3 -m pip install pyyaml openpyxl pytest playwright locust requests
   python3 -m playwright install chromium
   ```
   For cross-browser tests also: `python3 -m playwright install firefox webkit`

## Install

In Claude Code, type:
```
/plugin install qa-toolkit --marketplace kishore543/qa-toolkit
```
Then **restart Claude Code** (type `/exit` and start `claude` again) - commands load at start-up.
Type `/qa-toolkit:` and you should see the eleven commands.

**Get the latest version later:** `/plugin marketplace update kishore-qa`, then restart.

## Set up a project (once per project)

In the root folder of the project you are testing, create `qa-toolkit.yaml`:
```yaml
project: My Project

# Websites tests are ALLOWED to open. "*.example.com" means any sub-domain.
allowed_hosts:
  - staging.example.com
  - localhost

# Load tests (many users at once) - stricter: must be here AND above.
# Only your own QA/staging servers. Missing = only localhost.
load_test_hosts:
  - staging.example.com

# Security checks - same stricter rule, its own list.
security_test_hosts:
  - staging.example.com

# ALWAYS refused, even if listed above. Put production here.
blocked_hosts:
  - www.example.com
```
Never put passwords, OTPs or tokens in this file. They go in a `.env` file that you type
yourself, and `.env` must never be committed.

## How to use it - an example

```
/qa-toolkit:plan      Login page at https://staging.example.com/login - users log in with username and password
/qa-toolkit:cases     (uses the newest plan)
/qa-toolkit:automate  (uses the newest test case sheet - or name your own .xlsx)
```
Then open the reports:
```bash
open reports/report.html
open reports/report.xlsx
```
When tests fail because the developers changed the screen: `/qa-toolkit:heal`

Load test (tell the developers first):
```
/qa-toolkit:load      Learners on https://staging.example.com - log in, open the dashboard and course pages
```
It starts with 1 user and asks before every bigger step. Practice safely on your own computer
first: `python3 demo/load_demo_app.py` (in the toolkit folder) is a small site that gets slow on purpose.

Cross-browser (start with one short flow - each browser is a full run):
```
/qa-toolkit:browsers   tests/test_login.py in Chrome, Firefox and Safari
```
Or run one browser yourself: `python3 -m pytest tests/test_login.py --qa-browser firefox`.

Accessibility (read-only, safe on QA):
```
/qa-toolkit:a11y      Login, dashboard and settings pages on https://staging.example.com
```
Pages behind a login use a saved sign-in (`--state`) that you make by signing in yourself once.

API tests (ask the developers for the OpenAPI / Swagger file first):
```
/qa-toolkit:api       https://staging.example.com/openapi.json
```
Practice: `python3 demo/api_demo_app.py` is a small API with 4 bugs planted on purpose.

End of the sprint - can we release?
```
/qa-toolkit:release   Sprint 12 for the login and dashboard flows
```

Security check (tell the developers first):
```
/qa-toolkit:security   https://staging.example.com - safe checks, then access, input and login limits
```
It runs safest-first and asks before the login-abuse step. Each problem gets a severity and a fix.

Already have test cases in Excel? Skip the first two steps:
`/qa-toolkit:automate "Login Test Cases.xlsx"` - it reads any column names and all tabs,
and never edits your file.

## Credits

Accessibility checks use [axe-core](https://github.com/dequelabs/axe-core) by Deque Systems (MPL-2.0), shipped unmodified in `vendor/axe-core/`.

## Rules the toolkit follows

- **The test case sheet is the source of truth.** Tests never invent steps or expected results.
- **A failing check is never weakened** to make a test pass. A possible bug stays failing.
- **Only test sites** from `qa-toolkit.yaml` are opened.
- **Nothing is committed or pushed** unless you ask.
- Load and security tests: staging only, and tell the developers first.

## Jira bugs

`/qa-toolkit:bug` drafts bugs from the reports (test problems are left out), you check them,
then it files only the ones you pick. Add to `qa-toolkit.yaml`:
```yaml
jira:
  project_key: ABC
  issue_type: Bug
  env_file: .env
```
and your own login to `.env` (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` - create the token at
https://id.atlassian.com/manage-profile/security/api-tokens). `bug_file.py check` tests the login without
changing anything; `file` is a dry run until you add `--live`. No token? Import `reports/bugs/bugs-jira.csv`.

Filing every failed test straight from pytest is still possible with the companion
[qa-bug-reporter](https://github.com/kishore543/qa-bug-reporter) (`pytest --bug-report`); both use the same
`qa-fp-` labels, so they do not file duplicates of each other's bugs.

## Problems?

| You see | Do this |
|---|---|
| `Unknown command: /qa-toolkit:...` | Restart Claude Code - commands load at start-up |
| `qa-toolkit safety guard BLOCKED this` | The site is not in `allowed_hosts` of `qa-toolkit.yaml` (or it is in `blocked_hosts`) |
| `no qa-toolkit.yaml in this folder` | Start Claude Code / pytest from the project root, where the file is |
| `Excel report skipped: openpyxl is not installed` | `python3 -m pip install openpyxl` |
| `is not in load_test_hosts` | Load tests need their own list in `qa-toolkit.yaml` - add only your QA/staging site |
| Load report says `COULD NOT TEST` | No virtual user could log in - the report quotes why (wrong OTP, account switched off...). Fix `.env` or the account |
| `Locust is not installed` | `python3 -m pip install --user locust` |
| `is not in security_test_hosts` | Security checks need their own list in `qa-toolkit.yaml` - add only your QA/staging site |
| Security check says `NOT SAFE YET` | At least one High problem - the report says what and how to fix it |
| `unrecognized arguments: --qa-browser` | Your `conftest.py` is older - add the browser switch from the toolkit's conftest template (`/qa-toolkit:browsers` does it for you) |
| Cross-browser card says `Not installed` | `python3 -m playwright install firefox webkit` (with the project's Python) |
| Accessibility page shows `ended on .../login - signed in?` | The page needs a login: make a saved sign-in with `a11y_check.py --save-state .auth/state.json <login URL>` and pass `--state` |
| `bug_file.py` says `.env is missing JIRA_...` | Add your Jira login to the `.env` named in `qa-toolkit.yaml` (`jira: env_file:`) yourself |
| Jira says 400 on priority | The project hides the Priority field - the bug is filed without it automatically |
| Install fails with "repository not found" | You don't have access to the private repo yet - ask Kishore |

## For the maintainer

Try local changes without installing: `claude --plugin-dir ~/Downloads/qa-toolkit`.
Check the files before pushing: `claude plugin validate .`
Bump `version` in `.claude-plugin/plugin.json` when releasing, so colleagues get the update.
