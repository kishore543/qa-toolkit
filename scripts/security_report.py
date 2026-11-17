"""qa-toolkit: plain-language security report from reports/security/results.json.

    python3 security_report.py
    python3 security_report.py --results reports/security/results.json --out reports/security/security-report.html

Written for everyone: each problem says what was found, what it means for users,
and how the developers can fix it.
"""
import argparse
import html
import json
import os

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}
SEVERITY_LABEL = {"high": "▲▲ High", "medium": "▲ Medium", "low": "● Low"}

# Each line: (text, the group that covers it). A line is dropped once that group has run.
NOT_TESTED_YET = [
    ("Cookies after logging in (the session cookie)", "access"),
    ("Who can see what: an account opening admin pages, one workspace's login on another", "access"),
    ("Bad input: script or database text typed into search boxes and forms (XSS, SQL injection)", "input"),
    ("Login abuse: many wrong codes or many 'send code' requests (rate limits, account lock)", "login-abuse"),
    ("Data for the wrong user: changing an id in an address to reach someone else's record (IDOR)", "idor"),
    ("File uploads, payments, and the app's own business rules", "business"),
]


def esc(text):
    return html.escape(str(text or ""))


def verdict(findings):
    fails = [x for x in findings if x["status"] == "fail"]
    warns = [x for x in findings if x["status"] == "warn"]
    high = [x for x in fails if x["severity"] == "high"]
    if high:
        return "fail", "NOT SAFE YET - %d serious problem(s) and %d other(s) to fix" % (len(high), len(fails) - len(high))
    if fails:
        return "fail", "NEEDS FIXES - %d problem(s) to fix, none of them serious" % len(fails)
    if warns:
        return "warn", "MOSTLY OK - no problems, %d thing(s) worth a look" % len(warns)
    return "pass", "PASSED - no problems found in these checks"


def problem_card(x):
    kind = "Problem" if x["status"] == "fail" else "Worth a look"
    return """<div class="problem sev-%(sev)s">
 <div class="top"><span class="badge sev-%(sev)s">%(sevlabel)s</span> <span class="kind">%(kind)s · %(area)s</span></div>
 <h3>%(title)s</h3>
 <p><b>What we found:</b> %(found)s</p>
 <p><b>What it means:</b> %(meaning)s</p>
 <p class="fix"><b>How to fix (for developers):</b> %(fix)s</p>
</div>""" % dict(sev=x["severity"], sevlabel=SEVERITY_LABEL.get(x["severity"], ""), kind=kind,
                 area=esc(x["area"]), title=esc(x["title"]), found=esc(x["found"]),
                 meaning=esc(x["meaning"]), fix=esc(x["fix"]) or "-")


def rows(items, icon):
    return "".join("<tr><td>%s</td><td>%s %s</td><td>%s</td></tr>"
                   % (esc(x["area"]), icon, esc(x["title"]), esc(x["found"])) for x in items)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security check - {site}</title>
<style>
 :root {{ --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#eceef2; --head:#eef0f4;
         --pass-bg:#e6f6ec; --pass:#11652f; --fail-bg:#fdeaea; --fail:#a11b1b; --warn-bg:#fff4e0; --warn:#8a5300;
         --low-bg:#eef0f4; --low:#475467; --link:#2a78d6; }}
 @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
         --bg:#141414; --card:#1a1a19; --ink:#f0efec; --muted:#a8a79f; --line:#2c2c2a; --head:#242422;
         --pass-bg:#12321f; --pass:#7fd89b; --fail-bg:#3a1515; --fail:#ff9c94; --warn-bg:#3a2a0e; --warn:#f5c26b;
         --low-bg:#2a2a28; --low:#c9c8c1; --link:#3987e5; }} }}
 :root[data-theme="dark"] {{ --bg:#141414; --card:#1a1a19; --ink:#f0efec; --muted:#a8a79f; --line:#2c2c2a; --head:#242422;
         --pass-bg:#12321f; --pass:#7fd89b; --fail-bg:#3a1515; --fail:#ff9c94; --warn-bg:#3a2a0e; --warn:#f5c26b;
         --low-bg:#2a2a28; --low:#c9c8c1; --link:#3987e5; }}
 body {{ font-family:-apple-system, Segoe UI, Arial, sans-serif; margin:0; background:var(--bg); color:var(--ink); }}
 main {{ max-width:1000px; margin:0 auto; padding:24px 16px 48px; }}
 h1 {{ margin:0 0 4px; font-size:26px; }} h2 {{ margin-top:34px; font-size:20px; }} h3 {{ margin:8px 0; font-size:17px; }}
 .muted {{ color:var(--muted); font-size:14px; }}
 .verdict {{ padding:16px 20px; border-radius:10px; font-size:20px; font-weight:700; margin:20px 0 8px; }}
 .verdict.pass {{ background:var(--pass-bg); color:var(--pass); }} .verdict.fail {{ background:var(--fail-bg); color:var(--fail); }}
 .verdict.warn {{ background:var(--warn-bg); color:var(--warn); }}
 .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:16px; }}
 .card {{ background:var(--card); border-radius:10px; padding:14px 16px; }}
 .card .num {{ font-size:26px; font-weight:700; }} .card .lbl {{ color:var(--muted); font-size:14px; }}
 .problem {{ background:var(--card); border-radius:10px; padding:14px 18px; margin:12px 0; border-left:5px solid var(--low); }}
 .problem.sev-high {{ border-left-color:var(--fail); }} .problem.sev-medium {{ border-left-color:var(--warn); }}
 .problem p {{ margin:6px 0; line-height:1.45; overflow-wrap:anywhere; }} .problem .fix {{ color:var(--muted); }}
 .kind {{ color:var(--muted); font-size:13px; }}
 .badge {{ display:inline-block; padding:3px 10px; border-radius:999px; font-size:13px; font-weight:600; white-space:nowrap; }}
 .badge.sev-high {{ background:var(--fail-bg); color:var(--fail); }} .badge.sev-medium {{ background:var(--warn-bg); color:var(--warn); }}
 .badge.sev-low {{ background:var(--low-bg); color:var(--low); }}
 .wrap {{ overflow-x:auto; }}
 table {{ width:100%; border-collapse:collapse; background:var(--card); border-radius:10px; overflow:hidden; }}
 th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; overflow-wrap:anywhere; }}
 th {{ background:var(--head); font-size:13px; text-transform:uppercase; letter-spacing:.03em; }}
 ul {{ line-height:1.6; }} dt {{ font-weight:600; margin-top:8px; }} dd {{ margin:2px 0 0 0; color:var(--muted); }}
</style></head>
<body><main>
<h1>Security check: {site}</h1>
<div class="muted">Run on {run_at} · safe, read-only checks · pages: {pages}</div>
<div class="verdict {vcss}">{vtext}</div>
<div class="muted">This is a first, safe check - not a full penetration test. See "What was not tested yet" below.</div>

<div class="cards">
 <div class="card"><div class="num">{n_fail}</div><div class="lbl">Problems to fix</div></div>
 <div class="card"><div class="num">{n_warn}</div><div class="lbl">Worth a look</div></div>
 <div class="card"><div class="num">{n_pass}</div><div class="lbl">Checks passed</div></div>
 <div class="card"><div class="num">{n_info}</div><div class="lbl">For information</div></div>
</div>

<h2>Problems to fix</h2>
{problems}

<h2>Checks that passed</h2>
<div class="wrap"><table><tr><th>Area</th><th>Check</th><th>What we saw</th></tr>{passed}</table></div>

<h2>For information</h2>
<div class="wrap"><table><tr><th>Area</th><th>What</th><th>What we saw</th></tr>{info}</table></div>

<h2>What was tested</h2>
<ul>
 <li><b>HTTPS</b> - plain http:// is sent to https://; the certificate is valid and not about to run out</li>
 <li><b>Security headers</b> - the protections the site asks the browser to switch on</li>
 <li><b>Cookies</b> - cookies set for a signed-out visitor are protected</li>
 <li><b>CORS</b> - a made-up website asked for this site's data</li>
 <li><b>Left-open files</b> - secret settings, code history, backups, source maps</li>
 <li><b>Error pages</b> - a missing page shows no program details</li>
</ul>
<p class="muted">Only normal page visits were made - about 25 requests. Nothing was attacked, nothing was changed.</p>

<h2>What was not tested yet</h2>
<ul>{not_tested}</ul>

<h2>Words used in this report</h2>
<dl>
 <dt>▲▲ High</dt><dd>Fix before going live - it can directly expose users or data.</dd>
 <dt>▲ Medium</dt><dd>Fix soon - it removes a protection, so another mistake becomes much worse.</dd>
 <dt>● Low</dt><dd>Good practice - fix when convenient.</dd>
 <dt>Header</dt><dd>A short instruction the site sends to the browser with every page, e.g. "never show me inside another website".</dd>
 <dt>Cookie</dt><dd>A small note the browser keeps for a site - the login cookie is what keeps you signed in.</dd>
</dl>
</main></body></html>
"""


def main():
    parser = argparse.ArgumentParser(description="Plain-language security report")
    parser.add_argument("--results", default="reports/security/results.json")
    parser.add_argument("--out", default="reports/security/security-report.html")
    args = parser.parse_args()
    with open(args.results, encoding="utf-8") as f:
        r = json.load(f)
    items = r["findings"]
    problems = sorted([x for x in items if x["status"] in ("fail", "warn")],
                      key=lambda x: (x["status"] != "fail", SEVERITY_ORDER[x["severity"]]))
    vcss, vtext = verdict(items)
    done_groups = {x.get("group") for x in items}
    not_tested = [text for text, group in NOT_TESTED_YET if group not in done_groups]
    page = PAGE.format(
        site=esc(r["base_url"]), run_at=esc(r["run_at"]), pages=esc(", ".join(r["pages"])),
        vcss=vcss, vtext=esc(vtext),
        n_fail=sum(x["status"] == "fail" for x in items), n_warn=sum(x["status"] == "warn" for x in items),
        n_pass=sum(x["status"] == "pass" for x in items), n_info=sum(x["status"] == "info" for x in items),
        problems="".join(problem_card(x) for x in problems) or "<p>None found in these checks.</p>",
        passed=rows([x for x in items if x["status"] == "pass"], "✔"),
        info=rows([x for x in items if x["status"] == "info"], "ℹ"),
        not_tested="".join("<li>%s</li>" % esc(x) for x in not_tested)
        or "<li>The planned checks have all run - a full penetration test is still a separate, deeper job.</li>")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    print("Security report written: " + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
