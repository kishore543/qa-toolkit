"""qa-toolkit: build a plain-language HTML test report.

It joins two things:
  1. the test case sheet (CSV made by /qa-toolkit:cases)  -> WHAT should be tested
  2. the pytest results (JUnit XML)                        -> WHAT happened

Run from the project folder, after the tests:
    python3 -m pytest --junitxml=reports/results.xml
    python3 make_report.py --cases test-cases/login-test-cases.csv

Writes reports/report.html - one file, open it in any browser, email it to anyone.
"""
import argparse
import base64
import csv
import datetime
import html
import os
import re
import xml.etree.ElementTree as ET

# Everyday words for the test types in the sheet.
KIND = {
    "positive": "Normal use",
    "negative": "Wrong input",
    "boundary": "Edge case",
    "security": "Security",
    "ui": "Screen / looks",
}

# How each result is shown: (label, colour class, icon)
RESULT_STYLE = {
    "Passed": ("Passed", "pass", "✅"),
    "Failed": ("Failed", "fail", "❌"),
    "Blocked": ("Blocked", "blocked", "⏸"),
    "Not automated": ("Not automated yet", "manual", "✋"),
    "Skipped": ("Skipped", "blocked", "⏭"),
}


# ---------- reading the inputs ----------

def read_cases(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [{k.strip().strip('"'): (v or "").strip() for k, v in row.items()} for row in rows]


def tc_id_from_test_name(name):
    """test_TC_01_login_with... -> TC-01"""
    match = re.search(r"TC_(\d+)", name)
    return "TC-%s" % match.group(1) if match else None


def plain_failure(message):
    """Turn a technical error into 1-3 readable lines."""
    text = (message or "").split("Call log:")[0]
    text = re.sub(r"^\w*(Error|Exception):\s*", "", text.strip())
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    readable = []
    for line in lines[:3]:
        line = re.sub(r"^Actual value:", "What actually happened:", line)
        line = re.sub(r"^(Locator|Page URL|Page title) expected to", r"Expected: \1 to", line)
        readable.append(line)
    return " ".join(readable) or "The test stopped with an error."


def read_results(path):
    """Return {TC-ID: {"result": ..., "reason": ..., "seconds": ...}}."""
    results = {}
    if not os.path.isfile(path):
        return results
    for case in ET.parse(path).getroot().iter("testcase"):
        tc_id = tc_id_from_test_name(case.get("name", ""))
        if not tc_id:
            continue
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            result, reason = "Failed", plain_failure(failure.get("message") or failure.text)
        elif skipped is not None:
            result, reason = "Skipped", skipped.get("message", "")
        else:
            result, reason = "Passed", ""
        results[tc_id] = {"result": result, "reason": reason,
                          "seconds": float(case.get("time", 0) or 0),
                          "test_name": case.get("name")}
    return results


# ---------- joining sheet + results ----------

def combine(cases, results):
    rows = []
    for case in cases:
        tc_id = case.get("ID", "")
        run = results.get(tc_id)
        if run:
            result, reason = run["result"], run["reason"]
        elif case.get("Status", "").lower().startswith("blocked"):
            result, reason = "Blocked", "Waiting for an answer: " + case["Status"].split("-", 1)[-1].strip()
        else:
            result, reason = "Not automated", "No automated test exists for this test case yet."
        rows.append(dict(case, result=result, reason=reason))
    extra = [tc for tc in results if tc not in {c.get("ID") for c in cases}]
    return rows, extra


def verdict(counts):
    if counts["Failed"]:
        return "fail", "NOT READY - %d problem(s) found" % counts["Failed"]
    if counts["Blocked"] or counts["Not automated"] or counts["Skipped"]:
        return "blocked", "PARTLY TESTED - no problems found, but not everything was tested"
    return "pass", "ALL GOOD - every test case passed"


# ---------- building the HTML ----------

def esc(text):
    return html.escape(str(text or "")).replace("\n", "<br>")


def screenshot_tag(tc_id):
    path = os.path.join("reports", "screenshots", tc_id + ".png")
    if not os.path.isfile(path):
        return ""
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return ('<details><summary>Show screenshot of the screen at that moment</summary>'
            '<img src="data:image/png;base64,%s" alt="Screenshot for %s"></details>' % (data, tc_id))


def badge(result):
    label, css, icon = RESULT_STYLE[result]
    return '<span class="badge %s">%s %s</span>' % (css, icon, label)


def build_html(title, rows, extra):
    counts = {k: sum(1 for r in rows if r["result"] == k) for k in RESULT_STYLE}
    v_css, v_text = verdict(counts)
    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")

    # Problems first
    problems = [r for r in rows if r["result"] == "Failed"]
    problem_html = "".join(
        '<div class="problem"><h3>❌ %s &nbsp;%s</h3>'
        '<p><b>What we checked:</b> %s</p><p><b>What went wrong:</b> %s</p>%s</div>'
        % (esc(r["ID"]), esc(r["Title"]), esc(r.get("Expected result")), esc(r["reason"]), screenshot_tag(r["ID"]))
        for r in problems
    ) or '<p class="muted">No problems found. 🎉</p>'

    # All test cases, grouped by requirement
    table_rows = ""
    for r in rows:
        kind = KIND.get(r.get("Type", "").lower(), r.get("Type", ""))
        note = "" if r["result"] in ("Passed",) else '<div class="note">%s</div>' % esc(r["reason"])
        table_rows += (
            "<tr><td>%s</td><td><b>%s</b> %s<details><summary>Steps and expected result</summary>"
            "<p><b>Steps:</b><br>%s</p><p><b>Test data:</b><br>%s</p><p><b>Expected:</b> %s</p>"
            "</details>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(r.get("Requirement")), esc(r["ID"]), esc(r["Title"]), esc(r.get("Steps")),
               esc(r.get("Test data")), esc(r.get("Expected result")), note, esc(kind),
               esc(r.get("Priority")), badge(r["result"]))
        )

    # Requirement coverage
    reqs = {}
    for r in rows:
        for req in re.findall(r"R\d+", r.get("Requirement", "")):
            reqs.setdefault(req, []).append(r)
    coverage_rows = ""
    for req in sorted(reqs, key=lambda x: int(x[1:])):
        items = reqs[req]
        passed = sum(1 for i in items if i["result"] == "Passed")
        failed = sum(1 for i in items if i["result"] == "Failed")
        edge = sum(1 for i in items if i.get("Type", "").lower() in ("boundary", "negative", "security"))
        if failed:
            state = badge("Failed")
        elif passed == len(items):
            state = badge("Passed")
        else:
            state = '<span class="badge blocked">⏸ Partly tested</span>'
        edge_text = str(edge) if edge else '<span class="warn">0 - add edge cases</span>'
        coverage_rows += "<tr><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
            req, len(items), passed, edge_text, state)

    extra_html = ""
    if extra:
        extra_html = ('<p class="warn">⚠ These automated tests are not in the test case sheet: %s. '
                      'Add them to the sheet so they are tracked.</p>' % esc(", ".join(extra)))

    return TEMPLATE.format(
        title=esc(title), now=now, v_css=v_css, v_text=esc(v_text), total=len(rows),
        passed=counts["Passed"], failed=counts["Failed"],
        blocked=counts["Blocked"] + counts["Skipped"], manual=counts["Not automated"],
        problems=problem_html, table_rows=table_rows, coverage_rows=coverage_rows,
        extra=extra_html,
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test report - {title}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; background: #f6f7f9; color: #1d2330; }}
 main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }}
 h1 {{ margin: 0 0 4px; font-size: 26px; }} h2 {{ margin-top: 36px; font-size: 20px; }}
 .muted {{ color: #667085; }}
 .verdict {{ padding: 16px 20px; border-radius: 10px; font-size: 20px; font-weight: 700; margin: 20px 0; }}
 .verdict.pass {{ background: #e6f6ec; color: #11652f; }} .verdict.fail {{ background: #fdeaea; color: #a11b1b; }}
 .verdict.blocked {{ background: #fff4e0; color: #8a5300; }}
 .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
 .card {{ background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }}
 .card .num {{ font-size: 30px; font-weight: 700; }} .card .lbl {{ color: #667085; }}
 .problem {{ background: #fff; border-left: 5px solid #d92d20; border-radius: 8px; padding: 4px 16px 12px; margin: 12px 0; }}
 .problem h3 {{ margin: 12px 0 6px; }}
 table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; }}
 th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #eceef2; vertical-align: top; }}
 th {{ background: #eef0f4; font-size: 13px; text-transform: uppercase; letter-spacing: .03em; }}
 .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap; }}
 .badge.pass {{ background: #e6f6ec; color: #11652f; }} .badge.fail {{ background: #fdeaea; color: #a11b1b; }}
 .badge.blocked {{ background: #fff4e0; color: #8a5300; }} .badge.manual {{ background: #eef0f4; color: #475467; }}
 .note {{ color: #475467; font-size: 14px; margin-top: 6px; }} .warn {{ color: #a15c00; font-weight: 600; }}
 details summary {{ cursor: pointer; color: #2b59c3; font-size: 14px; margin-top: 6px; }}
 details img {{ max-width: 100%; border: 1px solid #ddd; margin-top: 8px; border-radius: 6px; }}
 .wrap {{ overflow-x: auto; }}
</style></head>
<body><main>
<h1>Test report: {title}</h1>
<div class="muted">Run on {now}</div>

<div class="verdict {v_css}">{v_text}</div>

<div class="cards">
 <div class="card"><div class="num">{total}</div><div class="lbl">Test cases</div></div>
 <div class="card"><div class="num">✅ {passed}</div><div class="lbl">Passed</div></div>
 <div class="card"><div class="num">❌ {failed}</div><div class="lbl">Failed</div></div>
 <div class="card"><div class="num">⏸ {blocked}</div><div class="lbl">Blocked (waiting for answers)</div></div>
 <div class="card"><div class="num">✋ {manual}</div><div class="lbl">Not automated yet</div></div>
</div>

<h2>Problems found</h2>
{problems}

<h2>Is every requirement tested?</h2>
<div class="wrap"><table>
<tr><th>Requirement</th><th>Test cases</th><th>Passed</th><th>Edge / wrong-input / security tests</th><th>State</th></tr>
{coverage_rows}
</table></div>

<h2>All test cases</h2>
{extra}
<div class="wrap"><table>
<tr><th>Req.</th><th>Test case</th><th>Kind</th><th>Priority</th><th>Result</th></tr>
{table_rows}
</table></div>

<h2>What the words mean</h2>
<ul class="muted">
 <li><b>Passed</b> - the app did exactly what the test case expected.</li>
 <li><b>Failed</b> - the app did something different. This is a problem to fix.</li>
 <li><b>Blocked</b> - we can't test it yet because a question about the rules is not answered.</li>
 <li><b>Not automated yet</b> - the test case exists but has no automated test; it must be tested by hand.</li>
 <li><b>Edge case</b> - testing the limits, e.g. just before and just after a time-out.</li>
</ul>
</main></body></html>
"""


def main():
    parser = argparse.ArgumentParser(description="Build a plain-language HTML test report")
    parser.add_argument("--cases", required=True, help="test case CSV file")
    parser.add_argument("--results", default="reports/results.xml", help="pytest JUnit XML file")
    parser.add_argument("--out", default="reports/report.html", help="where to write the report")
    parser.add_argument("--title", help="feature name (default: from the CSV file name)")
    args = parser.parse_args()

    title = args.title or os.path.basename(args.cases).replace("-test-cases.csv", "").replace("-", " ").title()
    rows, extra = combine(read_cases(args.cases), read_results(args.results))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(title, rows, extra))
    print("Report written: " + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
