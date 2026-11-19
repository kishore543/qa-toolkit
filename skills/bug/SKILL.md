---
name: bug
description: Turn the toolkit's results into ready-to-file bug reports - failed automated tests that look like app bugs, browser-only failures and accessibility problems - with steps, expected vs actual, environment, severity, priority and screenshot; skip test problems; have each one confirmed by hand; then file only the confirmed ones in Jira (dry run first, no duplicates) or export a Jira CSV. Use when someone asks to raise / log / file / report bugs, create Jira tickets from failures, or write bug reports.
---

# Bug reports (QA engineer style)

A bug report is a promise to a developer: "this is wrong in the APP, here is how to see it".
So this skill drafts bugs only for real app problems, asks for each one to be checked by hand,
and sends nothing anywhere until the person picks which ones to file.

Paths: the toolkit folder is two levels above this skill's folder.
- Drafts: `<toolkit>/scripts/bug_draft.py` (reads the reports, sends nothing)
- Jira: `<toolkit>/scripts/bug_file.py` (`check`, then `file` - dry run unless `--live`)

## Rules - never break these

- **Only app problems become bugs.** From the automated run, only "Possible bug in the app" is
  drafted. "Screen changed", "test out of step with the sheet" and "environment" are test problems:
  never filed (they are listed as "Not drafted" with the reason). "Needs a tester to look" is drafted
  only with `--include-unsure` and marked "check by hand first".
- **Reproduce before filing.** For every draft, repeat the steps by hand (Playwright browser tool
  or a short script) and say what you saw: confirmed / not a bug (test timing, test data, intended
  behaviour) / could not reproduce. Drafts marked "check by hand first" need this most.
  A failure that only happens because the test was too quick is NOT a bug.
- **One problem = one bug.** Drafts marked "same TC as ..." are the same test case seen by two
  sources (e.g. the main run and one browser): file it once.
- **Nothing is sent without a clear yes from the person.** Show the list of bugs you would file
  (id, title, severity) and wait for them to pick. `bug_file.py file` is a dry run; `--live` only
  after they said yes to exactly those ids; `--yes` skips the script's own question only then.
- **Jira login stays in `.env`** (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN), typed by the person.
  Never ask them to paste the token into chat; never print it.
- Never change a test, check or expected result to make a bug go away.

## Steps

1. **Make the drafts** from the project root (after `/qa-toolkit:automate`, and optionally the
   cross-browser and accessibility checks):
   ```
   python <toolkit>/scripts/bug_draft.py --cases <test case sheet> --title "<Product> - <flow> bugs" [--include-unsure]
   ```
   It reads `reports/results.xml`, `reports/browsers/` and `reports/a11y/` if they exist
   (`--only results a11y` to choose; `--results`, `--browsers`, `--a11y` for other folders).
   Output in `reports/bugs/`: `bugs.html` (review page with copy buttons), `bugs-jira.csv`
   (Jira CSV import), `B-01.md`... and `bugs.json`.

2. **Check each draft by hand** (rule above). Read its steps, expected, actual, technical details and
   screenshot; reproduce it; write one line per draft: confirmed / not a bug - why.
   Improve a draft's title or steps in `bugs.json` if they are unclear (keep the fingerprint).

3. **Show the person** `open reports/bugs/bugs.html` and your one-line verdicts. Ask which ones to file.

4. **File** - three ways, the person chooses:
   - **Jira, automatically:** needs a `jira:` section in `qa-toolkit.yaml`
     (`project_key`, `issue_type`, optional `labels`, `env_file`) and the login in `.env`.
     ```
     python <toolkit>/scripts/bug_file.py check                         # login + project, changes nothing
     python <toolkit>/scripts/bug_file.py file --ids B-04 B-07          # dry run - shows create / comment
     python <toolkit>/scripts/bug_file.py file --ids B-04 B-07 --live   # after the person says yes
     ```
     It creates each bug with priority, labels (`qa-fp-<fingerprint>`, `severity-<x>`) and the
     screenshot. If an open bug with the same fingerprint exists, it adds a "Seen again" comment
     instead of a duplicate. The Jira keys are saved back into `bugs.json`.
   - **Jira CSV import** (no token needed): Jira -> Settings -> System -> External system import ->
     CSV, file `reports/bugs/bugs-jira.csv` (delete the rows they did not pick first). No screenshots.
   - **Any tracker:** the Copy title / Copy description buttons on `bugs.html`, or the `B-xx.md` files.

5. **Report back:** which bugs were filed (keys / links), which were commented as seen again, and
   which drafts were dropped and why.

## Severity and priority

- **Severity** (how bad): Blocker (security, data loss, cannot sign in), Critical (server error,
  crash), Major (default: a feature does not work as the sheet says), Minor (layout, wording),
  Trivial. Accessibility: critical -> Critical, serious -> Major, moderate -> Minor, minor -> Trivial.
- **Priority** (how soon): from the test case Priority in the sheet (High -> High ...).
  Change either in `bugs.json` before filing if the person disagrees.

## Practice first

Point a test project's `jira:` at a free Jira Cloud site (atlassian.com, free plan) with a test
project, or practise the whole flow with the dry run - it shows exactly what would be created.
