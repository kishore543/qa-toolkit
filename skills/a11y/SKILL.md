---
name: a11y
description: Accessibility testing against WCAG 2.1 / 2.2 level AA - axe-core scan, a keyboard-only walk (focus moves, focus visible, no trap, nothing clickable left out), and a 320px phone-width (reflow) check, read-only, then a plain-language report with severity, who is affected, the WCAG rule and how to fix each problem. Use when someone asks for accessibility / a11y testing, WCAG or ADA compliance, screen reader or keyboard checks, colour contrast, or an accessibility report.
---

# Accessibility check (QA engineer style)

One script checks each page three ways and builds the report:
1. **axe-core** (Deque's industry-standard checker, shipped inside the toolkit) - WCAG 2.1 / 2.2
   A + AA rules, plus best practices.
2. **Keyboard walk** - presses Tab through the page like a keyboard-only user: does focus move,
   can you SEE where it is, does it get stuck, are clickable things left out?
3. **Phone width** - the page at 320px wide must not scroll sideways (WCAG 1.4.10 Reflow).

Paths: the toolkit folder is two levels above this skill's folder.
- Check: `<toolkit>/scripts/a11y_check.py` (builds the report at the end)
- Report only: `<toolkit>/scripts/a11y_report.py --out reports/a11y`
- axe-core: `<toolkit>/vendor/axe-core/axe.min.js` (MPL-2.0, nothing to install)

## Rules

- **Only sites in `qa-toolkit.yaml` `allowed_hosts`.** The check is read-only: it never clicks,
  types or submits - only the Tab key is pressed - so it is safe on QA like the normal tests.
- **Sign-in is saved, never typed by you.** Pages behind a login need `--state <file>`:
  - easiest: `a11y_check.py --save-state .auth/state.json <login URL>` opens a window, the PERSON
    signs in, presses Enter, and the sign-in is saved; or
  - reuse the project's own login page object + `.env` (as the tests do) and save
    `context.storage_state(path=...)`. Never print the OTP / password.
  - the state file holds a session cookie: put `.auth/` in `.gitignore`.
- **A signed-in state redirects public pages** (e.g. `/login` -> home). Check public pages in a
  separate run without `--state` (`--out reports/a11y_public`).
- **Verify the toolkit's own keyboard findings by hand** before reporting them ("cannot see focus",
  "cannot be reached with Tab", "looks clickable"). Press Tab yourself, or write a short Playwright
  script. axe findings are reliable, but read the element and the numbers before reporting.
- If the walk "ran out of Tab presses" (long lists), "not reached" means nothing - it is ignored.
  Use `--tabs 150` to walk further.
- Never say the site "is accessible". Automated checks find roughly a third to a half of problems;
  say what was checked and what still needs a manual check.

## Steps

1. **Check the setup.** `qa-toolkit.yaml` with the site in `allowed_hosts`; Python with
   `playwright pyyaml openpyxl` (the project's virtual environment is fine).

2. **Pick the pages.** Ask which pages, if not said: the main pages of each role, plus key forms.
   Give each a name: `"Tenants=https://qa.example.com/tenants"`. Only what is on screen at load
   is checked - list dialogs or later steps as a manual check (or open them in a small script).

3. **Run** from the project root:
   ```
   python <toolkit>/scripts/a11y_check.py --title "<Product> - <role>" \
       "Login=https://qa.example.com/login" "Signup=https://qa.example.com/signup"
   python <toolkit>/scripts/a11y_check.py --title "<Product> - <role> (signed in)" --state .auth/state.json \
       "Dashboard=https://qa.example.com/home" "Settings=https://qa.example.com/settings"
   ```
   Options: `--browser firefox|webkit`, `--tabs 150`, `--no-keyboard`, `--no-reflow`, `--out <folder>`.
   About 30-60 seconds per page.

4. **Check what was found** (rule above): read `reports/a11y/a11y-results.json` or the report;
   confirm keyboard findings by hand; look at the screenshot of each problem.

5. **Open and explain:** `open reports/a11y/a11y-report.html` (+ `.xlsx` with Problems, Elements,
   Pages tabs). Say the verdict, each problem in plain words (who it hurts, how to fix), what you
   confirmed by hand, the "needs a human look" list, and the manual checks still to do.

## Reading the verdict

| Verdict | Meaning |
|---|---|
| NO PROBLEMS FOUND | Automated checks found nothing - still do the short manual check |
| NEEDS FIXES | Only moderate / minor problems |
| NOT ACCESSIBLE YET | At least one critical or serious problem - fix before release |
| COULD NOT TEST | No page could be opened |

## Severity (from axe, same words)

- **Critical** - blocks some people completely (e.g. a button with no name, image with no description).
- **Serious** - very hard for some people (e.g. faint text, no visible focus, a field you cannot reach by keyboard).
- **Moderate** - harder than it should be (e.g. content outside landmarks, sideways scrolling on phones).
- **Minor** - small annoyance.

## The manual check that still matters (5-10 minutes per journey)

- Screen reader on one main journey: VoiceOver (Mac: Cmd+F5) or NVDA (Windows).
- Keyboard only: Tab, Shift+Tab, Enter, Space, Esc through the same journey - dialogs too.
- Browser zoom 200%: nothing cut off or overlapping.
- Error messages say what is wrong and how to fix it; videos have captions.

## Practice first

Make a local page with known problems - faint text (`color:#bbb`), an `<img>` with no alt, an icon
button with no text, `button:focus{outline:none}`, a clickable `<div onclick>`, a 900px-wide table -
and check it. Each should show up in the report before you trust a clean result on a real site.
