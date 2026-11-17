---
name: security
description: Safe security checks of a website on QA/staging - HTTPS and headers, cookies, who-can-see-what (access control), bad input (reflected XSS and SQL errors), and login rate limits - then a plain-language report with a severity for each problem and how to fix it. Use when someone asks for a security test, security check, "is it safe", OWASP basics, headers/HTTPS check, access-control or injection test, or a security report. Not a full penetration test.
---

# Security check (QA engineer style)

Four pieces, safest first. Each one adds to the SAME report (`reports/security/`), so you
can run piece 1 alone, or all four. Every finding says what was found, what it means for
users, and how developers fix it - with a severity (High / Medium / Low).

Paths: the toolkit folder is two levels above this skill's folder.
- Piece 1 (read-only): `<toolkit>/scripts/security_check.py`
- Piece 2 (access): `<toolkit>/scripts/security_authz.py`
- Piece 3 (bad input): `<toolkit>/scripts/security_inject.py`
- Piece 4 (login abuse): `<toolkit>/scripts/security_login_abuse.py`
- Report: `<toolkit>/scripts/security_report.py`

## Rules - never break these

- **Only sites your team owns, QA/staging only.** The site must be in BOTH `allowed_hosts`
  and a new `security_test_hosts` list in `qa-toolkit.yaml`. Without that list, only
  localhost is allowed. Never a production or public site. Every script refuses anything else.
- **Tell the developers before you start** - especially pieces 3 and 4.
- **Nothing is attacked or changed.** The checks only make normal requests and read the
  answers. If a secret file is found, its contents are NEVER copied into the report.
- **Pieces 2-4 log in** with a test account from `.env` (reusing the login in `load.yaml`).
  Secrets stay in `.env`; never print them.
- **Piece 4 can briefly lock the test account or send emails** - it is bounded (a few wrong
  codes, stops at the first limit) and asks before it runs. Do piece 4 last.
- **Verify every problem by hand before reporting it.** The scripts can mis-read a page.
  Confirm a header is really missing (`curl -I`), or a page really opens, before you call
  it a bug. Most false alarms come from not checking.

## Steps

1. **Check the setup.** `python3 -c "import requests, yaml"` (install with
   `pip install requests pyyaml` if missing). `qa-toolkit.yaml` must have the site in
   `allowed_hosts` and in `security_test_hosts`. If `security_test_hosts` is missing, show
   the person the two lines to add and why (it is the extra "yes" for security tests).

2. **Piece 1 - read-only** (safe to run on any allowed QA site, no login needed):
   `python3 <toolkit>/scripts/security_check.py https://qa.example.com --pages / /login`
   Checks HTTPS + redirect + certificate, security headers, cookies for a signed-out
   visitor, CORS, left-open files (`/.env`, `/.git`, backups, source maps), error pages.

3. **Piece 2 - who can see what** (needs `load.yaml` with a working `login:`):
   `python3 <toolkit>/scripts/security_authz.py --other-host https://other-workspace.qa.example.com`
   Logs in as the test account and checks: the session cookie is protected; admin-looking
   pages are blocked (it only flags a page that is HIDDEN from the menu but still opens by
   address); another workspace's site rejects this login.
   - **Role note:** to really test "a learner can't open admin pages", the test account must
     be a LOW-privilege one. If it is an admin, every admin page is in its own menu (intended)
     and the piece can only confirm that - say so.

4. **Piece 3 - bad input** (search fields; needs the login if search is behind it):
   `python3 <toolkit>/scripts/security_inject.py --targets "https://qa.example.com/courses?search={q}"`
   Sends a harmless marker with angle brackets (checks they come back escaped) and one quote
   (checks for a database error). GET/search only - it never submits a form that changes data.
   - Find the real search URL first (open the page, watch the Network tab, or try `?search=`,
     `?q=`). Pass it with `--targets ...{q}...`, or let it auto-find GET search forms.

5. **Piece 4 - login abuse** (LAST; can lock the account - tell the developers):
   `python3 <toolkit>/scripts/security_login_abuse.py --max-guesses 6 --max-sends 3`
   Sends a few WRONG codes (never the real OTP) and a few "send code" requests, and stops at
   the first limit. Asks to confirm first (pass `--yes` only after the person agrees).
   Afterwards, check the test account still logs in with the real code.

6. **Build the report** after each piece (it combines everything run so far):
   `python3 <toolkit>/scripts/security_report.py`
   Then open it: `open reports/security/security-report.html`.

7. **Explain it** in plain words: the verdict, each problem with its severity and fix, what
   passed, and - importantly - **what was NOT tested** (the report lists this). Be honest that
   this is a first safe check, not a full penetration test.

## Reading the verdict

| Verdict | Meaning |
|---|---|
| PASSED | No problems found in these checks |
| MOSTLY OK | No problems, a few low things worth a look |
| NEEDS FIXES | Problems to fix, none of them serious (no High) |
| NOT SAFE YET | At least one serious (High) problem - fix before going live |

## Severity

- **High** - fix before going live; can directly expose users or data (e.g. XSS, a secret file open, a login cookie readable by scripts).
- **Medium** - fix soon; removes a protection so another mistake becomes much worse (e.g. missing CSP, no limit on wrong codes).
- **Low** - good practice; fix when convenient (e.g. a version number shown).

## Practice first

Piece 1 is safe to try on your own computer. The injection checker can be proved on a
deliberately broken local server (reflects input raw / leaks a DB error on a quote) to see
it catch a real High finding before you trust a clean result on a real site.
