"""qa-toolkit: security check, piece 2 - WHO CAN SEE WHAT (access control).

Logs in once as a normal test account, then checks that the site keeps that
account inside its own permissions. The useful result is the NEGATIVE one:
a page that should be refused but is shown.

Run from the project folder (needs qa-toolkit.yaml, and load.yaml for the login):
    python3 security_authz.py
    python3 security_authz.py --admin-pages /users /approvals --role learner

It reuses the proven login from load.yaml (base_url + the `login:` steps), so there
is one place that knows how the app logs in. Secrets stay in .env.

Only GET requests are made (plus the normal login). Nothing is changed.
Writes/merges reports/security/results.json, then run security_report.py.

What it checks:
  - Session cookie after login: hidden from scripts (HttpOnly), https-only (Secure), SameSite
  - Role pages: can this account open pages that look like they are for admins?
  - Another workspace: does this account's login work on a different workspace's site?

IMPORTANT: the tool does not know the app's intended permissions. A page this account
can open is reported as "worth a look" with the heading it showed - a person decides
whether that account is meant to see it.
"""
import argparse
import datetime
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse

try:
    import requests
    import yaml
except ImportError:
    raise SystemExit("Missing packages. Run:  python3 -m pip install --user requests pyyaml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_url import check  # noqa: E402
from security_check import Findings, safety_check, set_cookie_lines, SESSION_WORDS  # noqa: E402

TIMEOUT = 20
HEADERS = {"User-Agent": "qa-toolkit-security-check (QA test, read-only)"}
DENY_WORDS = re.compile(r"access denied|not authori[sz]ed|forbidden|403|you (do not|don't) have permission"
                        r"|no permission|unauthori[sz]ed", re.I)
# Paths that usually belong to admins - checked only if they exist in the app.
ADMIN_GUESS = ["/users", "/users/import", "/approvals", "/reports/pillars", "/admin", "/settings/members"]


# ---------- values from .env (same rules as the load tool) ----------

PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def read_env_file(path=".env"):
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


def fill(value, known):
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: str(known.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: fill(v, known) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, known) for v in value]
    return value


# ---------- log in using load.yaml's proven login ----------

def do_login(session, base, login_steps, known):
    """Run the login steps with requests. Returns (ok, message, login_responses)."""
    responses = []
    for step in login_steps:
        path = fill(step["path"], known)
        if path.startswith("http") and urlparse(path).hostname != urlparse(base).hostname:
            return False, "Login step points to another site - refused.", responses
        multipart = {k: (None, str(v)) for k, v in (fill(step.get("multipart"), known) or {}).items()}
        try:
            r = session.request(step.get("method", "GET"), urljoin(base, path),
                                headers=dict(HEADERS, **(fill(step.get("headers"), known) or {})),
                                files=multipart or None, json=fill(step.get("json"), known),
                                data=fill(step.get("form"), known), timeout=TIMEOUT)
        except requests.RequestException as e:
            return False, "Could not reach the login page: %s" % e, responses
        responses.append(r)
        body = r.text or ""
        for words in ([step["fail_if_text"]] if isinstance(step.get("fail_if_text"), str) else step.get("fail_if_text", [])):
            if words.startswith("re:"):
                m = re.search(words[3:], body, re.M)
                if m:
                    return False, "The app said: %s" % (m.group(1) if m.groups() else m.group(0)), responses
            elif words in body:
                return False, "Login reply contained %r" % words, responses
    return True, "Logged in.", responses


# ---------- checks ----------

def check_session_cookie(f, login_responses):
    seen = {}
    for r in login_responses:
        for line in set_cookie_lines(r):
            name = line.split("=", 1)[0].strip()
            low = line.lower()
            if "expires=thu, 01 jan 1970" in low or re.search(r"max-age=0\b", low):
                continue
            seen[name] = dict(secure="; secure" in low, httponly="; httponly" in low,
                              samesite=(re.search(r"samesite=(\w+)", low) or [None, "not set"])[1])
    session_cookies = {n: c for n, c in seen.items() if SESSION_WORDS.search(n)}
    if not session_cookies:
        f.add("Session", "No session cookie seen at login", "info", "",
              "The login did not set a cookie we recognise as the session (it may use a header token instead).",
              "If the app keeps you logged in with a cookie, check its flags by hand.", group="access")
        return
    for name, c in sorted(session_cookies.items()):
        problems = []
        if not c["httponly"]:
            problems.append("no HttpOnly - page scripts can read it")
        if not c["secure"]:
            problems.append("no Secure - can be sent over plain http")
        if c["samesite"] in ("not set", "none"):
            problems.append("SameSite=%s - sent when another site makes the browser call this one" % c["samesite"])
        if problems:
            f.add("Session", "Login cookie '%s' is not fully protected" % name, "fail",
                  "high" if not c["httponly"] else "medium",
                  "After login, '%s' was set with: %s." % (name, "; ".join(problems)),
                  "This cookie keeps the user signed in. If a script or another site can get it, "
                  "someone can act as that user without their password.",
                  "Set it with: Secure; HttpOnly; SameSite=Lax (or Strict).", group="access")
        else:
            f.add("Session", "Login cookie '%s' is protected" % name, "pass", "",
                  "Secure, HttpOnly, SameSite=%s." % c["samesite"],
                  "The signed-in session can't be read by scripts or reused by other websites.", group="access")


def page_heading(text):
    m = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()[:60] if m else ""


def nav_links(session, base):
    """The paths this account's own pages link to - i.e. what the app offers this account."""
    links = set()
    for start in ("/overview", "/"):
        try:
            r = session.get(urljoin(base, start), headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            links |= set(re.findall(r'href="(/[a-z][a-z0-9/_-]*)"', r.text))
    return links


def check_role_pages(f, session, base, role, pages, in_menu):
    hidden_but_open = []
    for path in pages:
        try:
            r = session.get(urljoin(base, path), headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            continue
        code = r.status_code
        if code == 404:
            continue   # page doesn't exist in this app - skip quietly
        blocked_redirect = code in (301, 302, 303, 307, 308) and "/login" in r.headers.get("Location", "")
        if blocked_redirect or code in (401, 403) or (code == 200 and DENY_WORDS.search(r.text[:4000])):
            f.add("Role access", "A %s is blocked from %s" % (role, path), "pass", "",
                  "Answered %s%s." % (code, " → /login" if blocked_redirect else " (access denied page)" if code == 200 else ""),
                  "The account is kept out of a page it should not see.", group="access")
            continue
        if code == 200:
            heading = page_heading(r.text)
            if path in in_menu:
                # The app itself offers this page to this account - opening it is intended, not a gap.
                f.add("Role access", "%s is in the %s's own menu" % (path, role), "info", "",
                      "Answered 200%s, and it is linked from this account's own pages." %
                      (" (heading '%s')" % heading if heading else ""),
                      "The app gives this account this page on purpose - so this account is not a "
                      "restricted one for %s. To test restriction, use an account that should NOT have it." % path,
                      group="access")
            else:
                hidden_but_open.append(path)
                f.add("Role access", "A %s reached %s even though it is not in the menu" % (role, path),
                      "warn", "medium",
                      "Answered 200%s, but this page is NOT linked from the account's own menu." %
                      (" with the heading '%s'" % heading if heading else ""),
                      "The page is hidden from this account but can still be opened by typing the address. "
                      "If it is meant for admins only, this is a permission gap - hiding the menu link is "
                      "not real protection. Only your team knows the intended permission - please confirm.",
                      "Check the permission on the page AND the data behind it on the server, not just the menu.",
                      group="access")
    if not hidden_but_open:
        f.add("Role access", "No admin page was reachable past the menu for the %s" % role, "pass", "",
              "Tried: %s - each was either blocked, absent, or openly in this account's own menu." % ", ".join(pages),
              "Nothing hidden from this account could be reached by typing its address.", group="access")
    return hidden_but_open


def check_other_workspace(f, session, base, other, settings):
    """Does this account's logged-in session work on a different workspace's site?"""
    if not check(other, settings)[0]:
        f.add("Other workspace", "Skipped the other-workspace check", "info", "",
              "%s is not allowed by qa-toolkit.yaml, so it was not opened." % other,
              "Add it to allowed_hosts + security_test_hosts to include this check.", group="access")
        return
    other = other.rstrip("/") + "/"
    try:
        r = session.get(urljoin(other, "/overview"), headers=HEADERS, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as e:
        f.add("Other workspace", "Could not reach the other workspace", "info", "", str(e), "", group="access")
        return
    sent_to_login = r.status_code in (301, 302, 303, 307, 308) and "/login" in r.headers.get("Location", "")
    if r.status_code == 200 and not DENY_WORDS.search(r.text[:4000]):
        f.add("Other workspace", "This account's login worked on another workspace", "fail", "high",
              "The %s session opened %soverview and got 200." % (urlparse(base).hostname, urlparse(other).hostname),
              "One tenant's login should never open another tenant's workspace - that would let one "
              "customer's user reach another customer's pages.",
              "Tie the session to its own workspace; reject a session cookie used on a different tenant host.",
              group="access")
    else:
        f.add("Other workspace", "This account's login does NOT work on another workspace", "pass", "",
              "%soverview answered %s%s for the %s session." % (urlparse(other).hostname, r.status_code,
              " → /login" if sent_to_login else "", urlparse(base).hostname),
              "Each workspace keeps its own users - one tenant's login can't open another's.", group="access")


# ---------- run ----------

def main():
    parser = argparse.ArgumentParser(description="Access-control security checks (logs in as a test account)")
    parser.add_argument("--config", default="load.yaml", help="file with base_url + the login steps")
    parser.add_argument("--role", default="learner", help="what this test account is, for the report")
    parser.add_argument("--admin-pages", nargs="*", help="pages this account should NOT be able to open")
    parser.add_argument("--other-host", help="another workspace's site, to test cross-workspace login")
    parser.add_argument("--results", default="reports/security/results.json")
    args = parser.parse_args()

    if not os.path.isfile(args.config):
        raise SystemExit("No %s here. Piece 2 reuses the login from load.yaml - make it first "
                         "(or pass --config)." % args.config)
    with open(args.config, encoding="utf-8") as fp:
        load = yaml.safe_load(fp) or {}
    base = load["base_url"].rstrip("/") + "/"
    login_steps = load.get("login") or []
    if not login_steps:
        raise SystemExit("%s has no `login:` steps - piece 2 needs a login to test access control." % args.config)

    safety_check(base)   # must be in security_test_hosts
    known = read_env_file()
    with open("qa-toolkit.yaml", encoding="utf-8") as fp:
        settings = yaml.safe_load(fp) or {}

    session = requests.Session()
    f = Findings()
    print("Access-control check on %s (as %s)" % (base, args.role))

    ok, message, login_responses = do_login(session, base, login_steps, known)
    if not ok:
        f.add("Login", "Could not log in - access checks not run", "info", "", message,
              "Fix the login (account or .env) and run again. This is not a security result.", group="access")
    else:
        print("  logged in; checking pages...")
        check_session_cookie(f, login_responses)
        pages = args.admin_pages or ADMIN_GUESS
        in_menu = nav_links(session, base)
        check_role_pages(f, session, base, args.role, pages, in_menu)
        if args.other_host:
            check_other_workspace(f, session, base, args.other_host, settings)

    # Merge into the piece-1 report (replace any earlier access findings).
    existing = {"base_url": base.rstrip("/"), "pages": [], "findings": []}
    if os.path.isfile(args.results):
        with open(args.results, encoding="utf-8") as fp:
            existing = json.load(fp)
    existing["findings"] = [x for x in existing.get("findings", []) if x.get("group") != "access"] + f.items
    existing["run_at"] = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    existing.setdefault("base_url", base.rstrip("/"))
    os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
    with open(args.results, "w", encoding="utf-8") as fp:
        json.dump(existing, fp, indent=2)
    counts = {s: sum(1 for x in f.items if x["status"] == s) for s in ("fail", "warn", "pass", "info")}
    print("Done: %(fail)d problem(s), %(warn)d worth a look, %(pass)d passed, %(info)d for information." % counts)
    print("Merged into: " + os.path.abspath(args.results))


if __name__ == "__main__":
    main()
