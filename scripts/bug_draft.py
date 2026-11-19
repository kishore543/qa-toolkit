"""Bug drafts from the toolkit's own results - ready for a tester to check, then file.

    python3 <toolkit>/scripts/bug_draft.py --cases test-cases/x.csv [--results reports/results.xml]
            [--browsers reports/browsers] [--a11y reports/a11y] [--include-unsure] [--out reports/bugs]

Sources (each one optional, used if its files exist):
  results   failed automated tests. Only "Possible bug in the app" becomes a bug draft.
            "Needs a tester to look" is added only with --include-unsure (marked CHECK FIRST).
            Test problems (screen changed, test out of step with the sheet, environment) are NEVER
            drafted - they are listed under "Not drafted" with the reason.
  browsers  test cases that fail only in some browsers (cross-browser report).
  a11y      accessibility problems, one draft per problem type (all pages together).

Writes to <out>:
  bugs.json        every draft (status: draft) - bug_file.py reads this
  bugs.html        review page: one card per bug, copy buttons, screenshots
  bugs-jira.csv    Jira CSV import file (Summary, Issue Type, Priority, Labels, Description)
  B-01.md ...      one Markdown file per bug (paste into any tracker)

Each draft gets a fingerprint label (qa-fp-<12 chars>) that stays the same while the same bug
keeps happening, so bug_file.py can find it in Jira and comment instead of filing a duplicate.
"""
import argparse
import base64
import csv
import datetime
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_report import combine, read_results, site_under_test  # noqa: E402
from read_cases import read_cases  # noqa: E402

PRIORITY_FROM_SHEET = {"critical": "Highest", "highest": "Highest", "high": "High", "medium": "Medium", "med": "Medium",
                       "low": "Low", "lowest": "Lowest"}
SEVERITY_RULES = [  # first match wins; checked against the plain reason + error
    ("Blocker", r"security|data loss|cannot (log ?in|sign ?in)|another (tenant|workspace)"),
    ("Critical", r"\b5\d\d\b|internal server error|crash|exception"),
    ("Minor", r"\balign|overlap|cut off|truncat|layout|spelling|typo"),
]
A11Y_SEVERITY = {"critical": "Critical", "serious": "Major", "moderate": "Minor", "minor": "Trivial"}
A11Y_PRIORITY = {"critical": "High", "serious": "High", "moderate": "Medium", "minor": "Low"}


# ---------- small helpers ----------

def clean(text):
    """Remove the parts of a message that change every run (numbers, ids, urls, times)."""
    text = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    for pat, rep in [(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<uuid>"),
                     (r"\d{4}-\d{2}-\d{2}[T ][\d:.]+", "<time>"), (r"https?://\S+", "<url>"),
                     (r"\d+(\.\d+)?(ms|s)\b", "<duration>"), (r"\d+", "<n>")]:
        text = re.sub(pat, rep, text, flags=re.I)
    return " ".join(text.lower().split())


def fingerprint(*parts):
    return hashlib.sha1("|".join(clean(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def split_steps(text):
    """'1. Open X 2. Click Y' or lines -> ['Open X', 'Click Y']."""
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?:^|\n|\s)(?=\d{1,2}[.)]\s)", text)
    steps = [re.sub(r"^\d{1,2}[.)]\s*", "", p).strip(" ;\n") for p in parts]
    steps = [s for s in steps if s]
    return steps if len(steps) > 1 else [s.strip() for s in re.split(r"\n+", text) if s.strip()]


def severity_for(text, default="Major"):
    for sev, pat in SEVERITY_RULES:
        if re.search(pat, text or "", re.I):
            return sev
    return default


def esc(t):
    return html.escape(str(t if t is not None else ""))


def img_data(path):
    if path and os.path.isfile(path):
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    return None


# ---------- sources ----------

def from_results(cases_path, results_path, include_unsure, env):
    drafts, skipped = [], []
    if not (cases_path and os.path.isfile(results_path)):
        return drafts, skipped
    results, info = read_results(results_path)
    rows, _ = combine(read_cases(cases_path), results)
    if info.get("started"):
        env["Test run"] = info["started"].strftime("%d %b %Y %H:%M")
    for r in rows:
        if r["result"] != "Failed":
            continue
        cause = r.get("cause")
        tc = r.get("ID", "")
        if cause not in ("bug", "step") or (cause == "step" and not include_unsure):
            why = {"locator": "the screen changed - fix the test (/qa-toolkit:heal)",
                   "out_of_step": "the test does not match the test case sheet - fix the test",
                   "environment": "environment problem - run again",
                   "step": "the test could not finish a step - a tester must look first (use --include-unsure to draft it)"}.get(cause, cause)
            skipped.append({"source": "Automated test", "ref": tc, "title": r.get("Title", ""), "why": why})
            continue
        steps = split_steps(r.get("Steps"))
        if r.get("Test data"):
            steps.append("Test data: " + r["Test data"].replace("\n", "; "))
        pre = r.get("Preconditions") or ""
        shot = os.path.join("reports", "screenshots", tc + ".png")
        text = r.get("reason", "")  # plain words only - the technical error has CSS class names that fool the rules
        drafts.append({
            "source": "Automated test", "ref": tc, "flow": r.get("flow") or r.get("Area") or "",
            "title": "%s - %s (%s)" % (r.get("Title") or tc, short_actual(r.get("reason", "")), tc),
            "severity": severity_for(text), "priority": PRIORITY_FROM_SHEET.get(str(r.get("Priority", "")).strip().lower(), "Medium"),
            "preconditions": pre, "steps": steps, "expected": r.get("Expected result", ""),
            "actual": r.get("reason", ""), "error": (r.get("tech") or "")[:1500],
            "requirement": r.get("Requirement", ""), "screenshot": shot if os.path.isfile(shot) else None,
            "check_first": cause == "step",
            "why_bug": "The app showed something different from the Expected result in the test case sheet." if cause == "bug" else
                       "The test could not finish a step (a button stayed disabled or was missing) - confirm by hand that the APP is wrong before filing.",
            "fingerprint": fingerprint("test", tc, r.get("tech") or r.get("reason", "")),
        })
    return drafts, skipped


def short_actual(reason):
    """A short 'what went wrong' for the bug title."""
    reason = re.sub(r"\s+", " ", reason or "").strip()
    m = re.match(r'Expected to see "(.+?)", but it was not on the screen', reason)
    if m:
        return '"%s" is not shown' % m.group(1)[:60]
    m = re.match(r"Expected the page address to be (\S+)", reason)
    if m:
        return "opens the wrong page (expected %s)" % m.group(1)[:60]
    first = re.split(r"(?<=[.!?])\s", reason)[0]
    return (first[:90] + "...") if len(first) > 90 else first


def from_browsers(folder, env, cases_path=None):
    drafts = []
    run_file = os.path.join(folder or "", "run.json")
    if not folder or not os.path.isfile(run_file):
        return drafts
    sys.path.insert(0, HERE)
    import browser_report
    run, ran, rows = browser_report.load(folder, cases_path)
    label = {b["name"]: b for b in ran}
    for r in rows:
        if r["group"] != "browser":
            continue
        works = [label[n]["label"] for n, s in r["cells"].items() if s in ("Passed", "Flaky")]
        for n in r["failed"]:
            info = r["info"].get(n, {})
            b = label[n]
            shot = os.path.join(folder, n, "screenshots", r["id"] + ".png")
            drafts.append({
                "source": "Cross-browser", "ref": r["id"], "flow": r["flow"],
                "title": "[%s] %s - fails in %s, works in %s (%s)" % (b["label"], r["title"], b["label"], ", ".join(works), r["id"]),
                "severity": "Major", "priority": "Medium", "preconditions": "", "steps": [
                    "Open the site in %s (version %s%s)" % (b["label"], b["version"], ", " + b["device"] if b.get("device") else ""),
                    "Repeat the steps of test case %s" % r["id"]],
                "expected": "Works the same as in %s" % ", ".join(works), "actual": info.get("reason", "Failed"),
                "error": (info.get("tech") or "")[:1500], "requirement": "", "screenshot": shot if os.path.isfile(shot) else None,
                "check_first": True, "browser": "%s %s" % (b["label"], b["version"]),
                "why_bug": "Passes in %s but fails in %s, also on a second try. Confirm by hand in %s - if it works by hand, the test is too quick, not a bug." % (
                    ", ".join(works), b["label"], b["label"]),
                "fingerprint": fingerprint("browser", n, r["id"], info.get("tech") or info.get("reason", "")),
            })
    return drafts


def from_a11y(folder, env):
    drafts = []
    src = os.path.join(folder or "", "a11y-results.json")
    if not folder or not os.path.isfile(src):
        return drafts
    import a11y_report
    with open(src) as f:
        data = json.load(f)
    for i in a11y_report.collect(data):
        title, fix = a11y_report.PLAIN.get(i["id"], (i["help"], None))
        wcag, level = a11y_report.wcag_of(i["tags"])
        pages = sorted(set(i["pages"]))
        urls = [p["url"] for p in data["pages"] if p["name"] in pages]
        examples = ["%s: %s%s" % (e["page"], e["where"], (" - " + e["summary"].replace("\n", " ")) if e.get("summary") else "") for e in i["examples"][:5]]
        drafts.append({
            "source": "Accessibility", "ref": i["id"], "flow": ", ".join(pages),
            "title": "Accessibility: %s (%d element%s, %s)" % (title, i["count"], "" if i["count"] == 1 else "s", ", ".join(pages)),
            "severity": A11Y_SEVERITY.get(i["impact"], "Minor"), "priority": A11Y_PRIORITY.get(i["impact"], "Medium"),
            "preconditions": "Signed in" if data.get("signed_in") else "",
            "steps": ["Open %s" % u for u in urls] + ["Look at the elements listed under Actual result"],
            "expected": "Meets WCAG %s%s." % (wcag, (" (level %s)" % level) if level else ""),
            "actual": "%s. Who it affects: %s. Examples: %s" % (title, i.get("who") or a11y_report.who_of(i["tags"]), " | ".join(examples)),
            "error": "", "requirement": wcag, "screenshot": i.get("shot"),
            "fix": i.get("fix") or fix or "", "rule_url": i.get("url") or "",
            "check_first": bool(i.get("check")),
            "why_bug": "Found by axe-core (an automated WCAG checker)." if i.get("source") == "axe" else
                       "Found by the toolkit's keyboard / phone-width check - confirm by hand before filing.",
            "fingerprint": fingerprint("a11y", i["id"], ",".join(pages)),
        })
    return drafts


# ---------- outputs ----------

def description_text(b, env):
    """Plain text (Jira wiki-free Markdown) - used for .md, CSV and the copy button."""
    out = []
    if b.get("preconditions"):
        out += ["*Preconditions:* " + b["preconditions"], ""]
    if b.get("steps"):
        out += ["*Steps to reproduce*"] + ["%d. %s" % (i + 1, s) for i, s in enumerate(b["steps"])] + [""]
    out += ["*Expected result*", b.get("expected") or "-", "", "*Actual result*", b.get("actual") or "-", ""]
    if b.get("fix"):
        out += ["*How to fix*", b["fix"], ""]
    out += ["*Environment*"] + ["- %s: %s" % (k, v) for k, v in env.items() if v]
    if b.get("browser"):
        out.append("- Browser: " + b["browser"])
    out += ["", "*Source:* %s %s%s" % (b["source"], b["ref"], (" (" + b["requirement"] + ")") if b.get("requirement") else "")]
    if b.get("rule_url"):
        out.append("*Rule details:* " + b["rule_url"])
    if b.get("error"):
        out += ["", "*Technical details*", "{noformat}", b["error"], "{noformat}"]
    out += ["", "Found by qa-toolkit. Fingerprint: qa-fp-" + b["fingerprint"]]
    return "\n".join(out)


def write_outputs(out, drafts, skipped, env, title):
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if re.match(r"B-\d+\.md$", f):
            os.remove(os.path.join(out, f))
    for b in drafts:
        b["description"] = description_text(b, env)
        b["labels"] = ["qa-toolkit", "qa-fp-" + b["fingerprint"], {"Automated test": "qa-auto", "Cross-browser": "qa-browser",
                                                                    "Accessibility": "qa-a11y"}[b["source"]]]
        with open(os.path.join(out, b["id"] + ".md"), "w", encoding="utf-8") as f:
            f.write("# %s\n\n**Severity:** %s · **Priority:** %s · **Labels:** %s\n\n%s\n" % (
                b["title"], b["severity"], b["priority"], ", ".join(b["labels"]), b["description"]))
    with open(os.path.join(out, "bugs.json"), "w") as f:
        json.dump({"title": title, "made": datetime.datetime.now().isoformat(timespec="seconds"), "environment": env,
                   "bugs": drafts, "not_drafted": skipped}, f, indent=1)
    with open(os.path.join(out, "bugs-jira.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Summary", "Issue Type", "Priority", "Labels", "Labels", "Labels", "Description"])
        for b in drafts:
            w.writerow([b["title"][:250], "Bug", b["priority"]] + b["labels"] + [b["description"]])
    with open(os.path.join(out, "bugs.html"), "w", encoding="utf-8") as f:
        f.write(build_html(title, drafts, skipped, env))


SEV_CSS = {"Blocker": "crit", "Critical": "crit", "Major": "serious", "Minor": "warn", "Trivial": "none"}


def build_html(title, drafts, skipped, env):
    cards = []
    for b in drafts:
        shot = img_data(b.get("screenshot"))
        steps = "".join("<li>%s</li>" % esc(s) for s in b.get("steps") or [])
        cards.append(
            '<article class="card bug" id="%s"><div class="bh"><span class="bid">%s</span><span class="pill %s">%s</span>'
            '<span class="pill none">Priority %s</span><span class="src">%s · %s</span>%s</div>'
            '<h3>%s</h3><p class="why">%s</p>'
            '<div class="grid"><div>%s%s<h4>Expected</h4><p>%s</p><h4>Actual</h4><p>%s</p>%s</div>%s</div>'
            '<div class="foot"><span class="fp">qa-fp-%s</span><button class="btn" data-copy="t-%s">Copy title</button>'
            '<button class="btn" data-copy="d-%s">Copy description</button></div>'
            '<textarea hidden id="t-%s">%s</textarea><textarea hidden id="d-%s">%s</textarea></article>' % (
                b["id"], b["id"], SEV_CSS.get(b["severity"], "none"), esc(b["severity"]), esc(b["priority"]), esc(b["source"]), esc(b["ref"]),
                {"confirmed": '<span class="pill good">✅ Confirmed by hand</span>', "not-a-bug": '<span class="pill none">❌ Not a bug - do not file</span>'}.get(
                    b.get("status"), '<span class="pill warn">🔎 Check by hand first</span>' if b.get("check_first") else ""),
                esc(b["title"]), esc(b["why_bug"]),
                ('<h4>Preconditions</h4><p>%s</p>' % esc(b["preconditions"])) if b.get("preconditions") else "",
                ('<h4>Steps to reproduce</h4><ol>%s</ol>' % steps) if steps else "",
                esc(b.get("expected")), esc(b.get("actual")),
                ('<h4>How to fix</h4><p>%s</p>' % esc(b["fix"])) if b.get("fix") else "",
                ('<figure><img src="%s" alt="Screenshot for %s" loading="lazy"></figure>' % (shot, esc(b["id"]))) if shot else "",
                b["fingerprint"], b["id"], b["id"], b["id"], esc(b["title"]), b["id"], esc(b["description"])))
    skip = "".join('<li><b>%s</b> %s <span class="muted">- %s</span></li>' % (esc(s["ref"]), esc(s["title"]), esc(s["why"])) for s in skipped)
    counts = {}
    for b in drafts:
        counts[b["source"]] = counts.get(b["source"], 0) + 1
    meta = "".join("<span>%s</span>" % esc(x) for x in ["%s: %s" % kv for kv in env.items() if kv[1]] +
                   ["%d %s" % (n, s) for s, n in counts.items()])
    return TEMPLATE.replace("$title", esc(title)).replace("$meta", meta).replace("$n", str(len(drafts))).replace(
        "$cards", "".join(cards) or '<div class="card bug">✅ Nothing to file - no failure looked like an app bug.</div>').replace(
        "$skip", ('<ul class="card slist">%s</ul>' % skip) if skip else '<p class="muted">Nothing.</p>').replace(
        "$nskip", str(len(skipped)))


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Bug drafts - $title</title>
<style>
 :root { --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9; --ring:rgba(11,11,11,.10);
  --crit-bg:#f8e3e3; --serious-bg:#fbeae2; --warn-bg:#fdf3dc; --none-bg:#efeee9; --crit:#d03b3b; --serious:#ec835a; --warn:#fab219; --none:#c3c2b7;
  --hero:#0f1b2d; --hero2:#1d3557; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7;
  --ink3:#9a998f; --line:#2c2c2a; --ring:rgba(255,255,255,.10); --crit-bg:#3a1a1a; --serious-bg:#3a2218; --warn-bg:#352a10; --none-bg:#242422; --none:#4a4a46; } }
 * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 .wrap { max-width:1100px; margin:0 auto; padding:0 16px; } .muted { color:var(--ink3); }
 header { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:28px 0 30px; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; } h1 { font-size:30px; margin:4px 0 10px; }
 .meta { display:flex; flex-wrap:wrap; gap:8px; } .meta span { background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.14); padding:4px 10px; border-radius:999px; font-size:13px; }
 .note { margin-top:14px; font-size:14px; opacity:.92; max-width:760px; }
 h2 { font-size:21px; margin:30px 0 6px; } .lead { color:var(--ink2); margin:0 0 14px; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:16px 20px; margin-bottom:14px; }
 .bh { display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .bid { font:700 13px ui-monospace, Menlo, monospace; background:var(--none-bg); padding:2px 8px; border-radius:6px; }
 .src { color:var(--ink3); font-size:13px; } .bug h3 { margin:10px 0 4px; font-size:17px; } .why { color:var(--ink2); margin:0 0 6px; font-size:14px; }
 .pill { font-size:12.5px; font-weight:600; padding:2px 9px; border-radius:999px; } .pill.crit { background:var(--crit-bg); } .pill.serious { background:var(--serious-bg); }
 .pill.warn { background:var(--warn-bg); } .pill.none { background:var(--none-bg); } .pill.good { background:#e6f5e6; }
 .grid { display:grid; grid-template-columns:1fr minmax(0, 400px); gap:18px; } h4 { margin:12px 0 2px; font-size:13px; color:var(--ink3); text-transform:uppercase; letter-spacing:.06em; }
 .grid p { margin:0; } ol { margin:4px 0 0; padding-left:20px; }
 figure { margin:12px 0 0; } figure img { width:100%; max-height:320px; object-fit:contain; object-position:top left; border:1px solid var(--line); border-radius:8px; background:#fff; }
 .foot { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:14px; border-top:1px solid var(--line); padding-top:10px; }
 .fp { font:12px ui-monospace, Menlo, monospace; color:var(--ink3); flex:1; }
 .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:5px 11px; cursor:pointer; }
 .slist { list-style:none; } .slist li { padding:6px 0; border-bottom:1px solid var(--line); } .slist li:last-child { border:0; }
 footer { color:var(--ink3); font-size:12.5px; padding:24px 0 40px; }
 @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
</style></head><body>
<header><div class="wrap"><div class="eyebrow">Bug drafts - check before filing</div><h1>$title</h1><div class="meta">$meta</div>
<p class="note">$n draft(s). Nothing has been sent anywhere. Reproduce each one by hand, then file the confirmed ones (bug_file.py, or copy into your tracker, or import bugs-jira.csv).</p></div></header>
<main class="wrap"><h2>Bugs to file</h2>$cards
<h2>Not drafted ($nskip)</h2><p class="lead">Failures that are test problems, not app bugs - fix the tests instead.</p>$skip
<footer>Made by qa-toolkit</footer></main>
<script>
 document.querySelectorAll("[data-copy]").forEach(function (b) { b.addEventListener("click", function () {
  var t = document.getElementById(b.dataset.copy).value, done = function () { var o = b.textContent; b.textContent = "Copied ✓"; setTimeout(function () { b.textContent = o; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, function () {}); }); });
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Make bug drafts from the toolkit's results (nothing is sent anywhere)")
    ap.add_argument("--cases", help="test case sheet (CSV / Excel) - needed for automated test failures")
    ap.add_argument("--results", default="reports/results.xml")
    ap.add_argument("--browsers", default="reports/browsers", help="cross-browser results folder (used if it exists)")
    ap.add_argument("--a11y", default="reports/a11y", help="accessibility results folder (used if it exists)")
    ap.add_argument("--only", nargs="+", choices=["results", "browsers", "a11y"], help="use only these sources")
    ap.add_argument("--include-unsure", action="store_true", help="also draft 'Needs a tester to look' failures (marked CHECK FIRST)")
    ap.add_argument("--out", default="reports/bugs")
    ap.add_argument("--title", default="Bug drafts")
    ap.add_argument("--env", default="QA", help="environment name for the bug (default QA)")
    args = ap.parse_args()
    use = set(args.only or ["results", "browsers", "a11y"])

    env = {"Environment": args.env, "Site": site_under_test(None) or "", "Drafted": datetime.datetime.now().strftime("%d %b %Y %H:%M")}
    drafts, skipped = [], []
    if "results" in use:
        d, s = from_results(args.cases, args.results, args.include_unsure, env)
        drafts += d
        skipped += s
        if not args.cases and os.path.isfile(args.results):
            print("Note: --cases not given, so automated test failures were not read.")
    if "browsers" in use:
        drafts += from_browsers(args.browsers, env, args.cases)
    if "a11y" in use:
        drafts += from_a11y(args.a11y, env)
    for n, b in enumerate(drafts, 1):
        b["id"] = "B-%02d" % n
        b["status"] = "draft"
    # the same test case found by two sources (e.g. main run + one browser) is probably ONE problem
    for b in drafts:
        twins = [o["id"] for o in drafts if o is not b and o["source"] != "Accessibility" and b["source"] != "Accessibility" and o["ref"] == b["ref"]]
        if twins:
            b["related"] = twins
            b["why_bug"] += " Same test case as %s - probably one problem: file it once." % ", ".join(twins)
    write_outputs(args.out, drafts, skipped, env, args.title)
    print("%d bug draft(s), %d failure(s) not drafted (test problems)." % (len(drafts), len(skipped)))
    for b in drafts:
        print("  %s  %-8s %-7s %s%s%s" % (b["id"], b["severity"], b["priority"], b["title"][:95], "  [check by hand first]" if b.get("check_first") else "",
                                          "  [same TC as %s]" % ", ".join(b["related"]) if b.get("related") else ""))
    print("Review page: " + os.path.abspath(os.path.join(args.out, "bugs.html")))
    print("Nothing was sent anywhere. File confirmed bugs with bug_file.py (dry run first).")


if __name__ == "__main__":
    main()
