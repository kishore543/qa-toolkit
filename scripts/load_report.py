"""qa-toolkit: turn load test numbers into a plain-language HTML report.

Run from the project folder, after load_test.py:
    python3 load_report.py
Reads reports/load/results.json, writes reports/load/load-report.html
(one file - open it in any browser, email it to anyone).
"""
import argparse
import html
import json
import os

FAST_MS = 1000   # answers under 1 second feel instant to a person


# ---------- reading the numbers in plain words ----------

def error_words(error):
    """'Got answer 503' -> 'Server overloaded or down (503)'."""
    text = str(error)
    meanings = [
        ("no answer", "Could not connect - the server did not answer"),
        ("ConnectionRefused", "Could not connect - the server refused"),
        ("Timeout", "The server took too long and the request gave up"),
        ("429", "Too many requests - the server started turning users away (429)"),
        ("500", "Server error - something broke on the server (500)"),
        ("502", "Server overloaded or down (502)"),
        ("503", "Server overloaded or down (503)"),
        ("504", "Server too slow to answer in time (504)"),
        ("401", "Not logged in - the login did not work or ran out (401)"),
        ("403", "Not allowed (403)"),
        ("404", "Page or address not found (404) - check the step's path"),
    ]
    for sign, words in meanings:
        if sign in text:
            return words
    return text


def speed_word(p95, limit):
    if p95 > limit:
        return "slow", "🐢 Too slow"
    if p95 <= FAST_MS:
        return "pass", "⚡ Fast"
    return "pass", "✅ OK"


def error_percent(stats):
    return 100.0 * stats["failures"] / stats["requests"] if stats["requests"] else 0.0


def judge(r):
    """The verdict, the reasons, and where things started to go wrong."""
    limit_ms = r["limits"].get("p95_ms", 2000)
    limit_err = r["limits"].get("max_error_percent", 1)
    total = r["total"]
    err = error_percent(total)
    reasons, findings = [], []

    if total["requests"] == 0:
        return "fail", "NO RESULT - not a single request was sent", ["Check base_url and the steps."], []
    # Every login failed: the site was never really loaded - don't blame the server for it.
    # "Logged in" = the LAST login step worked at least once (any login step may be the one failing).
    login = [s for s in r["steps"] if s.get("login")]
    if login and login[-1]["requests"] - login[-1]["failures"] <= 0:
        login_names = {s["name"] for s in login}
        said = ["<b>%s</b> failed %d time(s): %s" % (html.escape(e["step"]), e["count"], html.escape(e["error"]))
                for e in r.get("errors", []) if e["step"] in login_names]
        return ("warn", "COULD NOT TEST - no virtual user could log in, so the site was never really loaded",
                ["Every login failed. Check the test accounts (users_file) and the values in .env "
                 "(for example the OTP), then run again. This is not a speed result."],
                said or ["The login never succeeded."])
    if total["p95_ms"] > limit_ms:
        reasons.append("Too slow: 95%% of answers took up to %s - the limit is %s."
                       % (seconds(total["p95_ms"]), seconds(limit_ms)))
    if err > limit_err:
        reasons.append("Too many errors: %.1f%% of requests failed - the limit is %s%%." % (err, limit_err))

    # Where did it start? The first moment answers got slower than the limit / errors appeared.
    slow = next((p for p in r["timeline"] if p["p95_ms"] > limit_ms), None)
    if slow:
        findings.append("Answers first became slower than %s at about <b>%d users</b> (after %d seconds)."
                        % (seconds(limit_ms), slow["users"], slow["second"]))
    first_error = next((p for p in r["timeline"] if p["failures_so_far"] > 0), None)
    if first_error:
        findings.append("The first errors appeared at about <b>%d users</b> (after %d seconds)."
                        % (first_error["users"], first_error["second"]))

    if reasons:
        return "fail", "FAILED - the site did not handle %d users at the same time well" % r["users"], reasons, findings
    # A step that never ran cannot pass - be honest instead of saying PASSED.
    not_reached = [s["name"] for s in r["steps"] if s["requests"] == 0]
    if not_reached:
        return ("warn", "NOT SURE - part of the journey was never reached",
                ["Never reached: %s. Run the test longer (duration_seconds) so every step is tested."
                 % ", ".join(not_reached)], findings)
    return "pass", "PASSED - the site handled %d users at the same time" % r["users"], [], findings


def seconds(ms):
    return "%.1f s" % (ms / 1000.0) if ms >= 1000 else "%d ms" % ms


# ---------- the charts: two small charts on the same timeline (never two scales on one chart) ----------

def nice_top(value):
    """Round the chart's top up to a tidy number: 87 -> 100, 2300 -> 2500, 4 -> 5."""
    for power in range(0, 7):          # smallest first: 1, 2, 2.5, 5, 10, 20, 25, 50, 100 ...
        for step in (1, 2, 2.5, 5):
            tidy = step * 10 ** power
            if tidy >= value:
                return tidy
    return value


def line_chart(points, key, label, limit=None, y_max=0, height=170):
    width, left, right, top, bottom = 760, 52, 16, 14, 26
    xs = [p["second"] for p in points] or [0]
    top_value = nice_top(max([p[key] for p in points] + [limit * 1.15 if limit else 0, y_max, 4]))
    x_max = max(xs[-1], 5)

    def x(v):
        return left + (width - left - right) * v / x_max

    def y(v):
        return top + (height - top - bottom) * (1 - v / top_value)

    grid = ""
    for i in range(6):                 # 5 equal parts of a tidy top: 0 2 4 6 8 10, 0 500 ... 2500
        value = top_value * i / 5
        grid += ('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="grid"/>'
                 '<text x="%d" y="%.1f" class="axis" text-anchor="end">%s</text>'
                 % (left, width - right, y(value), y(value), left - 6, y(value) + 4,
                    seconds(value) if key == "p95_ms" else "%d" % value))
    for sec in sorted({round(x_max * i / 5) for i in range(6)}):   # whole seconds, no repeats
        grid += '<text x="%.1f" y="%d" class="axis" text-anchor="middle">%ds</text>' % (x(sec), height - 6, sec)
    path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", x(p["second"]), y(p[key])) for i, p in enumerate(points))
    limit_svg = ""
    if limit:
        limit_svg = ('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" class="limit"/>'
                     '<text x="%d" y="%.1f" class="limit-label" text-anchor="end">Limit %s</text>'
                     % (left, width - right, y(limit), y(limit), width - right, y(limit) - 5, seconds(limit)))
    return ('<figure><figcaption>%s</figcaption>'
            '<svg viewBox="0 0 %d %d" class="chart" data-left="%d" data-right="%d" data-xmax="%s">'
            '%s%s<path d="%s" class="line"/><line class="cross" y1="%d" y2="%d"/></svg></figure>'
            % (label, width, height, left, right, x_max, grid, limit_svg, path, top, height - bottom))


# ---------- the page ----------

def build_html(r):
    v_css, v_text, reasons, findings = judge(r)
    limit_ms = r["limits"].get("p95_ms", 2000)
    limit_err = r["limits"].get("max_error_percent", 1)
    total = r["total"]
    esc = html.escape

    step_rows = ""
    for s in r["steps"]:
        css, word = speed_word(s["p95_ms"], limit_ms)
        err = error_percent(s)
        if not s["requests"]:
            css, word = "warn", "⏸ Not reached"
        elif err > limit_err:
            css, word = "slow", "❌ Errors"
        step_rows += ("<tr><td><b>%s</b><div class='muted'>%s %s</div></td><td>%d</td><td>%d (%.1f%%)</td>"
                      "<td>%s</td><td>%s</td><td>%s</td><td><span class='badge %s'>%s</span></td></tr>"
                      % (esc(s["name"]) + (" <span class='muted'>(login - once per user)</span>"
                                           if s.get("login") else ""), esc(s["method"]), esc(s["path"]), s["requests"], s["failures"], err,
                         seconds(s["median_ms"]), seconds(s["p95_ms"]), seconds(s["max_ms"]), css, word))

    error_rows = "".join("<tr><td>%s</td><td>%s</td><td>%d</td></tr>"
                         % (esc(e["step"]), esc(error_words(e["error"])), e["count"]) for e in r["errors"])
    errors_html = ("<div class='wrap'><table><tr><th>Step</th><th>What went wrong</th><th>How many times</th></tr>%s"
                   "</table></div>" % error_rows) if error_rows else "<p class='muted'>No errors. 🎉</p>"

    timeline_rows = "".join("<tr><td>%ds</td><td>%d</td><td>%s</td><td>%s</td><td>%d</td></tr>"
                            % (p["second"], p["users"], seconds(p["p95_ms"]), p["requests_per_second"],
                               p["failures_so_far"]) for p in r["timeline"])

    return TEMPLATE % dict(
        name=esc(r["name"]), run_at=esc(r["run_at"]), site=esc(r["base_url"]),
        v_css=v_css, v_text=esc(v_text),
        reasons="".join("<li>%s</li>" % esc(x) for x in reasons),
        findings="".join("<li>%s</li>" % x for x in findings) or "<li>No slowdown and no errors at any point.</li>",
        users=r["users"], ramp=r["ramp_up_seconds"], duration=r["duration_seconds"],
        requests=total["requests"], failed=total["failures"], err="%.1f" % error_percent(total),
        median=seconds(total["median_ms"]), p95=seconds(total["p95_ms"]), slowest=seconds(total["max_ms"]),
        limit=seconds(limit_ms), limit_err=limit_err,
        users_chart=line_chart(r["timeline"], "users", "Users active", y_max=r["users"]),
        speed_chart=line_chart(r["timeline"], "p95_ms", "95% of answers within (lower is better)", limit_ms),
        points=json.dumps([[p["second"], p["users"], seconds(p["p95_ms"]), p["failures_so_far"]]
                           for p in r["timeline"]]),
        step_rows=step_rows, errors=errors_html, timeline_rows=timeline_rows,
        journey="".join("<li>%s</li>" % esc(s["name"]) for s in r["steps"]),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Load test - %(name)s</title>
<style>
 :root { --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#eceef2; --head:#eef0f4;
         --series:#2a78d6; --limit:#d92d20; --pass-bg:#e6f6ec; --pass:#11652f; --fail-bg:#fdeaea; --fail:#a11b1b; --warn-bg:#fff4e0; --warn:#8a5300; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
         --bg:#141414; --card:#1a1a19; --ink:#f0efec; --muted:#a8a79f; --line:#2c2c2a; --head:#242422;
         --series:#3987e5; --limit:#f97066; --pass-bg:#12321f; --pass:#7fd89b; --fail-bg:#3a1515; --fail:#ff9c94; --warn-bg:#3a2a0e; --warn:#f5c26b; } }
 :root[data-theme="dark"] { --bg:#141414; --card:#1a1a19; --ink:#f0efec; --muted:#a8a79f; --line:#2c2c2a; --head:#242422;
         --series:#3987e5; --limit:#f97066; --pass-bg:#12321f; --pass:#7fd89b; --fail-bg:#3a1515; --fail:#ff9c94; --warn-bg:#3a2a0e; --warn:#f5c26b; }
 body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin:0; background:var(--bg); color:var(--ink); }
 main { max-width:1000px; margin:0 auto; padding:24px 16px 48px; }
 h1 { margin:0 0 4px; font-size:26px; } h2 { margin-top:34px; font-size:20px; }
 .muted { color:var(--muted); font-size:14px; }
 .verdict { padding:16px 20px; border-radius:10px; font-size:20px; font-weight:700; margin:20px 0 8px; }
 .verdict.pass { background:var(--pass-bg); color:var(--pass); } .verdict.fail { background:var(--fail-bg); color:var(--fail); }
 .verdict.warn, .badge.warn { background:var(--warn-bg); color:var(--warn); }
 .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-top:16px; }
 .card { background:var(--card); border-radius:10px; padding:14px 16px; }
 .card .num { font-size:26px; font-weight:700; } .card .lbl { color:var(--muted); font-size:14px; }
 table { width:100%%; border-collapse:collapse; background:var(--card); border-radius:10px; overflow:hidden; }
 th, td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
 th { background:var(--head); font-size:13px; text-transform:uppercase; letter-spacing:.03em; }
 .badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:13px; font-weight:600; white-space:nowrap; }
 .badge.pass { background:var(--pass-bg); color:var(--pass); } .badge.slow { background:var(--fail-bg); color:var(--fail); }
 .wrap { overflow-x:auto; }
 figure { margin:12px 0; background:var(--card); border-radius:10px; padding:12px 12px 4px; position:relative; }
 figcaption { font-weight:600; font-size:15px; margin-bottom:4px; }
 .chart { width:100%%; height:auto; display:block; touch-action:none; }
 .chart .grid { stroke:var(--line); stroke-width:1; } .chart .axis { fill:var(--muted); font-size:11px; }
 .chart .line { fill:none; stroke:var(--series); stroke-width:2; stroke-linejoin:round; }
 .chart .limit { stroke:var(--limit); stroke-width:1.5; stroke-dasharray:6 4; }
 .chart .limit-label { fill:var(--limit); font-size:12px; font-weight:600; }
 .chart .cross { stroke:var(--muted); stroke-width:1; visibility:hidden; }
 #tip { position:fixed; pointer-events:none; background:var(--card); color:var(--ink); border:1px solid var(--line);
        border-radius:8px; padding:8px 10px; font-size:13px; box-shadow:0 4px 12px rgba(0,0,0,.15); display:none; }
 details summary { cursor:pointer; color:var(--series); font-size:14px; margin-top:8px; }
</style></head>
<body><main>
<h1>Load test: %(name)s</h1>
<div class="muted">Run on %(run_at)s &middot; %(site)s</div>

<div class="verdict %(v_css)s">%(v_text)s</div>
<ul>%(reasons)s</ul>

<div class="cards">
 <div class="card"><div class="num">%(users)s</div><div class="lbl">Users at the same time</div></div>
 <div class="card"><div class="num">%(requests)s</div><div class="lbl">Requests sent</div></div>
 <div class="card"><div class="num">%(failed)s</div><div class="lbl">Failed (%(err)s%%, limit %(limit_err)s%%)</div></div>
 <div class="card"><div class="num">%(median)s</div><div class="lbl">Typical answer</div></div>
 <div class="card"><div class="num">%(p95)s</div><div class="lbl">95%% of answers within (limit %(limit)s)</div></div>
 <div class="card"><div class="num">%(slowest)s</div><div class="lbl">Slowest answer</div></div>
</div>

<h2>What happened as users joined</h2>
<ul>%(findings)s</ul>
%(users_chart)s
%(speed_chart)s
<details><summary>Show these numbers as a table</summary><div class="wrap"><table>
<tr><th>Time</th><th>Users active</th><th>95%% within</th><th>Requests a second</th><th>Errors so far</th></tr>
%(timeline_rows)s</table></div></details>

<h2>Each step of the journey</h2>
<div class="wrap"><table>
<tr><th>Step</th><th>Requests</th><th>Failed</th><th>Typical</th><th>95%% within</th><th>Slowest</th><th>Verdict</th></tr>
%(step_rows)s</table></div>

<h2>Errors</h2>
%(errors)s

<h2>What was tested</h2>
<ul class="muted">
 <li>Up to <b>%(users)s</b> virtual users at the same time, added slowly over %(ramp)s seconds; %(duration)s seconds in total.</li>
 <li>Every user repeated this journey, pausing like a real person between steps:<ol>%(journey)s</ol></li>
</ul>

<h2>What the words mean</h2>
<ul class="muted">
 <li><b>Virtual user</b> - the test pretends to be a person using the site, without opening a browser.</li>
 <li><b>Typical answer</b> - half of the answers were faster than this.</li>
 <li><b>95%% of answers within</b> - 95 out of every 100 answers were faster than this. It shows what the
     slowest normal users experience, so it is the main number to judge by.</li>
 <li><b>Failed</b> - the site answered with an error, or did not answer at all.</li>
 <li><b>Limit</b> - the pass rule agreed before the test (in load.yaml).</li>
</ul>
</main>
<div id="tip" role="status"></div>
<script>
 // Hover a chart: a line follows the pointer and the box shows both numbers for that moment.
 const points = %(points)s;   // [second, users, "95%% within", errors so far]
 const tip = document.getElementById('tip');
 document.querySelectorAll('.chart').forEach(svg => {
   const left = +svg.dataset.left, right = +svg.dataset.right, xmax = +svg.dataset.xmax;
   const width = svg.viewBox.baseVal.width;
   function show(ev) {
     if (!points.length) return;
     const box = svg.getBoundingClientRect();
     const sx = (ev.clientX - box.left) * width / box.width;
     const sec = Math.max(0, Math.min(xmax, (sx - left) / (width - left - right) * xmax));
     const p = points.reduce((a, b) => Math.abs(b[0] - sec) < Math.abs(a[0] - sec) ? b : a);
     const x = left + (width - left - right) * p[0] / xmax;
     document.querySelectorAll('.chart .cross').forEach(c => {
       c.setAttribute('x1', x); c.setAttribute('x2', x); c.style.visibility = 'visible'; });
     tip.textContent = '';
     [['After', p[0] + ' s'], ['Users active', p[1]], ['95%% within', p[2]], ['Errors so far', p[3]]].forEach(([k, v]) => {
       const row = document.createElement('div'); row.textContent = k + ': ' + v; tip.appendChild(row); });
     tip.style.display = 'block';
     tip.style.left = Math.min(ev.clientX + 14, innerWidth - 180) + 'px';
     tip.style.top = (ev.clientY + 14) + 'px';
   }
   svg.addEventListener('pointermove', show);
   svg.addEventListener('pointerleave', () => {
     tip.style.display = 'none';
     document.querySelectorAll('.chart .cross').forEach(c => c.style.visibility = 'hidden'); });
 });
</script>
</body></html>
"""


def main():
    parser = argparse.ArgumentParser(description="Plain-language load test report")
    parser.add_argument("--results", default="reports/load/results.json", help="numbers from load_test.py")
    parser.add_argument("--out", default="reports/load/load-report.html", help="where to write the report")
    args = parser.parse_args()
    if not os.path.isfile(args.results):
        raise SystemExit("No %s - run load_test.py first." % args.results)
    with open(args.results, encoding="utf-8") as f:
        results = json.load(f)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(results))
    print("Load report written: " + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
