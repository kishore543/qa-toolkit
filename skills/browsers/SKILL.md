---
name: browsers
description: Cross-browser testing - run the SAME automated Playwright tests in Chrome, Firefox, Safari (WebKit), Edge and imitated iPhone / iPad / Android screens, re-try browser-only failures to rule out flaky ones, and build a plain-language report showing which test cases work in which browser. Use when someone asks for cross-browser testing, browser compatibility, "does it work in Safari / Firefox / Edge / on mobile", or a browser matrix.
---

# Cross-browser testing (QA engineer style)

No new tests are written. The tests `/qa-toolkit:automate` already made are run once per
browser, and the report answers one question: **does anything work in one browser but not
in another?**

Paths: the toolkit folder is two levels above this skill's folder.
- Runner: `<toolkit>/scripts/browser_run.py` (runs every browser, re-tries, then builds the report)
- Report only: `<toolkit>/scripts/browser_report.py` (rebuild from the saved results)
- conftest template with the browser switch: `<toolkit>/skills/automate/templates/conftest.py`

## Browsers

| Name | What it is |
|---|---|
| `chromium` | Chrome's open-source engine (default) |
| `chrome`, `msedge` | the real Google Chrome / Microsoft Edge, only if installed on this computer |
| `firefox` | Firefox |
| `webkit` | the engine inside Safari - close to Safari, not the Safari app itself |
| `iphone`, `ipad`, `android` | that device's screen size, touch and browser engine - not a real phone |

## Rules

- **Only sites in `qa-toolkit.yaml`** - the conftest safety guard works in every browser.
- **Each browser is a full run of the tests.** Three browsers = three times the time and three
  times the test data created on the server. Start with ONE short flow, preferably one that only
  reads (e.g. sign-in, lists), before running everything.
- **Run browsers one after another** (the default). `--parallel` is faster but triples the load
  on the QA server at once - ask first.
- **Verify every browser-specific failure by hand before calling it a bug.** The most common
  false alarm: the test clicks before the page has finished loading, and the faster browsers
  (Chrome, Safari) hit it while Firefox does not. If it works by hand in that browser, it is a
  test to fix (wait for the page), not a compatibility bug.
- Never change checks or expected results to make a browser pass.

## Steps

1. **Check the setup.**
   - The project's `conftest.py` must have the `--qa-browser` option. If it does not, add it from
     the toolkit's conftest template: the `BROWSERS` table, `CONTEXT_OPTIONS`, `SCREENSHOTS`, the
     `--qa-browser` option, the `browser` fixture that launches the chosen engine, and
     `browser.new_context(**CONTEXT_OPTIONS)` (merge it into any viewport the project already sets).
     Back up the file first if the project is not in git. Show the person the change.
   - Install the browsers once: `python -m playwright install chromium firefox webkit`
     (use the project's own Python / virtual environment). The runner tells you if one is missing.
   - Check it collects: `python -m pytest <flow file> --collect-only -q --qa-browser webkit`.

2. **Pick what to run.** Ask which flow (test file) and which browsers, if not said.
   Default browsers: `chromium firefox webkit`. Add `iphone android` when the site is used on phones.

3. **Run** from the project root, with the project's own Python:
   ```
   python <toolkit>/scripts/browser_run.py tests/<role>/test_<flow>.py \
       --browsers chromium firefox webkit --cases <test case sheet> --title "<Role> - <Flow>"
   ```
   Options: `-k "TC_036 or TC_037"` for a few tests, `--parallel`, `--no-retry`,
   `--out reports/<role>/browsers` to keep one folder per role.
   It prints each browser's pass/fail as it goes. Long runs: run it in the background.

4. **What it does by itself:**
   - one pytest run per browser -> `reports/browsers/<browser>/results.xml` + failure screenshots
   - a test that failed in SOME browsers is run once more there: passes now = **flaky**, fails
     again = **browser-specific**
   - builds `reports/browsers/browser-report.html` and `.xlsx`

5. **Check every browser-specific failure** (rule above). Read the error and the screenshot from
   `reports/browsers/<browser>/screenshots/<ID>.png`. Then repeat the steps by hand in that browser,
   or with a short Playwright script in that engine. Decide: compatibility bug, or test timing.
   For a test timing problem, propose the fix (wait for the page / for the element to be ready)
   and ask before changing the test.

6. **Open and explain the report:** `open reports/browsers/browser-report.html`.
   Say the verdict, each browser-specific problem and what you found when you checked it by hand,
   and what was not covered (WebKit is not the Safari app; device imitation is not a real phone).

## Reading the verdict

| Verdict | Meaning |
|---|---|
| WORKS IN ALL N BROWSERS | Every test case gave the same result everywhere |
| SAME IN EVERY BROWSER | No browser differences; some tests fail everywhere - see the main test report |
| BROWSER PROBLEMS FOUND | Some test cases pass in one browser and fail in another - check each by hand |
| COULD NOT TEST | No browser produced results (not installed, or the tests could not start) |

## The groups in the report

- **Browser-specific** - fails in some browsers, works in others, also on a second try.
- **Fail in every browser** - not a browser problem: an app bug or a test to fix (main report).
- **Flaky** - failed once, passed on the second try in the same browser: timing or data.
- **Work in every browser** - passed everywhere.

## Practice first

Make a one-page local site where a button is removed only in Firefox
(`if (navigator.userAgent.includes("Firefox")) ...`), write two tests for it, and run them in
`chromium firefox webkit`. The report should show the button test as browser-specific in Firefox.
