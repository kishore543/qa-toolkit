"""qa-toolkit: security check, piece 3 - BAD INPUT (reflected XSS and SQL errors).

Types careful, HARMLESS test text into search boxes / query fields and looks at the
answer. It detects the SIGN of a weakness - it never delivers a working attack and
never changes or deletes anything.

  - XSS: sends a marker with angle brackets, e.g.  qainj1a2b<x>  and checks whether the
    < > come back UNescaped. If they do, a real <script> could have run. The marker is
    NOT a script, so nothing executes - we only prove the gap.
  - SQL: sends one single quote and looks for a database error message in the answer
    (that means the quote reached the database unescaped).

ONLY GET / search requests are made. Forms that create, change, invite, import, pay or
delete are never submitted (stored attacks are out of scope - see the report).

Run from the project folder (needs qa-toolkit.yaml; load.yaml if the search needs login):
    python3 security_inject.py --targets "https://qa.example.com/search?q={q}"
    python3 security_inject.py            # logs in via load.yaml, then auto-finds GET search forms

Writes/merges reports/security/results.json, then run security_report.py.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import uuid
from urllib.parse import urljoin, urlparse, quote, parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import requests
    import yaml
except ImportError:
    raise SystemExit("Missing packages. Run:  python3 -m pip install --user requests pyyaml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from security_check import Findings, safety_check  # noqa: E402
from security_authz import do_login, read_env_file  # noqa: E402  (reuse the proven login)

TIMEOUT = 20
HEADERS = {"User-Agent": "qa-toolkit-security-check (QA test)"}
PAUSE = 0.4   # be gentle on the server

# A database let the quote through unescaped if the answer shows one of these.
SQL_ERRORS = [
    "SQLSTATE", "SQL syntax", "syntax error at or near", "unterminated quoted string",
    "ORA-0", "ORA-1", "SQLiteException", "near \"'\": syntax error", "psycopg2", "psycopg.errors",
    "mysql_fetch", "MySqlException", "PG::SyntaxError", "ActiveRecord::StatementInvalid",
    "SequelizeDatabaseError", "PrismaClientKnownRequestError", "pg_query", "quoted string not properly terminated",
]


def new_marker():
    return "qainj" + uuid.uuid4().hex[:6]


def put_value(target, value):
    """Put the test value where {q} is, or onto the last query parameter."""
    if "{q}" in target:
        return target.replace("{q}", quote(value, safe=""))
    parts = urlsplit(target)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        pairs = [("q", "")]
    pairs[-1] = (pairs[-1][0], value)
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def discover_targets(session, base, pages):
    """Find GET search forms on the given pages: returns URL templates with {q}."""
    found = {}
    for path in pages:
        try:
            r = session.get(urljoin(base, path), headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        for form in re.findall(r"<form\b[^>]*>.*?</form>", r.text, re.S | re.I):
            method = (re.search(r'method="([^"]+)"', form, re.I) or ["", "get"])[1].lower()
            if method and method != "get":
                continue   # only GET forms - never auto-submit a POST (it may change data)
            action = (re.search(r'action="([^"]+)"', form, re.I) or ["", path])[1] or path
            names = re.findall(r'<input\b[^>]*\bname="([^"]+)"', form, re.I)
            search_name = next((n for n in names if re.search(r"search|query|^q$|term|keyword", n, re.I)), None)
            if search_name:
                url = urljoin(base, action)
                found[url + ("&" if "?" in url else "?") + search_name + "={q}"] = True
        # Also: a query the page already uses, e.g. ?search=... in a link
        for q in re.findall(r'href="([^"]*[?&](?:search|q|query|term)=[^"&]*)"', r.text, re.I):
            found[put_value(urljoin(base, q), "{q}").replace(quote("{q}", safe=""), "{q}")] = True
    return list(found)


def probe(session, target, kind, value, baseline_len):
    try:
        r = session.get(put_value(target, value), headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        return None, str(e)
    return r, None


def check_target(f, session, target, role_note):
    pretty = re.sub(r"=[^=&]*\{q\}", "={q}", target)
    # Baseline with a normal word
    base_marker = new_marker()
    r0, err = probe(session, target, "base", base_marker, 0)
    if r0 is None:
        f.add("Bad input", "Could not test %s" % pretty, "info", "", err, "", group="input")
        return
    time.sleep(PAUSE)

    # --- XSS: do the angle brackets come back unescaped? ---
    marker = new_marker()
    xss_value = "%s<x>" % marker          # harmless: <x> is not a script and runs nothing
    rx, _ = probe(session, target, "xss", xss_value, len(r0.text))
    time.sleep(PAUSE)
    if rx is not None and rx.status_code == 200:
        raw = "%s<x>" % marker in rx.text
        escaped = ("%s&lt;x&gt;" % marker in rx.text) or ("%s%%3Cx%%3E" % marker in rx.text)
        if raw and "html" in rx.headers.get("Content-Type", "").lower():
            f.add("Bad input", "Search text is shown back without escaping (possible XSS) at %s" % pretty,
                  "fail", "high",
                  "The marker '%s<x>' came back in the page with the < > NOT turned into &lt; &gt;. "
                  "(A real script was never sent.)" % marker,
                  "If angle brackets survive, someone could put a working <script> in a link and run it in "
                  "a victim's browser (cross-site scripting) - stealing the session or acting as them.",
                  "On the server, HTML-escape everything shown back from user input; add a Content-Security-Policy.",
                  group="input")
        elif raw:
            f.add("Bad input", "Input reflected raw in a non-HTML answer at %s" % pretty, "warn", "low",
                  "The marker came back unescaped, but the answer is %s (not HTML), so a browser would not "
                  "run it as a page." % (rx.headers.get("Content-Type", "unknown type")),
                  "Low risk here, but escape it anyway in case the same data is later shown on an HTML page.",
                  "Escape on output wherever this value is displayed.", group="input")
        else:
            f.add("Bad input", "Search text is safely escaped at %s" % pretty, "pass", "",
                  "The marker came back with < > turned into &lt; &gt; (or removed).",
                  "The field shows user input safely - a script in the search text would not run.", group="input")

    # --- SQL: does one quote cause a database error? ---
    marker = new_marker()
    rq, _ = probe(session, target, "sql", "%s'" % marker, len(r0.text))
    time.sleep(PAUSE)
    if rq is not None:
        hit = next((s for s in SQL_ERRORS if s in rq.text), None)
        if hit:
            f.add("Bad input", "A database error leaks when a quote is entered at %s" % pretty, "fail", "high",
                  "Entering one quote (') produced a database error in the answer: '%s'." % hit,
                  "The quote reached the database unescaped - a sign of SQL injection, which can read or "
                  "change the whole database. (No attack was run - only one quote was sent.)",
                  "Use parameters / prepared statements for every query; never build SQL by joining text.",
                  group="input")
        elif rq.status_code >= 500 and r0.status_code < 500:
            f.add("Bad input", "A quote makes the page fail (500) at %s" % pretty, "warn", "medium",
                  "A normal word answered %s, but one quote (') answered %s." % (r0.status_code, rq.status_code),
                  "The field may not handle special characters safely - worth a closer look for SQL injection.",
                  "Check the query uses parameters, and handle odd input without a server error.", group="input")
        else:
            f.add("Bad input", "A quote is handled safely at %s" % pretty, "pass", "",
                  "Entering one quote (') gave no database error (answered %s)." % rq.status_code,
                  "The field copes with special characters - a good sign against SQL injection.", group="input")


def main():
    parser = argparse.ArgumentParser(description="Bad-input checks (reflected XSS, SQL errors) - GET/search only")
    parser.add_argument("--config", default="load.yaml", help="file with base_url + login (for pages behind login)")
    parser.add_argument("--targets", nargs="*", help="search URLs with {q} where the text goes")
    parser.add_argument("--pages", nargs="*", default=["/", "/courses", "/open-library", "/learning", "/search"],
                        help="pages to auto-search for GET search forms")
    parser.add_argument("--no-login", action="store_true", help="don't log in (public search only)")
    parser.add_argument("--results", default="reports/security/results.json")
    args = parser.parse_args()

    load = {}
    if os.path.isfile(args.config):
        with open(args.config, encoding="utf-8") as fp:
            load = yaml.safe_load(fp) or {}
    base = (load.get("base_url") or (args.targets and "{uri.scheme}://{uri.netloc}".format(uri=urlparse(args.targets[0])))
            or "").rstrip("/") + "/"
    if not base.strip("/"):
        raise SystemExit("No base_url. Add load.yaml, or give full --targets URLs.")
    safety_check(base)

    session = requests.Session()
    f = Findings()
    print("Bad-input check on %s" % base)
    role_note = ""
    if not args.no_login and load.get("login"):
        ok, message, _ = do_login(session, base, load["login"], read_env_file())
        if not ok:
            f.add("Bad input", "Could not log in - searched only public pages", "info", "", message, "",
                  group="input")
        else:
            role_note = " (logged in)"
            print("  logged in" + role_note)

    targets = args.targets or discover_targets(session, base, args.pages)
    if not targets:
        f.add("Bad input", "No search field found to test", "info", "",
              "No GET search form or search link was found on: %s." % ", ".join(args.pages),
              "If the site searches through an API, give it with --targets, e.g. "
              "--targets \"%s/api/courses?search={q}\"." % base.rstrip("/"), group="input")
    else:
        print("  testing %d search field(s)..." % len(targets))
        for t in targets[:12]:
            # Never test a target on another host.
            if urlparse(put_value(t, "x")).hostname != urlparse(base).hostname:
                continue
            check_target(f, session, t, role_note)

    existing = {"base_url": base.rstrip("/"), "pages": [], "findings": []}
    if os.path.isfile(args.results):
        with open(args.results, encoding="utf-8") as fp:
            existing = json.load(fp)
    existing["findings"] = [x for x in existing.get("findings", []) if x.get("group") != "input"] + f.items
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
