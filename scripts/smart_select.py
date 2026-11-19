"""Smart regression: pick only the tests a change can affect - instead of running everything.

    python3 <toolkit>/scripts/smart_select.py --notes "Renamed Suspend access to Suspend now; invite count fix"
    python3 <toolkit>/scripts/smart_select.py --notes-file release-notes.txt
    python3 <toolkit>/scripts/smart_select.py --files app/tenants/[id]/config/page.tsx app/platform-users/invite.tsx
    python3 <toolkit>/scripts/smart_select.py --git ~/code/lurniq-web --since v2.3.0        # changed files from the app's git

How it works:
  1. Index every test: its flow file, test name, docstring (steps + expected), the page objects it uses,
     and those page objects' addresses and on-screen names (button / heading text in the locators).
  2. Turn the change into words: file paths are split ("tenants/[id]/config/page.tsx" -> tenants, config),
     release notes are split into words; common words are dropped; simple word endings are ignored
     (invite / invited / invitation).
  3. Score each test: a word in the flow file name or page address counts most, then the test title,
     then page-object text, then the steps. Tests over the threshold are picked, with the words that matched.
  4. Always add the smoke tests (qa-toolkit.yaml  smart: always: [TC-036, ...]) so sign-in etc. is always checked.
Writes reports/smart/selection.md + selection.json and prints the pytest command to run.
The tester (or Claude) should read the list and add / remove tests with judgement - this is a starting point.
"""
import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys

STOP = set("""a an the and or of to in on for with from by is are was be been it this that these those as at into not no
new old add added adds adding fix fixed fixes fixing update updated updates change changed changes changing remove removed
improve improved minor small bug bugs issue issues release version v page pages component components index src app lib libs
utils util test tests spec specs ts tsx js jsx py css scss json md html ui api server client hooks hook types type style styles
module modules public assets shared common core main layout layouts route routes helper helpers file files id ids web
should can will when then also more less only some all any its their our your via per get set use used using make made
open opens click clicks button screen text see shown show shows user users after before now rename renamed renames
instead still already again into onto""".split())


def stem(w):
    w = w.lower()
    if w.endswith("ss"):   # access, success - not a plural
        return w
    for suf in ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ies", "ied", "es", "ed", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf in ("ies", "ied") else "")
    return w


def words(text):
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")   # camelCase -> camel Case
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z]{2,}", text):
        w = w.lower()
        if w not in STOP:
            out.add(stem(w))
    return out


# ---------- the index ----------

def page_object_text(pages_dir):
    """{ClassName: text of the class (addresses, locator names, method names)}"""
    found = {}
    for root, _, files in os.walk(pages_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            src = open(path, encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    seg = ast.get_source_segment(src, node) or ""
                    strings = " ".join(re.findall(r"[\"']([^\"']{3,})[\"']", seg))
                    methods = " ".join(n.name for n in node.body if isinstance(n, ast.FunctionDef))
                    found[node.name] = {"file": path, "text": strings + " " + methods + " " + f}
    return found


def data_urls(project):
    """URL constants from test_data (e.g. ROLES_URL = ADMIN_URL + "/platform-users/roles") -> their paths."""
    urls = {}
    for root, _, files in os.walk(os.path.join(project, "test_data")):
        for f in files:
            if f.endswith(".py"):
                for name, val in re.findall(r"^(\w+_URL)\s*=\s*(.+)$", open(os.path.join(root, f), encoding="utf-8").read(), re.M):
                    urls[name] = " ".join(re.findall(r"[\"']([^\"']+)[\"']", val))
    return urls


def index_tests(tests_dir, pages, urls):
    tests = []
    for root, _, files in os.walk(tests_dir):
        for f in sorted(files):
            if not (f.startswith("test_") and f.endswith(".py")):
                continue
            path = os.path.join(root, f)
            src = open(path, encoding="utf-8").read()
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            flow = re.sub(r"^test_|\.py$", "", f)
            for node in tree.body:
                if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
                    continue
                body = ast.get_source_segment(src, node) or ""
                doc = ast.get_docstring(node) or ""
                m = re.match(r"test_([A-Za-z][A-Za-z0-9]*)_(\d+)", node.name)
                tc = "%s-%s" % (m.group(1).upper(), m.group(2)) if m else node.name
                header = doc.splitlines()[0] if doc else ""
                parts = [p.strip() for p in header.split("|")]
                title = parts[-1] if len(parts) >= 5 else node.name
                priority = parts[3] if len(parts) >= 5 else ""
                used = [c for c in pages if re.search(r"\b%s\b" % c, body)]
                fixtures = [a.arg for a in node.args.args]
                po_text = " ".join(pages[c]["text"] for c in used)
                url_text = " ".join(v for k, v in urls.items() if k in body or k in po_text)
                tests.append({"id": tc, "name": node.name, "file": path, "flow": flow, "title": title, "priority": priority,
                              "node": "%s::%s" % (path, node.name),
                              "w_flow": words(flow) | words(url_text), "w_title": words(title) | words(node.name),
                              "w_pages": words(po_text) | words(" ".join(used)) | words(" ".join(fixtures)),
                              "w_doc": words(doc)})
    return tests


WEIGHTS = (("w_flow", 3, "flow / page address"), ("w_title", 2, "test title"), ("w_pages", 1.5, "screen it uses"), ("w_doc", 1, "steps"))


def rarity(tests):
    """How special each word is: a word found in most tests (e.g. 'tenant' in a tenant console) says little.
    Returns {word: weight}; words in more than half of the tests get 0."""
    import math
    n = len(tests) or 1
    df = {}
    for t in tests:
        for w in t["w_flow"] | t["w_title"] | t["w_pages"] | t["w_doc"]:
            df[w] = df.get(w, 0) + 1
    return {w: (0.0 if c > n / 2.0 else math.log((n + 1.0) / (c + 1.0))) for w, c in df.items()}


def score(test, change, weight_of):
    total, why = 0.0, {}
    for key, weight, label in WEIGHTS:
        hit = {w for w in change & test[key] if weight_of.get(w, 0) > 0}
        if hit:
            total += weight * sum(weight_of[w] for w in hit)
            why.setdefault(label, set()).update(hit)
    return total, why


# ---------- the change ----------

def change_words(args):
    text, files = "", list(args.files or [])
    if args.notes:
        text += " " + args.notes
    if args.notes_file:
        text += " " + open(args.notes_file, encoding="utf-8").read()
    if args.git:
        out = subprocess.run(["git", "-C", os.path.expanduser(args.git), "diff", "--name-only", "%s...HEAD" % args.since],
                             capture_output=True, text=True)
        if out.returncode != 0:
            sys.exit("git diff failed: %s" % out.stderr.strip())
        files += out.stdout.split()
    path_text = " ".join(re.sub(r"[/\\._\-\[\]()]+", " ", f) for f in files)
    return words(text) | words(path_text), files


def durations(project):
    """Seconds per test from the flaky history or the last results file (to estimate time)."""
    secs = {}
    hist = os.path.join(project, "reports", "history", "runs.jsonl")
    if os.path.isfile(hist):
        for line in open(hist, encoding="utf-8"):
            for r in json.loads(line)["results"]:
                if r.get("seconds"):
                    secs[r["id"]] = r["seconds"]
    return secs


def main():
    ap = argparse.ArgumentParser(description="Pick the tests a change can affect")
    ap.add_argument("--notes", help="release notes / ticket text")
    ap.add_argument("--notes-file")
    ap.add_argument("--files", nargs="+", help="changed file paths (from the developers or the app's git)")
    ap.add_argument("--git", help="the APP's git folder (read-only: git diff --name-only)")
    ap.add_argument("--since", default="HEAD~1", help="with --git: compare against this tag / branch / commit")
    ap.add_argument("--tests", default="tests")
    ap.add_argument("--pages", default="pages")
    ap.add_argument("--threshold", type=float, default=4.0, help="minimum score to pick a test (default 4)")
    ap.add_argument("--out", default="reports/smart")
    args = ap.parse_args()
    if not (args.notes or args.notes_file or args.files or args.git):
        sys.exit("Say what changed: --notes, --notes-file, --files or --git.")

    change, files = change_words(args)
    if not change:
        sys.exit("No useful words found in the change description.")
    pages = page_object_text(args.pages)
    tests = index_tests(args.tests, pages, data_urls("."))
    always = []
    try:
        import yaml
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            always = [str(x).upper() for x in ((yaml.safe_load(f) or {}).get("smart") or {}).get("always") or []]
    except Exception:
        pass

    weight_of = rarity(tests)
    common = sorted(w for w in change if weight_of.get(w) == 0.0)
    if common:
        print("Too common to decide anything (in over half of the tests): %s" % ", ".join(common))
    picked = []
    for t in tests:
        s, why = score(t, change, weight_of)
        if s >= args.threshold or t["id"] in always:
            picked.append(dict(t, score=s, why={k: sorted(v) for k, v in why.items()}, smoke=t["id"] in always and s < args.threshold))
    picked.sort(key=lambda t: (t["flow"], -t["score"], t["id"]))
    secs = durations(".")
    est_all = sum(secs.get(t["id"], 15) for t in tests)
    est_sel = sum(secs.get(t["id"], 15) for t in picked)

    os.makedirs(args.out, exist_ok=True)
    nodes = [t["node"] for t in picked]
    cmd = "python -m pytest " + " ".join('"%s"' % n for n in nodes) if nodes else ""
    with open(os.path.join(args.out, "selection.json"), "w") as f:
        json.dump({"made": datetime.datetime.now().isoformat(timespec="seconds"), "change_words": sorted(change), "files": files,
                   "picked": [{k: t[k] for k in ("id", "flow", "title", "priority", "node", "score", "why", "smoke")} for t in picked],
                   "total_tests": len(tests)}, f, indent=1)
    with open(os.path.join(args.out, "tests.txt"), "w") as f:
        f.write("\n".join(nodes) + "\n")
    lines = ["# Smart regression - %d of %d tests" % (len(picked), len(tests)), "",
             "Change words: %s" % ", ".join(sorted(change)), "",
             "About %d min instead of %d min for everything." % (round(est_sel / 60.0) or 1, round(est_all / 60.0) or 1), ""]
    flow = None
    for t in picked:
        if t["flow"] != flow:
            flow = t["flow"]
            lines += ["", "## %s" % flow.replace("_", " ").capitalize()]
        reason = "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in t["why"].items()) or "smoke test (always run)"
        lines.append("- **%s** %s - %s" % (t["id"], t["title"], reason))
    with open(os.path.join(args.out, "selection.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    print("Change words: %s" % ", ".join(sorted(change)))
    print("Picked %d of %d tests (about %d min instead of %d min):" % (len(picked), len(tests), round(est_sel / 60.0) or 1, round(est_all / 60.0) or 1))
    flow = None
    for t in picked:
        if t["flow"] != flow:
            flow = t["flow"]
            print("  %s" % flow)
        reason = "; ".join("%s: %s" % (k, ", ".join(v)) for k, v in t["why"].items()) or "smoke (always)"
        print("    %s %-55s %s" % (t["id"], t["title"][:55], reason[:80]))
    if nodes:
        print("\nRun them:\n  python -m pytest $(cat %s)" % os.path.join(args.out, "tests.txt"))
    print("List: %s" % os.path.abspath(os.path.join(args.out, "selection.md")))


if __name__ == "__main__":
    main()
