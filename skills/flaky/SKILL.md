---
name: flaky
description: Flaky test tracker - keeps a history of every automated run and sorts each test into Flaky (result flips), Broke recently, Broken, Fixed recently, Stable; can re-run tests N times to measure flakiness now; builds a report with a pass/fail strip per test. Use when someone asks about flaky / unstable / intermittent tests, "is this failure real", tests that pass on re-run, or test reliability.
---

# Flaky test tracker (QA engineer style)

A flaky test fails sometimes and passes sometimes without any change - its failures say little
about the app. This skill remembers every run so flaky tests are found and fixed instead of
being mistaken for bugs (or ignored when a real bug appears).

Script: `<toolkit>/scripts/flaky.py` (toolkit = two levels above this skill's folder).

## Rules
- **Never record runs made to fail on purpose** (proof runs with wrong data) - they look flaky.
- Each browser is tracked separately (a `browsers/<name>/` results file gets that context).
- A flaky test is a TEST problem first: make it steady (wait for the page, unique test data, no
  shared state). Never "fix" it by weakening a check. If it flips because the APP is unstable
  (random 500s, slow saves), that is a bug - report it.

## Steps
1. **Record** results after each run (or all old ones once):
   `python <toolkit>/scripts/flaky.py record reports/results.xml [more.xml] [--label nightly]`
   The same file is never recorded twice.
2. **Measure now** (optional) - run suspicious tests N times and record each run:
   `python <toolkit>/scripts/flaky.py rerun tests/<role>/test_<flow>.py -n 3 -k "TC_085 or TC_104" --python .venv/bin/python`
3. **Report:** `python <toolkit>/scripts/flaky.py report [--last 10]` ->
   `reports/history/flaky-report.html` (+ `flaky.json`). Explain each group:
   Flaky (fail %, how often it flipped, different errors), Broke recently (since when - what changed
   that day?), Broken, Fixed recently, Stable.
4. **Act:** for each flaky test, look at its errors and propose the fix to the test (ask before
   changing it); for "broke recently", check the app change of that day; real bugs -> `/qa-toolkit:bug`.
