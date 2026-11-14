---
name: heal
description: Self-heal broken automated tests - when a Playwright test fails because a locator no longer finds its element (the developers renamed an id, class or text), find the element again in the real page, fix the locator in the page object, and re-run. Never changes checks or expected results. Use when tests fail with "waiting for locator", "element not found", "strict mode violation", or someone asks to heal / fix broken locators.
---

# Self-heal broken locators

A test can fail for two very different reasons:

| Reason | Example | What to do |
|---|---|---|
| **The test is out of date** - a locator no longer finds its element | `#login-btn` was renamed to `data-testid="login"` | **Heal it**: fix the locator |
| **The app is wrong** - the element is found, but shows the wrong thing | the error text says "Invalid" instead of "Your username is invalid!" | **Never heal.** It is a possible bug - leave the test failing |

Healing a real bug would hide it. So this skill only ever changes **locators**.

## Steps

1. **Find the failing tests.** Run `python3 -m pytest -v --junitxml=reports/results.xml`
   (or only the tests the person names, with `-k`). List each failure.

2. **Sort every failure into one group** - read the error, do not guess:
   - **Locator broken** - `Timeout ... waiting for locator(...)`, `element(s) not found`,
     `strict mode violation` (the locator now matches more than one element),
     or `expect(...).to_be_visible()` failing on a locator that matches **nothing**.
   - **Test out of step with the sheet** - the element was found and the app matches the
     test case sheet's Expected result, but the test's own expected value or test data
     says something else (the test was written or edited wrongly). **Do not heal.**
     Show the exact mismatch and suggest the fix; change it only if the person approves,
     and log it as "test data corrected", not as a heal.
   - **Possible app bug** - the element was found but the app does not match the test
     case sheet's Expected result. **Do not heal**, do not touch the test.
   Always compare with the test case sheet - it is the source of truth, not the test code.
   - **Environment** - site down, network error, test account or login problem. **Do not heal.**
   Only the first group goes on to step 3.

3. **Find the element again in the real page.** For each broken locator:
   - Open the page with the Playwright browser tool and repeat the test's steps up to
     the failing step, so the page is in the same state.
   - Find the element **that means the same thing** - use the page object's name for it
     (`submit`, `username`...), the test case step ("Click Submit") and the old locator
     as clues.
   - Pick a new locator in this order: `id`, `get_by_role(name=...)`, `get_by_label`,
     `get_by_test_id`, `get_by_text`. No long CSS chains, XPath or `nth()`.
   - **Check it matches exactly one element** on the page.
   - If the element is **gone** from the page, or there are **several equally good
     candidates**, do not heal. Report it as "needs a human: element missing or unclear"
     - it may be a removed feature or a bug.

4. **Show the planned fixes before changing anything** - one line each:
   `pages/login_page.py  submit:  "#login-btn"  ->  "#submit"   (found by: button text "Submit", id)`
   Ask the person to approve. Apply only what they approve.

5. **Change only the locator line** in the page object. Never change:
   checks (`should_...`, `expect`, `assert`), expected results, test steps, test data,
   timeouts, or the test case sheet. Never add `sleep`, `try/except` or `if` to make a
   test pass.

6. **Re-run** every test that uses the changed page object.
   - Passes now: healed.
   - Still fails: undo the change, and report it under "could not heal".

7. **Write the heal log** - add to `reports/heal-log.md` (create it if missing).
   Take the date and time from the computer's local clock (run `date`):
   ```
   ## <date and time>
   | Test | Page object | Old locator | New locator | How it was found | Result |
   |---|---|---|---|---|---|
   | TC-01 | pages/login_page.py: submit | #login-btn | #submit | button text "Submit", id | Healed - passes |
   ```
   Also list the failures that were **not** healed and why (possible bug / environment /
   needs a human).

8. **Rebuild the report** if the project has one:
   `python3 <toolkit>/scripts/make_report.py --cases <sheet>`
   (the toolkit folder is two levels above this skill's folder).

9. **Summary in plain words:** how many failures, how many healed, how many tests were out of step with the sheet, how many are possible
   bugs, how many need a human. Tell the person to tell the developers about the
   possible bugs - and that a locator that keeps breaking is a sign the app needs
   stable test ids (`data-testid`).

## Rules

- Only open websites listed in `qa-toolkit.yaml`.
- Do not commit or push unless the person asks.
