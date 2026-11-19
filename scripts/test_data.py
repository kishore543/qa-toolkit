"""Test data: make realistic + edge-case data, remember what the tests created, clean it up safely.

  MAKE - a CSV / Excel file of test data, normal rows plus edge cases a tester would try:
    python3 <toolkit>/scripts/test_data.py make --fields "name:name, email:email, phone:phone, joined:date, role:choice(Learner|Manager)" \
            --rows 10 --edge --out test_data/generated/people.csv
    Field types: name first last email phone company city text int(min-max) number date future past choice(a|b|c) bool uuid
    Edge rows (with --edge), each labelled in the "_case" column: empty, spaces only, too long, accents & non-Latin
    letters, script text, quote / SQL text, invalid email, duplicate email, past / far-future date, negative / huge number.
    Emails use --email-domain (default example.com, which can never receive mail) and a run stamp, so re-runs never clash.

  TRACK - remember a record a test created (call it from fixtures / helpers, or from the command line):
    python3 <toolkit>/scripts/test_data.py track --kind task --name "QA task 1119" --id 8f2a91c3 [--url ...]
    -> one line in test_data/created.jsonl

  LIST / CLEANUP - what is still there, and remove it (dry run unless --yes):
    python3 <toolkit>/scripts/test_data.py list
    python3 <toolkit>/scripts/test_data.py cleanup [--kind task] [--older-than 1d] [--yes]
    Cleanup only touches records in created.jsonl, only with a rule in qa-toolkit.yaml, only on allowed hosts:
      test_data:
        cleanup:
          task: {method: DELETE, url: "https://qa.example.com/api/tasks/{id}", token_env: API_TOKEN}
    Kinds without a rule (e.g. records the app cannot delete) are listed "to clean by hand / ask the developers".
"""
import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FIRST = ["Aarav", "Diya", "Kabir", "Ananya", "Rohan", "Meera", "Arjun", "Isha", "Vikram", "Sana", "Nikhil", "Priya", "Rahul", "Neha", "Karthik", "Lakshmi"]
LAST = ["Sharma", "Iyer", "Reddy", "Khan", "Patel", "Nair", "Gupta", "Menon", "Rao", "Singh", "Das", "Pillai", "Joshi", "Bose"]
COMPANY = ["Northwind Labs", "Bluepeak Systems", "Greenleaf Retail", "Orbit Finance", "Kite Logistics", "Sunrise Health"]
CITY = ["Bengaluru", "Chennai", "Hyderabad", "Mumbai", "Pune", "Delhi", "Kochi", "Kolkata"]
TRACK_FILE = os.path.join("test_data", "created.jsonl")


def parse_fields(spec):
    fields = []
    for part in re.split(r",\s*(?![^()]*\))", spec):
        if not part.strip():
            continue
        name, _, kind = part.partition(":")
        kind = kind.strip() or "text"
        m = re.match(r"(\w+)(?:\((.*)\))?$", kind)
        if not m:
            sys.exit("Field '%s': type '%s' not understood" % (name, kind))
        fields.append({"name": name.strip(), "type": m.group(1).lower(), "arg": m.group(2)})
    return fields


def value(f, rnd, n, stamp, domain, person):
    t, arg = f["type"], f["arg"]
    first, last = person   # one person per row, so name and email match
    if t == "name":
        return "%s %s" % (first, last)
    if t == "first":
        return first
    if t == "last":
        return last
    if t == "email":
        return "qa.%s.%s.%s.%d@%s" % (first.lower(), last.lower(), stamp, n, domain)
    if t == "phone":
        return "+91 9%d" % rnd.randint(100000000, 999999999)
    if t == "company":
        return "QA %s %s" % (rnd.choice(COMPANY), stamp)
    if t == "city":
        return rnd.choice(CITY)
    if t in ("int", "number"):
        lo, hi = (int(x) for x in (arg or "1-100").split("-", 1))
        return rnd.randint(lo, hi)
    if t in ("date", "past", "future"):
        today = datetime.date.today()
        delta = rnd.randint(1, 300) * (-1 if t == "past" else 1) if t != "date" else rnd.randint(-200, 200)
        return (today + datetime.timedelta(days=delta)).isoformat()
    if t == "choice":
        return rnd.choice((arg or "a|b").split("|"))
    if t == "bool":
        return rnd.choice(["true", "false"])
    if t == "uuid":
        return str(uuid.UUID(int=rnd.getrandbits(128)))
    return "QA text %s %d" % (stamp, n)


def edge_rows(fields, base, stamp, domain):
    """One row per edge case, each changing ONE field of a normal row, so a failure points at that value."""
    rows = []

    def row(case, field, val):
        r = dict(base)
        r[field] = val
        r["_case"] = "%s (%s)" % (case, field)
        rows.append(r)
    texts = [f for f in fields if f["type"] in ("name", "first", "last", "text", "company", "city")]
    for f in texts[:2]:
        row("empty", f["name"], "")
        row("spaces only", f["name"], "   ")
        row("too long - 256 characters", f["name"], ("Long" * 64)[:256])
        row("accents and non-Latin letters", f["name"], "Zoë Ñúñez 李雷 राहुल")
        row("script text (must show as plain text)", f["name"], "<script>alert('qa')</script>")
        row("quote / SQL text", f["name"], "O'Brien\"; DROP TABLE users;--")
    for f in [f for f in fields if f["type"] == "email"][:1]:
        row("invalid email - no @", f["name"], "qa.no-at-sign.%s" % domain)
        row("invalid email - spaces", f["name"], "qa test@%s" % domain)
        row("duplicate email", f["name"], base[f["name"]])
        row("upper-case email", f["name"], base[f["name"]].upper())
    for f in [f for f in fields if f["type"] == "phone"][:1]:
        row("phone with letters", f["name"], "+91 98ABC12345")
        row("phone too short", f["name"], "12345")
    for f in [f for f in fields if f["type"] in ("date", "past", "future")][:1]:
        row("date in the past", f["name"], "2001-01-01")
        row("date far in the future", f["name"], "2099-12-31")
        row("impossible date", f["name"], "2026-02-30")
        row("wrong date format", f["name"], "31/12/2026")
    for f in [f for f in fields if f["type"] in ("int", "number")][:1]:
        row("negative number", f["name"], -1)
        row("zero", f["name"], 0)
        row("huge number", f["name"], 10 ** 12)
        row("text in a number field", f["name"], "abc")
    for f in [f for f in fields if f["type"] == "choice"][:1]:
        row("value not in the list", f["name"], "NotAValidChoice")
    return rows


def make(args):
    fields = parse_fields(args.fields)
    rnd = random.Random(args.seed)
    stamp = datetime.datetime.now().strftime("%m%d%H%M")
    rows = []
    for n in range(1, args.rows + 1):
        person = (rnd.choice(FIRST), rnd.choice(LAST))
        r = {f["name"]: value(f, rnd, n, stamp, args.email_domain, person) for f in fields}
        r["_case"] = "normal"
        rows.append(r)
    if args.edge and rows:
        rows += edge_rows(fields, rows[0], stamp, args.email_domain)
    cols = [f["name"] for f in fields] + ["_case"]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if args.out.endswith(".xlsx"):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "Test data"
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "A2"
        wb.save(args.out)
    else:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    edge = sum(1 for r in rows if r["_case"] != "normal")
    print("Wrote %s: %d normal row(s) + %d edge case(s). The _case column says what each row tries." % (args.out, len(rows) - edge, edge))
    print("Remove the _case column if the app's import does not accept extra columns.")


# ---------- tracking + cleanup ----------

def track(kind, name="", rid="", url="", note=""):
    """Remember a record a test created. Import-free: tests can also append the same JSON line themselves."""
    os.makedirs(os.path.dirname(TRACK_FILE), exist_ok=True)
    with open(TRACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "name": name, "id": str(rid), "url": url, "note": note,
                            "created": datetime.datetime.now().isoformat(timespec="seconds"), "cleaned": None}) + "\n")


def load_tracked():
    out = []
    if os.path.isfile(TRACK_FILE):
        for line in open(TRACK_FILE, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def save_tracked(items):
    with open(TRACK_FILE, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")


def age(text):
    m = re.match(r"(\d+)\s*([mhd])$", text or "0d")
    if not m:
        sys.exit("--older-than like 30m, 6h or 2d")
    n, u = int(m.group(1)), m.group(2)
    return datetime.timedelta(minutes=n) if u == "m" else datetime.timedelta(hours=n) if u == "h" else datetime.timedelta(days=n)


def cmd_list(args):
    items = [i for i in load_tracked() if not i.get("cleaned")]
    if not items:
        print("Nothing tracked (or everything is cleaned). Tracking file: %s" % TRACK_FILE)
        return
    by = {}
    for i in items:
        by.setdefault(i["kind"], []).append(i)
    rules = cleanup_rules()
    for kind, its in sorted(by.items()):
        oldest = min(i["created"] for i in its)[:16].replace("T", " ")
        print("%-14s %3d record(s), oldest %s - %s" % (kind, len(its), oldest,
                                                       "cleanup rule: %s" % rules[kind]["method"] if kind in rules else "no cleanup rule - clean by hand / ask the developers"))
        for i in its[:5]:
            print("    %s  %s  %s" % (i["created"][:16].replace("T", " "), i["id"], i["name"][:60]))
        if len(its) > 5:
            print("    ... %d more" % (len(its) - 5))


def cleanup_rules():
    try:
        import yaml
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            return (((yaml.safe_load(f) or {}).get("test_data") or {}).get("cleanup")) or {}
    except Exception:
        return {}


def read_env(name):
    if os.environ.get(name):
        return os.environ[name]
    if os.path.isfile(".env"):
        for line in open(".env", encoding="utf-8"):
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def cmd_cleanup(args):
    import yaml
    from check_url import check
    with open("qa-toolkit.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}
    rules = cleanup_rules()
    items = load_tracked()
    cutoff = datetime.datetime.now() - age(args.older_than)
    todo = [i for i in items if not i.get("cleaned") and (not args.kind or i["kind"] == args.kind)
            and datetime.datetime.fromisoformat(i["created"]) <= cutoff]
    if not todo:
        print("Nothing to clean.")
        return
    by_hand = [i for i in todo if i["kind"] not in rules]
    plan = []
    for i in todo:
        if i["kind"] not in rules:
            continue
        rule = rules[i["kind"]]
        url = rule["url"].format(id=i["id"], name=i["name"])
        ok, why = check(url, settings)
        if not ok:
            print("  BLOCKED %s: %s" % (url, why))
            continue
        plan.append((i, rule, url))
    print("%s - %d record(s) to remove:" % ("CLEANUP" if args.yes else "DRY RUN (nothing is removed)", len(plan)))
    for i, rule, url in plan:
        print("  %s %s   (%s %s)" % (rule.get("method", "DELETE"), url, i["kind"], i["name"][:40]))
    if by_hand:
        print("No cleanup rule - clean by hand or ask the developers (%d):" % len(by_hand))
        for i in by_hand[:10]:
            print("  %s  %s  %s" % (i["kind"], i["id"], i["name"][:60]))
    if not args.yes or not plan:
        if plan:
            print("Run again with --yes to remove them.")
        return
    import requests
    done = 0
    for i, rule, url in plan:
        headers = {}
        if rule.get("token_env"):
            headers["Authorization"] = "Bearer " + read_env(rule["token_env"])
        try:
            r = requests.request(rule.get("method", "DELETE"), url, headers=headers, timeout=30)
            if r.status_code < 300 or r.status_code == 404:   # 404 = already gone
                i["cleaned"] = datetime.datetime.now().isoformat(timespec="seconds")
                done += 1
            else:
                print("  could not remove %s: %s" % (url, r.status_code))
        except requests.RequestException as e:
            print("  could not remove %s: %s" % (url, str(e)[:120]))
    save_tracked(items)
    print("Removed %d of %d." % (done, len(plan)))


def main():
    ap = argparse.ArgumentParser(description="Test data: make, track, list, clean up")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--fields", required=True, help='e.g. "name:name, email:email, joined:date, role:choice(Learner|Manager)"')
    m.add_argument("--rows", type=int, default=10)
    m.add_argument("--edge", action="store_true", help="add edge-case rows")
    m.add_argument("--email-domain", default="example.com")
    m.add_argument("--seed", type=int, default=7, help="same seed = same data (repeatable)")
    m.add_argument("--out", default="test_data/generated/data.csv", help=".csv or .xlsx")
    t = sub.add_parser("track")
    t.add_argument("--kind", required=True)
    t.add_argument("--name", default="")
    t.add_argument("--id", default="")
    t.add_argument("--url", default="")
    sub.add_parser("list")
    c = sub.add_parser("cleanup")
    c.add_argument("--kind")
    c.add_argument("--older-than", default="0m", help="only records older than this, e.g. 2h or 1d")
    c.add_argument("--yes", action="store_true", help="really remove (default is a dry run)")
    args = ap.parse_args()
    if args.cmd == "make":
        make(args)
    elif args.cmd == "track":
        track(args.kind, args.name, args.id, args.url)
        print("Tracked %s %s" % (args.kind, args.id or args.name))
    elif args.cmd == "list":
        cmd_list(args)
    else:
        cmd_cleanup(args)


if __name__ == "__main__":
    main()
