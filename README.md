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
| `/qa-toolkit:heal` | When tests break because the screen changed, finds the element again and fixes the locator - only after you approve. Never hides a real bug. | Fixed locators + `reports/heal-log.md` |

**The reports** (`reports/report.html` and `reports/report.xlsx`):
- a verdict at the top, e.g. *NOT READY - 1 possible bug in the app, 2 tests need updating*
- every failure says why: **possible bug in the app**, **test needs updating**,
  **test out of step with the sheet**, or **environment problem** - and what to do next
- a screenshot of the screen at the moment of each failure
- is every requirement tested? (coverage table)
- the Excel file has Summary, Problems and All test cases tabs, with filters

**The safety guard:** tests and the AI's browser may only open websites listed in your
project's `qa-toolkit.yaml`. Anything else - including production - is refused.

## Before you start (once per computer)

1. **Claude Code** installed and signed in.
2. **Access to this repo** - it is private: ask Kishore to add your GitHub account. Check that
   `git clone https://github.com/kishore543/qa-toolkit` works for you.
3. **Python 3.9+ and Node.js** (Node runs the Playwright browser for the AI).
4. The Python packages and the browser:
   ```bash
   python3 -m pip install pyyaml openpyxl pytest playwright
   python3 -m playwright install chromium
   ```

## Install

In Claude Code, type:
```
/plugin install qa-toolkit --marketplace kishore543/qa-toolkit
```
Then **restart Claude Code** (type `/exit` and start `claude` again) - commands load at start-up.
Type `/qa-toolkit:` and you should see the four commands.

**Get the latest version later:** `/plugin marketplace update kishore-qa`, then restart.

## Set up a project (once per project)

In the root folder of the project you are testing, create `qa-toolkit.yaml`:
```yaml
project: My Project

# Websites tests are ALLOWED to open. "*.example.com" means any sub-domain.
allowed_hosts:
  - staging.example.com
  - localhost

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

Already have test cases in Excel? Skip the first two steps:
`/qa-toolkit:automate "Login Test Cases.xlsx"` - it reads any column names and all tabs,
and never edits your file.

## Rules the toolkit follows

- **The test case sheet is the source of truth.** Tests never invent steps or expected results.
- **A failing check is never weakened** to make a test pass. A possible bug stays failing.
- **Only test sites** from `qa-toolkit.yaml` are opened.
- **Nothing is committed or pushed** unless you ask.
- Load and security tests: staging only, and tell the developers first.

## Optional: Jira bugs from failed tests

Failed tests can be filed as Jira bugs, without duplicates, with the companion
[qa-bug-reporter](https://github.com/kishore543/qa-bug-reporter) (also private):
```bash
python3 -m pytest --bug-report
```

## Problems?

| You see | Do this |
|---|---|
| `Unknown command: /qa-toolkit:...` | Restart Claude Code - commands load at start-up |
| `qa-toolkit safety guard BLOCKED this` | The site is not in `allowed_hosts` of `qa-toolkit.yaml` (or it is in `blocked_hosts`) |
| `no qa-toolkit.yaml in this folder` | Start Claude Code / pytest from the project root, where the file is |
| `Excel report skipped: openpyxl is not installed` | `python3 -m pip install openpyxl` |
| Install fails with "repository not found" | You don't have access to the private repo yet - ask Kishore |

## For the maintainer

Try local changes without installing: `claude --plugin-dir ~/Downloads/qa-toolkit`.
Check the files before pushing: `claude plugin validate .`
Bump `version` in `.claude-plugin/plugin.json` when releasing, so colleagues get the update.
