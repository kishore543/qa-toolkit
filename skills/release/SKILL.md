---
name: release
description: Release readiness (go / no-go) dashboard - combines every qa-toolkit result (functional tests, cross-browser, accessibility, load, security, filed bugs) into one page that says READY TO RELEASE / READY WITH RISKS / NOT READY, lists the blockers and risks, links each full report, and gives a short summary to paste into chat or email. Use when someone asks "can we release?", "is it ready?", go/no-go, release sign-off, a release / test summary report, or a QA status for a sprint.
---

# Release readiness (QA engineer style)

The question at the end of every sprint is "can we release?". This skill answers it from the
results the other commands already made - it runs no tests itself - and is honest about what
was not tested. It advises; the release owner decides and signs off.

Paths: the toolkit folder is two levels above this skill's folder.
- Dashboard: `<toolkit>/scripts/release_report.py`

## What it reads (each part optional)

| Area | From | Made by |
|---|---|---|
| Functional tests | `reports/results.xml` + the test case sheet (`--cases`) | `/qa-toolkit:automate` |
| Cross-browser | `reports/browsers/run.json` | `/qa-toolkit:browsers` |
| Accessibility | `reports/a11y*/a11y-results.json` (all of them) | `/qa-toolkit:a11y` |
| Load | `reports/load/**/results*.json` (biggest run per journey) | `/qa-toolkit:load` |
| Security | `reports/security/results.json` | `/qa-toolkit:security` |
| Bugs filed | `reports/bugs/bugs.json` (Jira keys + links) | `/qa-toolkit:bug` |

## The rules (same verdicts as each area's own report)

- **Blocker:** a possible app bug on a Critical / High test case; a critical accessibility problem;
  load FAILED; a High security finding.
- **Risk:** possible app bugs on lower-priority cases; failures not checked yet; tests that need
  fixing; high-priority test cases not tested; browser-only failures; serious accessibility
  problems; load not clear; Medium security findings; any result older than 7 days.
- **Overall:** any blocker -> NOT READY. Any risk, or a required area not tested -> READY WITH RISKS.
  Otherwise READY TO RELEASE.
- Teams can change the rules in `qa-toolkit.yaml`:
  ```yaml
  release:
    required: [functional, a11y]           # areas that must be tested (default: functional)
    block_on_bug_priority: [critical, high]
    a11y_block_on: serious                 # critical (default) or serious
    max_age_days: 7
  ```

## Steps

1. **Check what exists.** List the `reports/` folder and tell the person which areas have results
   and how old they are. If a result is old or from another build, offer to re-run that command first.
   Before the dashboard, the "possible app bugs" should have been checked by hand (`/qa-toolkit:bug`
   step 2) - a test timing problem is not a release blocker.

2. **Build it** from the project root:
   ```
   python <toolkit>/scripts/release_report.py --cases <test case sheet> --title "<Product>" --release "<Sprint 12 / v2.4>"
   ```
   Output: `reports/release/release-report.html` (dashboard, Print / PDF button) and
   `reports/release/release-summary.md` (short text for Slack / Teams / email).

3. **Explain it** in plain words: the verdict, each blocker (and who must act), the risks the owner
   must accept or fix, and what was NOT tested. Never call something ready that was not tested.
   If you confirmed by hand that a "possible bug" is really a test problem, say so - and fix the
   test or re-run, so the dashboard shows the truth rather than explaining it away.

4. **Share** (only if the person asks): the summary text is ready to paste; the HTML prints to PDF.
   Posting to a channel or emailing it is the person's action, or needs their clear yes first.
