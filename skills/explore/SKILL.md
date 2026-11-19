---
name: explore
description: Exploratory agent - walks through a web app on its own (read-only: follows links and opens tabs, never submits forms or clicks action buttons) and reports what looks broken - JavaScript crashes, server errors, failed calls, broken links and images, slow pages, and wrong text on screen like "undefined", "NaN", "[object Object]", "Something went wrong". Use when someone asks for exploratory testing, a smoke check of the whole site, "find bugs nobody wrote a test for", broken links, or console errors.
---

# Exploratory check (QA engineer style)

Test cases only check what someone thought of. This skill walks through the app like a curious
tester and reports anything that looks broken - safely.

Script: `<toolkit>/scripts/explore.py` (toolkit = two levels above this skill's folder).

## Rules
- **Read-only.** It follows links and clicks tabs only; it never fills or submits forms and never
  clicks action buttons. Links that look risky (logout, delete, remove, suspend, deactivate, revoke,
  reset, download / export) are never opened. Only `qa-toolkit.yaml` allowed hosts.
- Pages behind a login: `--state` with a saved sign-in (see `/qa-toolkit:a11y` for making one).
- **Check each finding by hand** before filing: open the page, reproduce, read the console.
- Deeper exploring (typing odd values into forms, trying flows) can follow with the Playwright
  browser tool - but anything that changes data needs the person's yes first, and only on QA.

## Steps
1. **Run:**
   ```
   python <toolkit>/scripts/explore.py https://qa.example.com/ [--state .auth/state.json] [--max-pages 40]
   ```
   Options: `--per-pattern 2` (pages with only a different id), `--slow-ms 4000`, `--no-tabs`,
   `--deny "<regex>"`, `--browser firefox`, `--out reports/explore`.
2. **Read** `reports/explore/explore-report.html`: verdict, each finding (High: crash, 5xx, page
   that does not open; Medium: failed calls, broken links / images, wrong text; Low: slow pages,
   missing titles) with the page and a screenshot, pages visited, and what was not opened.
3. **Check** each High / Medium finding by hand; say what you confirmed. Confirmed -> `/qa-toolkit:bug`.
4. **Say what it could not cover:** forms and data-changing flows (covered by the automated tests),
   pages only reachable through buttons, and anything past `--max-pages`.
