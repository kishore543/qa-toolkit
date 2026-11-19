"""Cross-browser report: which test cases work in which browser, in plain words.

    python3 <toolkit>/scripts/browser_report.py [--out reports/browsers] [--cases sheet.csv] [--title "..."]

Reads what browser_run.py left in the out folder:
  run.json                      browsers, versions, timings, what was re-tried
  <browser>/results.xml         pytest results in that browser
  <browser>/retry.xml           second try of tests that failed only in that browser
  <browser>/screenshots/<ID>.png
Writes <out>/browser-report.html and <out>/browser-report.xlsx.

Every test case lands in exactly one group:
  works       passed in every browser
  browser     fails in some browsers but works in others, also on a second try  -> compatibility problem
  everywhere  fails in every browser -> NOT a browser problem (an app bug or a test to fix; see the main report)
  flaky       failed once in a browser, passed on the second try -> random, not a browser problem
  skipped     skipped / not run in at least one browser, never failed
"""
import argparse
import base64
import datetime
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_report import read_results  # noqa: E402  (same folder)
from read_cases import read_cases  # noqa: E402

CELL = {  # state -> (icon, short word, css)
    "Passed": ("✅", "Passed", "good"),
    "Flaky": ("🔁", "Passed on retry", "warn"),
    "Failed": ("❌", "Failed", "crit"),
    "Skipped": ("⏭", "Skipped", "none"),
    "Missing": ("–", "Not run", "none"),
}
GROUPS = [  # key, title, css, icon, one-line meaning
    ("browser", "Browser-specific problems", "crit", "❌", "Work in one browser, fail in another - also on a second try"),
    ("everywhere", "Fail in every browser", "serious", "⚠️", "Not a browser problem - an app bug or a test to fix"),
    ("flaky", "Flaky", "warn", "🔁", "Failed once, passed on the second try - random, not the browser"),
    ("skipped", "Skipped somewhere", "none", "⏭", "Skipped or not run in at least one browser"),
    ("works", "Work in every browser", "good", "✅", "Passed in every browser tested"),
]


def esc(text):
    return html.escape(str(text if text is not None else "")).replace("\n", "<br>")


def pct(part, whole):
    return int(round(100.0 * part / whole)) if whole else 0


def duration(seconds):
    seconds = int(seconds or 0)
    return "%dm %02ds" % (seconds // 60, seconds % 60) if seconds >= 60 else "%ds" % seconds


def screenshot(out, browser, tc):
    path = os.path.join(out, browser, "screenshots", tc + ".png")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def tc_sort_key(tc):
    m = re.match(r"([A-Z0-9]+)-(\d+)", tc)
    return (m.group(1), int(m.group(2))) if m else (tc, 0)


# ---------- reading ----------

def load(out, cases_path):
    with open(os.path.join(out, "run.json")) as f:
        run = json.load(f)
    ran = [b for b in run["browsers"] if b.get("version")]
    per, retry = {}, {}
    for b in ran:
        per[b["name"]] = read_results(os.path.join(out, b["name"], "results.xml"))[0]
        retry[b["name"]] = read_results(os.path.join(out, b["name"], "retry.xml"))[0]
    ran = [b for b in ran if per[b["name"]]]  # a browser with no results at all could not test
    cases = {}
    if cases_path:
        for c in read_cases(cases_path):
            cases[c.get("ID", "").upper()] = c

    rows = []
    for tc in sorted(set().union(*[set(per[b["name"]]) for b in ran]) if ran else [], key=tc_sort_key):
        cells, info = {}, {}
        for b in ran:
            n = b["name"]
            r = per[n].get(tc)
            if not r:
                cells[n] = "Missing"
                continue
            state = r["result"]
            if state == "Failed" and retry[n].get(tc, {}).get("result") == "Passed":
                state = "Flaky"
            cells[n] = state
            info[n] = r
        states = list(cells.values())
        failed = [n for n, s in cells.items() if s == "Failed"]
        if failed and len(failed) == len(cells):
            group = "everywhere"
        elif failed:
            group = "browser"
        elif "Flaky" in states:
            group = "flaky"
        elif any(s in ("Skipped", "Missing") for s in states):
            group = "skipped"
        else:
            group = "works"
        any_info = next(iter(info.values()), {})
        case = cases.get(tc, {})
        rows.append({"id": tc, "title": case.get("Title") or (any_info.get("test_name") or "").split(tc.replace("-", "_"), 1)[-1].strip("_").replace("_", " "),
                     "flow": any_info.get("flow") or "Other", "priority": case.get("Priority", ""),
                     "cells": cells, "info": info, "group": group, "failed": failed})
    return run, ran, rows


def verdict(ran, rows, run):
    count = {g[0]: sum(1 for r in rows if r["group"] == g[0]) for g in GROUPS}
    names = ", ".join(b["label"] for b in ran)
    if not ran:
        return "blocked", "COULD NOT TEST", "No browser produced results - see the browser cards below.", count
    if count["browser"]:
        where = sorted({next(b["label"] for b in ran if b["name"] == n) for r in rows if r["group"] == "browser" for n in r["failed"]})
        return "fail", "BROWSER PROBLEMS FOUND", "%d test case%s in some browsers but fail%s in %s - check by hand before reporting a bug." % (
            count["browser"], " works" if count["browser"] == 1 else "s work", "s" if count["browser"] == 1 else "", " and ".join(where)), count
    if count["everywhere"]:
        return "warnv", "SAME IN EVERY BROWSER", (
            "No browser-specific problems in %s. %d test case%s fail in every browser - that is not a browser "
            "problem; check them in the main test report." % (names, count["everywhere"], "" if count["everywhere"] == 1 else "s")), count
    return "pass", "WORKS IN ALL %d BROWSERS" % len(ran), "Every test case gave the same result in %s." % names, count


# ---------- HTML ----------

def browser_cards(run, ran, rows):
    out = []
    for b in run["browsers"]:
        n = b["name"]
        if b not in ran:
            why = b.get("problem") or "No results were produced - see the run log in this browser's folder."
            out.append('<div class="bcard off"><div class="bname">%s</div><div class="babout">%s</div>'
                       '<div class="pill none">⚠️ Not tested</div><div class="bnote">%s</div></div>'
                       % (esc(b["label"]), esc(b["about"]), esc(why)))
            continue
        total = sum(1 for r in rows if n in r["cells"] and r["cells"][n] != "Missing")
        ok = sum(1 for r in rows if r["cells"].get(n) in ("Passed", "Flaky"))
        bad = sum(1 for r in rows if r["cells"].get(n) == "Failed")
        only = sum(1 for r in rows if r["group"] == "browser" and n in r["failed"])
        flag = ('<div class="pill crit">❌ %d fail%s only here</div>' % (only, "s" if only == 1 else "")) if only else \
               '<div class="pill good">✅ no browser-only failures</div>'
        out.append(
            '<div class="bcard"><div class="bname">%s</div><div class="babout">%s</div>'
            '<div class="bver">version %s%s · %s</div>'
            '<div class="bnum"><b>%d</b> / %d passed <span class="muted">(%d%%)</span></div>'
            '<div class="stack" role="img" aria-label="%d passed, %d failed"><span class="seg good" style="flex:%d"></span>'
            '<span class="seg crit" style="flex:%d"></span><span class="seg none" style="flex:%d"></span></div>%s</div>'
            % (esc(b["label"]), esc(b["about"]), esc(b["version"]), esc(" · " + b["device"]) if b.get("device") else "",
               duration(b.get("seconds")), ok, total, pct(ok, total), ok, bad, ok, bad, max(0, total - ok - bad), flag))
    return "".join(out)


def kpis(count, total):
    tiles = []
    for key, title, css, icon, meaning in GROUPS:
        if key == "skipped" and not count[key]:
            continue
        tiles.append('<div class="kpi %s"><div class="k-lbl">%s %s</div><div class="k-num">%d</div>'
                     '<div class="k-sub">%s</div></div>' % (css, icon, esc(title), count[key], esc(meaning)))
    return "".join(tiles)


def chips(r, ran):
    return "".join('<span class="chip %s">%s %s</span>' % (CELL[r["cells"].get(b["name"], "Missing")][2],
                   CELL[r["cells"].get(b["name"], "Missing")][0], esc(b["label"])) for b in ran)


def problem_cards(rows, ran, out_dir):
    probs = [r for r in rows if r["group"] == "browser"]
    if not probs:
        return '<div class="card empty">✅ None - no test case behaved differently between browsers.</div>'
    html_out = []
    for r in probs:
        label = {b["name"]: b["label"] for b in ran}
        works_in = [label[n] for n, s in r["cells"].items() if s in ("Passed", "Flaky")]
        parts = []
        for n in r["failed"]:
            info = r["info"].get(n, {})
            shot = screenshot(out_dir, n, r["id"])
            parts.append('<div class="fail-in"><h4>❌ In %s</h4><p>%s</p>%s%s</div>' % (
                esc(label[n]), esc(info.get("reason") or "Failed."),
                '<details><summary>Technical details</summary><pre>%s</pre></details>' % esc(info.get("tech")) if info.get("tech") else "",
                '<a href="%s" target="_blank"><img src="%s" alt="Screen in %s when %s failed" loading="lazy"></a>' % (shot, shot, esc(label[n]), esc(r["id"])) if shot else ""))
        html_out.append(
            '<div class="card prob"><div class="ph"><span class="tcid">%s</span><b>%s</b><span class="muted">%s%s</span></div>'
            '<div class="chips">%s</div><div class="fails">%s</div>'
            '<div class="todo"><b>What to do:</b> open the site in %s and repeat the steps by hand. If it fails there too '
            'but works in %s, it is a compatibility bug - report it with the browser name and version from this report. '
            'If it works by hand, the test is acting before the page is ready (faster browsers hit this first) - '
            'make the test wait for the page; it is not an app bug.</div></div>'
            % (esc(r["id"]), esc(r["title"]), esc(r["flow"]), (" · " + esc(r["priority"])) if r["priority"] else "",
               chips(r, ran), "".join(parts), esc(" / ".join(label[n] for n in r["failed"])), esc(", ".join(works_in) or "the other browsers")))
    return "".join(html_out)


def short_list(rows, ran, group, empty, note):
    sel = [r for r in rows if r["group"] == group]
    if not sel:
        return '<p class="muted">%s</p>' % esc(empty)
    items = "".join('<li><span class="tcid">%s</span> %s <span class="muted">· %s</span><div class="chips">%s</div></li>'
                    % (esc(r["id"]), esc(r["title"]), esc(r["flow"]), chips(r, ran)) for r in sel)
    return '<p class="lead">%s</p><ul class="slist card">%s</ul>' % (esc(note), items)


def matrix(rows, ran):
    head = "".join('<th>%s</th>' % esc(b["label"]) for b in ran)
    body, flow = [], None
    for r in sorted(rows, key=lambda r: (r["flow"], tc_sort_key(r["id"]))):
        if r["flow"] != flow:
            flow = r["flow"]
            body.append('<tr class="flowhead"><td colspan="%d">%s</td></tr>' % (len(ran) + 3, esc(flow)))
        cells = "".join('<td class="cell %s">%s <span>%s</span></td>' % (
            CELL[r["cells"].get(b["name"], "Missing")][2], CELL[r["cells"].get(b["name"], "Missing")][0],
            CELL[r["cells"].get(b["name"], "Missing")][1]) for b in ran)
        g = next(x for x in GROUPS if x[0] == r["group"])
        body.append('<tr data-group="%s"><td class="tcid">%s</td><td>%s</td>%s<td><span class="pill %s">%s %s</span></td></tr>'
                    % (r["group"], esc(r["id"]), esc(r["title"]), cells, g[2], g[3], esc(g[1])))
    return ('<div class="filters"><button class="btn on" data-f="all">All test cases</button>'
            '<button class="btn" data-f="diff">Only differences</button></div>'
            '<div class="tablewrap card"><table class="mx"><thead><tr><th>ID</th><th>Test case</th>%s<th>Group</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % (head, "".join(body)))


def build_html(title, run, ran, rows, out_dir):
    css, word, reason, count = verdict(ran, rows, run)
    started = run.get("started", "")
    try:
        started = datetime.datetime.fromisoformat(started).strftime("%d %b %Y, %H:%M")
    except ValueError:
        pass
    total_s = sum(b.get("seconds") or 0 for b in ran)
    wall = max([b.get("seconds") or 0 for b in ran] or [0]) if run.get("parallel") else total_s
    meta = "".join("<span>%s</span>" % esc(x) for x in [
        started, "%d test cases" % len(rows), "%d browsers" % len(ran), "took " + duration(wall),
        "tests: " + " ".join(run.get("targets") or [])])
    retried = sum(len(v) for v in (run.get("retried") or {}).values())
    return TEMPLATE.replace("$title", esc(title)).replace("$meta", meta).replace("$vcss", css).replace(
        "$vword", esc(word)).replace("$vreason", esc(reason)).replace("$cards", browser_cards(run, ran, rows)).replace(
        "$kpis", kpis(count, len(rows))).replace("$problems", problem_cards(rows, ran, out_dir)).replace(
        "$nprob", str(count["browser"])).replace(
        "$everywhere", short_list(rows, ran, "everywhere", "None.",
                                  "These fail in every browser, so the browser is not the reason. Look at them in the main test report (possible app bug, or a test that needs updating).")).replace(
        "$flaky", short_list(rows, ran, "flaky", "None - no test passed only on a second try.",
                             "These failed once and passed when run again in the same browser. That points to timing or test data, not the browser. Worth making them steadier.")).replace(
        "$matrix", matrix(rows, ran)).replace("$retried", str(retried)).replace(
        "$generated", datetime.datetime.now().strftime("%d %b %Y %H:%M"))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cross-browser report - $title</title>
<style>
 :root {
  --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9;
  --ring:rgba(11,11,11,.10); --shadow:0 1px 2px rgba(0,0,0,.05), 0 4px 16px rgba(0,0,0,.04);
  --good:#0ca30c; --good-bg:#e6f5e6; --warn:#fab219; --warn-bg:#fdf3dc; --serious:#ec835a; --serious-bg:#fbeae2;
  --crit:#d03b3b; --crit-bg:#f8e3e3; --none:#c3c2b7; --none-bg:#efeee9; --accent:#2a78d6; --hero:#0f1b2d; --hero2:#1d3557;
 }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --ink3:#9a998f; --line:#2c2c2a;
  --ring:rgba(255,255,255,.10); --shadow:none; --good-bg:#14301a; --warn-bg:#352a10; --serious-bg:#3a2218;
  --crit-bg:#3a1a1a; --none:#4a4a46; --none-bg:#242422; --accent:#3987e5; --hero:#0b1422; --hero2:#16294a; } }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 .wrap { max-width:1160px; margin:0 auto; padding:0 16px; }
 .muted { color:var(--ink3); font-weight:400; }
 h2 { font-size:22px; margin:0 0 4px; letter-spacing:-.01em; } .lead { color:var(--ink2); margin:0 0 14px; }
 section { padding:30px 0 4px; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; box-shadow:var(--shadow); }
 header.hero { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:30px 0 34px; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; }
 .hero h1 { font-size:32px; margin:4px 0 10px; letter-spacing:-.02em; }
 .meta { display:flex; flex-wrap:wrap; gap:8px; }
 .meta span { background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.14); padding:4px 10px; border-radius:999px; font-size:13px; }
 .verdict { margin-top:16px; display:inline-flex; gap:12px; align-items:flex-start; padding:12px 16px; border-radius:12px;
   background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.16); max-width:720px; }
 .vdot { width:14px; height:14px; border-radius:50%; margin-top:5px; flex:none; box-shadow:0 0 0 4px rgba(255,255,255,.12); }
 .verdict.fail .vdot { background:var(--crit); } .verdict.warnv .vdot { background:var(--warn); }
 .verdict.blocked .vdot { background:var(--none); } .verdict.pass .vdot { background:var(--good); }
 .vh { font-weight:800; font-size:18px; letter-spacing:.02em; } .vr { opacity:.9; font-size:14px; }
 .bgrid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin-top:-22px; }
 .bcard { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:16px 18px; box-shadow:var(--shadow); }
 .bcard.off { opacity:.85; border-style:dashed; }
 .bname { font-size:19px; font-weight:800; } .babout { color:var(--ink2); font-size:13px; min-height:38px; }
 .bver { color:var(--ink3); font-size:12.5px; margin:6px 0 8px; } .bnum { font-size:14px; margin-bottom:6px; } .bnum b { font-size:24px; }
 .bnote { font-size:13px; color:var(--ink2); margin-top:8px; }
 .stack { display:flex; gap:2px; height:12px; margin-bottom:10px; } .seg { border-radius:4px; }
 .seg.good { background:var(--good); } .seg.crit { background:var(--crit); } .seg.none { background:var(--none); }
 .pill { display:inline-flex; gap:5px; align-items:center; font-size:12.5px; font-weight:600; padding:2px 9px; border-radius:999px; white-space:nowrap; }
 .pill.good, .chip.good, .cell.good { background:var(--good-bg); } .pill.crit, .chip.crit, .cell.crit { background:var(--crit-bg); }
 .pill.warn, .chip.warn, .cell.warn { background:var(--warn-bg); } .pill.serious { background:var(--serious-bg); }
 .pill.none, .chip.none, .cell.none { background:var(--none-bg); }
 .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:12px; }
 .kpi { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:14px 16px 14px 18px; position:relative; overflow:hidden; box-shadow:var(--shadow); }
 .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--line); }
 .kpi.good::before { background:var(--good); } .kpi.crit::before { background:var(--crit); } .kpi.warn::before { background:var(--warn); }
 .kpi.serious::before { background:var(--serious); } .kpi.none::before { background:var(--none); }
 .k-lbl { color:var(--ink2); font-size:13px; font-weight:600; } .k-num { font-size:32px; font-weight:800; font-variant-numeric:tabular-nums; }
 .k-sub { color:var(--ink3); font-size:12.5px; }
 .empty { padding:16px 20px; }
 .prob { padding:16px 20px; margin-bottom:14px; } .ph { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; }
 .tcid { font-family:ui-monospace, Menlo, monospace; font-size:13px; font-weight:700; background:var(--none-bg); padding:1px 7px; border-radius:6px; }
 .chips { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0; } .chip { font-size:12.5px; padding:2px 9px; border-radius:999px; }
 .fails { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:14px; }
 .fail-in h4 { margin:0 0 4px; font-size:14px; } .fail-in p { margin:0 0 8px; color:var(--ink2); }
 .fail-in img { width:100%; max-width:560px; max-height:260px; object-fit:cover; object-position:top; border-radius:8px; border:1px solid var(--line); }
 details { margin-bottom:8px; font-size:13px; } pre { white-space:pre-wrap; word-break:break-word; font-size:12px; background:var(--none-bg); padding:8px; border-radius:8px; max-height:220px; overflow:auto; }
 .todo { font-size:13.5px; color:var(--ink2); border-top:1px solid var(--line); padding-top:10px; margin-top:6px; }
 .slist { list-style:none; margin:0; padding:6px 18px; } .slist li { padding:10px 0; border-bottom:1px solid var(--line); } .slist li:last-child { border:0; }
 .slist .chips { margin:6px 0 0; }
 .filters { display:flex; gap:8px; margin-bottom:10px; }
 .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; }
 .btn.on { border-color:var(--ink); font-weight:600; }
 .tablewrap { overflow-x:auto; } table.mx { width:100%; border-collapse:collapse; font-size:13.5px; }
 .mx th { text-align:left; font-size:12.5px; color:var(--ink2); padding:10px; border-bottom:1px solid var(--line); white-space:nowrap; }
 .mx td { padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:middle; }
 .mx td.cell { white-space:nowrap; font-size:12.5px; border-left:2px solid var(--surface); }
 .flowhead td { background:var(--bg); font-weight:700; font-size:13px; }
 .note { padding:16px 20px; } .note ul { margin:6px 0 0; padding-left:20px; } .note li { margin:4px 0; }
 footer { color:var(--ink3); font-size:12.5px; padding:30px 0 40px; }
 @media (max-width:640px) { .hero h1 { font-size:24px; } .mx td.cell span { display:none; } }
</style></head><body>
<header class="hero"><div class="wrap">
 <div class="eyebrow">Cross-browser test report</div>
 <h1>$title</h1>
 <div class="meta">$meta</div>
 <div class="verdict $vcss"><span class="vdot"></span><div><div class="vh">$vword</div><div class="vr">$vreason</div></div></div>
</div></header>
<main class="wrap">
 <div class="bgrid" style="margin-top:22px">$cards</div>
 <section><div class="kpis">$kpis</div></section>
 <section><h2>Browser-specific problems <span class="muted">($nprob)</span></h2>
  <p class="lead">Test cases that pass in one browser and fail in another - even on a second try. These are the ones this report is for.</p>
  $problems</section>
 <section><h2>Fail in every browser</h2>$everywhere</section>
 <section><h2>Flaky</h2>$flaky</section>
 <section><h2>Every test case, every browser</h2>
  <p class="lead">✅ Passed · 🔁 Passed on retry · ❌ Failed · ⏭ Skipped · – Not run</p>$matrix</section>
 <section><div class="card note"><b>What this test covers - and what it does not</b><ul>
  <li>The same automated tests ran in each browser. A test that failed in only some browsers was run once more there ($retried re-run) so random failures are not reported as browser bugs.</li>
  <li><b>Safari</b> here is WebKit, the engine inside Safari. It is very close, but not the real Safari app on a Mac or iPhone.</li>
  <li><b>iPhone / iPad / Android</b> imitate the screen size, touch and browser engine - not a real phone. Real devices need a device cloud (for example BrowserStack or LambdaTest).</li>
  <li>Looks (layout, fonts, overlapping text) are only checked where a test checks them. Visual comparison is a separate test.</li>
 </ul></div></section>
 <footer>Made by qa-toolkit · $generated</footer>
</main>
<script>
 document.querySelectorAll(".filters .btn").forEach(function (b) {
  b.addEventListener("click", function () {
   document.querySelectorAll(".filters .btn").forEach(function (x) { x.classList.toggle("on", x === b); });
   var diff = b.dataset.f === "diff";
   document.querySelectorAll("table.mx tbody tr[data-group]").forEach(function (tr) {
    tr.hidden = diff && tr.dataset.group === "works";
   });
   document.querySelectorAll("table.mx tr.flowhead").forEach(function (h) {
    var n = h.nextElementSibling, any = false;
    while (n && !n.classList.contains("flowhead")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
    h.hidden = !any;
   });
  });
 });
</script>
</body></html>
"""


# ---------- Excel ----------

def write_excel(path, title, run, ran, rows):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
    except ImportError:
        return None
    fills = {"good": "E6F5E6", "crit": "F8E3E3", "warn": "FDF3DC", "none": "EFEEE9", "serious": "FBEAE2"}
    bold = Font(bold=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Matrix"
    head = ["ID", "Test case", "Flow"] + [b["label"] for b in ran] + ["Group"]
    ws.append(head)
    for r in sorted(rows, key=lambda r: (r["flow"], tc_sort_key(r["id"]))):
        g = next(x for x in GROUPS if x[0] == r["group"])
        ws.append([r["id"], r["title"], r["flow"]] + [CELL[r["cells"].get(b["name"], "Missing")][1] for b in ran] + [g[1]])
        for i, b in enumerate(ran):
            css = CELL[r["cells"].get(b["name"], "Missing")][2]
            ws.cell(ws.max_row, 4 + i).fill = PatternFill("solid", fgColor=fills[css])
        ws.cell(ws.max_row, len(head)).fill = PatternFill("solid", fgColor=fills[g[2]])
    ws2 = wb.create_sheet("Browser problems")
    ws2.append(["ID", "Test case", "Flow", "Fails in", "Works in", "Why it failed", "Technical details"])
    label = {b["name"]: b["label"] for b in ran}
    for r in rows:
        if r["group"] != "browser":
            continue
        for n in r["failed"]:
            ws2.append([r["id"], r["title"], r["flow"], label[n],
                        ", ".join(label[x] for x, s in r["cells"].items() if s in ("Passed", "Flaky")),
                        r["info"].get(n, {}).get("reason", ""), r["info"].get(n, {}).get("tech", "")])
    ws3 = wb.create_sheet("Browsers")
    ws3.append(["Browser", "What it is", "Version", "Passed", "Failed", "Fails only here", "Time", "Note"])
    for b in run["browsers"]:
        n = b["name"]
        if b in ran:
            ws3.append([b["label"], b["about"], b["version"],
                        sum(1 for r in rows if r["cells"].get(n) in ("Passed", "Flaky")),
                        sum(1 for r in rows if r["cells"].get(n) == "Failed"),
                        sum(1 for r in rows if r["group"] == "browser" and n in r["failed"]),
                        duration(b.get("seconds")), ""])
        else:
            ws3.append([b["label"], b["about"], "", "", "", "", "", "Not tested: " + (b.get("problem") or "no results")])
    for sheet, widths in ((ws, [10, 60, 24] + [16] * len(ran) + [26]), (ws2, [10, 50, 22, 16, 22, 60, 60]),
                          (ws3, [16, 46, 16, 9, 9, 15, 10, 50])):
        for c in sheet[1]:
            c.font = bold
        for i, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = w
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    wb.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="Build the cross-browser report")
    ap.add_argument("--out", default="reports/browsers", help="folder browser_run.py wrote to")
    ap.add_argument("--cases", help="test case sheet (CSV/Excel) for titles and priorities")
    ap.add_argument("--title", default="Cross-browser test")
    args = ap.parse_args()
    if not os.path.isfile(os.path.join(args.out, "run.json")):
        sys.exit("No %s - run browser_run.py first." % os.path.join(args.out, "run.json"))
    run, ran, rows = load(args.out, args.cases)
    page = os.path.join(args.out, "browser-report.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(build_html(args.title, run, ran, rows, args.out))
    css, word, reason, count = verdict(ran, rows, run)
    print("Verdict: %s - %s" % (word, reason))
    print("Report written: " + os.path.abspath(page))
    xl = write_excel(os.path.join(args.out, "browser-report.xlsx"), args.title, run, ran, rows)
    if xl:
        print("Excel report written: " + os.path.abspath(xl))


if __name__ == "__main__":
    main()
