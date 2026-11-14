---
name: automate
description: Automate test cases from a test case sheet (CSV or Excel .xlsx) as Playwright + Python (pytest) tests the way a QA engineer does - page objects, separate test data, one test per TC ID - then run them and build a plain-language HTML report. Use when someone asks to automate test cases, write automation scripts, or run the automation and get a report.
---

# Automate test cases (QA engineer style)

Start from the test case sheet, not from the code. Every automated test is one
test case from the sheet, named after its TC ID, and the report shows the result
of every test case in plain words.

Paths: the toolkit folder is two levels above this skill's folder.
- Sheet reader: `<toolkit>/scripts/read_cases.py` (CSV or Excel, any column names, all tabs)
- Report script: `<toolkit>/scripts/make_report.py`
- conftest template: `templates/conftest.py` (in this skill's folder)

## Steps

1. **Pick the sheet.** Use the CSV or Excel file the person names, or the newest file
   in `test-cases/`. Run `python3 <toolkit>/scripts/read_cases.py <sheet>` to see what
   it contains (tabs, test case count, blocked), and tell the person.
   Only `Ready` test cases are automated. `Blocked` ones are left for the report.
   A big Excel workbook: ask which tabs to automate first instead of doing all at once.
   Never edit the person's Excel file.

2. **Check the setup.**
   - `qa-toolkit.yaml` must exist in the project root, and the website under test
     must be in `allowed_hosts`. If not, stop and tell the person.
   - If there is no `conftest.py`, copy `templates/conftest.py` to the project root.
     If there is one, do not overwrite it - make sure it has the safety guard and
     the screenshot-on-failure parts from the template.

3. **Explore the real pages first.** Open each page with the Playwright browser tool
   and look at the page before writing locators. Never guess a locator.
   Prefer, in this order: `id`, `get_by_role(name=...)`, `get_by_label`,
   `get_by_test_id`, `get_by_text`. Avoid long CSS chains, XPath and `nth()`.

4. **Page objects** - one class per page in `pages/<page_name>_page.py`:
   - locators in `__init__`
   - actions named like the manual steps: `open()`, `enter_username()`, `click_submit()`
   - checks named `should_...()`, using Playwright `expect` (it waits by itself)
   Reuse page objects that already exist instead of making new ones.

5. **Test data** in `test_data/<feature>_data.py`. Public demo values may go there.
   Real passwords, OTPs and tokens are read from environment variables (`.env`),
   never written in the code.

6. **One test per Ready test case** in `tests/test_<feature>.py`
   (for an Excel workbook: one test file per tab, e.g. `tests/test_super_admin.py`):
   - name: the sheet's ID with `-` turned into `_`, then a short title:
     `TC-01` -> `test_TC_01_<short_title>`, `SA-12` -> `test_SA_12_<short_title>`.
     The report matches tests to the sheet by this ID, so never change or invent IDs.
   - docstring: a header line, then the numbered steps and the expected result,
     copied from the sheet (the Jira bug reporter turns these into the ticket).
     Header: `<ID> | <Requirement, or Area if none> | <Type, or - if none> | <Priority> | <title>`
     ```
     """TC-01 | R1 | Positive | High | <title>

     1. <step 1 from the sheet>
     2. <step 2 from the sheet>
     Expected: <expected result from the sheet>
     """
     ```
     Put the test data values into the steps (e.g. `1. Enter username: student`)
     so a developer can repeat them, but never a real password, OTP or token - write
     `<from .env>` instead.
   - body: page-object calls in the same order as the manual steps, with `# Step n` comments
   - end: checks for **every part** of the Expected result
     (e.g. "heading shown; form gone; no error" = three checks)
   - no `time.sleep`, no `if` that skips the check, no `try/except` around checks
   - tests must not depend on each other; each one starts from a fresh page

7. **Run the tests:** `python3 -m pytest -v --junitxml=reports/results.xml`

8. **When a test fails, decide why - honestly:**
   - **Test problem** (wrong locator, page not loaded yet): fix the test and run again.
   - **The app behaves differently from the Expected result:** that is a possible bug.
     Leave the test failing. Never change the Expected result or weaken a check
     to make a test pass. Say clearly in the summary that it looks like a bug.

9. **Prove the positive tests can fail.** For each Positive test, run it once with
   wrong test data (for example a wrong password) and confirm it fails, then put the
   correct data back and run everything again. A test that can never fail is useless.

10. **Cannot automate a test case?** (needs a real email inbox, a CAPTCHA, a human
    judgement...) Do not write a fake test. Leave it out and give the reason in the summary;
    the report will show it as "Not automated yet".

11. **Build the report:**
    `python3 <toolkit>/scripts/make_report.py --cases <the CSV or Excel file>`
    It writes `reports/report.html`.

12. **Summary in plain words:** how many test cases were automated, passed, failed,
    blocked and not automated; the likely bugs; and the command to open the report:
    `open reports/report.html`

## Rules

- The test case sheet is the source of truth. Never invent steps or expected results.
- Only open websites listed in `qa-toolkit.yaml`.
- Do not commit or push unless the person asks.
