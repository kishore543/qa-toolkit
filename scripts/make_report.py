"""qa-toolkit: build a plain-language HTML test report.

It joins two things:
  1. the test case sheet (CSV or Excel)  -> WHAT should be tested
  2. the pytest results (JUnit XML)      -> WHAT happened

Run from the project folder, after the tests:
    python3 -m pytest --junitxml=reports/results.xml
    python3 make_report.py --cases test-cases/login-test-cases.csv
    python3 make_report.py --cases "Login Test Cases.xlsx"
    python3 make_report.py --cases test-cases/super_admin/sheet.csv \
        --results reports/super_admin/results.xml --out reports/super_admin/report.html

Test names are matched to sheet IDs: test_TC_01_... -> TC-01, test_SA_12_... -> SA-12.
Tests are grouped by FLOW = the test file they live in (test_suspend_restore.py -> "Suspend restore").
Writes report.html - one file, open it in any browser, email it to anyone - and report.xlsx
with the same results (tabs: Summary, Problems, Passed, All test cases).
"""
import argparse
import base64
import datetime
import html
import os
import re
import string
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

NO_FLOW = "Not linked to a flow"


# ---------- reading the inputs ----------

def tc_id_from_test_name(name):
    """test_TC_01_login_with... -> TC-01,  test_SA_12_register... -> SA-12,  test_E2E_03_... -> E2E-03"""
    match = re.match(r"test_([A-Za-z][A-Za-z0-9]*)_(\d+)", name)
    return "%s-%s" % (match.group(1).upper(), match.group(2)) if match else None


def flow_from_classname(classname):
    """tests.super_admin.test_suspend_restore -> 'Suspend restore' (the flow = the test file)."""
    module = (classname or "").split(".")[-1]
    module = re.sub(r"^test_", "", module)
    return module.replace("_", " ").strip().capitalize() or NO_FLOW


def human_pattern(text):
    """Make a regex from a test readable: ^Invitations pending \\d+ Awaiting -> Invitations pending <number> Awaiting"""
    text = re.sub(r"^re\.compile\(r?['\"](.*)['\"]\)$", r"\1", text.strip())
    text = text.replace("\\d+", "<number>").replace("[0-9a-f-]+", "<id>").replace("\\s*", " ").replace("\\s+", " ")
    text = re.sub(r"\\(.)", r"\1", text)
    return text.strip("^$").strip()


def target_of(message):
    """What the test was working with, in words, from Playwright's call log:
    'waiting for get_by_role("button", name=re.compile(r"^Sign out"))' -> the "Sign out" button."""
    line = re.search(r"waiting for (.+)", message or "")
    if not line:
        return None
    chain = line.group(1)
    # The LAST locator in a chain is the thing itself: get_by_role("row")...get_by_role("button", name="Onboard")
    parts = list(re.finditer(
        r"get_by_role\([\"'](?P<role>\w+)[\"'](?:,\s*name=(?P<name>re\.compile\(r?[\"'].+?[\"']\)|[\"'].+?[\"']))?"
        r"|get_by_(?:text|label|placeholder|title|alt_text|test_id)\([\"'](?P<text>.+?)[\"']"
        r"|(?<!_)locator\([\"'](?P<css>.+?)[\"']\)", chain))
    if not parts:
        return None
    last = parts[-1]
    if last.group("role"):
        role, name = last.group("role"), last.group("name")
        name = human_pattern(name.strip("\"'")) if name else ""
        role = {"textbox": "field", "dialog": "dialog box", "main": "main page area", "alert": "message",
                "status": "message", "combobox": "list", "checkbox": "tick box", "radio": "option"}.get(role, role)
        return ('the "%s" %s' % (name, role)) if name else "the %s" % role
    if last.group("text"):
        return '"%s"' % human_pattern(last.group("text"))
    return "the element %s" % last.group("css")


def looked_for(message):
    """The text the test was looking for (for comparing with the sheet), e.g. Certificate verified.
    None when the test used a technical locator such as "#login-btn"."""
    target = target_of(message)   # the LAST locator in the chain, e.g. the "Suspended" heading
    found = re.match(r'(?:the )?"(.+?)"', target or "")
    return found.group(1) if found else None


def screen_headings(message):
    """Headings the screen showed at the moment of failure (newer Playwright puts a
    snapshot of the screen in the error): 'heading "This certificate could not be verified"'."""
    return re.findall(r'heading "([^"]+)"', message or "")


ACTIONS = {"click": "click", "dblclick": "double-click", "fill": "type into", "type": "type into",
           "press": "press a key in", "check": "tick", "uncheck": "untick", "select_option": "choose an option in",
           "hover": "point at", "set_input_files": "upload a file to"}

EXPECTS = {  # Playwright's words -> everyday words
    "be visible": "be on the screen", "be hidden": "be gone from the screen",
    "have text": "show the text", "contain text": "contain the text",
    "have accessible name": "read", "have value": "have the value", "have count": "appear this many times:",
    "be enabled": "be clickable", "be disabled": "be not clickable", "be checked": "be ticked",
    "have attribute": "have the setting", "be editable": "be editable",
}


def shorten(text, limit=220):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def plain_failure(message):
    """Turn a technical error into 1-2 sentences anyone can read."""
    full = message or ""
    setup = full.startswith("failed on setup") or "failed on setup" in full[:60]
    prefix = "The test could not prepare its test data, so it never started. " if setup else ""
    target = target_of(full)

    # 1. A step could not be done: Locator.click: Timeout 15000ms exceeded.
    action = re.search(r"Locator\.(\w+): Timeout (\d+)ms", full)
    if action:
        verb = ACTIONS.get(action.group(1), action.group(1))
        secs = int(action.group(2)) // 1000
        if " disabled" in full.split("Call log:")[-1][:600] or "not enabled" in full:
            why = "it was on the screen but stayed disabled (not clickable)"
        elif "detached from the DOM" in full:
            why = "the screen kept changing under it"
        elif "not visible" in full:
            why = "it was there but hidden"
        elif "resolved to" in full:
            why = "it was there but never became ready"
        else:
            why = "it was never found on the screen"
        return prefix + "The test tried to %s %s, but could not within %d seconds - %s." % (
            verb, target or "an element", secs, why)

    # 2. The page address was wrong
    url = re.search(r"Page URL expected to (?:be|match) (.+?)\s*(?:\n|Actual value|$)", full)
    if url:
        actual = re.search(r"Actual value: (\S+)", full)
        return prefix + "Expected the page address to be %s, but it was %s." % (
            human_pattern(url.group(1).strip("'\"")), actual.group(1) if actual else "different")

    # 3. A check (expect) did not match
    check = re.search(r"Locator expected (?:not )?to ([a-z ]+?)(?: '(.+?)')?\s*(?:\n|$|Actual)", full)
    if check:
        kind = check.group(1).strip()
        what = EXPECTS.get(kind, kind)
        wanted = check.group(2)   # only the value Playwright says was expected - never guessed
        actual = re.search(r"Actual value: ?(.*)", full)
        actual = actual.group(1).strip() if actual else ""
        if not actual or actual.startswith("- ") or kind == "be visible":
            shown = screen_headings(full)
            seen = (" The screen showed: " + "; ".join('"%s"' % h for h in shown[:3]) + ".") if shown else ""
            return prefix + "Expected to see %s, but it was not on the screen.%s" % (target or "an element", seen)
        if wanted:
            sentence = 'Expected %s to %s "%s"' % (target or "it", what, shorten(human_pattern(wanted), 120))
        else:
            sentence = "%s did not show what the test case expects" % (target or "The element")
            sentence = sentence[0].upper() + sentence[1:]
        return prefix + sentence + '. What actually happened: "%s".' % shorten(actual, 200)

    if "element(s) not found" in full:
        shown = screen_headings(full)
        seen = (" The screen showed: " + "; ".join('"%s"' % h for h in shown[:3]) + ".") if shown else ""
        return prefix + "Expected to see %s, but it was not on the screen.%s" % (
            ('"%s"' % looked_for(full)) if looked_for(full) else (target or "an element"), seen)

    # 4. The tester's own message (assert ..., "message") - keep it, drop pytest's diff lines
    text = re.sub(r"^failed on setup with \"?", "", full.split("Call log:")[0].strip())
    text = re.sub(r"^[\w.]*(Error|Exception):\s*", "", text)
    text = re.split(r"\n\s*assert |\n\s*Left contains|Full diff:", text)[0]
    lines = [l.strip() for l in text.splitlines() if l.strip() and not re.match(r"^(assert |\+|where |- )", l.strip())]
    return prefix + (shorten(" ".join(lines[:2]), 300) or "The test stopped with an error.")


def technical(message):
    """The first useful lines of the real error, for testers (shown folded away)."""
    lines = [l.rstrip() for l in (message or "").splitlines() if l.strip()]
    keep = [l for l in lines if not re.match(r"^\s*(-\s+(main|banner|complementary|navigation|link|paragraph|img|/url|text|list|listitem|generic)\b)", l)]
    return "\n".join(shorten(l, 260) for l in keep[:10])


# ---------- why did it fail? ----------

# (group, what it means in plain words, what to do next, who acts)
CAUSES = {
    "environment": ("Could not run (environment)",
                    "The website could not be reached, or the test was stopped before it could check anything.",
                    "Check the site is up and the test account works, then run again. Not a bug in the app.",
                    "QA"),
    "locator": ("Test needs updating (screen changed)",
                "The test could not find a button or field on the screen - the developers probably renamed or moved it.",
                "Run /qa-toolkit:heal to fix the test. Not a bug in the app, unless the element was removed on purpose.",
                "QA"),
    "out_of_step": ("Test out of step with the sheet",
                    "The app did what the test case sheet says, but the test expects something else - the test is wrong.",
                    "Correct the test so it matches the sheet. Not a bug in the app.",
                    "QA"),
    "step": ("Needs a tester to look",
             "The test could not finish one of its steps (a button or field was missing, disabled or kept changing), "
             "so the Expected result was never checked.",
             "Open the screenshot. If the app is wrong (for example a button stays disabled), raise a bug. "
             "If the screen changed, run /qa-toolkit:heal. If the test was too quick, fix the test.",
             "QA"),
    "bug": ("Possible bug in the app",
            "The app showed something different from the Expected result in the test case sheet.",
            "Tell the developers (raise a bug). Do not change the test.",
            "Developers"),
}

# The sections of "What needs attention", in order: (title, who it is for, causes in it)
ATTENTION = [
    ("Possible bugs in the app", "for the developers", ["bug"]),
    ("Needs a tester to look", "the test could not finish, so nothing was checked", ["step"]),
    ("Tests to update", "for QA - the app may be fine", ["out_of_step", "locator"]),
    ("Could not run", "environment - run again", ["environment"]),
]

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
    """Sort one failure into: environment / step / locator / out_of_step / bug.

    message        - the full error text from pytest
    sheet_expected - the Expected result of this test case in the sheet (the source of truth)
    whole_sheet    - the Expected results of all test cases (a test may first check something
                     another case promises, e.g. "Certificate verified" before checking privacy)
    """
    text = simple(message)
    if any(sign in text for sign in ENVIRONMENT_SIGNS):
        return "environment"
    # A STEP could not be done (click / fill ... timed out, or the test data could not be
    # prepared). Nothing was checked yet, so we cannot call it a bug - a tester must look.
    if re.search(r"locator\.\w+: timeout", text) or text.startswith("failed on setup"):
        return "step"
    not_found = any(sign in text for sign in LOCATOR_SIGNS) or "expected to be visible" in text
    if not_found:
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
    """Return ({TC-ID: {...}}, run_info). run_info = {"seconds": total, "started": datetime or None}."""
    results, info = {}, {"seconds": 0.0, "started": None}
    if not os.path.isfile(path):
        return results, info
    root = ET.parse(path).getroot()
    for suite in root.iter("testsuite"):
        info["seconds"] += float(suite.get("time", 0) or 0)
        stamp = suite.get("timestamp")
        if stamp and not info["started"]:
            try:
                info["started"] = datetime.datetime.fromisoformat(stamp)
            except ValueError:
                pass
    for case in root.iter("testcase"):
        tc_id = tc_id_from_test_name(case.get("name", ""))
        if not tc_id:
            continue
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        skipped = case.find("skipped")
        raw = ""
        if failure is not None:
            raw = "%s\n%s" % (failure.get("message") or "", failure.text or "")  # full error, for root_cause
            result, reason = "Failed", plain_failure(failure.get("message") or failure.text)
        elif skipped is not None:
            result, reason = "Skipped", skipped.get("message", "")
        else:
            result, reason = "Passed", ""
        results[tc_id] = {"result": result, "reason": reason, "raw": raw,
                          "tech": technical(failure.get("message") or failure.text) if failure is not None else "",
                          "seconds": float(case.get("time", 0) or 0),
                          "test_name": case.get("name"), "flow": flow_from_classname(case.get("classname"))}
    return results, info


# ---------- joining sheet + results ----------

def combine(cases, results):
    rows = []
    whole_sheet = " ".join(c.get("Expected result", "") for c in cases)
    for case in cases:
        tc_id = case.get("ID", "")
        run = results.get(tc_id.upper())
        cause, flow, seconds, tech = None, None, None, ""
        if run:
            result, reason, flow, seconds, tech = run["result"], run["reason"], run["flow"], run["seconds"], run["tech"]
            if result == "Failed":
                cause = root_cause(run["raw"], case.get("Expected result") or "", whole_sheet)
        elif case.get("Status", "").lower().startswith("blocked"):
            result, reason = "Blocked", "Waiting for an answer: " + case["Status"].split("-", 1)[-1].strip()
        else:
            result, reason = "Not automated", "No automated test exists for this test case yet."
        rows.append(dict(case, result=result, reason=reason, cause=cause, flow=flow, seconds=seconds, tech=tech))
    place_unrun_in_flows(rows)
    extra = [tc for tc in results if tc not in {c.get("ID", "").upper() for c in cases}]
    return rows, extra


def place_unrun_in_flows(rows):
    """Blocked / not automated cases have no test file. Put each one in the flow whose tests
    cover the same requirement (R5...), or the same Area / tab, so every flow shows its gaps."""
    by_key = {}
    for r in rows:
        if r["flow"]:
            for key in keys_of(r):
                by_key.setdefault(key, {}).setdefault(r["flow"], 0)
                by_key[key][r["flow"]] += 1
    for i, r in enumerate(rows):
        if r["flow"]:
            continue
        votes = {}
        for key in keys_of(r):
            for flow, n in by_key.get(key, {}).items():
                votes[flow] = votes.get(flow, 0) + n
        if votes:
            r["flow"] = max(votes, key=votes.get)
            continue
        # No test shares its requirement: sheets list cases flow by flow, so use the
        # flow of the nearest test case above it (or below it, for the first ones).
        near = [rows[j]["flow"] for j in range(i - 1, -1, -1) if rows[j]["flow"] not in (None, NO_FLOW)] \
            or [rows[j]["flow"] for j in range(i + 1, len(rows)) if rows[j]["flow"] not in (None, NO_FLOW)]
        r["flow"] = near[0] if near else NO_FLOW


def keys_of(r):
    reqs = re.findall(r"R\d+", r.get("Requirement", ""))
    return reqs or [r.get("Area") or r.get("Sheet") or ""]


def counts_of(rows):
    return {k: sum(1 for r in rows if r["result"] == k) for k in RESULT_STYLE}


def cause_counts(rows):
    return {c: sum(1 for r in rows if r["cause"] == c) for c in CAUSES}


def verdict(counts, rows):
    if counts["Failed"]:
        by_cause = cause_counts(rows)
        parts = []
        if by_cause["bug"]:
            parts.append("%d possible bug(s) in the app" % by_cause["bug"])
        if by_cause["step"]:
            parts.append("%d need(s) a tester to look" % by_cause["step"])
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


def minutes(seconds):
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return "%d seconds" % seconds
    return "%d min %02d s" % (seconds // 60, seconds % 60)


def plain_summary(rows, info):
    """The report in 3-5 short sentences - the part a manager reads."""
    c, by = counts_of(rows), cause_counts(rows)
    ran = c["Passed"] + c["Failed"] + c["Skipped"]
    when = info["started"].strftime("%d %b %Y at %H:%M") if info.get("started") else "this run"
    lines = ["We ran <b>%d automated tests</b> (out of %d test cases) on %s; the run took %s."
             % (ran, len(rows), when, minutes(info.get("seconds")))]
    if c["Passed"]:
        lines.append("✅ <b>%d worked</b> exactly as the test cases expect." % c["Passed"])
    if c["Failed"]:
        parts = []
        if by["bug"]:
            parts.append("<b>%d look like bugs in the app</b> (for the developers)" % by["bug"])
        if by["step"]:
            parts.append("%d could not finish a step and need a tester to look" % by["step"])
        if by["locator"] + by["out_of_step"]:
            parts.append("%d are tests that need updating" % (by["locator"] + by["out_of_step"]))
        if by["environment"]:
            parts.append("%d could not run because of the environment" % by["environment"])
        lines.append("❌ <b>%d did not pass</b>: %s." % (c["Failed"], ", ".join(parts)))
    if c["Blocked"] + c["Skipped"]:
        lines.append("⏸ %d could not be tested yet, because a question about the rules is still open."
                     % (c["Blocked"] + c["Skipped"]))
    if c["Not automated"]:
        lines.append("✋ %d have no automated test yet and must be tested by hand." % c["Not automated"])
    return lines


def grouping(rows):
    """Group by requirement (R1, R2...) when the sheet has them, otherwise by Area / tab.
    Returns (use_reqs, label, groups_of) - groups_of(row) gives the groups of one test case."""
    use_reqs = any(re.search(r"R\d+", r.get("Requirement", "")) for r in rows)

    def groups_of(r):
        if use_reqs:
            return re.findall(r"R\d+", r.get("Requirement", ""))
        return [r.get("Area") or r.get("Sheet") or "-"]

    return use_reqs, ("Requirement" if use_reqs else "Area"), groups_of


def flows_in_order(rows):
    """Flows in the order their first test case appears in the sheet."""
    order = []
    for r in rows:
        if r["flow"] not in order:
            order.append(r["flow"])
    if NO_FLOW in order:
        order.remove(NO_FLOW)
        order.append(NO_FLOW)
    return order


PROBLEM_ORDER = ["bug", "step", "out_of_step", "locator", "environment"]  # possible app bugs first


# ---------- building the HTML ----------

def esc(text):
    return html.escape(str(text or "")).replace("\n", "<br>")


def anchor(text):
    return "flow-" + re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def screenshot_tag(tc_id, out_dir):
    for folder in (os.path.join(out_dir, "screenshots"), os.path.join("reports", "screenshots")):
        path = os.path.join(folder, tc_id + ".png")
        if os.path.isfile(path):
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode()
            return ('<details><summary>Show the screen at the moment it failed</summary>'
                    '<img src="data:image/png;base64,%s" alt="Screenshot for %s"></details>' % (data, tc_id))
    return ""


def badge(result, label=None):
    text, css, icon = RESULT_STYLE[result]
    return '<span class="badge %s">%s %s</span>' % (css, icon, esc(label or text))


def bar(c, total):
    """A thin bar showing the share of each result (the numbers are in the cards next to it)."""
    parts = [("Passed", "pass"), ("Failed", "fail"), ("Blocked", "blocked"), ("Not automated", "manual")]
    segs = ""
    for key, css in parts:
        n = c[key] + (c["Skipped"] if key == "Blocked" else 0)
        if n:
            segs += '<span class="seg %s" style="flex:%d" title="%s: %d"></span>' % (css, n, RESULT_STYLE[key][0], n)
    return '<div class="bar" role="img" aria-label="%d of %d passed">%s</div>' % (c["Passed"], total, segs)


def flow_table(rows):
    out = ""
    for flow in flows_in_order(rows):
        items = [r for r in rows if r["flow"] == flow]
        c = counts_of(items)
        blocked = c["Blocked"] + c["Skipped"]
        if c["Failed"]:
            state = badge("Failed", "%d failed" % c["Failed"])
        elif c["Passed"] == len(items):
            state = badge("Passed", "All passed")
        elif c["Passed"]:
            state = '<span class="badge partly">✅ Passed what ran</span>'
        else:
            state = '<span class="badge manual">Not tested yet</span>'
        out += ('<tr><td><a href="#%s">%s</a></td><td class="n">%d</td><td class="n">%s</td><td class="n">%s</td>'
                '<td class="n">%s</td><td class="n">%s</td><td>%s</td></tr>'
                % (anchor(flow), esc(flow), len(items), c["Passed"] or "-", c["Failed"] or "-",
                   blocked or "-", c["Not automated"] or "-", state))
    return out


def problem_card(r, out_dir):
    name, meaning, todo, who = CAUSES[r["cause"]]
    tech = ('<details><summary>Technical details (for testers)</summary><pre>%s</pre></details>' % esc(r["tech"])) \
        if r.get("tech") else ""
    return ('<div class="problem %s"><h4>❌ %s &nbsp;%s</h4><div class="flowtag">Flow: %s</div>'
            '<p><b>What we checked:</b> %s</p>'
            '<p><b>What happened:</b> %s</p>'
            '<div class="cause"><b>%s.</b> %s<br><b>What to do:</b> %s</div>%s%s</div>'
            % (r["cause"], esc(r["ID"]), esc(r["Title"]), esc(r["flow"]),
               esc(r.get("Expected result")), esc(r["reason"]), esc(name), esc(meaning), esc(todo),
               screenshot_tag(r["ID"], out_dir), tech))


def attention_html(rows, out_dir):
    failed = [r for r in rows if r["result"] == "Failed"]
    if not failed:
        return '<p class="good">Nothing failed. 🎉</p>'
    out = ""
    for title, who, causes in ATTENTION:
        items = [r for r in failed if r["cause"] in causes]
        if not items:
            continue
        out += '<h3>%s (%d) <span class="muted">- %s</span></h3>' % (esc(title), len(items), esc(who))
        out += "".join(problem_card(r, out_dir) for r in items)
    return out


def passed_html(rows):
    passed = [r for r in rows if r["result"] == "Passed"]
    if not passed:
        return '<p class="muted">No test passed in this run.</p>'
    out = ""
    for flow in flows_in_order(rows):
        items = [r for r in passed if r["flow"] == flow]
        if not items:
            continue
        total = sum(1 for r in rows if r["flow"] == flow and r["result"] in ("Passed", "Failed", "Skipped"))
        lis = "".join(
            '<li><span class="tick">✅</span><div><b>%s</b> %s <span class="muted">· %s</span>'
            '<div class="checked">Checked: %s</div></div></li>'
            % (esc(r["ID"]), esc(r["Title"]), esc(minutes(r["seconds"])), esc(r.get("Expected result")))
            for r in items)
        out += ('<details class="flow" id="%s-passed"><summary><b>%s</b> - %d of %d automated tests passed</summary>'
                '<ul class="passed">%s</ul></details>' % (anchor(flow), esc(flow), len(items), total, lis))
    return out


def not_tested_html(rows):
    out = ""
    for result, title, note in (("Blocked", "Blocked - waiting for an answer", "These need a decision from the product owner or developers first."),
                                ("Not automated", "Not automated yet", "These must be tested by hand, or automated later.")):
        items = [r for r in rows if r["result"] == result or (result == "Blocked" and r["result"] == "Skipped")]
        if not items:
            continue
        lis = ""
        for flow in flows_in_order(rows):
            group = [r for r in items if r["flow"] == flow]
            if group:
                lis += '<li class="fl"><b>%s</b> (%d)</li>' % (esc(flow), len(group))
                lis += "".join('<li>%s <b>%s</b> %s<div class="checked">%s</div></li>'
                               % (RESULT_STYLE[r["result"]][2], esc(r["ID"]), esc(r["Title"]), esc(r["reason"]))
                               for r in group)
        out += ('<details class="flow"><summary><b>%s</b> - %d test cases</summary><p class="muted">%s</p>'
                '<ul class="plain">%s</ul></details>' % (esc(title), len(items), esc(note), lis))
    return out or '<p class="good">Every test case was run.</p>'


def build_html(title, rows, extra, info, out_dir):
    c = counts_of(rows)
    v_css, v_text = verdict(c, rows)
    use_reqs, group_label, groups_of = grouping(rows)

    # All test cases - with filter buttons
    table_rows = ""
    for r in rows:
        kind = KIND.get(r.get("Type", "").lower(), r.get("Type", ""))
        note = "" if r["result"] == "Passed" else '<div class="note">%s</div>' % esc(r["reason"])
        if r["cause"]:
            note = '<div class="note"><b>%s.</b> %s</div>' % (esc(CAUSES[r["cause"]][0]), esc(r["reason"]))
        key = "Blocked" if r["result"] == "Skipped" else r["result"]
        table_rows += (
            '<tr data-result="%s"><td>%s</td><td><b>%s</b> %s<details><summary>Steps and expected result</summary>'
            "<p><b>Steps:</b><br>%s</p><p><b>Test data:</b><br>%s</p><p><b>Expected:</b> %s</p>"
            "</details>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (esc(key), esc(r["flow"]), esc(r["ID"]), esc(r["Title"]), esc(r.get("Steps")),
               esc(r.get("Test data")), esc(r.get("Expected result")), note, esc(", ".join(groups_of(r))),
               esc(kind), esc(r.get("Priority")), badge(r["result"]))
        )

    # Coverage per requirement / area
    reqs = {}
    for r in rows:
        for req in groups_of(r):
            reqs.setdefault(req, []).append(r)
    has_types = any(r.get("Type") for r in rows)
    coverage_rows = ""
    for req in (sorted(reqs, key=lambda x: int(x[1:])) if use_reqs else reqs):
        items = reqs[req]
        passed = sum(1 for i in items if i["result"] == "Passed")
        failed = sum(1 for i in items if i["result"] == "Failed")
        edge = sum(1 for i in items if i.get("Type", "").lower() in ("boundary", "negative", "security"))
        if failed:
            state = badge("Failed")
        elif passed == len(items):
            state = badge("Passed")
        else:
            state = '<span class="badge partly">⏸ Partly tested</span>'
        if not has_types:
            edge_text = '<span class="muted">not marked in the sheet</span>'
        else:
            edge_text = str(edge) if edge else '<span class="warn">0 - add edge cases</span>'
        coverage_rows += '<tr><td>%s</td><td class="n">%d</td><td class="n">%d</td><td>%s</td><td>%s</td></tr>' % (
            esc(req), len(items), passed, edge_text, state)

    extra_html = ""
    if extra:
        extra_html = ('<p class="warn">⚠ These automated tests are not in the test case sheet: %s. '
                      'Add them to the sheet so they are tracked.</p>' % esc(", ".join(extra)))

    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    return string.Template(TEMPLATE).substitute(
        title=esc(title), now=now, v_css=v_css, v_text=esc(v_text), total=len(rows),
        passed=c["Passed"], failed=c["Failed"], blocked=c["Blocked"] + c["Skipped"], manual=c["Not automated"],
        bar=bar(c, len(rows)), summary="".join("<li>%s</li>" % s for s in plain_summary(rows, info)),
        flow_rows=flow_table(rows), attention=attention_html(rows, out_dir), passed_list=passed_html(rows),
        not_tested=not_tested_html(rows), table_rows=table_rows, coverage_rows=coverage_rows,
        extra=extra_html, group=group_label, group_lower=group_label.lower(),
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test report - $title</title>
<style>
 body { font-family: -apple-system, Segoe UI, Arial, sans-serif; margin: 0; background: #f6f7f9; color: #1d2330; line-height: 1.45; }
 main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
 h1 { margin: 0 0 4px; font-size: 26px; } h2 { margin: 40px 0 6px; font-size: 21px; }
 h2 + .lead { margin-top: 0; } h3 { margin: 26px 0 8px; font-size: 17px; }
 .lead { color: #475467; margin: 0 0 12px; }
 .muted { color: #667085; font-weight: 400; } .good { color: #11652f; font-weight: 600; }
 .verdict { padding: 16px 20px; border-radius: 10px; font-size: 20px; font-weight: 700; margin: 20px 0 14px; }
 .verdict.pass { background: #e6f6ec; color: #11652f; } .verdict.fail { background: #fdeaea; color: #a11b1b; }
 .verdict.blocked { background: #fff4e0; color: #8a5300; }
 .summary { background: #fff; border-radius: 10px; padding: 14px 20px; margin: 0 0 14px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
 .summary h2 { margin: 0 0 6px; font-size: 16px; } .summary ul { margin: 0; padding-left: 18px; } .summary li { margin: 4px 0; }
 .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
 .card { background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
 .card .num { font-size: 30px; font-weight: 700; } .card .lbl { color: #667085; }
 .bar { display: flex; gap: 2px; height: 10px; margin: 14px 0 0; border-radius: 5px; overflow: hidden; }
 .seg.pass { background: #2f9e57; } .seg.fail { background: #d92d20; } .seg.blocked { background: #f0a020; } .seg.manual { background: #b8bfcc; }
 .problem { background: #fff; border-left: 5px solid #d92d20; border-radius: 8px; padding: 4px 16px 12px; margin: 10px 0; }
 .problem h4 { margin: 12px 0 2px; font-size: 16px; } .problem p { margin: 6px 0; }
 .problem.step { border-left-color: #f0a020; } .problem.locator, .problem.out_of_step { border-left-color: #f79009; }
 .problem.environment { border-left-color: #98a2b3; }
 .flowtag { color: #667085; font-size: 13px; }
 .cause { background: #f6f7f9; border-radius: 6px; padding: 8px 12px; font-size: 15px; margin-top: 8px; }
 pre { white-space: pre-wrap; word-break: break-word; background: #f6f7f9; padding: 10px; border-radius: 6px; font-size: 12px; }
 table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; }
 th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #eceef2; vertical-align: top; }
 th { background: #eef0f4; font-size: 13px; text-transform: uppercase; letter-spacing: .03em; }
 td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
 .badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; white-space: nowrap; }
 .badge.pass { background: #e6f6ec; color: #11652f; } .badge.fail { background: #fdeaea; color: #a11b1b; }
 .badge.blocked, .badge.partly { background: #fff4e0; color: #8a5300; } .badge.manual { background: #eef0f4; color: #475467; }
 .note { color: #475467; font-size: 14px; margin-top: 6px; } .warn { color: #a15c00; font-weight: 600; }
 details summary { cursor: pointer; color: #2b59c3; font-size: 14px; margin-top: 6px; }
 details.flow { background: #fff; border-radius: 10px; padding: 10px 16px; margin: 8px 0; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
 details.flow > summary { color: #1d2330; font-size: 15px; margin: 0; }
 ul.passed, ul.plain { list-style: none; padding: 0; margin: 10px 0 4px; }
 ul.passed li { display: flex; gap: 8px; padding: 8px 0; border-top: 1px solid #eceef2; }
 ul.plain li { padding: 6px 0; border-top: 1px solid #eceef2; } ul.plain li.fl { border-top: 0; padding-top: 12px; }
 .checked { color: #475467; font-size: 14px; }
 details img { max-width: 100%; border: 1px solid #ddd; margin-top: 8px; border-radius: 6px; }
 .wrap { overflow-x: auto; }
 .filters { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; }
 .filters button { border: 1px solid #d0d5dd; background: #fff; border-radius: 999px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
 .filters button[aria-pressed="true"] { background: #1d2330; color: #fff; border-color: #1d2330; }
 @media print { details { display: block; } details > summary { display: none; } .filters { display: none; } }
</style></head>
<body><main>
<h1>Test report: $title</h1>
<div class="muted">Report made on $now</div>

<div class="verdict $v_css">$v_text</div>

<div class="summary"><h2>In one minute</h2><ul>$summary</ul></div>

<div class="cards">
 <div class="card"><div class="num">$total</div><div class="lbl">Test cases</div></div>
 <div class="card"><div class="num">✅ $passed</div><div class="lbl">Passed</div></div>
 <div class="card"><div class="num">❌ $failed</div><div class="lbl">Failed</div></div>
 <div class="card"><div class="num">⏸ $blocked</div><div class="lbl">Blocked (waiting for answers)</div></div>
 <div class="card"><div class="num">✋ $manual</div><div class="lbl">Not automated yet</div></div>
</div>
$bar

<h2>Results by flow</h2>
<p class="lead">Each flow is one business journey (one test file). Click a flow to see what passed in it.</p>
<div class="wrap"><table>
<tr><th>Flow</th><th class="n">Test cases</th><th class="n">✅ Passed</th><th class="n">❌ Failed</th><th class="n">⏸ Blocked</th><th class="n">✋ Not automated</th><th>State</th></tr>
$flow_rows
</table></div>

<h2>What needs attention</h2>
<p class="lead">Every failed test, sorted by who has to act. Possible app bugs come first.</p>
$attention

<h2>What passed - checked and working</h2>
<p class="lead">Every test below ran on the real site and the app did exactly what the test case expects. Open a flow to see each check.</p>
$passed_list

<h2>Not tested in this run</h2>
$not_tested

<h2>Is every $group_lower tested?</h2>
<div class="wrap"><table>
<tr><th>$group</th><th class="n">Test cases</th><th class="n">Passed</th><th>Edge / wrong-input / security tests</th><th>State</th></tr>
$coverage_rows
</table></div>

<h2>All test cases</h2>
$extra
<div class="filters" role="group" aria-label="Show test cases">
 <button type="button" data-show="all" aria-pressed="true">All</button>
 <button type="button" data-show="Passed" aria-pressed="false">✅ Passed</button>
 <button type="button" data-show="Failed" aria-pressed="false">❌ Failed</button>
 <button type="button" data-show="Blocked" aria-pressed="false">⏸ Blocked</button>
 <button type="button" data-show="Not automated" aria-pressed="false">✋ Not automated</button>
</div>
<div class="wrap"><table id="all">
<tr><th>Flow</th><th>Test case</th><th>$group</th><th>Kind</th><th>Priority</th><th>Result</th></tr>
$table_rows
</table></div>

<h2>What the words mean</h2>
<ul class="muted">
 <li><b>Flow</b> - one business journey, for example "Suspend and restore". Each flow has its own test file.</li>
 <li><b>Passed</b> - the test ran on the real site and the app did exactly what the test case expected.</li>
 <li><b>Failed</b> - the test did not pass. Each failure says why:</li>
 <li style="margin-left:20px"><b>Possible bug in the app</b> - the app does not do what the test case sheet says. Developers must fix it.</li>
 <li style="margin-left:20px"><b>Needs a tester to look</b> - the test could not finish a step (a button was missing or disabled), so the result was never checked. A tester decides: bug, or test to fix.</li>
 <li style="margin-left:20px"><b>Test needs updating</b> - the screen changed or the test was written wrongly. Testers fix the test; the app may be fine.</li>
 <li style="margin-left:20px"><b>Could not run</b> - the site or test account was not available. Run again later.</li>
 <li><b>Blocked</b> - we can't test it yet because a question about the rules is not answered.</li>
 <li><b>Not automated yet</b> - the test case exists but has no automated test; it must be tested by hand.</li>
 <li><b>Edge case</b> - testing the limits, e.g. just before and just after a time-out.</li>
</ul>
</main>
<script>
 document.querySelectorAll(".filters button").forEach(function (b) {
  b.addEventListener("click", function () {
   document.querySelectorAll(".filters button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
   var show = b.getAttribute("data-show");
   document.querySelectorAll("#all tr[data-result]").forEach(function (tr) {
    tr.style.display = (show === "all" || tr.getAttribute("data-result") === show) ? "" : "none";
   });
  });
 });
 document.querySelectorAll('a[href^="#flow-"]').forEach(function (a) {
  a.addEventListener("click", function () {
   var d = document.getElementById(a.getAttribute("href").slice(1) + "-passed");
   if (d) { d.open = true; d.scrollIntoView({behavior: "smooth"}); }
  });
 });
</script>
</body></html>
"""


# ---------- building the Excel report ----------

# Cell colours for each result (background, text) - the same colours as the HTML report
EXCEL_COLOURS = {
    "Passed": ("E6F6EC", "11652F"), "Failed": ("FDEAEA", "A11B1B"),
    "Blocked": ("FFF4E0", "8A5300"), "Skipped": ("FFF4E0", "8A5300"),
    "Not automated": ("EEF0F4", "475467"),
}


def write_excel(path, title, rows, extra, info):
    """Write the same results as an Excel file with 4 tabs: Summary, Problems, Passed, All test cases."""
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
            if result_col is not None and values[result_col] in EXCEL_COLOURS:
                colour(sheet.cell(sheet.max_row, result_col + 1), values[result_col])
        for i, width in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = width
        return start

    counts = counts_of(rows)
    v_css, v_text = verdict(counts, rows)
    use_reqs, group_label, groups_of = grouping(rows)
    book = Workbook()

    # Tab 1: Summary - verdict, plain summary, results by flow, is every requirement tested?
    summary = book.active
    summary.title = "Summary"
    summary.append(["Test report: " + title])
    summary["A1"].font = Font(bold=True, size=16)
    summary.append(["Report made on " + datetime.datetime.now().strftime("%d %b %Y, %H:%M")])
    summary.append([v_text])
    summary["A3"].font = Font(bold=True, size=13, color={"fail": "A11B1B", "blocked": "8A5300", "pass": "11652F"}[v_css])
    summary.append([])
    for line in plain_summary(rows, info):
        summary.append([re.sub(r"<[^>]+>", "", line)])
    summary.append([])
    flow_data = []
    for flow in flows_in_order(rows):
        items = [r for r in rows if r["flow"] == flow]
        c = counts_of(items)
        state = "Failed" if c["Failed"] else ("Passed" if c["Passed"] == len(items) else "Blocked")
        flow_data.append([flow, len(items), c["Passed"], c["Failed"], c["Blocked"] + c["Skipped"], c["Not automated"], state])
    start = table(summary, ["Flow", "Test cases", "Passed", "Failed", "Blocked", "Not automated", "State"],
                  flow_data, [44, 12, 10, 10, 10, 14, 16])
    for row in range(start + 1, summary.max_row + 1):
        cell = summary.cell(row, 7)
        colour(cell, cell.value)
        cell.value = {"Failed": "Has failures", "Passed": "All passed", "Blocked": "Partly tested"}[cell.value]
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
    start = table(summary, [group_label, "Test cases", "Passed", "Failed", "State"], coverage, [44, 12, 10, 10, 10, 14, 16])
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
    table(problems, ["ID", "Flow", "Test case", "Why it failed", "Who acts", "What to do", "What we checked", "What happened"],
          [[r["ID"], r["flow"], r["Title"], CAUSES[r["cause"]][0], CAUSES[r["cause"]][3], CAUSES[r["cause"]][2],
            r.get("Expected result", ""), r["reason"]] for r in failed] or [["", "", "No problems found.", "", "", "", "", ""]],
          [10, 22, 34, 24, 12, 40, 44, 50])
    problems.freeze_panes = "A2"

    # Tab 3: Passed - what was checked and works
    passed_tab = book.create_sheet("Passed")
    table(passed_tab, ["ID", "Flow", "Test case", "What we checked (and it worked)", "Time (s)"],
          [[r["ID"], r["flow"], r["Title"], r.get("Expected result", ""), round(r["seconds"] or 0, 1)]
           for r in rows if r["result"] == "Passed"] or [["", "", "No test passed.", "", ""]],
          [10, 22, 40, 70, 10])
    passed_tab.freeze_panes = "A2"
    passed_tab.auto_filter.ref = passed_tab.dimensions

    # Tab 4: All test cases - one row each, with filters so anyone can show e.g. only "Failed"
    cases = book.create_sheet("All test cases")
    table(cases, ["ID", "Flow", group_label, "Test case", "Kind", "Priority", "Result", "Why it failed / note",
                  "Expected result", "Steps", "Test data"],
          [[r["ID"], r["flow"], ", ".join(groups_of(r)), r["Title"], KIND.get(r.get("Type", "").lower(), r.get("Type", "")),
            r.get("Priority", ""), r["result"],
            (CAUSES[r["cause"]][0] + ". " if r["cause"] else "") + (r["reason"] if r["result"] != "Passed" else ""),
            r.get("Expected result", ""), r.get("Steps", ""), r.get("Test data", "")] for r in rows],
          [10, 22, 16, 36, 14, 10, 16, 44, 44, 44, 28], result_col=6)
    cases.freeze_panes = "A2"
    cases.auto_filter.ref = cases.dimensions

    book.save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description="Build a plain-language HTML test report")
    parser.add_argument("--cases", required=True, help="test case CSV or Excel file")
    parser.add_argument("--results", default="reports/results.xml", help="pytest JUnit XML file")
    parser.add_argument("--out", default="reports/report.html", help="where to write the report")
    parser.add_argument("--title", help="feature name (default: from the CSV file name)")
    args = parser.parse_args()

    name = os.path.splitext(os.path.basename(args.cases))[0]
    title = args.title or re.sub(r"[-_ ]?test[-_ ]?cases$", "", name, flags=re.I).replace("-", " ").replace("_", " ").strip().title()
    results, info = read_results(args.results)
    rows, extra = combine(read_cases(args.cases), results)
    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(title, rows, extra, info, out_dir))
    print("Report written: " + os.path.abspath(args.out))
    excel = write_excel(os.path.splitext(args.out)[0] + ".xlsx", title, rows, extra, info)
    if excel:
        print("Excel report written: " + os.path.abspath(excel))


if __name__ == "__main__":
    main()
