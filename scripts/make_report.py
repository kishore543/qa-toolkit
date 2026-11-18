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
        cause, flow, seconds, tech, test_name = None, None, None, "", None
        if run:
            result, reason, flow, seconds, tech = run["result"], run["reason"], run["flow"], run["seconds"], run["tech"]
            test_name = run["test_name"]
            if result == "Failed":
                cause = root_cause(run["raw"], case.get("Expected result") or "", whole_sheet)
        elif case.get("Status", "").lower().startswith("blocked"):
            result, reason = "Blocked", "Waiting for an answer: " + case["Status"].split("-", 1)[-1].strip()
        else:
            result, reason = "Not automated", "No automated test exists for this test case yet."
        rows.append(dict(case, result=result, reason=reason, cause=cause, flow=flow, seconds=seconds, tech=tech,
                         test_name=test_name))
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
#
# One self-contained file (no internet needed): open it, email it, print it to PDF.
# Colours follow one rule: a status colour (passed / failed / blocked / by hand) always comes
# with its icon and its word, so the report also reads in black-and-white and for colour-blind readers.

STATUS_CSS = {"Passed": "good", "Failed": "crit", "Blocked": "warn", "Skipped": "warn", "Not automated": "none"}
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "med": 2, "low": 3}


def esc(text):
    return html.escape(str(text or "")).replace("\n", "<br>")


def attr(text):
    return html.escape(str(text or ""), quote=True)


def anchor(text):
    return "flow-" + re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def pct(part, whole):
    return int(round(100.0 * part / whole)) if whole else 0


def screenshot_data(tc_id, out_dir):
    for folder in (os.path.join(out_dir, "screenshots"), os.path.join("reports", "screenshots")):
        path = os.path.join(folder, tc_id + ".png")
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode(), path
    return None, None


def badge(result, label=None):
    text, _, icon = RESULT_STYLE[result]
    return '<span class="pill %s">%s %s</span>' % (STATUS_CSS[result], icon, esc(label or text))


def site_under_test(given):
    """The site the tests ran against: --site, or the first real host in qa-toolkit.yaml."""
    if given:
        return given
    try:
        import yaml
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            hosts = (yaml.safe_load(f) or {}).get("allowed_hosts") or []
        real = [h for h in hosts if h and not str(h).startswith(("localhost", "127.", "*"))]
        return "https://" + real[0] if real else None
    except Exception:
        return None


def stack_bar(parts, scale_total, label):
    """A stacked bar: parts = [(css, icon, word, n)]. Width is relative to scale_total, so longer = more cases."""
    total = sum(n for _, _, _, n in parts)
    segs = "".join(
        '<span class="seg %s" style="flex:%d" data-tip="%s %s: %d of %d"></span>' % (css, n, icon, attr(word), n, total)
        for css, icon, word, n in parts if n)
    width = max(4, pct(total, scale_total)) if scale_total else 100
    return '<div class="track"><div class="stack" style="width:%d%%" role="img" aria-label="%s">%s</div></div>' % (
        width, attr(label), segs)


def status_parts(c):
    return [("good", "✅", "Passed", c["Passed"]), ("crit", "❌", "Failed", c["Failed"]),
            ("warn", "⏸", "Blocked", c["Blocked"] + c["Skipped"]), ("none", "✋", "Not automated", c["Not automated"])]


def legend(parts):
    return '<div class="legend">%s</div>' % "".join(
        '<span><i class="sw %s"></i>%s %s <b>%d</b></span>' % (css, icon, esc(word), n) for css, icon, word, n in parts)


def kpi_tiles(rows):
    c, by = counts_of(rows), cause_counts(rows)
    ran = c["Passed"] + c["Failed"] + c["Skipped"]
    fail_note = ", ".join(x for x in (
        "%d possible bug%s" % (by["bug"], "" if by["bug"] == 1 else "s") if by["bug"] else "",
        "%d to look at" % by["step"] if by["step"] else "",
        "%d to update" % (by["locator"] + by["out_of_step"]) if by["locator"] + by["out_of_step"] else "",
        "%d environment" % by["environment"] if by["environment"] else "") if x) or "nothing failed"
    tiles = [
        ("ink", "", "Test cases", len(rows), "in the test case sheet"),
        ("accent", "🤖", "Automated", ran, "%d%% of the test cases" % pct(ran, len(rows))),
        ("good", "✅", "Passed", c["Passed"], "%d%% of the automated tests" % pct(c["Passed"], ran)),
        ("crit", "❌", "Failed", c["Failed"], fail_note),
        ("warn", "⏸", "Blocked", c["Blocked"] + c["Skipped"], "waiting for answers"),
        ("none", "✋", "By hand", c["Not automated"], "not automated yet"),
    ]
    return "".join('<div class="kpi %s"><div class="k-lbl">%s %s</div><div class="k-num">%d</div>'
                   '<div class="k-sub">%s</div></div>' % (css, icon, esc(lbl), n, esc(sub))
                   for css, icon, lbl, n, sub in tiles)


def flow_rows_html(rows):
    flows = flows_in_order(rows)
    biggest = max([sum(1 for r in rows if r["flow"] == f) for f in flows] or [1])
    out = ""
    for flow in flows:
        items = [r for r in rows if r["flow"] == flow]
        c = counts_of(items)
        ran = c["Passed"] + c["Failed"] + c["Skipped"]
        if c["Failed"]:
            state = badge("Failed", "%d failed" % c["Failed"])
        elif ran and c["Passed"] == len(items):
            state = badge("Passed", "All passed")
        elif c["Passed"]:
            state = '<span class="pill good soft">✅ Passed what ran</span>'
        else:
            state = '<span class="pill none">✋ Not tested yet</span>'
        out += ('<div class="flowrow"><a class="fname" href="#%s">%s</a>%s'
                '<div class="fnum"><b>%d</b>/%d passed</div><div class="fstate">%s</div></div>'
                % (anchor(flow), esc(flow), stack_bar(status_parts(c), biggest, flow), c["Passed"], len(items), state))
    return out


def bug_text(r, site, when):
    """A ready-to-paste bug report (Jira, Azure DevOps, email...)."""
    shot = "reports/screenshots/%s.png" % r["ID"]
    lines = ["Title: [%s] %s (%s)" % (r["flow"], r["Title"], r["ID"]),
             "Environment: %s - tested %s" % (site or "QA", when),
             "Priority: %s" % (r.get("Priority") or "-"),
             "Requirement: %s" % (r.get("Requirement") or "-")]
    if r.get("Preconditions"):
        lines.append("Preconditions: %s" % r["Preconditions"])
    lines += ["", "Steps to reproduce:", r.get("Steps") or "-"]
    if r.get("Test data"):
        lines += ["", "Test data: %s" % r["Test data"]]
    lines += ["", "Expected result: %s" % (r.get("Expected result") or "-"),
              "Actual result: %s" % r["reason"], "",
              "Evidence: screenshot %s" % shot, "Found by automated test: %s" % (r.get("test_name") or r["ID"])]
    return "\n".join(lines)


def problem_card(r, out_dir, site, when):
    name, meaning, todo, who = CAUSES[r["cause"]]
    data, _ = screenshot_data(r["ID"], out_dir)
    shot = ('<button type="button" class="thumb" aria-label="Enlarge the screenshot of %s">'
            '<img src="%s" alt="Screen at the moment %s failed" loading="lazy"><span>🔍 Enlarge</span></button>'
            % (attr(r["ID"]), data, attr(r["ID"]))) if data else '<div class="noshot">No screenshot</div>'
    tech = ('<details class="tech"><summary>Technical details (for testers)</summary><pre>%s</pre></details>'
            % esc(r["tech"])) if r.get("tech") else ""
    prio = r.get("Priority") or ""
    prio_html = '<span class="tag prio-%s">%s priority</span>' % (
        attr(prio.lower()), esc(prio)) if prio else ""
    copy = ""
    if r["cause"] in ("bug", "step"):
        copy = ('<button type="button" class="btn copy">📋 Copy bug report</button>'
                '<textarea class="bugtext" hidden>%s</textarea>' % html.escape(bug_text(r, site, when)))
    return ('<article class="pcard %s"><div class="pbody">'
            '<div class="phead"><span class="tcid">%s</span><h4>%s</h4></div>'
            '<div class="tags"><span class="tag">🧭 %s</span>%s<span class="tag">👤 %s</span></div>'
            '<div class="ea"><div><div class="ea-l">✔ Expected (test case sheet)</div><p>%s</p></div>'
            '<div><div class="ea-l">✖ What happened</div><p>%s</p></div></div>'
            '<div class="why"><b>%s.</b> %s <br><b>What to do:</b> %s</div>'
            '<div class="actions">%s</div>%s</div><div class="pshot">%s</div></article>'
            % (r["cause"], esc(r["ID"]), esc(r["Title"]), esc(r["flow"]), prio_html, esc(who),
               esc(r.get("Expected result")), esc(r["reason"]), esc(name), esc(meaning), esc(todo), copy, tech, shot))


def attention_html(rows, out_dir, site, when):
    failed = [r for r in rows if r["result"] == "Failed"]
    if not failed:
        return '<div class="empty good-ink">🎉 Nothing failed in this run.</div>'
    out = ""
    for title, who, causes in ATTENTION:
        items = sorted((r for r in failed if r["cause"] in causes),
                       key=lambda r: PRIORITY_RANK.get((r.get("Priority") or "").lower(), 9))
        if items:
            out += '<h3 class="group %s">%s <span class="count">%d</span> <span class="muted">- %s</span></h3>' % (
                causes[0], esc(title), len(items), esc(who))
            out += "".join(problem_card(r, out_dir, site, when) for r in items)
    return out


def coverage_map(rows):
    """One tile per requirement (or area): its colour says if it is proven, broken or still open."""
    use_reqs, label, groups_of = grouping(rows)
    groups = {}
    for r in rows:
        for g in groups_of(r):
            groups.setdefault(g, []).append(r)
    names = sorted(groups, key=lambda x: int(x[1:])) if use_reqs else list(groups)
    tiles, table = "", ""
    tally = {"crit": 0, "good": 0, "warn": 0, "none": 0}
    for g in names:
        items = groups[g]
        c = counts_of(items)
        if c["Failed"]:
            css, word = "crit", "❌ has a failure"
        elif c["Passed"] == len(items):
            css, word = "good", "✅ fully passed"
        elif c["Passed"]:
            css, word = "warn", "◐ partly tested"
        else:
            css, word = "none", "○ not tested yet"
        tally[css] += 1
        tip = "%s: %d test case(s) - %d passed, %d failed, %d not run - %s" % (
            g, len(items), c["Passed"], c["Failed"], len(items) - c["Passed"] - c["Failed"], word[2:])
        tiles += '<span class="tile %s" data-tip="%s"><b>%s</b><small>%d/%d</small></span>' % (
            css, attr(tip), esc(g), c["Passed"], len(items))
        table += '<tr><td>%s</td><td class="n">%d</td><td class="n">%d</td><td class="n">%d</td><td>%s</td></tr>' % (
            esc(g), len(items), c["Passed"], c["Failed"], esc(word))
    leg = legend([("good", "✅", "Fully passed", tally["good"]), ("crit", "❌", "Has a failure", tally["crit"]),
                  ("warn", "◐", "Partly tested", tally["warn"]), ("none", "○", "Not tested yet", tally["none"])])
    return label, len(names), tiles, leg, table


def slowest_html(rows, top=8):
    ran = sorted((r for r in rows if r["seconds"]), key=lambda r: -r["seconds"])[:top]
    if not ran:
        return ""
    longest = ran[0]["seconds"]
    return "".join(
        '<div class="slow"><span class="sl-name"><b>%s</b> %s</span><div class="sl-track"><span class="sl-bar" '
        'style="width:%d%%" data-tip="%s took %s"></span></div><span class="sl-val">%s</span></div>'
        % (esc(r["ID"]), esc(r["Title"]), max(3, pct(r["seconds"], longest)), attr(r["ID"]),
           attr(minutes(r["seconds"])), esc(minutes(r["seconds"]))) for r in ran)


def passed_html(rows):
    passed = [r for r in rows if r["result"] == "Passed"]
    if not passed:
        return '<div class="empty">No test passed in this run.</div>'
    out = ""
    for flow in flows_in_order(rows):
        items = [r for r in passed if r["flow"] == flow]
        if not items:
            continue
        ran = sum(1 for r in rows if r["flow"] == flow and r["result"] in ("Passed", "Failed", "Skipped"))
        lis = "".join(
            '<li><span class="ok">✅</span><div><div><b>%s</b> %s <span class="muted">· %s</span></div>'
            '<div class="checked">Checked: %s</div></div></li>'
            % (esc(r["ID"]), esc(r["Title"]), esc(minutes(r["seconds"])), esc(r.get("Expected result")))
            for r in items)
        out += ('<details class="fold" id="%s-passed"><summary><span class="fold-t">%s</span>'
                '<span class="mini"><span style="width:%d%%"></span></span>'
                '<span class="muted">%d of %d automated tests passed</span></summary><ul class="list">%s</ul></details>'
                % (anchor(flow), esc(flow), pct(len(items), ran), len(items), ran, lis))
    return out


def not_tested_html(rows):
    out = ""
    for result, title, note in (("Blocked", "⏸ Blocked - waiting for an answer",
                                 "These need a decision from the product owner or developers before they can be tested."),
                                ("Not automated", "✋ Not automated yet",
                                 "These must be tested by hand, or automated later.")):
        items = [r for r in rows if r["result"] == result or (result == "Blocked" and r["result"] == "Skipped")]
        if not items:
            continue
        lis = ""
        for flow in flows_in_order(rows):
            group = [r for r in items if r["flow"] == flow]
            if group:
                lis += '<li class="sub">%s <span class="muted">(%d)</span></li>' % (esc(flow), len(group))
                lis += "".join('<li><span class="ok">%s</span><div><div><b>%s</b> %s</div><div class="checked">%s</div></div></li>'
                               % (RESULT_STYLE[r["result"]][2], esc(r["ID"]), esc(r["Title"]), esc(r["reason"]))
                               for r in group)
        out += ('<details class="fold"><summary><span class="fold-t">%s</span><span class="muted">%d test cases</span>'
                '</summary><p class="muted">%s</p><ul class="list">%s</ul></details>' % (esc(title), len(items), esc(note), lis))
    return out or '<div class="empty good-ink">Every test case was run.</div>'


def all_rows_html(rows):
    _, _, groups_of = grouping(rows)
    out = ""
    for r in rows:
        kind = KIND.get(r.get("Type", "").lower(), r.get("Type", ""))
        note = ""
        if r["cause"]:
            note = '<div class="note"><b>%s.</b> %s</div>' % (esc(CAUSES[r["cause"]][0]), esc(r["reason"]))
        elif r["result"] != "Passed":
            note = '<div class="note">%s</div>' % esc(r["reason"])
        key = "Blocked" if r["result"] == "Skipped" else r["result"]
        out += ('<tr data-result="%s"><td>%s</td><td><b>%s</b> %s<details><summary>Steps and expected result</summary>'
                '<p><b>Steps:</b><br>%s</p><p><b>Test data:</b><br>%s</p><p><b>Expected:</b> %s</p></details>%s</td>'
                '<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                % (attr(key), esc(r["flow"]), esc(r["ID"]), esc(r["Title"]), esc(r.get("Steps")),
                   esc(r.get("Test data")), esc(r.get("Expected result")), note, esc(", ".join(groups_of(r))),
                   esc(kind), esc(r.get("Priority")), badge(r["result"])))
    return out


def build_html(title, rows, extra, info, out_dir, site=None):
    c = counts_of(rows)
    v_css, v_text = verdict(c, rows)
    head, _, rest = v_text.partition(" - ")
    ran = c["Passed"] + c["Failed"] + c["Skipped"]
    when = info["started"].strftime("%d %b %Y, %H:%M") if info.get("started") else \
        datetime.datetime.now().strftime("%d %b %Y, %H:%M")
    cov_label, cov_n, tiles, cov_legend, cov_table = coverage_map(rows)
    attention_n = c["Failed"]
    not_run = c["Blocked"] + c["Skipped"] + c["Not automated"]
    extra_html = ""
    if extra:
        extra_html = ('<p class="warnline">⚠ These automated tests are not in the test case sheet: %s. '
                      'Add them to the sheet so they are tracked.</p>' % esc(", ".join(extra)))
    meta = ['<span>🗓 %s</span>' % esc(when), '<span>⏱ %s</span>' % esc(minutes(info.get("seconds")))]
    if site:
        meta.insert(0, '<span>🌐 %s</span>' % esc(site))
    meta.append('<span>🧪 %d automated · %d test cases</span>' % (ran, len(rows)))
    return string.Template(TEMPLATE).substitute(
        title=esc(title), when=esc(when), meta="".join(meta), v_css=v_css, v_head=esc(head),
        v_rest=esc(rest or "Every automated test passed."),
        rate=pct(c["Passed"], ran), rate_deg=int(round(3.6 * pct(c["Passed"], ran))), passed=c["Passed"], ran=ran,
        summary="".join("<li>%s</li>" % s for s in plain_summary(rows, info)), kpis=kpi_tiles(rows),
        allbar=stack_bar(status_parts(c), 0, "All test cases by result"), alllegend=legend(status_parts(c)),
        flows=flow_rows_html(rows), flowlegend=legend(status_parts(c)),
        attention_n=attention_n, attention=attention_html(rows, out_dir, site, when),
        passed_list=passed_html(rows), not_run=not_run, not_tested=not_tested_html(rows),
        cov_label=esc(cov_label), cov_lower=esc(cov_label.lower()), cov_n=cov_n, tiles=tiles,
        cov_legend=cov_legend, cov_table=cov_table, slowest=slowest_html(rows),
        extra=extra_html, table_rows=all_rows_html(rows), total=len(rows),
        made=datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
    )


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test report - $title</title>
<style>
 :root {
  --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9;
  --ring:rgba(11,11,11,.10); --shadow:0 1px 2px rgba(0,0,0,.05), 0 4px 16px rgba(0,0,0,.04);
  --good:#0ca30c; --good-ink:#006300; --warn:#fab219; --warn-ink:#7a5200; --serious:#ec835a;
  --crit:#d03b3b; --crit-ink:#a32020; --none:#c3c2b7; --none-ink:#52514e; --accent:#2a78d6;
  --hero:#0f1b2d; --hero2:#1d3557;
 }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --ink3:#9a998f; --line:#2c2c2a;
  --ring:rgba(255,255,255,.10); --shadow:none; --good-ink:#3fc93f; --warn-ink:#fab219; --crit-ink:#f08080;
  --none:#4a4a46; --none-ink:#c3c2b7; --accent:#3987e5; --hero:#0b1422; --hero2:#16294a; } }
 * { box-sizing: border-box; }
 body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 .wrap { max-width:1160px; margin:0 auto; padding:0 16px; }
 .muted { color:var(--ink3); font-weight:400; } .good-ink { color:var(--good-ink); }
 h2 { font-size:22px; margin:0 0 4px; letter-spacing:-.01em; } h3 { font-size:16px; margin:22px 0 10px; }
 .lead { color:var(--ink2); margin:0 0 16px; }
 section { padding:34px 0 6px; scroll-margin-top:56px; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; box-shadow:var(--shadow); }

 /* hero */
 header.hero { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:30px 0 34px; }
 .hero-grid { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:center; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; }
 .hero h1 { font-size:32px; margin:4px 0 10px; letter-spacing:-.02em; }
 .meta { display:flex; flex-wrap:wrap; gap:8px; }
 .meta span { background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.14); padding:4px 10px; border-radius:999px; font-size:13px; }
 .verdict { margin-top:16px; display:inline-flex; gap:12px; align-items:flex-start; padding:12px 16px; border-radius:12px;
   background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.16); max-width:640px; }
 .verdict .vh { font-weight:800; font-size:18px; letter-spacing:.02em; }
 .verdict .vdot { width:14px; height:14px; border-radius:50%; margin-top:5px; flex:none; box-shadow:0 0 0 4px rgba(255,255,255,.12); }
 .verdict.fail .vdot { background:var(--crit); } .verdict.blocked .vdot { background:var(--warn); } .verdict.pass .vdot { background:var(--good); }
 .verdict .vr { opacity:.9; font-size:14px; }
 .gauge { width:150px; height:150px; border-radius:50%; display:grid; place-items:center;
   background:conic-gradient(var(--good) calc($rate_deg * 1deg), rgba(255,255,255,.14) 0); }
 .gauge > div { width:118px; height:118px; border-radius:50%; background:var(--hero); display:grid; place-items:center; text-align:center; }
 .gauge b { font-size:34px; line-height:1; display:block; } .gauge small { font-size:12px; opacity:.8; }

 /* nav */
 nav.tabs { position:sticky; top:0; z-index:5; background:color-mix(in srgb, var(--surface) 92%, transparent);
   backdrop-filter:saturate(1.4) blur(8px); border-bottom:1px solid var(--line); }
 nav.tabs .wrap { display:flex; gap:4px; overflow-x:auto; align-items:center; }
 nav.tabs a { padding:12px 10px; color:var(--ink2); text-decoration:none; font-size:14px; white-space:nowrap; border-bottom:2px solid transparent; }
 nav.tabs a:hover { color:var(--ink); border-bottom-color:var(--line); }
 nav.tabs .n { background:var(--line); color:var(--ink); border-radius:999px; padding:0 7px; font-size:12px; margin-left:4px; }
 nav.tabs .spacer { flex:1; }
 .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; }
 .btn:hover { border-color:var(--ink3); }

 /* overview */
 .summary { padding:18px 22px; } .summary h3 { margin:0 0 8px; }
 .summary ul { margin:0; padding-left:20px; } .summary li { margin:5px 0; }
 .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin:14px 0; }
 .kpi { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:14px 16px 14px 18px; position:relative; overflow:hidden; box-shadow:var(--shadow); }
 .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--line); }
 .kpi.good::before { background:var(--good); } .kpi.crit::before { background:var(--crit); } .kpi.warn::before { background:var(--warn); }
 .kpi.none::before { background:var(--none); } .kpi.accent::before { background:var(--accent); } .kpi.ink::before { background:var(--ink); }
 .k-lbl { color:var(--ink2); font-size:13px; font-weight:600; } .k-num { font-size:32px; font-weight:800; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
 .k-sub { color:var(--ink3); font-size:12.5px; }
 .barcard { padding:16px 20px; }

 /* bars + legend */
 .track { width:100%; } .stack { display:flex; gap:2px; height:14px; }
 .seg { border-radius:4px; min-width:3px; cursor:default; }
 .seg.good, .sw.good { background:var(--good); } .seg.crit, .sw.crit { background:var(--crit); }
 .seg.warn, .sw.warn { background:var(--warn); } .seg.none, .sw.none { background:var(--none); }
 .legend { display:flex; flex-wrap:wrap; gap:6px 18px; margin-top:10px; font-size:13px; color:var(--ink2); }
 .legend span { display:inline-flex; align-items:center; gap:6px; } .legend b { color:var(--ink); }
 .sw { width:10px; height:10px; border-radius:3px; display:inline-block; }

 /* flows */
 .flows { padding:8px 20px 16px; }
 .flowrow { display:grid; grid-template-columns:minmax(150px, 220px) 1fr 110px 150px; gap:14px; align-items:center; padding:11px 0; border-bottom:1px solid var(--line); }
 .flowrow:last-of-type { border-bottom:0; }
 .fname { color:var(--ink); font-weight:600; text-decoration:none; } .fname:hover { text-decoration:underline; }
 .fnum { color:var(--ink2); font-size:13px; text-align:right; font-variant-numeric:tabular-nums; } .fnum b { color:var(--ink); }
 .fstate { text-align:right; }

 /* pills + tags */
 .pill { display:inline-block; padding:3px 10px; border-radius:999px; font-size:12.5px; font-weight:700; white-space:nowrap; }
 .pill.good { background:color-mix(in srgb, var(--good) 14%, var(--surface)); color:var(--good-ink); }
 .pill.crit { background:color-mix(in srgb, var(--crit) 14%, var(--surface)); color:var(--crit-ink); }
 .pill.warn { background:color-mix(in srgb, var(--warn) 22%, var(--surface)); color:var(--warn-ink); }
 .pill.none { background:color-mix(in srgb, var(--none) 30%, var(--surface)); color:var(--none-ink); }
 .pill.soft { font-weight:600; }
 .tag { display:inline-block; font-size:12px; color:var(--ink2); border:1px solid var(--line); border-radius:6px; padding:1px 8px; margin:0 6px 6px 0; }
 .prio-high, .prio-critical { border-color:var(--crit); color:var(--crit-ink); font-weight:700; }
 .prio-medium, .prio-med { border-color:var(--warn); color:var(--warn-ink); font-weight:600; }

 /* problems */
 h3.group { display:flex; align-items:center; gap:8px; } h3.group .count { background:var(--line); border-radius:999px; padding:0 9px; font-size:13px; }
 h3.group::before { content:""; width:10px; height:10px; border-radius:3px; background:var(--none); }
 h3.group.bug::before { background:var(--crit); } h3.group.step::before { background:var(--serious); }
 h3.group.out_of_step::before, h3.group.locator::before { background:var(--warn); }
 .pcard { display:grid; grid-template-columns:1fr 230px; gap:18px; background:var(--surface); border:1px solid var(--ring);
   border-left:5px solid var(--none); border-radius:14px; padding:16px 18px; margin:0 0 12px; box-shadow:var(--shadow); }
 .pcard.bug { border-left-color:var(--crit); } .pcard.step { border-left-color:var(--serious); }
 .pcard.out_of_step, .pcard.locator { border-left-color:var(--warn); }
 .phead { display:flex; gap:10px; align-items:baseline; } .phead h4 { margin:0 0 8px; font-size:16px; }
 .tcid { font-family:ui-monospace, Menlo, monospace; font-size:13px; font-weight:700; background:var(--bg); border:1px solid var(--line); border-radius:6px; padding:1px 7px; white-space:nowrap; }
 .ea { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:6px 0 10px; }
 .ea > div { background:var(--bg); border-radius:10px; padding:10px 12px; } .ea p { margin:4px 0 0; font-size:14px; }
 .ea-l { font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--ink3); }
 .ea > div:last-child .ea-l { color:var(--crit-ink); }
 .why { font-size:14px; color:var(--ink2); border-top:1px dashed var(--line); padding-top:8px; }
 .actions { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap; }
 .thumb { display:block; width:100%; padding:0; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:var(--bg); cursor:zoom-in; position:relative; }
 .thumb img { width:100%; display:block; aspect-ratio:4/3; object-fit:cover; object-position:top; }
 .thumb span { position:absolute; right:6px; bottom:6px; background:rgba(0,0,0,.65); color:#fff; font-size:12px; padding:2px 8px; border-radius:6px; }
 .noshot { color:var(--ink3); font-size:13px; border:1px dashed var(--line); border-radius:10px; padding:20px; text-align:center; }
 details.tech summary, details summary { cursor:pointer; color:var(--accent); font-size:13.5px; margin-top:8px; }
 pre { white-space:pre-wrap; word-break:break-word; background:var(--bg); padding:10px; border-radius:8px; font-size:12px; color:var(--ink2); }

 /* coverage map */
 .tiles { display:grid; grid-template-columns:repeat(auto-fill, minmax(62px, 1fr)); gap:6px; padding:16px 20px 4px; }
 .tile { border-radius:8px; padding:6px 4px; text-align:center; font-size:13px; color:#fff; cursor:default; }
 .tile b { display:block; } .tile small { opacity:.9; font-size:11px; }
 .tile.good { background:var(--good); } .tile.crit { background:var(--crit); }
 .tile.warn { background:var(--warn); color:#0b0b0b; } .tile.none { background:var(--none); color:var(--none-ink); }
 .tiles + .legend { padding:0 20px 16px; }

 /* slowest */
 .slow { display:grid; grid-template-columns:minmax(180px, 2fr) 3fr 90px; gap:12px; align-items:center; padding:7px 0; font-size:14px; }
 .sl-name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .sl-track { background:var(--bg); border-radius:4px; height:10px; }
 .sl-bar { display:block; height:10px; background:var(--accent); border-radius:4px; } .sl-val { text-align:right; color:var(--ink2); font-variant-numeric:tabular-nums; }

 /* folds + lists */
 details.fold { background:var(--surface); border:1px solid var(--ring); border-radius:12px; padding:12px 16px; margin:0 0 8px; box-shadow:var(--shadow); }
 details.fold > summary { display:flex; align-items:center; gap:12px; color:var(--ink); margin:0; list-style:none; }
 details.fold > summary::before { content:"▸"; color:var(--ink3); transition:transform .15s; } details.fold[open] > summary::before { transform:rotate(90deg); }
 .fold-t { font-weight:700; min-width:180px; }
 .mini { width:90px; height:8px; background:var(--bg); border-radius:4px; overflow:hidden; } .mini span { display:block; height:8px; background:var(--good); }
 ul.list { list-style:none; margin:10px 0 0; padding:0; }
 ul.list li { display:flex; gap:10px; padding:9px 0; border-top:1px solid var(--line); } ul.list li.sub { font-weight:700; border-top:0; padding-top:14px; }
 .checked { color:var(--ink2); font-size:13.5px; }

 /* table */
 .toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:0 0 10px; }
 .toolbar input { font:inherit; font-size:14px; padding:7px 12px; border-radius:8px; border:1px solid var(--line); background:var(--surface); color:var(--ink); min-width:240px; }
 .chip { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:999px; padding:5px 12px; cursor:pointer; }
 .chip[aria-pressed="true"] { background:var(--ink); color:var(--surface); border-color:var(--ink); }
 .tablewrap { overflow-x:auto; }
 table { width:100%; border-collapse:collapse; background:var(--surface); }
 th, td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; font-size:14px; }
 th { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--ink3); background:var(--bg); }
 td.n { text-align:right; font-variant-numeric:tabular-nums; }
 .note { color:var(--ink2); font-size:13px; margin-top:6px; }
 .empty { padding:18px; font-weight:600; }
 .warnline { color:var(--warn-ink); font-weight:600; }
 .glossary li { margin:4px 0; color:var(--ink2); }
 footer { color:var(--ink3); font-size:13px; padding:30px 0 40px; }

 /* tooltip + lightbox */
 #tip { position:fixed; z-index:20; pointer-events:none; background:var(--ink); color:var(--surface); font-size:12.5px; padding:6px 9px; border-radius:6px; opacity:0; transition:opacity .08s; max-width:320px; }
 dialog { border:0; padding:0; border-radius:12px; max-width:min(1100px, 94vw); background:var(--surface); }
 dialog::backdrop { background:rgba(0,0,0,.7); } dialog img { display:block; max-width:100%; max-height:86vh; }
 dialog form { position:absolute; top:8px; right:8px; } dialog .btn { background:rgba(0,0,0,.7); color:#fff; border:0; }
 .toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:var(--ink); color:var(--surface); padding:8px 14px; border-radius:8px; font-size:14px; opacity:0; transition:opacity .2s; z-index:30; }

 @media (max-width: 760px) {
  .hero-grid { grid-template-columns:1fr; } .gauge { width:120px; height:120px; } .gauge > div { width:94px; height:94px; } .gauge b { font-size:26px; } .gauge small { font-size:10px; line-height:1.25; }
  .flowrow { grid-template-columns:1fr auto; } .flowrow .track { grid-column:1 / -1; order:3; } .fstate { display:none; }
  .pcard { grid-template-columns:1fr; } .ea { grid-template-columns:1fr; } .fold-t { min-width:0; } .mini { display:none; }
  .slow { grid-template-columns:1fr 70px; } .sl-track { display:none; }
 }
 @media print {
  nav.tabs, .btn, .toolbar, .thumb span, #tip { display:none !important; }
  body { background:#fff; } section { break-inside:auto; } .pcard, .kpi { break-inside:avoid; box-shadow:none; }
  details > summary { list-style:none; } details:not([open]) > *:not(summary) { display:block; }
 }
</style></head>
<body>
<header class="hero"><div class="wrap hero-grid">
 <div>
  <div class="eyebrow">QA test report</div>
  <h1>$title</h1>
  <div class="meta">$meta</div>
  <div class="verdict $v_css"><span class="vdot"></span><div><div class="vh">$v_head</div><div class="vr">$v_rest</div></div></div>
 </div>
 <div class="gauge" role="img" aria-label="$rate percent of automated tests passed"><div><div><b>$rate%</b><small>$passed of $ran automated<br>tests passed</small></div></div></div>
</div></header>

<nav class="tabs" aria-label="Report sections"><div class="wrap">
 <a href="#overview">Overview</a><a href="#flows">Flows</a>
 <a href="#attention">Needs attention<span class="n">$attention_n</span></a>
 <a href="#passed">Passed<span class="n">$passed</span></a>
 <a href="#coverage">Coverage</a><a href="#nottested">Not tested<span class="n">$not_run</span></a>
 <a href="#all">All test cases<span class="n">$total</span></a>
 <span class="spacer"></span><button type="button" class="btn" id="print">🖨 Save as PDF</button>
</div></nav>

<main class="wrap">
<section id="overview">
 <div class="card summary"><h3>In one minute</h3><ul>$summary</ul></div>
 <div class="kpis">$kpis</div>
 <div class="card barcard"><div class="k-lbl" style="margin-bottom:8px">All $total test cases by result</div>$allbar $alllegend</div>
</section>

<section id="flows">
 <h2>Health by flow</h2>
 <p class="lead">Each flow is one business journey, with its own test file. Bar length = number of test cases. Click a flow to see what passed in it.</p>
 <div class="card flows">$flows $flowlegend</div>
</section>

<section id="attention">
 <h2>What needs attention</h2>
 <p class="lead">Every failed test, sorted by who has to act - possible app bugs first, highest priority first. Use <b>Copy bug report</b> to paste a ready bug into Jira.</p>
 $attention
</section>

<section id="passed">
 <h2>What passed - checked and working</h2>
 <p class="lead">Each test below ran on the real site, and the app did exactly what the test case expects. Open a flow to see every check.</p>
 $passed_list
</section>

<section id="coverage">
 <h2>Is every $cov_lower covered?</h2>
 <p class="lead">One tile per $cov_lower ($cov_n in total). The number shows how many of its test cases passed. Hover over a tile for details.</p>
 <div class="card"><div class="tiles">$tiles</div>$cov_legend
  <details style="padding:0 20px 16px"><summary>Show as a table</summary><div class="tablewrap"><table>
  <tr><th>$cov_label</th><th class="n">Test cases</th><th class="n">Passed</th><th class="n">Failed</th><th>State</th></tr>$cov_table</table></div></details>
 </div>
 <h3>Slowest tests</h3>
 <div class="card" style="padding:10px 20px">$slowest</div>
</section>

<section id="nottested">
 <h2>Not tested in this run</h2>
 <p class="lead">Test cases that are blocked by an open question, or must still be tested by hand.</p>
 $not_tested
</section>

<section id="all">
 <h2>All test cases</h2>
 $extra
 <div class="toolbar">
  <input id="q" type="search" placeholder="Search ID, title or flow…" aria-label="Search test cases">
  <button type="button" class="chip" data-show="all" aria-pressed="true">All</button>
  <button type="button" class="chip" data-show="Passed" aria-pressed="false">✅ Passed</button>
  <button type="button" class="chip" data-show="Failed" aria-pressed="false">❌ Failed</button>
  <button type="button" class="chip" data-show="Blocked" aria-pressed="false">⏸ Blocked</button>
  <button type="button" class="chip" data-show="Not automated" aria-pressed="false">✋ Not automated</button>
 </div>
 <div class="card tablewrap"><table id="cases">
  <tr><th>Flow</th><th>Test case</th><th>$cov_label</th><th>Kind</th><th>Priority</th><th>Result</th></tr>
  $table_rows
 </table></div>
</section>

<section id="words">
 <h2>What the words mean</h2>
 <ul class="glossary">
  <li><b>Flow</b> - one business journey, for example "Suspend and restore". Each flow has its own test file.</li>
  <li><b>✅ Passed</b> - the test ran on the real site and the app did exactly what the test case expected.</li>
  <li><b>❌ Possible bug in the app</b> - the app does not do what the test case sheet says. Developers must fix it.</li>
  <li><b>❌ Needs a tester to look</b> - the test could not finish a step (a button was missing or disabled), so the result was never checked. A tester decides: bug, or test to fix.</li>
  <li><b>❌ Test needs updating</b> - the screen changed or the test was written wrongly. Testers fix the test; the app may be fine.</li>
  <li><b>❌ Could not run</b> - the site or test account was not available. Run again later.</li>
  <li><b>⏸ Blocked</b> - we can't test it yet because a question about the rules is not answered.</li>
  <li><b>✋ Not automated yet</b> - the test case exists but has no automated test; it must be tested by hand.</li>
 </ul>
</section>
<footer>Test run started $when · report made $made with qa-toolkit · the test case sheet is the source of truth for every expected result.</footer>
</main>

<div id="tip" role="tooltip"></div>
<div class="toast" id="toast">Bug report copied</div>
<dialog id="shot"><form method="dialog"><button class="btn">✕ Close</button></form><img alt="Screenshot"></dialog>

<script>
(function () {
 var tip = document.getElementById("tip");
 document.addEventListener("mousemove", function (e) {
  var t = e.target.closest("[data-tip]");
  if (!t) { tip.style.opacity = 0; return; }
  tip.textContent = t.getAttribute("data-tip");
  var x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + "px"; tip.style.top = (e.clientY + 16) + "px"; tip.style.opacity = 1;
 });
 var shown = "all", q = document.getElementById("q");
 function filter() {
  var text = (q.value || "").toLowerCase();
  document.querySelectorAll("#cases tr[data-result]").forEach(function (tr) {
   var ok = (shown === "all" || tr.getAttribute("data-result") === shown) && tr.textContent.toLowerCase().indexOf(text) !== -1;
   tr.style.display = ok ? "" : "none";
  });
 }
 document.querySelectorAll(".chip").forEach(function (b) {
  b.addEventListener("click", function () {
   document.querySelectorAll(".chip").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
   shown = b.getAttribute("data-show"); filter();
  });
 });
 q.addEventListener("input", filter);
 document.querySelectorAll('a[href^="#flow-"]').forEach(function (a) {
  a.addEventListener("click", function (e) {
   var d = document.getElementById(a.getAttribute("href").slice(1) + "-passed");
   if (d) { e.preventDefault(); d.open = true; d.scrollIntoView({behavior: "smooth", block: "start"}); }
  });
 });
 var dlg = document.getElementById("shot");
 document.querySelectorAll(".thumb").forEach(function (b) {
  b.addEventListener("click", function () { dlg.querySelector("img").src = b.querySelector("img").src; dlg.showModal(); });
 });
 dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
 var toast = document.getElementById("toast");
 document.querySelectorAll(".copy").forEach(function (b) {
  b.addEventListener("click", function () {
   var text = b.nextElementSibling.value;
   function done() { toast.style.opacity = 1; setTimeout(function () { toast.style.opacity = 0; }, 1600); }
   if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done); }
   else { var t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
          document.execCommand("copy"); document.body.removeChild(t); done(); }
  });
 });
 document.getElementById("print").addEventListener("click", function () {
  document.querySelectorAll("details").forEach(function (d) { d.open = true; }); window.print();
 });
})();
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
    parser.add_argument("--site", help="site the tests ran on (default: first real host in qa-toolkit.yaml)")
    args = parser.parse_args()

    name = os.path.splitext(os.path.basename(args.cases))[0]
    title = args.title or re.sub(r"[-_ ]?test[-_ ]?cases$", "", name, flags=re.I).replace("-", " ").replace("_", " ").strip().title()
    results, info = read_results(args.results)
    rows, extra = combine(read_cases(args.cases), results)
    out_dir = os.path.dirname(args.out) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(title, rows, extra, info, out_dir, site_under_test(args.site)))
    print("Report written: " + os.path.abspath(args.out))
    excel = write_excel(os.path.splitext(args.out)[0] + ".xlsx", title, rows, extra, info)
    if excel:
        print("Excel report written: " + os.path.abspath(excel))


if __name__ == "__main__":
    main()
