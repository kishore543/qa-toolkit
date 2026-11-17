"""qa-toolkit: security check, piece 1 - SAFE, READ-ONLY checks of a website.

Run from the project folder (it needs qa-toolkit.yaml there):
    python3 security_check.py https://staging.example.com
    python3 security_check.py https://staging.example.com --pages / /login /courses

What it checks (only normal page visits - nothing is attacked, nothing is changed):
  - HTTPS: plain http:// is sent to https://, the certificate is valid and not expiring
  - Security headers: the browser protections the site switches on (HSTS, CSP, ...)
  - Cookies: session cookies are hidden from scripts and only sent over HTTPS
  - CORS: other websites cannot read this site's data with the visitor's login
  - Left-open files: /.env, /.git, source maps ... that should never be public
  - Error pages: no program code or stack traces shown to visitors

Writes reports/security/results.json (read by security_report.py).
This is NOT a full penetration test - see "What was not tested" in the report.
"""
import argparse
import datetime
import json
import os
import re
import socket
import ssl
import sys
import uuid
from urllib.parse import urljoin, urlparse

try:
    import requests
    import yaml
except ImportError:
    raise SystemExit("Missing packages. Run:  python3 -m pip install --user requests pyyaml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_url import check  # noqa: E402  (the same safety guard as the other tools)

TIMEOUT = 20
HEADERS = {"User-Agent": "qa-toolkit-security-check (QA test, read-only)"}
SIX_MONTHS = 15552000   # seconds - the usual minimum for HSTS


# ---------- safety ----------

def safety_check(url, project_settings_path="qa-toolkit.yaml"):
    """Security checks need a second, stricter "yes": the site must be in security_test_hosts.
    Without that list, only your own computer is allowed."""
    if not os.path.isfile(project_settings_path):
        raise SystemExit("qa-toolkit safety guard: no qa-toolkit.yaml in this folder - nothing is allowed.")
    with open(project_settings_path, encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}
    allowed, reason = check(url, settings)
    if not allowed:
        raise SystemExit("qa-toolkit safety guard BLOCKED this security check: %s (%s)" % (url, reason))
    hosts = settings.get("security_test_hosts") or ["localhost", "127.0.0.1"]
    if not check(url, {"allowed_hosts": hosts, "blocked_hosts": settings.get("blocked_hosts")})[0]:
        raise SystemExit(
            "qa-toolkit safety guard BLOCKED this security check: %s is not in security_test_hosts "
            "of qa-toolkit.yaml.\nSecurity checks need their own list - only sites your team owns, "
            "QA/staging only, after telling the developers. Add it like this:\n"
            "  security_test_hosts:\n    - %s" % (url, urlparse(url).hostname))


# ---------- results ----------

class Findings:
    """Every check ends as one line: passed / problem / worth a look / for information."""

    def __init__(self):
        self.items = []

    def add(self, area, title, status, severity, found, meaning, fix="", group="readonly"):
        # status: pass | fail | warn | info     severity (for fail/warn): high | medium | low
        # group: readonly (piece 1) | access (piece 2) - lets the report know what has been covered
        self.items.append(dict(area=area, title=title, status=status,
                               severity=severity if status in ("fail", "warn") else "",
                               found=found, meaning=meaning, fix=fix, group=group))


def get(session, url, **kw):
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("allow_redirects", True)
    try:
        return session.get(url, headers=dict(HEADERS, **kw.pop("headers", {})), **kw)
    except requests.RequestException as e:
        return e


def set_cookie_lines(response):
    """Every Set-Cookie line of a reply and of the redirects before it."""
    lines = []
    for r in list(response.history) + [response]:
        try:
            lines += r.raw.headers.getlist("Set-Cookie")
        except AttributeError:
            if r.headers.get("Set-Cookie"):
                lines.append(r.headers["Set-Cookie"])
    return lines


# ---------- 1. HTTPS ----------

def check_https(f, base):
    host = urlparse(base).hostname
    plain = get(requests.Session(), "http://%s/" % host, allow_redirects=False, timeout=10)
    if isinstance(plain, Exception):
        f.add("HTTPS", "Plain http:// is not offered", "pass", "",
              "http://%s/ does not answer - only https:// works." % host,
              "Visitors can't end up on an unencrypted connection.")
    elif plain.status_code in (301, 302, 307, 308) and plain.headers.get("Location", "").startswith("https://"):
        f.add("HTTPS", "http:// is sent to https://", "pass", "",
              "http://%s/ answers %s and sends the visitor to %s" % (host, plain.status_code, plain.headers["Location"]),
              "Someone typing the address without https still gets the encrypted site.")
    else:
        f.add("HTTPS", "http:// is NOT sent to https://", "fail", "high",
              "http://%s/ answered %s without moving the visitor to https." % (host, plain.status_code),
              "On public Wi-Fi, someone nearby could read or change the page, including login codes.",
              "Redirect every http:// request to https:// (301), and add the HSTS header.")

    # The certificate: valid, and how long until it runs out
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                version = tls.version()
        expires = datetime.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (expires - datetime.datetime.utcnow()).days
        status, sev = ("pass", "") if days > 30 else (("warn", "medium") if days > 14 else ("fail", "high"))
        f.add("HTTPS", "Certificate is valid (%d days left)" % days if status == "pass"
              else "Certificate runs out in %d days" % days, status, sev,
              "Valid certificate for %s, runs out on %s; connection uses %s."
              % (host, expires.strftime("%d %b %Y"), version),
              "When the certificate runs out, browsers show a full-page warning and nobody can use the site.",
              "Renew the certificate (or check that automatic renewal works)." if status != "pass" else "")
        if version in ("TLSv1", "TLSv1.1"):
            f.add("HTTPS", "Old encryption version used", "fail", "medium", "The connection used %s." % version,
                  "TLS 1.0/1.1 have known weaknesses and are switched off in modern browsers.",
                  "Allow only TLS 1.2 and 1.3 on the server / load balancer.")
    except ssl.SSLCertVerificationError as e:
        f.add("HTTPS", "Certificate is NOT valid", "fail", "high", "The certificate was refused: %s" % e.verify_message,
              "Browsers warn every visitor that the site may be fake; people learn to click through warnings.",
              "Install a valid certificate for this exact host name.")
    except (OSError, ValueError) as e:
        f.add("HTTPS", "Certificate could not be checked", "info", "", "Could not open an HTTPS connection: %s" % e,
              "Check by hand in the browser (padlock icon).")


# ---------- 2. Security headers ----------

def check_headers(f, pages):
    """pages: {path: response}. A header only 'passes' if every page has it."""
    def missing_on(test):
        return [p for p, r in pages.items() if not test({k.lower(): v for k, v in r.headers.items()})]

    def where(paths):
        return "Missing on: %s" % ", ".join(paths)

    checked = ", ".join(pages)

    # HSTS
    bad = missing_on(lambda h: "strict-transport-security" in h)
    short = [p for p, r in pages.items() if "strict-transport-security" in {k.lower() for k in r.headers}
             and int((re.search(r"max-age=(\d+)", r.headers.get("Strict-Transport-Security", "")) or [0, 0])[1])
             < SIX_MONTHS]
    if bad:
        f.add("Headers", "HSTS header missing", "fail", "medium", where(bad),
              "The browser is not told 'always use https for this site', so the very first visit "
              "can still be pushed onto plain http by someone on the same network.",
              "Add: Strict-Transport-Security: max-age=31536000; includeSubDomains")
    elif short:
        f.add("Headers", "HSTS time is short", "warn", "low", "max-age below 6 months on: %s" % ", ".join(short),
              "The protection runs out quickly between visits.", "Use max-age=31536000 (one year).")
    else:
        f.add("Headers", "HSTS header present", "pass", "", "Checked on: %s" % checked,
              "Browsers remember to always use https for this site.")

    # Content-Security-Policy
    bad = missing_on(lambda h: "content-security-policy" in h)
    report_only = [p for p in bad if "content-security-policy-report-only" in
                   {k.lower() for k in pages[p].headers}]
    if bad:
        f.add("Headers", "Content-Security-Policy missing" if not report_only else
              "Content-Security-Policy only in 'report' mode", "fail" if not report_only else "warn", "medium",
              where(bad),
              "CSP is the browser's second line of defence: if someone manages to put a script into a page "
              "(cross-site scripting), CSP can stop it from running. Without it there is no second line.",
              "Add a Content-Security-Policy header (start with Report-Only, then enforce it).")
    else:
        weak = sorted({p for p, r in pages.items()
                       if re.search(r"script-src[^;]*'unsafe-(inline|eval)'|default-src[^;]*'unsafe-(inline|eval)'",
                                    r.headers.get("Content-Security-Policy", ""))
                       or ("script-src" not in r.headers.get("Content-Security-Policy", "")
                           and "default-src" not in r.headers.get("Content-Security-Policy", ""))})
        if weak:
            f.add("Headers", "Content-Security-Policy is weak for scripts", "warn", "low",
                  "Scripts are not limited, or 'unsafe-inline' / 'unsafe-eval' is allowed, on: %s" % ", ".join(weak),
                  "The policy exists but would not stop an injected script - so it gives little protection.",
                  "Limit script-src to your own domains and use nonces or hashes instead of 'unsafe-inline'.")
        else:
            f.add("Headers", "Content-Security-Policy present", "pass", "", "Checked on: %s" % checked,
                  "The browser blocks scripts that don't come from the allowed places.")

    # Clickjacking
    bad = missing_on(lambda h: h.get("x-frame-options", "").upper() in ("DENY", "SAMEORIGIN")
                     or "frame-ancestors" in h.get("content-security-policy", ""))
    if bad:
        f.add("Headers", "Pages can be shown inside another website (clickjacking)", "fail", "medium", where(bad),
              "A fake website could show this site invisibly inside its own page and trick a logged-in "
              "user into clicking buttons here (e.g. 'approve', 'delete').",
              "Add: X-Frame-Options: DENY (or CSP frame-ancestors 'self').")
    else:
        f.add("Headers", "Pages can't be shown inside other websites", "pass", "", "Checked on: %s" % checked,
              "Protects against clickjacking.")

    # nosniff
    bad = missing_on(lambda h: h.get("x-content-type-options", "").lower() == "nosniff")
    if bad:
        f.add("Headers", "X-Content-Type-Options missing", "fail", "low", where(bad),
              "The browser may guess a file's type, so an uploaded file could be run as a script.",
              "Add: X-Content-Type-Options: nosniff")
    else:
        f.add("Headers", "X-Content-Type-Options: nosniff", "pass", "", "Checked on: %s" % checked,
              "The browser never guesses file types.")

    # Referrer-Policy
    bad = missing_on(lambda h: "referrer-policy" in h)
    if bad:
        f.add("Headers", "Referrer-Policy missing", "warn", "low", where(bad),
              "When a user clicks a link to another site, the full address of this page (which may hold "
              "IDs or an email) can be passed along.",
              "Add: Referrer-Policy: strict-origin-when-cross-origin")
    else:
        f.add("Headers", "Referrer-Policy present", "pass", "", "Checked on: %s" % checked,
              "Page addresses are not leaked to other sites.")

    # Version leaks
    leaks = set()
    for r in pages.values():
        for name in ("Server", "X-Powered-By", "X-AspNet-Version", "X-AspNetMvc-Version"):
            value = r.headers.get(name, "")
            if value and (name != "Server" or re.search(r"\d", value)):
                leaks.add("%s: %s" % (name, value))
    if leaks:
        f.add("Headers", "Server software and version shown", "warn", "low", "; ".join(sorted(leaks)),
              "Tells an attacker exactly which software to look up known weaknesses for.",
              "Remove these headers or hide the version numbers.")
    else:
        f.add("Headers", "No software versions shown", "pass", "", "Checked on: %s" % checked,
              "Attackers don't get a free list of the software in use.")


# ---------- 3. Cookies ----------

SESSION_WORDS = re.compile(r"sess|token|auth|sid|jwt|login|refresh|remember", re.I)


def check_cookies(f, pages):
    seen = {}
    for path, r in pages.items():
        for line in set_cookie_lines(r):
            name = line.split("=", 1)[0].strip()
            low = line.lower()
            if "expires=thu, 01 jan 1970" in low or re.search(r"max-age=0\b", low):
                continue   # a cookie being deleted - nothing to protect
            seen[name] = dict(secure="; secure" in low, httponly="; httponly" in low,
                              samesite=(re.search(r"samesite=(\w+)", low) or [None, "not set"])[1], path=path)
    if not seen:
        f.add("Cookies", "No cookies set before login", "info", "",
              "None of the checked pages set a cookie for a signed-out visitor.",
              "Cookies after login (the session) are checked in the login piece of the security tests.")
        return
    for name, c in sorted(seen.items()):
        session = bool(SESSION_WORDS.search(name))
        problems = []
        if not c["secure"]:
            problems.append("no Secure (can be sent over plain http)")
        if session and not c["httponly"]:
            problems.append("no HttpOnly (page scripts can read it)")
        if c["samesite"] in ("not set", "none"):
            problems.append("SameSite=%s (sent along when another website makes the browser call this site)"
                            % c["samesite"])
        if session and not c["httponly"]:
            status, sev = "fail", "high"
        elif session and not c["secure"]:
            status, sev = "fail", "medium"
        else:   # only SameSite missing (browsers then default to Lax), or a non-login cookie
            status, sev = "warn", "low"
        if problems:
            f.add("Cookies", "Cookie '%s' is not fully protected" % name, status, sev,
                  "Set on %s: %s." % (c["path"], "; ".join(problems)),
                  "This looks like a login/session cookie - if stolen, someone can act as the user."
                  if session else "A cookie without these flags is easier to read or misuse.",
                  "Set the cookie with: Secure; HttpOnly; SameSite=Lax (or Strict).")
        else:
            f.add("Cookies", "Cookie '%s' is protected" % name, "pass", "",
                  "Secure, %sSameSite=%s." % ("HttpOnly, " if c["httponly"] else "", c["samesite"]),
                  "Only sent over https and not usable by other websites.")


# ---------- 4. CORS ----------

def check_cors(f, session, base, paths):
    origin = "https://qa-toolkit-cors-test.example"
    worst = None
    for path in paths:
        r = get(session, urljoin(base, path), headers={"Origin": origin}, allow_redirects=False)
        if isinstance(r, Exception):
            continue
        allow = r.headers.get("Access-Control-Allow-Origin", "")
        creds = r.headers.get("Access-Control-Allow-Credentials", "").lower() == "true"
        if allow == origin and creds:
            worst = ("fail", "high", path, "answers to ANY website, and allows the visitor's login to be used")
            break
        if allow == origin and not worst:
            worst = ("warn", "low", path, "answers to ANY website (without the login)")
    if worst and worst[0] == "fail":
        f.add("CORS", "Any website can read this site's data as the logged-in user", "fail", "high",
              "%s %s (tested with a made-up website as Origin)." % (worst[2], worst[3]),
              "A fake website a user visits could quietly read their data from this site.",
              "Only allow your own front-end origins in Access-Control-Allow-Origin; never copy the Origin back.")
    elif worst:
        f.add("CORS", "Site answers cross-site requests from any website", "warn", "low",
              "%s %s." % (worst[2], worst[3]),
              "Fine for public data; a problem if any private data is served without a login cookie.",
              "Allow only your own origins unless the data is meant to be public.")
    else:
        f.add("CORS", "Other websites can't read this site's data", "pass", "",
              "Checked %s with a made-up website as Origin - not allowed." % ", ".join(paths),
              "A fake website can't read users' data through their browser.")


# ---------- 5. Left-open files ----------

def looks_html(r):
    return "html" in r.headers.get("Content-Type", "").lower() or r.text.lstrip()[:15].lower().startswith(("<!doc", "<html"))


LEFT_OPEN = [
    # path, what it is, how we know it's real (not the site's normal "page not found" page), severity
    ("/.env", "the server's secret settings file (passwords, keys)",
     lambda r: not looks_html(r) and re.search(r"(?m)^[A-Z][A-Z0-9_]+=", r.text), "high"),
    ("/.git/HEAD", "the source code history (the whole code can be downloaded)",
     lambda r: r.text.startswith("ref:") or re.fullmatch(r"[0-9a-f]{40}\s*", r.text or ""), "high"),
    ("/.git/config", "the source code settings",
     lambda r: "[core]" in r.text, "high"),
    ("/.DS_Store", "a Mac folder file that lists file names",
     lambda r: r.content[:8] == b"\x00\x00\x00\x01Bud1", "low"),
    ("/server-status", "the web server's live status page",
     lambda r: "Server Status" in r.text and "Apache" in r.text, "medium"),
    ("/phpinfo.php", "a PHP information page",
     lambda r: "PHP Version" in r.text and "phpinfo" in r.text.lower(), "medium"),
    ("/backup.zip", "a backup file",
     lambda r: r.content[:2] == b"PK", "high"),
    ("/database.sql", "a database dump",
     lambda r: not looks_html(r) and re.search(r"CREATE TABLE|INSERT INTO", r.text, re.I), "high"),
]


def check_left_open(f, session, base, first_page):
    found = []
    for path, what, is_real, sev in LEFT_OPEN:
        r = get(session, urljoin(base, path), allow_redirects=False)
        if isinstance(r, Exception) or r.status_code != 200:
            continue
        if is_real(r):
            found.append(path)
            # Never copy the content into the report - it may hold real secrets.
            f.add("Left-open files", "%s is open to everyone" % path, "fail", sev,
                  "%s answered 200 and looks like %s. (Its content is NOT copied into this report.)" % (path, what),
                  "Anyone on the internet can download it.",
                  "Block this path on the web server and remove the file from the public folder. "
                  "If it holds secrets, change them - assume they were seen.")
    if not found:
        f.add("Left-open files", "No secret or backup files found", "pass", "",
              "Tried: %s - none were open." % ", ".join(p for p, *_ in LEFT_OPEN),
              "The usual files that leak secrets or code are not public.")

    # Source maps: the original, readable source code of the front end
    scripts = re.findall(r'<script[^>]+src="([^"]+\.js)"', first_page.text if first_page is not None else "")
    if scripts:
        js = urljoin(base, scripts[-1])
        r = get(session, js + ".map", allow_redirects=False)
        if not isinstance(r, Exception) and r.status_code == 200 and '"sources"' in r.text[:5000]:
            f.add("Left-open files", "Front-end source code is readable (source maps)", "warn", "low",
                  "%s.map is public." % urlparse(js).path,
                  "Anyone can read the original front-end code, with comments - it makes finding weak spots easier.",
                  "Don't publish .map files on QA/production (or limit them to the team).")
        else:
            f.add("Left-open files", "No source maps published", "pass", "", "Tried %s.map." % urlparse(js).path,
                  "The front-end code is only available in its packed form.")

    # Information only: robots.txt and security.txt
    r = get(session, urljoin(base, "/robots.txt"), allow_redirects=False)
    if not isinstance(r, Exception) and r.status_code == 200 and not looks_html(r):
        hidden = re.findall(r"(?im)^disallow:\s*(\S+)", r.text)
        f.add("Left-open files", "robots.txt lists %d hidden path(s)" % len(hidden), "info", "",
              ", ".join(hidden[:15]) or "No Disallow lines.",
              "robots.txt is public - it should not be the only thing hiding admin or private pages.")
    r = get(session, urljoin(base, "/.well-known/security.txt"), allow_redirects=False)
    has = not isinstance(r, Exception) and r.status_code == 200 and "contact:" in r.text.lower()
    f.add("Left-open files", "security.txt %s" % ("present" if has else "not present"), "info", "",
          "/.well-known/security.txt %s." % ("lists a security contact" if has else "was not found"),
          "Optional: tells people who find a weakness how to report it to the team.")


# ---------- 6. Error pages ----------

STACK_SIGNS = ["Traceback (most recent call last)", "at Object.<anonymous>", "node_modules/",
               "Exception in thread", "Stack trace:", "SQLSTATE[", "ORA-0", "Whitelabel Error Page",
               "DEBUG = True", "Microsoft .NET Framework", "Fatal error</b>:"]


def check_errors(f, session, base):
    path = "/qa-toolkit-missing-%s" % uuid.uuid4().hex[:8]
    weird = path + "?id=%27%22%3C"   # a quote and a < - just to see if the error page shows code
    shown = []
    status = None
    for p in (path, weird):
        r = get(session, urljoin(base, p), allow_redirects=False)
        if isinstance(r, Exception):
            continue
        status = status or r.status_code
        shown += [s for s in STACK_SIGNS if s in r.text]
    if shown:
        f.add("Error pages", "Error page shows program details", "fail", "medium",
              "The 'page not found' answer contains: %s" % ", ".join(sorted(set(shown))),
              "Program code, file paths or database messages help an attacker plan an attack.",
              "Show a plain error page to visitors; keep details in the server logs only.")
    else:
        f.add("Error pages", "Error pages show no program details", "pass", "",
              "A missing page answered %s with no code, paths or stack traces." % status,
              "Visitors (and attackers) only see a plain message.")
    if status == 200:
        f.add("Error pages", "Missing pages answer '200 OK'", "info", "",
              "%s answered 200 instead of 404." % path,
              "Not a security hole, but scanners and monitoring can't tell real pages from missing ones.")


# ---------- run ----------

def main():
    parser = argparse.ArgumentParser(description="Safe, read-only security checks")
    parser.add_argument("url", help="site to check, e.g. https://staging.example.com")
    parser.add_argument("--pages", nargs="*", default=["/", "/login"], help="pages to check headers on")
    parser.add_argument("--out", default="reports/security/results.json")
    args = parser.parse_args()

    base = args.url.rstrip("/") + "/"
    safety_check(base)
    session = requests.Session()
    f = Findings()
    print("Security check (read-only) on %s" % base)

    pages = {}
    for path in args.pages:
        r = get(session, urljoin(base, path))
        if isinstance(r, Exception):
            print("  could not open %s: %s" % (path, r))
            continue
        if urlparse(r.url).hostname != urlparse(base).hostname:
            print("  %s moved to another site (%s) - skipped" % (path, r.url))
            continue
        pages[path] = r
    if not pages:
        raise SystemExit("None of the pages could be opened - check the address.")

    check_https(f, base)
    check_headers(f, pages)
    check_cookies(f, pages)
    check_cors(f, session, base, list(pages))
    check_left_open(f, session, base, next(iter(pages.values())))
    check_errors(f, session, base)

    results = {
        "kind": "security-readonly",
        "base_url": base.rstrip("/"),
        "run_at": datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
        "pages": list(pages),
        "findings": f.items,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2)
    counts = {s: sum(1 for x in f.items if x["status"] == s) for s in ("fail", "warn", "pass", "info")}
    print("Done: %(fail)d problem(s), %(warn)d worth a look, %(pass)d passed, %(info)d for information." % counts)
    print("Results saved: " + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
