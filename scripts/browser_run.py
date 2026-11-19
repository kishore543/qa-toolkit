"""Cross-browser run: the SAME automated tests, once per browser, then one report.

    python3 <toolkit>/scripts/browser_run.py tests/super_admin/test_tenant_list.py
    python3 <toolkit>/scripts/browser_run.py tests/ --browsers chromium firefox webkit iphone --cases test-cases/x.csv

What it does:
  1. Checks each browser is installed (and tells you the install command if not).
  2. Runs pytest once per browser (conftest.py's --qa-browser switch), one results file each:
     reports/browsers/<browser>/results.xml, failure screenshots in .../<browser>/screenshots/.
  3. A test that failed in SOME browsers but passed in others is run once more in the browsers
     where it failed. If it passes then, it is "flaky" (random), not a browser bug.
  4. Builds the report: reports/browsers/browser-report.html (+ .xlsx).

Run it with the project's own Python (the one that has pytest + playwright), from the project root.
The project's conftest.py must have the --qa-browser option (qa-toolkit's template has it).
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))

# name -> (label people see, engine, channel, device, what it stands for)
BROWSERS = {
    "chromium": ("Chrome", "chromium", None, None, "Chromium - the open-source Chrome"),
    "chrome": ("Google Chrome", "chromium", "chrome", None, "the Google Chrome installed on this computer"),
    "msedge": ("Microsoft Edge", "chromium", "msedge", None, "the Microsoft Edge installed on this computer"),
    "firefox": ("Firefox", "firefox", None, None, "Firefox"),
    "webkit": ("Safari", "webkit", None, None, "WebKit - the engine inside Safari"),
    "iphone": ("iPhone", "webkit", None, "iPhone 13", "Safari's engine with an iPhone 13 screen and touch"),
    "ipad": ("iPad", "webkit", None, "iPad Pro 11", "Safari's engine with an iPad Pro 11 screen and touch"),
    "android": ("Android phone", "chromium", None, "Pixel 7", "Chrome's engine with a Pixel 7 screen and touch"),
}
INSTALL = {"chromium": "chromium", "firefox": "firefox", "webkit": "webkit"}


def check_browsers(names):
    """Return {name: version or None} and {name: problem text}."""
    from playwright.sync_api import sync_playwright
    versions, problems = {}, {}
    with sync_playwright() as p:
        for name in names:
            _, engine, channel, _, _ = BROWSERS[name]
            try:
                kwargs = {"headless": True}
                if channel:
                    kwargs["channel"] = channel
                b = getattr(p, engine).launch(**kwargs)
                versions[name] = b.version
                b.close()
            except Exception as e:
                versions[name] = None
                if channel:
                    problems[name] = "%s is not installed on this computer." % BROWSERS[name][0]
                else:
                    problems[name] = ("Not installed. Install once with: %s -m playwright install %s"
                                      % (os.path.basename(sys.executable), INSTALL[engine]))
                print("  %-9s NOT READY - %s (%s)" % (name, problems[name], str(e).splitlines()[0][:120]))
                continue
            print("  %-9s ready - version %s" % (name, versions[name]))
    return versions, problems


def pytest_cmd(targets, name, xml, extra):
    return [sys.executable, "-m", "pytest", *targets, "--qa-browser", name, "--junitxml", xml,
            "-p", "no:cacheprovider", "-q", "-rN", *extra]


def env_for(out, name):
    env = dict(os.environ)
    env["QA_SCREENSHOTS"] = os.path.join(out, name, "screenshots")
    return env


def node_id(classname, test):
    """JUnit classname + name -> pytest node id. tests.x.test_a -> tests/x/test_a.py::test;
    tests.x.test_a.TestB -> tests/x/test_a.py::TestB::test (when the test sits in a class)."""
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if os.path.isfile(path):
            return "::".join([path] + parts[cut:] + [test])
    return "/".join(parts) + ".py::" + test


def outcome_of(xml):
    """{node id: Passed/Failed/Skipped} from a JUnit file."""
    found = {}
    if not os.path.isfile(xml):
        return found
    for case in ET.parse(xml).getroot().iter("testcase"):
        cls, test = case.get("classname", ""), case.get("name", "")
        node = node_id(cls, test)
        if case.find("failure") is not None or case.find("error") is not None:
            found[node] = "Failed"
        elif case.find("skipped") is not None:
            found[node] = "Skipped"
        else:
            found[node] = "Passed"
    return found


def run_all(targets, names, out, extra, parallel):
    """Run pytest per browser. Returns {name: seconds}."""
    seconds, procs = {}, {}
    for name in names:
        os.makedirs(os.path.join(out, name, "screenshots"), exist_ok=True)
        for f in os.listdir(os.path.join(out, name, "screenshots")):
            os.remove(os.path.join(out, name, "screenshots", f))
        for f in ("results.xml", "retry.xml"):
            if os.path.isfile(os.path.join(out, name, f)):
                os.remove(os.path.join(out, name, f))
        log = open(os.path.join(out, name, "run.log"), "w")
        cmd = pytest_cmd(targets, name, os.path.join(out, name, "results.xml"), extra)
        print("\n> %s: running ... (log: %s)" % (BROWSERS[name][0], os.path.join(out, name, "run.log")), flush=True)
        start = time.time()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env_for(out, name))
        procs[name] = (proc, start, log)
        if not parallel:
            proc.wait()
            log.close()
            seconds[name] = time.time() - start
            report_line(name, out, seconds[name])
    for name, (proc, start, log) in procs.items():
        if name not in seconds:
            proc.wait()
            log.close()
            seconds[name] = time.time() - start
            report_line(name, out, seconds[name])
    return seconds


def report_line(name, out, secs):
    res = outcome_of(os.path.join(out, name, "results.xml"))
    if not res:
        print("  %s: no results - see %s" % (BROWSERS[name][0], os.path.join(out, name, "run.log")))
        return
    c = {k: sum(1 for v in res.values() if v == k) for k in ("Passed", "Failed", "Skipped")}
    print("  %s: %d passed, %d failed, %d skipped  (%dm %02ds)" % (
        BROWSERS[name][0], c["Passed"], c["Failed"], c["Skipped"], secs // 60, secs % 60))


def retry_mixed(names, out, extra):
    """Tests that failed in some browsers but passed in at least one: run again where they failed."""
    results = {n: outcome_of(os.path.join(out, n, "results.xml")) for n in names}
    nodes = set().union(*[set(r) for r in results.values()]) if results else set()
    todo = {}
    for node in nodes:
        states = {n: results[n].get(node) for n in names if results[n]}
        if "Failed" in states.values() and "Passed" in states.values():
            for n, s in states.items():
                if s == "Failed":
                    todo.setdefault(n, []).append(node)
    retried = {}
    for name, failed in todo.items():
        print("\n> %s: %d test(s) failed here but passed in another browser - running them once more ..."
              % (BROWSERS[name][0], len(failed)), flush=True)
        xml = os.path.join(out, name, "retry.xml")
        log = open(os.path.join(out, name, "retry.log"), "w")
        subprocess.run(pytest_cmd(sorted(failed), name, xml, extra), stdout=log, stderr=subprocess.STDOUT,
                       env=env_for(out, name))
        log.close()
        again = outcome_of(xml)
        retried[name] = sorted(failed)
        flaky = [n for n in failed if again.get(n) == "Passed"]
        print("  %d passed on the second try (flaky), %d failed again (browser problem)"
              % (len(flaky), len(failed) - len(flaky)), flush=True)
    return retried


def main():
    ap = argparse.ArgumentParser(description="Run the same tests in several browsers and build one report")
    ap.add_argument("targets", nargs="+", help="test files / folders / node ids for pytest")
    ap.add_argument("--browsers", nargs="+", default=["chromium", "firefox", "webkit"], choices=sorted(BROWSERS),
                    help="browsers to run (default: chromium firefox webkit)")
    ap.add_argument("--out", default="reports/browsers", help="results folder (default reports/browsers)")
    ap.add_argument("--cases", help="test case sheet (CSV/Excel) - adds titles to the report")
    ap.add_argument("--title", default="Cross-browser test", help="report title")
    ap.add_argument("-k", dest="keyword", help="only tests matching this pytest -k expression")
    ap.add_argument("--parallel", action="store_true",
                    help="run all browsers at the same time (faster; more load on the QA server)")
    ap.add_argument("--no-retry", action="store_true", help="do not re-run browser-only failures")
    args = ap.parse_args()

    if not os.path.isfile("conftest.py"):
        sys.exit("Run this from the project root (the folder with conftest.py).")
    with open("conftest.py", encoding="utf-8") as f:
        if "--qa-browser" not in f.read():
            sys.exit("conftest.py has no --qa-browser option yet. Add it from qa-toolkit's conftest template first.")
    extra = ["-k", args.keyword] if args.keyword else []

    print("Checking browsers ...")
    versions, problems = check_browsers(args.browsers)
    ready = [n for n in args.browsers if versions.get(n)]
    started = datetime.datetime.now()
    seconds, retried = {}, {}
    if ready:
        seconds = run_all(args.targets, ready, args.out, extra, args.parallel)
        if not args.no_retry and len(ready) > 1:
            retried = retry_mixed(ready, args.out, extra)
    os.makedirs(args.out, exist_ok=True)
    run = {"started": started.isoformat(timespec="seconds"), "targets": args.targets, "keyword": args.keyword,
           "browsers": [{"name": n, "label": BROWSERS[n][0], "about": BROWSERS[n][4], "device": BROWSERS[n][3],
                         "version": versions.get(n), "problem": problems.get(n),
                         "seconds": round(seconds.get(n, 0), 1)} for n in args.browsers],
           "retried": retried, "parallel": args.parallel}
    with open(os.path.join(args.out, "run.json"), "w") as f:
        json.dump(run, f, indent=1)

    cmd = [sys.executable, os.path.join(HERE, "browser_report.py"), "--out", args.out, "--title", args.title]
    if args.cases:
        cmd += ["--cases", args.cases]
    print("", flush=True)
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    main()
