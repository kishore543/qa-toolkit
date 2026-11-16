"""qa-toolkit: build a plain-language HTML test report.

It joins two things:
  1. the test case sheet (CSV or Excel)  -> WHAT should be tested
  2. the pytest results (JUnit XML)      -> WHAT happened

Run from the project folder, after the tests:
    python3 -m pytest --junitxml=reports/results.xml
    python3 make_report.py --cases test-cases/login-test-cases.csv
    python3 make_report.py --cases "Login Test Cases.xlsx"

Test names are matched to sheet IDs: test_TC_01_... -> TC-01, test_SA_12_... -> SA-12.
Writes reports/report.html - one file, open it in any browser, email it to anyone -
and reports/report.xlsx with the same results (tabs: Summary, Problems, All test cases).
"""
import argparse
import base64
import datetime
import html
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from read_cases import read_cases  # noqa: E402  (same folder: reads CSV and Excel)

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

def tc_id_from_test_name(name):
    """test_TC_01_login_with... -> TC-01,  test_SA_12_register... -> SA-12,  test_E2E_03_... -> E2E-03"""
    match = re.match(r"test_([A-Za-z][A-Za-z0-9]*)_(\d+)", name)
    return "%s-%s" % (match.group(1).upper(), match.group(2)) if match else None


def looked_for(message):
    """What the test was looking for on the screen, in words, from Playwright's call log:
    'waiting for get_by_role("heading", name="Certificate verified")' -> Certificate verified.
    None when the test used a technical locator such as "#login-btn"."""
    found = re.search(r"waiting for .*?(?:name|has_text)=[\"'](.+?)[\"']", message or "") \
        or re.search(r"waiting for get_by_(?:text|label|placeholder|title|alt_text)\([\"'](.+?)[\"']", message or "")
    return found.group(1) if found else None


def screen_headings(message):
    """Headings the screen showed at the moment of failure (newer Playwright puts a
    snapshot of the screen in the error): 'heading "This certificate could not be verified"'."""
    return re.findall(r'heading "([^"]+)"', message or "")


def plain_failure(message):
    """Turn a technical error into 1-3 readable lines."""
    full = message or ""
    if "element(s) not found" in full:          # Playwright: something was not on the screen
        wanted, shown = looked_for(full), screen_headings(full)
        text = "Expected to see: %s. " % ('"%s"' % wanted if wanted else "an element the test looks for")
        text += "What actually happened: it was not on the screen"
        if shown:
            text += " - the screen showed: " + "; ".join('"%s"' % h for h in shown[:3])
        return text + "."
    text = full.split("Call log:")[0]
    text = re.sub(r"^\w*(Error|Exception):\s*", "", text.strip())
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # keep the tester's own message; drop pytest's "assert 404 != 404 / + where ..." lines
    # and the screen snapshot lines ("- banner:")
    lines = [l for l in lines if not re.match(r"^(assert |\+|where |- )", l)]
    readable = []
    for line in lines[:3]:
        line = re.sub(r"^Actual value:", "What actually happened:", line)
        line = re.sub(r"^(Locator|Page URL|Page title) expected to", r"Expected: \1 to", line)
        readable.append(line)
    return " ".join(readable) or "The test stopped with an error."


# ---------- why did it fail? (the same 4 groups as the heal skill) ----------

# (group, what it means in plain words, what to do next)
CAUSES = {
    "environment": ("Environment problem",
                    "The website could not be reached, or the test was stopped before it could check anything.",
                    "Check the site is up and the test account works, then run again. Not a bug in the app."),
    "locator": ("Test needs updating (screen changed)",
                "The test could not find a button or field on the screen - the developers probably renamed or moved it.",
                "Run /qa-toolkit:heal to fix the test. Not a bug in the app, unless the element was removed on purpose."),
    "out_of_step": ("Test out of step with the sheet",
                    "The app did what the test case sheet says, but the test expects something else - the test is wrong.",
                    "Correct the test so it matches the sheet. Not a bug in the app."),
    "bug": ("Possible bug in the app",
            "The app showed something different from the Expected result in the test case sheet.",
            "Tell the developers (raise a bug). Do not change the test."),
}

ENVIRONMENT_SIGNS = ("net::err_", "err_name_not_resolved", "err_connection", "connection refused",
                     "safety guard blocked", "page.goto: timeout", "browser has been closed",
                     "target page, context or browser has been closed")
LOCATOR_SIGNS = ("element(s) not found", "waiting for locator", "strict mode violation",
                 "resolved to 0 elements")


def tests_expected_value(message):
    """The value the TEST expected, taken from the error, e.g.
    "Locator expected to have text 'Your username is invalid!'" -> Your username is invalid!"""
    found = re.search(r"expected to (?:have|contain) [\w ]*?'(.+?)'", message) \
        or re.search(r"expected to (?:have|contain) [\w ]*?\"(.+?)\"", message)
    return found.group(1) if found else None


def simple(text):
    """Lower case, single spaces - so small spacing differences don't matter when comparing."""
    return " ".join(str(text or "").lower().split())


def root_cause(message, sheet_expected, whole_sheet=""):
    """Sort one failure into: environment / locator / out_of_step / bug.

    message        - the full error text from pytest
    sheet_expected - the Expected result of this test case in the sheet (the source of truth)
    whole_sheet    - the Expected results of all test cases (a test may first check something
                     another case promises, e.g. "Certificate verified" before checking privacy)
    """
    text = simple(message)
    if any(sign in text for sign in ENVIRONMENT_SIGNS):
        return "environment"
    if any(sign in text for sign in LOCATOR_SIGNS):
        # Not found - but WHAT was not found? If it is something the sheet promises
        # ("'Certificate verified' with learner..."), the app did not show the expected
        # result: a possible bug. A technical locator ("#login-btn") the sheet never
        # mentions means the screen changed: the test needs updating.
        wanted = looked_for(message)
        if wanted and simple(wanted) in simple(sheet_expected + " " + whole_sheet):
            return "bug"
        return "locator"
    wanted = tests_expected_value(message)
    if wanted and simple(wanted) not in simple(sheet_expected):
        return "out_of_step"   # the test checks for something the sheet never asked for
    return "bug"


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
        raw = ""
        if failure is not None:
            result, reason = "Failed", plain_failure(failure.get("message") or failure.text)
            raw = "%s\n%s" % (failure.get("message") or "", failure.text or "")  # full error, for root_cause
        elif skipped is not None:
            result, reason = "Skipped", skipped.get("message", "")
        else:
            result, reason = "Passed", ""
        results[tc_id] = {"result": result, "reason": reason, "raw": raw,
                          "seconds": float(case.get("time", 0) or 0),
                          "test_name": case.get("name")}
    return results


# ---------- joining sheet + results ----------

def combine(cases, results):
    rows = []
    whole_sheet = " ".join(c.get("Expected result", "") for c in cases)
    for case in cases:
        tc_id = case.get("ID", "")
        run = results.get(tc_id.upper())
        cause = None
        if run:
            result, reason = run["result"], run["reason"]
            if result == "Failed":
                cause = root_cause(run["raw"], case.get("Expected result") or "", whole_sheet)
        elif case.get("Status", "").lower().startswith("blocked"):
            result, reason = "Blocked", "Waiting for an answer: " + case["Status"].split("-", 1)[-1].strip()
        else:
            result, reason = "Not automated", "No automated test exists for this test case yet."
        rows.append(dict(case, result=result, reason=reason, cause=cause))
    extra = [tc for tc in results if tc not in {c.get("ID", "").upper() for c in cases}]
    return rows, extra


def verdict(counts, rows):
    if counts["Failed"]:
        by_cause = {c: sum(1 for r in rows if r["cause"] == c) for c in CAUSES}
        parts = []
        if by_cause["bug"]:
            parts.append("%d possible bug(s) in the app" % by_cause["bug"])
        if by_cause["locator"] or by_cause["out_of_step"]:
            parts.append("%d test(s) need updating" % (by_cause["locator"] + by_cause["out_of_step"]))
        if by_cause["environment"]:
            parts.append("%d could not run (environment)" % by_cause["environment"])
        if by_cause["bug"]:
            return "fail", "NOT READY - " + ", ".join(parts)
        # Failures, but none points at the app: be honest - we can't say it is good yet.
        return "blocked", "NOT SURE YET - no app bugs found, but " + ", ".join(parts)
    if counts["Blocked"] or counts["Not automated"] or counts["Skipped"]:
        return "blocked", "PARTLY TESTED - no problems found, but not everything was tested"
    return "pass", "ALL GOOD - every test case passed"


def grouping(rows):
    """Group by requirement (R1, R2...) when the sheet has them, otherwise by Area / tab.
    Returns (use_reqs, label, groups_of) - groups_of(row) gives the groups of one test case."""
    use_reqs = any(re.search(r"R\d+", r.get("Requirement", "")) for r in rows)

    def groups_of(r):
        if use_reqs:
            return re.findall(r"R\d+", r.get("Requirement", ""))
        return [r.get("Area") or r.get("Sheet") or "-"]

    return use_reqs, ("Requirement" if use_reqs else "Area"), groups_of


PROBLEM_ORDER = ["bug", "out_of_step", "locator", "environment"]  # possible app bugs first


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
    v_css, v_text = verdict(counts, rows)
    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")

    # Problems first - possible app bugs at the top, then tests to fix, then environment
    problems = sorted((r for r in rows if r["result"] == "Failed"), key=lambda r: PROBLEM_ORDER.index(r["cause"]))
    problem_html = "".join(
        '<div class="problem %s"><h3>❌ %s &nbsp;%s</h3>'
        '<div class="cause"><b>Why it failed: %s</b><br>%s<br><b>What to do:</b> %s</div>'
        '<p><b>What we checked:</b> %s</p><p><b>What went wrong:</b> %s</p>%s</div>'
        % (r["cause"], esc(r["ID"]), esc(r["Title"]),
           esc(CAUSES[r["cause"]][0]), esc(CAUSES[r["cause"]][1]), esc(CAUSES[r["cause"]][2]),
           esc(r.get("Expected result")), esc(r["reason"]), screenshot_tag(r["ID"]))
        for r in problems
    ) or '<p class="muted">No problems found. 🎉</p>'

    use_reqs, group_label, groups_of = grouping(rows)

    # All test cases
    table_rows = ""
    for r in rows:
        kind = KIND.get(r.get("Type", "").lower(), r.get("Type", ""))
        note = "" if r["result"] in ("Passed",) else '<div class="note">%s</div>' % esc(r["reason"])
        if r["cause"]:
            note = '<div class="note"><b>%s.</b> %s</div>' % (esc(CAUSES[r["cause"]][0]), esc(r["reason"]))
        table_rows += (
            "<tr><td>%s</td><td><b>%s</b> %s<details><summary>Steps and expected result</summary>"
            "<p><b>Steps:</b><br>%s</p><p><b>Test data:</b><br>%s</p><p><b>Expected:</b> %s</p>"
            "</details>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(", ".join(groups_of(r))), esc(r["ID"]), esc(r["Title"]), esc(r.get("Steps")),
               esc(r.get("Test data")), esc(r.get("Expected result")), note, esc(kind),
               esc(r.get("Priority")), badge(r["result"]))
        )

    # Coverage per requirement / area
    reqs = {}
    for r in rows:
        for req in groups_of(r):
            reqs.setdefault(req, []).append(r)
    has_types = any(r.get("Type") for r in rows)
    order = (lambda x: int(x[1:])) if use_reqs else None
    coverage_rows = ""
    for req in sorted(reqs, key=order) if use_reqs else reqs:
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
        if not has_types:
            edge_text = '<span class="muted">not marked in the sheet</span>'
        else:
            edge_text = str(edge) if edge else '<span class="warn">0 - add edge cases</span>'
        coverage_rows += "<tr><td>%s</td><td>%d</td><td>%d</td><td>%s</td><td>%s</td></tr>" % (
            esc(req), len(items), passed, edge_text, state)

    extra_html = ""
    if extra:
        extra_html = ('<p class="warn">⚠ These automated tests are not in the test case sheet: %s. '
                      'Add them to the sheet so they are tracked.</p>' % esc(", ".join(extra)))

    return TEMPLATE.format(
        title=esc(title), now=now, v_css=v_css, v_text=esc(v_text), total=len(rows),
        passed=counts["Passed"], failed=counts["Failed"],
        blocked=counts["Blocked"] + counts["Skipped"], manual=counts["Not automated"],
        problems=problem_html, table_rows=table_rows, coverage_rows=coverage_rows,
        extra=extra_html, group=group_label, group_lower=group_label.lower(),
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
 .problem.locator, .problem.out_of_step {{ border-left-color: #f79009; }}
 .problem.environment {{ border-left-color: #98a2b3; }}
 .cause {{ background: #f6f7f9; border-radius: 6px; padding: 8px 12px; font-size: 15px; }}
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

<h2>Is every {group_lower} tested?</h2>
<div class="wrap"><table>
<tr><th>{group}</th><th>Test cases</th><th>Passed</th><th>Edge / wrong-input / security tests</th><th>State</th></tr>
{coverage_rows}
</table></div>

<h2>All test cases</h2>
{extra}
<div class="wrap"><table>
<tr><th>{group}</th><th>Test case</th><th>Kind</th><th>Priority</th><th>Result</th></tr>
{table_rows}
</table></div>

<h2>What the words mean</h2>
<ul class="muted">
 <li><b>Passed</b> - the app did exactly what the test case expected.</li>
 <li><b>Failed</b> - the test did not pass. Each failure says why:</li>
 <li style="margin-left:20px"><b>Possible bug in the app</b> - the app does not do what the test case sheet says. Developers must fix it.</li>
 <li style="margin-left:20px"><b>Test needs updating</b> - the screen changed or the test was written wrongly. Testers fix the test; the app may be fine.</li>
 <li style="margin-left:20px"><b>Environment problem</b> - the site or test account was not available. Run again later.</li>
 <li><b>Blocked</b> - we can't test it yet because a question about the rules is not answered.</li>
 <li><b>Not automated yet</b> - the test case exists but has no automated test; it must be tested by hand.</li>
 <li><b>Edge case</b> - testing the limits, e.g. just before and just after a time-out.</li>
</ul>
</main></body></html>
"""


# ---------- building the Excel report ----------

# Cell colours for each result (background, text) - the same colours as the HTML report
EXCEL_COLOURS = {
    "Passed": ("E6F6EC", "11652F"), "Failed": ("FDEAEA", "A11B1B"),
    "Blocked": ("FFF4E0", "8A5300"), "Skipped": ("FFF4E0", "8A5300"),
    "Not automated": ("EEF0F4", "475467"),
}


def write_excel(path, title, rows, extra):
    """Write the same results as an Excel file with 3 tabs: Summary, Problems, All test cases."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        print("Excel report skipped: openpyxl is not installed (python3 -m pip install openpyxl)")
        return None

    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="EEF0F4")
    wrap = Alignment(wrap_text=True, vertical="top")

    def colour(cell, result):
        back, text = EXCEL_COLOURS[result]
        cell.fill = PatternFill("solid", fgColor=back)
        cell.font = Font(bold=True, color=text)

    def table(sheet, headers, data, widths, result_col=None):
        """A header row, then the data rows: wrapped text, filters and a frozen header."""
        sheet.append(headers)
        start = sheet.max_row
        for cell in sheet[start]:
            cell.font, cell.fill = bold, header_fill
        for values in data:
            sheet.append(values)
            for cell in sheet[sheet.max_row]:
                cell.alignment = wrap
            if result_col is not None:
                colour(sheet.cell(sheet.max_row, result_col + 1), values[result_col])
        for i, width in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = width
        return start

    counts = {k: sum(1 for r in rows if r["result"] == k) for k in RESULT_STYLE}
    v_css, v_text = verdict(counts, rows)
    use_reqs, group_label, groups_of = grouping(rows)
    book = Workbook()

    # Tab 1: Summary - the verdict, the counts, and is every requirement / area tested?
    summary = book.active
    summary.title = "Summary"
    summary.append(["Test report: " + title])
    summary["A1"].font = Font(bold=True, size=16)
    summary.append(["Run on " + datetime.datetime.now().strftime("%d %b %Y, %H:%M")])
    summary.append([v_text])
    summary["A3"].font = Font(bold=True, size=13, color={"fail": "A11B1B", "blocked": "8A5300", "pass": "11652F"}[v_css])
    summary.append([])
    summary.append(["Test cases", len(rows)])
    for label, key in (("Passed", "Passed"), ("Failed", "Failed"), ("Blocked (waiting for answers)", "Blocked"),
                       ("Not automated yet", "Not automated")):
        summary.append([label, counts[key] + (counts["Skipped"] if key == "Blocked" else 0)])
    for key in PROBLEM_ORDER:
        n = sum(1 for r in rows if r["cause"] == key)
        if n:
            summary.append(["   of which: " + CAUSES[key][0], n])
    summary.append([])
    groups = {}
    for r in rows:
        for g in groups_of(r):
            groups.setdefault(g, []).append(r)
    names = sorted(groups, key=lambda x: int(x[1:])) if use_reqs else list(groups)
    coverage = []
    for g in names:
        items = groups[g]
        passed = sum(1 for i in items if i["result"] == "Passed")
        failed = sum(1 for i in items if i["result"] == "Failed")
        state = "Failed" if failed else ("Passed" if passed == len(items) else "Blocked")
        coverage.append([g, len(items), passed, failed, state])
    start = table(summary, [group_label, "Test cases", "Passed", "Failed", "State"], coverage, [34, 12, 10, 10, 14])
    for row in range(start + 1, summary.max_row + 1):
        cell = summary.cell(row, 5)
        colour(cell, cell.value)
        if cell.value == "Blocked":
            cell.value = "Partly tested"
    if extra:
        summary.append([])
        summary.append(["Automated tests not in the test case sheet: " + ", ".join(extra)])

    # Tab 2: Problems - only the failures, possible app bugs first
    problems = book.create_sheet("Problems")
    failed = sorted((r for r in rows if r["result"] == "Failed"), key=lambda r: PROBLEM_ORDER.index(r["cause"]))
    table(problems, ["ID", "Test case", "Why it failed", "What to do", "What we checked", "What went wrong"],
          [[r["ID"], r["Title"], CAUSES[r["cause"]][0], CAUSES[r["cause"]][2], r.get("Expected result", ""),
            r["reason"]] for r in failed] or [["", "No problems found.", "", "", "", ""]],
          [10, 34, 26, 40, 44, 50])
    problems.freeze_panes = "A2"

    # Tab 3: All test cases - one row each, with filters so anyone can show e.g. only "Failed"
    cases = book.create_sheet("All test cases")
    table(cases, ["ID", group_label, "Test case", "Kind", "Priority", "Result", "Why it failed / note",
                  "Expected result", "Steps", "Test data"],
          [[r["ID"], ", ".join(groups_of(r)), r["Title"], KIND.get(r.get("Type", "").lower(), r.get("Type", "")),
            r.get("Priority", ""), r["result"],
            (CAUSES[r["cause"]][0] + ". " if r["cause"] else "") + (r["reason"] if r["result"] != "Passed" else ""),
            r.get("Expected result", ""), r.get("Steps", ""), r.get("Test data", "")] for r in rows],
          [10, 16, 36, 14, 10, 16, 44, 44, 44, 28], result_col=5)
    cases.freeze_panes = "A2"
    cases.auto_filter.ref = cases.dimensions

    book.save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description="Build a plain-language HTML test report")
    parser.add_argument("--cases", required=True, help="test case CSV file")
    parser.add_argument("--results", default="reports/results.xml", help="pytest JUnit XML file")
    parser.add_argument("--out", default="reports/report.html", help="where to write the report")
    parser.add_argument("--title", help="feature name (default: from the CSV file name)")
    args = parser.parse_args()

    name = os.path.splitext(os.path.basename(args.cases))[0]
    title = args.title or re.sub(r"[-_ ]?test[-_ ]?cases$", "", name, flags=re.I).replace("-", " ").replace("_", " ").strip().title()
    rows, extra = combine(read_cases(args.cases), read_results(args.results))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(title, rows, extra))
    print("Report written: " + os.path.abspath(args.out))
    excel = write_excel(os.path.splitext(args.out)[0] + ".xlsx", title, rows, extra)
    if excel:
        print("Excel report written: " + os.path.abspath(excel))


if __name__ == "__main__":
    main()
