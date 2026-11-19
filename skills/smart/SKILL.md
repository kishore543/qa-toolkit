---
name: smart
description: Smart regression - from release notes, a ticket, or the list of changed files (or the app's git diff), pick only the automated tests the change can affect, with the reason for each, plus always-run smoke tests, and give the command to run them. Use when someone asks which tests to run for a change / release / hotfix, "run only what's affected", impact analysis, or a faster regression.
---

# Smart regression (QA engineer style)

Running all tests for every small change wastes time; running too few misses bugs. This skill
picks the tests a change can affect and says WHY each one was picked, so a tester can check it.

Script: `<toolkit>/scripts/smart_select.py` (toolkit = two levels above this skill's folder).

## How it decides
It indexes every test (flow file, title, steps, the page objects it uses, their addresses and
on-screen names) and matches the words of the change against it. Rare words count more than
common ones ("suspend" picks the suspend tests; "tenant" in a tenant console decides nothing).
Smoke tests listed in `qa-toolkit.yaml` always run:
```yaml
smart:
  always: [TC-036, TC-044]     # e.g. sign-in, access check
```

## Steps
1. **Get the change** from the person: release notes / ticket text, or the changed files from the
   developers, or the app's git folder + the last released tag (read-only `git diff --name-only`).
2. **Pick:**
   ```
   python <toolkit>/scripts/smart_select.py --notes "<release notes>"
   python <toolkit>/scripts/smart_select.py --files app/tenants/[id]/config/page.tsx ...
   python <toolkit>/scripts/smart_select.py --git ~/code/app --since v2.3.0
   ```
   Output: the picked tests grouped by flow with the matched words, the time saved, and
   `reports/smart/tests.txt` + `selection.md`.
3. **Review with judgement** (this is the important part): read the change and the list. Add tests
   the words missed (e.g. a change to sign-in affects every flow; a shared component affects every
   page using it), drop clearly unrelated picks, and say why. When in doubt, include the flow.
   A risky or large change -> recommend the full run instead.
4. **Run:** `python -m pytest $(cat reports/smart/tests.txt) --junitxml reports/results.xml`, then
   the usual report (`make_report.py`) - tests not run show as "not run in this regression".
