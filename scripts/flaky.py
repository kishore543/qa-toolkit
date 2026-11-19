"""Flaky test tracker: remembers every run and tells FLAKY tests (pass sometimes) apart from BROKEN ones.

    python3 <toolkit>/scripts/flaky.py record reports/results.xml [more.xml ...] [--label nightly]
    python3 <toolkit>/scripts/flaky.py rerun tests/super_admin/test_x.py -n 3 [-k "TC_045"]   # run N times, record each
    python3 <toolkit>/scripts/flaky.py report [--last 10]

History lives in reports/history/runs.jsonl (one line per run; the same results file is never recorded twice).
Each test case is put in one group, looking at its last N runs (default 10):
  Flaky           passed and failed more than once - the result flips (timing, test data, environment)
  Broke recently  passed before, fails since a date - something changed (app or test)
  Broken          fails every run
  Fixed recently  failed before, passes now
  Stable          passes every run
  Not enough runs fewer than 2 runs so far
Do NOT record runs made to fail on purpose (proof runs) - they would look flaky.
Report: reports/history/flaky-report.html (+ flaky.json for the release dashboard).
"""
import argparse
import datetime
import hashlib
import html
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from make_report import read_results  # noqa: E402

GROUPS = [  # key, title, css, icon, meaning
    ("flaky", "Flaky", "warn", "🔁", "Result flips between pass and fail - fix the test or the environment, do not trust its failures"),
    ("broke", "Broke recently", "crit", "📉", "Passed before, fails every run since - look at what changed"),
    ("broken", "Broken", "crit", "❌", "Fails every run"),
    ("fixed", "Fixed recently", "good", "📈", "Failed before, passes now"),
    ("stable", "Stable", "good", "✅", "Passes every run"),
    ("new", "Not enough runs", "none", "•", "Fewer than 2 runs so far"),
]


def history_path(folder):
    return os.path.join(folder, "runs.jsonl")


def load_history(folder):
    runs = []
    if os.path.isfile(history_path(folder)):
        with open(history_path(folder), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    runs.append(json.loads(line))
    return runs


def record(files, folder, label, context=""):
    os.makedirs(folder, exist_ok=True)
    known = {r["run_id"] for r in load_history(folder)}
    added = 0
    for path in files:
        if not os.path.isfile(path):
            print("  not found: %s" % path)
            continue
        results, info = read_results(path)
        if not results:
            print("  no test case results in %s" % path)
            continue
        when = info["started"].replace(tzinfo=None) if info.get("started") else datetime.datetime.fromtimestamp(os.path.getmtime(path))
        run_id = hashlib.sha1(("%s|%s|%d" % (os.path.abspath(path), when.isoformat(), len(results))).encode()).hexdigest()[:12]
        if run_id in known:
            print("  already recorded: %s" % path)
            continue
        ctx = context or guess_context(path)
        line = {"run_id": run_id, "when": when.isoformat(timespec="seconds"), "label": label or "", "source": path, "context": ctx,
                "results": [{"id": tc, "flow": r["flow"], "result": r["result"], "seconds": round(r["seconds"], 1),
                             "error": (r["reason"] or "")[:200]} for tc, r in results.items()]}
        with open(history_path(folder), "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        known.add(run_id)
        added += 1
        c = {k: sum(1 for r in line["results"] if r["result"] == k) for k in ("Passed", "Failed", "Skipped")}
        print("  recorded %s (%s%s): %d passed, %d failed, %d skipped" % (path, when.strftime("%d %b %H:%M"),
              ", " + ctx if ctx else "", c["Passed"], c["Failed"], c["Skipped"]))
    return added


def guess_context(path):
    """reports/browsers/firefox/results.xml -> 'firefox' (each browser is tracked separately)."""
    parts = os.path.normpath(path).split(os.sep)
    if "browsers" in parts:
        i = parts.index("browsers")
        if i + 1 < len(parts) - 1:
            return parts[i + 1]
    return ""


def classify(seq):
    """seq = ['Passed', 'Failed', ...] oldest first (skips removed)."""
    if len(seq) < 2:
        return "new"
    flips = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    if flips == 0:
        return "stable" if seq[0] == "Passed" else "broken"
    if flips == 1:
        return "broke" if seq[-1] == "Failed" else "fixed"
    return "flaky"


def analyse(runs, last):
    by = {}
    for run in sorted(runs, key=lambda r: r["when"]):
        for r in run["results"]:
            key = (r["id"], run.get("context", ""))
            by.setdefault(key, []).append(dict(r, when=run["when"], label=run.get("label", "")))
    tests = []
    for (tc, ctx), entries in by.items():
        ran = [e for e in entries if e["result"] in ("Passed", "Failed")][-last:]
        seq = [e["result"] for e in ran]
        group = classify(seq)
        fails = [e for e in ran if e["result"] == "Failed"]
        since = None
        if group == "broke":
            since = next(e["when"] for e in ran if e["result"] == "Failed")
        errors = sorted({e["error"][:90] for e in fails if e["error"]})
        tests.append({"id": tc, "context": ctx, "flow": entries[-1]["flow"], "group": group, "runs": len(ran),
                      "fails": len(fails), "fail_pct": round(100.0 * len(fails) / len(ran)) if ran else 0,
                      "flips": sum(1 for a, b in zip(seq, seq[1:]) if a != b), "since": since,
                      "last_error": fails[-1]["error"] if fails else "", "different_errors": len(errors),
                      "strip": [(e["result"], e["when"], e["label"]) for e in ran]})
    order = {g[0]: i for i, g in enumerate(GROUPS)}
    tests.sort(key=lambda t: (order[t["group"]], -t["fail_pct"], t["id"]))
    return tests


def esc(t):
    return html.escape(str(t if t is not None else ""))


def build_html(tests, runs, last):
    count = {g[0]: sum(1 for t in tests if t["group"] == g[0]) for g in GROUPS}
    kpis = "".join('<div class="kpi %s"><div class="k-lbl">%s %s</div><div class="k-num">%d</div><div class="k-sub">%s</div></div>'
                   % (g[2], g[3], g[1], count[g[0]], esc(g[4])) for g in GROUPS if g[0] != "new" or count["new"])
    rows = []
    for t in tests:
        g = next(x for x in GROUPS if x[0] == t["group"])
        strip = "".join('<span class="dot %s" title="%s %s%s">%s</span>' % (
            "p" if s == "Passed" else "f", esc(w.replace("T", " ")), s, (" · " + esc(lb)) if lb else "", "✓" if s == "Passed" else "✗")
            for s, w, lb in t["strip"])
        note = ""
        if t["group"] == "flaky":
            note = "fails %d%% of runs, flipped %d times%s" % (t["fail_pct"], t["flips"],
                                                              ", %d different errors" % t["different_errors"] if t["different_errors"] > 1 else "")
        elif t["group"] == "broke":
            note = "failing since %s" % t["since"].replace("T", " ")[:16]
        rows.append('<tr data-group="%s"><td class="tcid">%s</td><td>%s%s</td><td><span class="pill %s">%s %s</span></td>'
                    '<td class="strip">%s</td><td>%s<div class="err">%s</div></td></tr>' % (
                        t["group"], esc(t["id"]), esc(t["flow"]), (' <span class="ctx">%s</span>' % esc(t["context"])) if t["context"] else "",
                        g[2], g[3], g[1], strip, esc(note), esc(t["last_error"][:160]) if t["group"] != "stable" else ""))
    first = min((r["when"] for r in runs), default="")[:10]
    lastw = max((r["when"] for r in runs), default="")[:10]
    filters = '<button class="btn on" data-f="all">All (%d)</button>' % len(tests) + "".join(
        '<button class="btn" data-f="%s">%s %s (%d)</button>' % (g[0], g[3], g[1], count[g[0]]) for g in GROUPS if count[g[0]])
    return TEMPLATE.replace("$kpis", kpis).replace("$rows", "".join(rows)).replace("$filters", filters).replace(
        "$meta", esc("%d runs recorded · %s to %s · judged on the last %d runs of each test" % (len(runs), first, lastw, last))).replace(
        "$generated", datetime.datetime.now().strftime("%d %b %Y %H:%M"))


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Flaky test tracker</title>
<style>
 :root { --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9; --ring:rgba(11,11,11,.10);
  --good:#0ca30c; --good-bg:#e6f5e6; --warn:#fab219; --warn-bg:#fdf3dc; --crit:#d03b3b; --crit-bg:#f8e3e3; --none:#c3c2b7; --none-bg:#efeee9;
  --hero:#0f1b2d; --hero2:#1d3557; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7;
  --ink3:#9a998f; --line:#2c2c2a; --ring:rgba(255,255,255,.10); --good-bg:#14301a; --warn-bg:#352a10; --crit-bg:#3a1a1a; --none-bg:#242422; --none:#4a4a46; } }
 * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 .wrap { max-width:1160px; margin:0 auto; padding:0 16px; }
 header { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:28px 0 30px; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; } h1 { margin:4px 0 6px; font-size:30px; }
 .meta { opacity:.85; font-size:14px; }
 .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:12px; margin:22px 0; }
 .kpi { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:14px 16px 14px 18px; position:relative; overflow:hidden; }
 .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--none); }
 .kpi.good::before { background:var(--good); } .kpi.warn::before { background:var(--warn); } .kpi.crit::before { background:var(--crit); }
 .k-lbl { font-size:13px; font-weight:600; color:var(--ink2); } .k-num { font-size:30px; font-weight:800; } .k-sub { font-size:12.5px; color:var(--ink3); }
 .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:10px; }
 .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; }
 .btn.on { border-color:var(--ink); font-weight:600; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; overflow-x:auto; }
 table { width:100%; border-collapse:collapse; font-size:13.5px; } th { text-align:left; font-size:12.5px; color:var(--ink2); padding:10px; border-bottom:1px solid var(--line); }
 td { padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; } .tcid { font:700 12.5px ui-monospace, Menlo, monospace; white-space:nowrap; }
 .ctx { font-size:11.5px; background:var(--none-bg); border-radius:6px; padding:1px 6px; }
 .pill { font-size:12.5px; font-weight:600; padding:2px 9px; border-radius:999px; white-space:nowrap; }
 .pill.good { background:var(--good-bg); } .pill.warn { background:var(--warn-bg); } .pill.crit { background:var(--crit-bg); } .pill.none { background:var(--none-bg); }
 .strip { white-space:nowrap; } .dot { display:inline-grid; place-items:center; width:18px; height:18px; margin-right:2px; border-radius:4px; font-size:11px; font-weight:800; color:#fff; }
 .dot.p { background:var(--good); } .dot.f { background:var(--crit); }
 .err { color:var(--ink3); font-size:12.5px; margin-top:2px; }
 .note { color:var(--ink2); font-size:13.5px; margin:18px 0; } footer { color:var(--ink3); font-size:12.5px; padding:24px 0 40px; }
</style></head><body>
<header><div class="wrap"><div class="eyebrow">Flaky test tracker</div><h1>Which tests can we trust?</h1><div class="meta">$meta</div></div></header>
<main class="wrap"><div class="kpis">$kpis</div><div class="filters">$filters</div>
<div class="card"><table><thead><tr><th>Test case</th><th>Flow</th><th>Group</th><th>Last runs (oldest → newest)</th><th>Notes / last error</th></tr></thead><tbody>$rows</tbody></table></div>
<p class="note">✓ passed · ✗ failed (hover a square for the date). A flaky test's failure says little about the app: make it steady (wait for the page, unique test data) before trusting it.
Runs made to fail on purpose (proof runs) should not be recorded.</p>
<footer>Made by qa-toolkit · $generated</footer></main>
<script>
 document.querySelectorAll(".filters .btn").forEach(function (b) { b.addEventListener("click", function () {
  document.querySelectorAll(".filters .btn").forEach(function (x) { x.classList.toggle("on", x === b); });
  document.querySelectorAll("tbody tr").forEach(function (tr) { tr.hidden = b.dataset.f !== "all" && tr.dataset.group !== b.dataset.f; }); }); });
</script></body></html>
"""


def report(folder, last):
    runs = load_history(folder)
    if not runs:
        sys.exit("No history yet - record a run first: flaky.py record reports/results.xml")
    tests = analyse(runs, last)
    with open(os.path.join(folder, "flaky-report.html"), "w", encoding="utf-8") as f:
        f.write(build_html(tests, runs, last))
    with open(os.path.join(folder, "flaky.json"), "w", encoding="utf-8") as f:
        json.dump({"made": datetime.datetime.now().isoformat(timespec="seconds"), "last": last,
                   "tests": [{k: v for k, v in t.items() if k != "strip"} for t in tests]}, f, indent=1)
    count = {g[0]: sum(1 for t in tests if t["group"] == g[0]) for g in GROUPS}
    print("%d runs, %d test cases: %s" % (len(runs), len(tests), ", ".join("%s %d" % (g[1], count[g[0]]) for g in GROUPS if count[g[0]])))
    for t in tests:
        if t["group"] in ("flaky", "broke", "broken"):
            print("  %-15s %s%s  %s" % (next(g[1] for g in GROUPS if g[0] == t["group"]), t["id"], (" [%s]" % t["context"]) if t["context"] else "",
                                       " ".join("✓" if s == "Passed" else "✗" for s, _, _ in t["strip"])))
    print("Report: " + os.path.abspath(os.path.join(folder, "flaky-report.html")))


def rerun(targets, n, keyword, folder, python):
    os.makedirs(os.path.join(folder, "reruns"), exist_ok=True)
    files = []
    for i in range(1, n + 1):
        xml = os.path.join(folder, "reruns", "rerun-%s-%d.xml" % (time.strftime("%Y%m%d-%H%M%S"), i))
        cmd = [python, "-m", "pytest", *targets, "--junitxml", xml, "-q", "-p", "no:cacheprovider"] + (["-k", keyword] if keyword else [])
        print("Run %d of %d ..." % (i, n), flush=True)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        files.append(xml)
    record(files, folder, "rerun")


def main():
    ap = argparse.ArgumentParser(description="Flaky test tracker")
    ap.add_argument("--folder", default="reports/history", help="where the history is kept")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="add results files (pytest JUnit XML) to the history")
    r.add_argument("files", nargs="+")
    r.add_argument("--label", default="")
    r.add_argument("--context", default="", help="e.g. a browser name; default: from the path")
    rr = sub.add_parser("rerun", help="run tests N times and record every run")
    rr.add_argument("targets", nargs="+")
    rr.add_argument("-n", type=int, default=3)
    rr.add_argument("-k", dest="keyword")
    rr.add_argument("--python", default=sys.executable, help="the project's Python (with pytest + playwright)")
    rp = sub.add_parser("report", help="build the flaky report")
    rp.add_argument("--last", type=int, default=10, help="judge each test on its last N runs (default 10)")
    args = ap.parse_args()
    if args.cmd == "record":
        record(args.files, args.folder, args.label, args.context)
    elif args.cmd == "rerun":
        rerun(args.targets, args.n, args.keyword, args.folder, args.python)
        report(args.folder, 10)
    else:
        report(args.folder, args.last)


if __name__ == "__main__":
    main()
