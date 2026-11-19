"""Release dashboard: one page that says READY TO RELEASE / READY WITH RISKS / NOT READY, and why.

    python3 <toolkit>/scripts/release_report.py --cases test-cases/x.csv --release "Sprint 12" [--reports reports]

It reads whatever the other toolkit commands left in the reports folder (each part is optional):
  functional     reports/results.xml + the test case sheet        (/qa-toolkit:automate)
  cross-browser  reports/browsers/run.json                        (/qa-toolkit:browsers)
  accessibility  reports/a11y*/a11y-results.json                  (/qa-toolkit:a11y)
  load           reports/load/**/results*.json                    (/qa-toolkit:load)
  security       reports/security/results.json                    (/qa-toolkit:security)
  bugs           reports/bugs/bugs.json (filed Jira keys)         (/qa-toolkit:bug)
Each area gets PASS / RISK / BLOCKER / NOT TESTED using the same verdicts as its own report.
Overall: any BLOCKER -> NOT READY; any RISK (or a required area not tested) -> READY WITH RISKS; else READY.

Rules can be changed in qa-toolkit.yaml (all optional):
  release:
    required: [functional]                  # areas that must have been tested
    block_on_bug_priority: [critical, high] # possible app bug on these test-case priorities = BLOCKER
    a11y_block_on: critical                 # critical | serious
    max_age_days: 7                         # older results are a RISK ("run again")
Writes <reports>/release/release-report.html and release-summary.md (to paste into chat / email).
The tool advises; the release decision and sign-off stay with people.
"""
import argparse
import datetime
import glob
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ORDER = {"block": 0, "risk": 1, "notrun": 2, "pass": 3}
STATUS = {  # key -> (label, css, icon)
    "block": ("Blocker", "crit", "⛔"), "risk": ("Risk", "warn", "⚠️"),
    "pass": ("Pass", "good", "✅"), "notrun": ("Not tested", "none", "–"),
}
DEFAULTS = {"required": ["functional"], "block_on_bug_priority": ["critical", "high", "blocker", "highest"],
            "a11y_block_on": "critical", "max_age_days": 7}


def esc(t):
    return html.escape(str(t if t is not None else ""))


def rules():
    conf = dict(DEFAULTS)
    try:
        import yaml
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            conf.update((yaml.safe_load(f) or {}).get("release") or {})
    except Exception:
        pass
    conf["block_on_bug_priority"] = [str(p).lower() for p in conf["block_on_bug_priority"]]
    return conf


def parse_when(text):
    for fmt in ("%d %b %Y, %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            pass
    return None


def area(key, name, icon):
    return {"key": key, "name": name, "icon": icon, "status": "notrun", "headline": "Not tested in this release",
            "numbers": [], "blockers": [], "risks": [], "notes": [], "report": None, "when": None}


def settle(a):
    a["status"] = "block" if a["blockers"] else "risk" if a["risks"] else "pass"


def link(path, out_dir):
    return os.path.relpath(path, out_dir) if path and os.path.isfile(path) else None


# ---------- the areas ----------

def functional(cases, reports, conf, out_dir, xml=None, key="functional", name="Functional tests", icon="🤖", report_name="report.html"):
    a = area(key, name, icon)
    xml = xml or os.path.join(reports, "results.xml")
    if not (cases and os.path.isfile(xml)):
        if os.path.isfile(xml):
            a["notes"].append("results.xml exists but no --cases sheet was given, so it was not read.")
        return a
    from make_report import cause_counts, combine, counts_of, read_cases, read_results
    results, info = read_results(xml)
    rows, _ = combine(read_cases(cases), results)
    c, by = counts_of(rows), cause_counts(rows)
    ran = c["Passed"] + c["Failed"] + c["Skipped"]
    a["when"] = info["started"].replace(tzinfo=None) if info.get("started") else None
    a["report"] = link(os.path.join(os.path.dirname(xml), report_name), out_dir)
    a["numbers"] = ["%d test cases" % len(rows), "%d ran" % ran, "%d passed" % c["Passed"], "%d failed" % c["Failed"],
                    "%d%% pass rate" % (round(100.0 * c["Passed"] / ran) if ran else 0)]
    for r in rows:
        pr = str(r.get("Priority", "")).strip().lower()
        label = "%s %s (%s priority)" % (r.get("ID"), r.get("Title", ""), r.get("Priority") or "no")
        if r["result"] == "Failed" and r["cause"] == "bug":
            (a["blockers"] if pr in conf["block_on_bug_priority"] else a["risks"]).append("Possible app bug: " + label)
    if by["step"]:
        a["risks"].append("%d failure(s) not checked yet - the test could not finish a step (a tester must look)" % by["step"])
    if by["locator"] + by["out_of_step"]:
        a["risks"].append("%d test(s) need fixing - those test cases were not really tested" % (by["locator"] + by["out_of_step"]))
    if by["environment"]:
        a["risks"].append("%d test(s) could not run (environment)" % by["environment"])
    untested_high = [r for r in rows if r["result"] in ("Blocked", "Not automated", "Skipped")
                     and str(r.get("Priority", "")).strip().lower() in conf["block_on_bug_priority"]]
    if untested_high:
        a["risks"].append("%d high-priority test case(s) not tested (blocked or not automated): %s" % (
            len(untested_high), ", ".join(r["ID"] for r in untested_high[:8]) + (" ..." if len(untested_high) > 8 else "")))
    other = c["Blocked"] + c["Not automated"] + c["Skipped"] - len(untested_high)
    if other > 0:
        a["notes"].append("%d lower-priority test case(s) not tested yet" % other)
    settle(a)
    a["headline"] = {"block": "Possible app bugs on important test cases", "risk": "No serious app bug, but gaps to close",
                     "pass": "Every test case that ran passed"}[a["status"]]
    return a


def api(reports, conf, out_dir):
    run_file = os.path.join(reports, "api", "run.json")
    if not os.path.isfile(run_file):
        return area("api", "API", "🔌")
    with open(run_file) as f:
        run = json.load(f)
    a = functional(run["sheet"], reports, conf, out_dir, xml=os.path.join(reports, "api", "results.xml"),
                   key="api", name="API", icon="🔌", report_name="api-report.html")
    if not run.get("writes"):
        a["notes"].append("Read-only run (GET only) - create / update / delete were not tested")
    a["headline"] = a["headline"].replace("test cases", "API checks")
    return a


def browsers(reports, conf, out_dir, cases=None):
    a = area("browsers", "Cross-browser", "🌐")
    folder = os.path.join(reports, "browsers")
    if not os.path.isfile(os.path.join(folder, "run.json")):
        return a
    import browser_report
    run, ran, rows = browser_report.load(folder, cases)
    css, word, reason, count = browser_report.verdict(ran, rows, run)
    a["when"] = parse_when(run.get("started"))
    a["report"] = link(os.path.join(folder, "browser-report.html"), out_dir)
    a["numbers"] = [", ".join(b["label"] for b in ran) or "no browser ran", "%d test cases" % len(rows),
                    "%d browser-specific" % count["browser"], "%d flaky" % count["flaky"]]
    if not ran:
        a["risks"].append("No browser produced results")
    for r in rows:
        if r["group"] == "browser":
            a["risks"].append("%s %s fails in %s - check by hand" % (r["id"], r["title"],
                              " and ".join(b["label"] for b in ran if b["name"] in r["failed"])))
    if count["flaky"]:
        a["notes"].append("%d flaky test(s) - passed on a second try" % count["flaky"])
    for b in run["browsers"]:
        if not b.get("version"):
            a["notes"].append("%s was not tested (%s)" % (b["label"], b.get("problem") or "no results"))
    settle(a)
    a["headline"] = word.capitalize()
    return a


def accessibility(reports, conf, out_dir):
    a = area("a11y", "Accessibility", "♿")
    files = sorted(glob.glob(os.path.join(reports, "a11y*", "a11y-results.json")))
    if not files:
        return a
    import a11y_report
    pages, worst = 0, {}
    for fpath in files:
        with open(fpath) as f:
            data = json.load(f)
        when = parse_when(data.get("started"))
        a["when"] = min(filter(None, [a["when"], when])) if (a["when"] or when) else None
        pages += sum(1 for p in data["pages"] if not p.get("error"))
        for i in a11y_report.collect(data):
            title = a11y_report.PLAIN.get(i["id"], (i["help"],))[0]
            key = (i["id"], i["impact"])
            w = worst.setdefault(key, {"title": title, "impact": i["impact"], "count": 0, "pages": set()})
            w["count"] += i["count"]
            w["pages"].update(i["pages"])
        rep = os.path.join(os.path.dirname(fpath), "a11y-report.html")
        if os.path.isfile(rep) and not a["report"]:
            a["report"] = link(rep, out_dir)
    block_levels = ["critical"] + (["serious"] if conf["a11y_block_on"] == "serious" else [])
    by = {}
    for w in worst.values():
        by[w["impact"]] = by.get(w["impact"], 0) + 1
        text = "%s %s: %s (%d element%s, %s)" % (w["impact"].capitalize(), "problem", w["title"], w["count"],
                                                 "" if w["count"] == 1 else "s", ", ".join(sorted(w["pages"])))
        if w["impact"] in block_levels:
            a["blockers"].append(text)
        elif w["impact"] in ("critical", "serious"):
            a["risks"].append(text)
        else:
            a["notes"].append(text)
    a["numbers"] = ["%d page%s" % (pages, "" if pages == 1 else "s"), "WCAG 2.1/2.2 AA"] + \
                   ["%d %s" % (by[k], k) for k in ("critical", "serious", "moderate", "minor") if by.get(k)]
    settle(a)
    a["headline"] = {"block": "Problems that block some people completely", "risk": "Serious problems for some people",
                     "pass": "No critical or serious problems found"}[a["status"]]
    return a


def load(reports, conf, out_dir):
    a = area("load", "Load", "📈")
    files = [f for f in glob.glob(os.path.join(reports, "load", "**", "results*.json"), recursive=True)]
    if not files:
        return a
    import load_report
    journeys = {}
    for fpath in files:
        with open(fpath) as f:
            r = json.load(f)
        name = r.get("name") or os.path.dirname(fpath)
        best = journeys.get(name)
        if not best or r.get("users", 0) > best[0].get("users", 0):
            journeys[name] = (r, fpath)
    for name, (r, fpath) in sorted(journeys.items()):
        css, headline, reasons, findings = load_report.judge(r)
        when = parse_when(r.get("run_at"))
        a["when"] = min(filter(None, [a["when"], when])) if (a["when"] or when) else None
        t = r["total"]
        line = "%s: %d users - %s (typical %s, 95%% within %s, %d of %d requests failed)" % (
            name, r["users"], re.sub(r"<[^>]+>", "", headline), load_report.seconds(t["median_ms"]),
            load_report.seconds(t["p95_ms"]), t["failures"], t["requests"])
        (a["blockers"] if css == "fail" else a["risks"] if css == "warn" else a["notes"]).append(line)
        a["numbers"].append("%s: %d users" % (name, r["users"]))
        rep = fpath.replace("results", "load-report").replace(".json", ".html")
        if os.path.isfile(rep) and not a["report"]:
            a["report"] = link(rep, out_dir)
    settle(a)
    a["headline"] = {"block": "The site did not handle the load", "risk": "Load result not clear",
                     "pass": "Handled the users tested"}[a["status"]]
    return a


def security(reports, conf, out_dir):
    a = area("security", "Security", "🔐")
    fpath = os.path.join(reports, "security", "results.json")
    if not os.path.isfile(fpath):
        return a
    import security_report
    with open(fpath) as f:
        r = json.load(f)
    findings = r.get("findings", [])
    a["when"] = parse_when(r.get("run_at"))
    a["report"] = link(os.path.join(reports, "security", "security-report.html"), out_dir)
    fails = [x for x in findings if x["status"] == "fail"]
    a["numbers"] = ["%d checks" % len(findings), "%d passed" % sum(1 for x in findings if x["status"] == "pass"),
                    "%d problem(s)" % len(fails)]
    for x in fails:
        text = "%s: %s" % (x.get("severity", "").capitalize() or "Problem", x["title"])
        (a["blockers"] if x.get("severity") == "high" else a["risks"]).append(text)
    for x in findings:
        if x["status"] == "warn":
            a["notes"].append("Worth a look: " + x["title"])
    settle(a)
    a["headline"] = re.sub(r"\s*-.*", "", security_report.verdict(findings)[1]).capitalize()
    return a


def bugs_filed(reports):
    fpath = os.path.join(reports, "bugs", "bugs.json")
    if not os.path.isfile(fpath):
        return [], None
    with open(fpath) as f:
        data = json.load(f)
    base = None
    try:
        import yaml
        import bug_file
        with open("qa-toolkit.yaml", encoding="utf-8") as fh:
            env_file = os.path.expanduser(((yaml.safe_load(fh) or {}).get("jira") or {}).get("env_file") or ".env")
        base = (bug_file.read_env(env_file).get("JIRA_BASE_URL") or "").rstrip("/") or None  # only the address, never the token
    except Exception:
        pass
    return [b for b in data.get("bugs", []) if b.get("jira")], base


# ---------- verdict + page ----------

def overall(areas, conf):
    blockers = [(a, t) for a in areas for t in a["blockers"]]
    risks = [(a, t) for a in areas for t in a["risks"]]
    missing = [a for a in areas if a["status"] == "notrun" and a["key"] in conf["required"]]
    if blockers:
        return "block", "NOT READY", "%d blocker%s must be fixed before release." % (len(blockers), "" if len(blockers) == 1 else "s"), blockers, risks, missing
    if risks or missing:
        n = len(risks) + len(missing)
        return "risk", "READY WITH RISKS", "No blockers, but %d risk%s - release only if the owner accepts them." % (n, "" if n == 1 else "s"), blockers, risks, missing
    return "pass", "READY TO RELEASE", "Every area that was tested passed.", blockers, risks, missing


def stale(areas, conf):
    now = datetime.datetime.now()
    for a in areas:
        if a["status"] != "notrun" and a["when"]:
            days = (now - a["when"]).days
            a["age"] = days
            if days > conf["max_age_days"]:
                a["risks"].append("Result is %d days old - run it again on the release build" % days)
                if a["status"] == "pass":
                    a["status"] = "risk"


def card(a):
    label, css, icon = STATUS[a["status"]]
    when = a["when"].strftime("%d %b %Y") if a["when"] else ""
    items = "".join('<li class="b">⛔ %s</li>' % esc(t) for t in a["blockers"]) + \
        "".join('<li class="r">⚠️ %s</li>' % esc(t) for t in a["risks"]) + \
        "".join('<li class="n">%s</li>' % esc(t) for t in a["notes"][:6])
    return ('<article class="card area %s"><div class="ah"><span class="aic">%s</span><h3>%s</h3><span class="pill %s">%s %s</span></div>'
            '<p class="hl">%s</p><div class="nums">%s</div>%s<div class="af"><span class="muted">%s</span>%s</div></article>' % (
                css, a["icon"], esc(a["name"]), css, icon, label, esc(a["headline"]),
                "".join("<span>%s</span>" % esc(n) for n in a["numbers"]),
                ('<ul class="items">%s</ul>' % items) if items else "",
                esc(("Tested " + when) if when else ("" if a["status"] != "notrun" else "Run the matching /qa-toolkit command to include it")),
                ('<a href="%s">Open full report →</a>' % esc(a["report"])) if a["report"] else ""))


def build(title, release, areas, conf, filed, jira_base):
    css, word, reason, blockers, risks, missing = overall(areas, conf)
    blist = "".join('<li><b>%s</b> - %s</li>' % (esc(a["name"]), esc(t)) for a, t in blockers)
    rlist = "".join('<li><b>%s</b> - %s</li>' % (esc(a["name"]), esc(t)) for a, t in risks) + \
        "".join('<li><b>%s</b> - required, but not tested in this release</li>' % esc(a["name"]) for a in missing)
    tested = [a for a in areas if a["status"] != "notrun"]
    nottested = [a["name"] for a in areas if a["status"] == "notrun"]
    bugs = "".join('<li><a href="%s/browse/%s">%s</a> %s <span class="muted">· %s / %s</span></li>' % (
        esc(jira_base or ""), esc(b["jira"]), esc(b["jira"]), esc(b["title"]), esc(b["severity"]), esc(b["priority"]))
        if jira_base else '<li><b>%s</b> %s</li>' % (esc(b["jira"]), esc(b["title"])) for b in filed)
    strip = "".join('<a class="seg %s" href="#a-%s">%s<span>%s</span><em>%s %s</em></a>' % (
        STATUS[a["status"]][1], a["key"], a["icon"], esc(a["name"]), STATUS[a["status"]][2], STATUS[a["status"]][0]) for a in areas)
    return (TEMPLATE.replace("$title", esc(title)).replace("$release", esc(release)).replace("$vcss", css).replace("$vword", esc(word))
            .replace("$vreason", esc(reason)).replace("$strip", strip)
            .replace("$cards", "".join('<div id="a-%s">%s</div>' % (a["key"], card(a)) for a in areas))
            .replace("$blockers", ('<ol class="card list b">%s</ol>' % blist) if blist else '<p class="muted">None.</p>')
            .replace("$risks", ('<ol class="card list r">%s</ol>' % rlist) if rlist else '<p class="muted">None.</p>')
            .replace("$bugs", ('<ul class="card list">%s</ul>' % bugs) if bugs else '<p class="muted">No bugs filed from this toolkit yet.</p>')
            .replace("$tested", esc(", ".join(a["name"] for a in tested) or "nothing"))
            .replace("$nottested", esc(", ".join(nottested) or "nothing - every area was tested"))
            .replace("$rules", esc("Blocker = possible app bug on %s test cases, a %s accessibility problem, load FAILED, or a High security finding. "
                                   "Results older than %d days are a risk." % ("/".join(p for p in conf["block_on_bug_priority"] if p in ("critical", "high")),
                                   " or ".join(["critical"] + (["serious"] if conf["a11y_block_on"] == "serious" else [])), conf["max_age_days"])))
            .replace("$generated", datetime.datetime.now().strftime("%d %b %Y %H:%M"))), (word, reason, blockers, risks, missing, tested, nottested)


def summary_md(title, release, areas, result, filed, jira_base):
    word, reason, blockers, risks, missing, tested, nottested = result
    out = ["**%s - %s: %s**" % (title, release, word), reason, ""]
    for a in areas:
        out.append("- %s %s: %s - %s" % (STATUS[a["status"]][2], a["name"], STATUS[a["status"]][0], a["headline"]))
    if blockers:
        out += ["", "**Blockers**"] + ["%d. %s - %s" % (i + 1, a["name"], t) for i, (a, t) in enumerate(blockers)]
    if risks or missing:
        out += ["", "**Risks**"] + ["- %s - %s" % (a["name"], t) for a, t in risks] + ["- %s - not tested" % a["name"] for a in missing]
    if filed:
        out += ["", "**Bugs filed**"] + ["- %s %s" % ("%s/browse/%s" % (jira_base, b["jira"]) if jira_base else b["jira"], b["title"]) for b in filed]
    out += ["", "Not tested: " + (", ".join(nottested) or "nothing"), "", "_Made by qa-toolkit. The release decision and sign-off stay with the team._"]
    return "\n".join(out) + "\n"


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Release - $release</title>
<style>
 :root { --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9; --ring:rgba(11,11,11,.10);
  --shadow:0 1px 2px rgba(0,0,0,.05), 0 4px 16px rgba(0,0,0,.04); --accent:#2a78d6;
  --good:#0ca30c; --good-bg:#e6f5e6; --warn:#fab219; --warn-bg:#fdf3dc; --crit:#d03b3b; --crit-bg:#f8e3e3; --none:#c3c2b7; --none-bg:#efeee9;
  --hero:#0f1b2d; --hero2:#1d3557; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --ink3:#9a998f;
  --line:#2c2c2a; --ring:rgba(255,255,255,.10); --shadow:none; --accent:#3987e5; --good-bg:#14301a; --warn-bg:#352a10; --crit-bg:#3a1a1a;
  --none:#4a4a46; --none-bg:#242422; --hero:#0b1422; --hero2:#16294a; } }
 * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 a { color:var(--accent); } .wrap { max-width:1160px; margin:0 auto; padding:0 16px; } .muted { color:var(--ink3); }
 header { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:30px 0 70px; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; }
 h1 { font-size:22px; margin:4px 0 14px; font-weight:600; opacity:.95; }
 .big { display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
 .light { width:64px; height:64px; border-radius:50%; flex:none; box-shadow:0 0 0 6px rgba(255,255,255,.10), 0 0 30px rgba(255,255,255,.12); display:grid; place-items:center; font-size:30px; }
 .v-block .light { background:var(--crit); } .v-risk .light { background:var(--warn); } .v-pass .light { background:var(--good); }
 .vword { font-size:42px; font-weight:850; letter-spacing:-.01em; line-height:1.05; } .vreason { font-size:16px; opacity:.92; }
 .strip { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:8px; margin-top:-44px; }
 .seg { display:flex; flex-direction:column; align-items:center; gap:2px; padding:12px 8px; border-radius:12px; text-decoration:none; color:var(--ink);
   background:var(--surface); border:1px solid var(--ring); box-shadow:var(--shadow); border-top:5px solid var(--none); font-size:20px; }
 .seg span { font-size:13px; font-weight:600; color:var(--ink2); } .seg em { font-style:normal; font-size:12.5px; font-weight:700; color:var(--ink); } .seg.crit { border-top-color:var(--crit); } .seg.warn { border-top-color:var(--warn); }
 .seg.good { border-top-color:var(--good); }
 h2 { font-size:21px; margin:32px 0 8px; } .lead { color:var(--ink2); margin:0 0 12px; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; box-shadow:var(--shadow); }
 .list { margin:0; padding:12px 18px 12px 38px; } .list li { margin:6px 0; } .list.b { border-left:4px solid var(--crit); } .list.r { border-left:4px solid var(--warn); }
 .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(330px, 1fr)); gap:14px; }
 .area { padding:16px 18px; height:100%; border-top:4px solid var(--none); display:flex; flex-direction:column; }
 .area.crit { border-top-color:var(--crit); } .area.warn { border-top-color:var(--warn); } .area.good { border-top-color:var(--good); }
 .ah { display:flex; align-items:center; gap:10px; } .aic { font-size:22px; } .ah h3 { margin:0; font-size:17px; flex:1; }
 .pill { font-size:12.5px; font-weight:700; padding:2px 10px; border-radius:999px; white-space:nowrap; }
 .pill.crit { background:var(--crit-bg); } .pill.warn { background:var(--warn-bg); } .pill.good { background:var(--good-bg); } .pill.none { background:var(--none-bg); }
 .hl { margin:8px 0; font-weight:600; } .nums { display:flex; flex-wrap:wrap; gap:6px; }
 .nums span { font-size:12.5px; background:var(--none-bg); padding:2px 8px; border-radius:6px; }
 .items { margin:10px 0 0; padding:0; list-style:none; font-size:13.5px; } .items li { padding:4px 0; border-top:1px solid var(--line); }
 .items li.n { color:var(--ink2); } .af { margin-top:auto; padding-top:12px; display:flex; justify-content:space-between; gap:10px; font-size:13px; }
 .sign { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:18px; padding:18px 20px; }
 .sign div { border-bottom:1px solid var(--ink3); padding-top:34px; font-size:13px; color:var(--ink3); }
 .note { padding:14px 18px; font-size:13.5px; color:var(--ink2); }
 .btn { font:inherit; font-size:13px; border:1px solid rgba(255,255,255,.3); background:rgba(255,255,255,.1); color:#fff; border-radius:8px; padding:6px 12px; cursor:pointer; float:right; }
 footer { color:var(--ink3); font-size:12.5px; padding:30px 0 40px; }
 @media print { .btn { display:none; } header { padding-bottom:30px; } .strip { margin-top:10px; } body { background:#fff; } }
 @media (max-width:640px) { .vword { font-size:30px; } .grid { grid-template-columns:1fr; } }
</style></head><body>
<header class="v-$vcss"><div class="wrap"><button class="btn" onclick="window.print()">Print / PDF</button>
 <div class="eyebrow">Release readiness · $title</div><h1>$release</h1>
 <div class="big"><div class="light"></div><div><div class="vword">$vword</div><div class="vreason">$vreason</div></div></div>
</div></header>
<main class="wrap">
 <nav class="strip">$strip</nav>
 <h2>Blockers</h2><p class="lead">Fix these before releasing.</p>$blockers
 <h2>Risks</h2><p class="lead">Not blocking on their own - the release owner decides whether to accept each one.</p>$risks
 <h2>Each area</h2><div class="grid">$cards</div>
 <h2>Bugs filed</h2>$bugs
 <h2>Sign-off</h2><div class="card sign"><div>QA</div><div>Release owner</div><div>Date and decision</div></div>
 <h2>How this was decided</h2><div class="card note">
  <p><b>Tested:</b> $tested. <b>Not tested:</b> $nottested.</p>
  <p>$rules Rules can be changed in qa-toolkit.yaml (release:). This page advises - the decision is the team's.</p></div>
 <footer>Made by qa-toolkit · $generated</footer>
</main></body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Release readiness dashboard from all qa-toolkit reports")
    ap.add_argument("--cases", help="test case sheet (CSV / Excel) for the functional results")
    ap.add_argument("--reports", default="reports", help="the reports folder (default reports)")
    ap.add_argument("--release", default="Release " + datetime.date.today().strftime("%d %b %Y"), help='e.g. "Sprint 12" or "v2.4"')
    ap.add_argument("--title", default="", help="product / module name")
    ap.add_argument("--out", help="output folder (default <reports>/release)")
    args = ap.parse_args()
    conf = rules()
    out_dir = args.out or os.path.join(args.reports, "release")
    os.makedirs(out_dir, exist_ok=True)
    title = args.title or "Release check"

    areas = [functional(args.cases, args.reports, conf, out_dir), api(args.reports, conf, out_dir), browsers(args.reports, conf, out_dir, args.cases),
             accessibility(args.reports, conf, out_dir), load(args.reports, conf, out_dir), security(args.reports, conf, out_dir)]
    stale(areas, conf)
    filed, jira_base = bugs_filed(args.reports)
    page, result = build(title, args.release, areas, conf, filed, jira_base)
    with open(os.path.join(out_dir, "release-report.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(out_dir, "release-summary.md"), "w", encoding="utf-8") as f:
        f.write(summary_md(title, args.release, areas, result, filed, jira_base))
    word, reason = result[0], result[1]
    print("%s - %s" % (word, reason))
    for a in areas:
        print("  %s %-17s %-10s %s" % (STATUS[a["status"]][2], a["name"], STATUS[a["status"]][0], a["headline"]))
    print("Dashboard: " + os.path.abspath(os.path.join(out_dir, "release-report.html")))
    print("Summary to paste: " + os.path.abspath(os.path.join(out_dir, "release-summary.md")))


if __name__ == "__main__":
    main()
