"""qa-toolkit: security check, piece 4 - LOGIN ABUSE (rate limits).

Checks two protections against someone attacking the login:
  - Wrong code guessing: can you try many wrong OTPs without being slowed or locked?
    (A 6-digit code is only 1,000,000 guesses - without a limit it can be brute-forced.)
  - Code flooding: can you ask for "send me a code" over and over (email spam / cost)?

SAFETY - this is the one piece that can affect the real account, so it is bounded hard:
  - It uses DELIBERATELY WRONG codes, never the real OTP.
  - It sends only a few requests (default 6 guesses, 4 sends) and STOPS the moment a limit
    appears. If there is no limit, it still stops at the small cap - and that is the finding.
  - Tell the developers first. The test account may get a short lock, and a few extra
    code emails may be sent to the test inbox.

Run from the project folder (needs qa-toolkit.yaml and load.yaml with the `login:` steps):
    python3 security_login_abuse.py
    python3 security_login_abuse.py --max-guesses 6 --max-sends 4

Writes/merges reports/security/results.json, then run security_report.py.
"""
import argparse
import datetime
import json
import os
import re
import sys
import time

try:
    import requests
    import yaml
except ImportError:
    raise SystemExit("Missing packages. Run:  python3 -m pip install --user requests pyyaml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from security_check import Findings, safety_check  # noqa: E402
from security_authz import read_env_file, fill  # noqa: E402

TIMEOUT = 20
HEADERS = {"User-Agent": "qa-toolkit-security-check (QA test)"}
WRONG_CODE = "000000"   # a wrong-on-purpose code (format-valid, almost certainly not the real one)
PAUSE = 0.6             # gentle, and enough to see a per-second limit

# Signs the server is applying a limit (as opposed to just "wrong code").
LIMIT_WORDS = re.compile(
    r"too many|many (attempts|requests|tries)|rate.?limit|try again (later|in a|in \d)|"
    r"temporarily (locked|blocked|disabled)|account (is )?locked|locked out|slow down|"
    r"exceeded|please wait|blocked|throttl", re.I)
WRONG_WORDS = re.compile(r"invalid|incorrect|wrong|not match|expired|enter the", re.I)


def send(session, base, step, known, code_override=None):
    """Run one login step (send-code or verify). code_override replaces the OTP field."""
    multipart = {}
    for k, v in (fill(step.get("multipart"), known) or {}).items():
        if code_override is not None and re.search(r"code|otp", k, re.I):
            v = code_override
        multipart[k] = (None, str(v))
    try:
        r = session.request(step.get("method", "GET"), base.rstrip("/") + "/" + step["path"].lstrip("/"),
                            headers=dict(HEADERS, **(fill(step.get("headers"), known) or {})),
                            files=multipart or None, timeout=TIMEOUT)
        return r.status_code, (r.text or "")
    except requests.RequestException as e:
        return None, str(e)


def limited(code, body):
    return code == 429 or bool(LIMIT_WORDS.search(body[:3000]))


def check_wrong_codes(f, base, send_step, verify_step, known, max_guesses):
    """Send one real code request, then several WRONG codes; stop when a limit appears."""
    session = requests.Session()
    code, body = send(session, base, send_step, known)   # ask for a code first (one email)
    if code is None:
        f.add("Login abuse", "Could not start the wrong-code test", "info", "", body, "", group="login-abuse")
        return
    tried = 0
    for i in range(1, max_guesses + 1):
        code, body = send(session, base, verify_step, known, code_override=WRONG_CODE)
        tried = i
        if limited(code, body):
            msg = (re.search(r'"error":"([^"]+)"', body) or re.search(r"<title>([^<]+)</title>", body))
            msg = msg.group(1) if msg else "a limit message"
            f.add("Login abuse", "Wrong codes are rate-limited (good)", "pass", "",
                  "%d wrong code(s) were allowed, then the next was blocked with: “%s”" % (i - 1, msg),
                  "An attacker can't just keep guessing the 6-digit code - after a few wrong tries the login "
                  "stops accepting guesses and forces a new code.",
                  group="login-abuse")
            return
        time.sleep(PAUSE)
    # No limit within our small cap.
    f.add("Login abuse", "No limit seen on wrong code guesses", "fail", "medium",
          "Sent %d wrong codes in a row; each was only told '%s' - no slow-down, lock or block appeared."
          % (tried, "invalid" if WRONG_WORDS.search(body) else "rejected"),
          "A 6-digit code has 1,000,000 possibilities. With no limit, an attacker could try them all and "
          "break into an account. (The test stopped at %d on purpose.)" % tried,
          "Lock or slow the login after a few wrong codes (e.g. 5), per account and per IP; expire the code quickly.",
          group="login-abuse")


def check_code_flood(f, base, send_step, known, max_sends):
    """Ask for a code several times quickly; stop when a limit appears."""
    session = requests.Session()
    sent = 0
    for i in range(1, max_sends + 1):
        code, body = send(session, base, send_step, known)
        sent = i
        if limited(code, body):
            f.add("Login abuse", "Repeated 'send code' is rate-limited (good)", "pass", "",
                  "After %d request(s), asking for a code started being blocked (answer %s)." % (i, code),
                  "Someone can't flood a person's inbox with login codes, or run up email cost.",
                  group="login-abuse")
            return
        time.sleep(PAUSE)
    f.add("Login abuse", "No limit seen on repeated 'send code'", "warn", "low",
          "Asked for a code %d times in a row with no limit (each sends an email)." % sent,
          "Without a limit, someone could flood a user's inbox with login codes, or raise the email bill. "
          "(The test stopped at %d on purpose.)" % sent,
          "Add a short cooldown and a per-account/day cap on sending codes (a client-side timer is not enough).",
          group="login-abuse")


def main():
    parser = argparse.ArgumentParser(description="Login rate-limit checks (bounded, wrong codes only)")
    parser.add_argument("--config", default="load.yaml")
    parser.add_argument("--max-guesses", type=int, default=6, help="most wrong codes to try (stops earlier on a limit)")
    parser.add_argument("--max-sends", type=int, default=4, help="most 'send code' requests to try")
    parser.add_argument("--results", default="reports/security/results.json")
    parser.add_argument("--yes", action="store_true", help="skip the are-you-sure prompt")
    args = parser.parse_args()

    if args.max_guesses > 15 or args.max_sends > 10:
        raise SystemExit("Keep it small: --max-guesses <= 15 and --max-sends <= 10, to avoid locking the account.")
    if not os.path.isfile(args.config):
        raise SystemExit("No %s here - this piece reuses the login from load.yaml." % args.config)
    with open(args.config, encoding="utf-8") as fp:
        load = yaml.safe_load(fp) or {}
    base = load["base_url"].rstrip("/") + "/"
    login = load.get("login") or []
    if len(login) < 2:
        raise SystemExit("load.yaml needs a 2-step login (send code, then verify) for this piece.")
    safety_check(base)
    send_step, verify_step = login[0], login[1]
    known = read_env_file()

    if not args.yes:
        print("This sends up to %d WRONG codes and %d 'send code' requests to %s."
              % (args.max_guesses, args.max_sends, base))
        print("It can briefly lock the test account and send a few emails. Tell the developers first.")
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            raise SystemExit("Stopped - nothing sent.")

    f = Findings()
    print("Login-abuse check on %s (bounded)" % base)
    print("  testing wrong-code guessing...")
    check_wrong_codes(f, base, send_step, verify_step, known, args.max_guesses)
    print("  testing repeated send-code...")
    check_code_flood(f, base, send_step, known, args.max_sends)

    existing = {"base_url": base.rstrip("/"), "pages": [], "findings": []}
    if os.path.isfile(args.results):
        with open(args.results, encoding="utf-8") as fp:
            existing = json.load(fp)
    existing["findings"] = [x for x in existing.get("findings", []) if x.get("group") != "login-abuse"] + f.items
    existing["run_at"] = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    existing.setdefault("base_url", base.rstrip("/"))
    os.makedirs(os.path.dirname(args.results) or ".", exist_ok=True)
    with open(args.results, "w", encoding="utf-8") as fp:
        json.dump(existing, fp, indent=2)
    counts = {s: sum(1 for x in f.items if x["status"] == s) for s in ("fail", "warn", "pass", "info")}
    print("Done: %(fail)d problem(s), %(warn)d worth a look, %(pass)d passed." % counts)
    print("NOTE: if a limit was hit, the test account may be briefly locked - wait a few minutes before using it.")
    print("Merged into: " + os.path.abspath(args.results))


if __name__ == "__main__":
    main()
