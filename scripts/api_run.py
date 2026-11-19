"""Run API test cases (made by api_cases.py) and build the plain-language report.

    python3 <toolkit>/scripts/api_run.py test-cases/api/tasks-api-test-cases.json --base-url http://localhost:8800
    python3 <toolkit>/scripts/api_run.py <cases.json> --base-url https://qa.example.com/api --allow-writes

Rules it follows:
  - the base URL must be in qa-toolkit.yaml allowed_hosts (production in blocked_hosts is always refused)
  - only GET requests run unless you pass --allow-writes (POST / PUT / PATCH / DELETE change data)
  - records are only changed or deleted if THIS run created them (create -> read -> update -> delete);
    anything it created and did not delete is deleted at the end (when the API has a DELETE)
  - the token is read from the environment / .env (--token-env, default API_TOKEN) and never printed
Results: <out>/results.xml (same format as pytest), then make_report.py builds <out>/api-report.html + .xlsx.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from xml.sax.saxutils import escape, quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_url import check  # noqa: E402

STATUS_WORDS = {200: "OK", 201: "created", 204: "no content", 400: "bad input", 401: "not logged in", 403: "not allowed",
                404: "not found", 409: "conflict", 422: "bad input", 429: "too many requests", 500: "server error (crash)",
                502: "server down", 503: "server down", 504: "server too slow"}


def words(code):
    return "%s (%s)" % (code, STATUS_WORDS.get(code, "")) if code in STATUS_WORDS else str(code)


def read_env(name):
    if os.environ.get(name):
        return os.environ[name]
    if os.path.isfile(".env"):
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ---------- answer shape check (a small JSON-schema check, no extra library) ----------

def shape_problems(value, schema, where="the answer"):
    if not schema:
        return []
    out = []
    if value is None:
        return [] if schema.get("nullable") else ["%s is empty (null) - a value was expected" % where]
    t = schema.get("type") or ("object" if "properties" in schema else None)
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    if t in kinds:
        ok = isinstance(value, kinds[t]) and not (t in ("integer", "number") and isinstance(value, bool))
        if not ok:
            said = {"boolean": "true/false", "integer": "a whole number", "number": "a number", "string": "text",
                    "array": "a list", "object": "a record"}[t]
            got = "the text %s" % json.dumps(value) if isinstance(value, str) else json.dumps(value)[:60]
            return ["%s should be %s but was %s" % (where, said, got)]
    if schema.get("enum") and value not in schema["enum"]:
        out.append("%s is %s - allowed: %s" % (where, json.dumps(value), ", ".join(map(str, schema["enum"]))))
    if t == "object" and isinstance(value, dict):
        for f in schema.get("required", []):
            if f not in value:
                out.append("field '%s' is missing from %s" % (f, where))
        for k, sub in (schema.get("properties") or {}).items():
            if k in value:
                out += shape_problems(value[k], sub, "field '%s'" % k)
    if t == "array" and isinstance(value, list):
        for i, item in enumerate(value[:20]):
            out += shape_problems(item, schema.get("items", {}), "item %d" % (i + 1))
            if len(out) > 8:
                break
    if schema.get("format") == "email" and isinstance(value, str) and not re.match(r"[^@\s]+@[^@\s]+\.\w+$", value):
        out.append("%s is not an email address: %s" % (where, value))
    return out


# ---------- running ----------

def headers_for(auth, schemes, token):
    h = {"Accept": "application/json"}
    if auth == "none" or not schemes:
        return h
    value = token if auth == "token" else "qa-wrong-token-000"
    s = schemes[0]
    if s["kind"] == "bearer":
        h["Authorization"] = "Bearer " + value
    elif s["kind"] == "header":
        h[s["name"]] = value
    elif s["kind"] == "basic":
        import base64
        h["Authorization"] = "Basic " + base64.b64encode(value.encode()).decode()
    return h


def fill_path(case, created):
    addr = case["path"]
    for k, v in case["path_values"].items():
        if str(v).startswith("@created:"):
            got = created.get(v.split(":", 1)[1])
            if not got:
                return None
            v = got
        addr = addr.replace("{%s}" % k, str(v))
    return addr


def find_id(body):
    if isinstance(body, dict):
        for k in ("id", "uuid", "_id"):
            if k in body:
                return body[k]
        for k, v in body.items():
            if k.lower().endswith("id") and isinstance(v, (str, int)):
                return v
    return None


def run(meta, base, token, allow_writes, slow_ms):
    import requests
    created, done, deleted = {}, [], set()
    schemes = meta.get("security") or []
    for c in meta["cases"]:
        r = {"case": c, "status": "Passed", "message": "", "detail": "", "ms": 0}
        done.append(r)
        if c["write"] and not allow_writes:
            r.update(status="Skipped", message="Changes data (%s) - run with --allow-writes on a QA site to include it." % c["method"])
            continue
        addr = fill_path(c, created)
        if addr is None:
            r.update(status="Skipped", message="Needs a record created by the 'works with valid data' create test, which did not succeed.")
            continue
        url = base.rstrip("/") + addr
        start = time.time()
        try:
            resp = requests.request(c["method"], url, headers=headers_for(c["auth"], schemes, token),
                                    json=c["body"] if c["body"] is not None and c["method"] != "GET" else None, timeout=30)
        except requests.RequestException as e:
            r.update(status="Failed", message="Could not connect: %s" % str(e).splitlines()[0][:200],
                     detail="%s %s" % (c["method"], url))
            continue
        r["ms"] = int((time.time() - start) * 1000)
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = resp.text[:300]
        r["code"] = resp.status_code
        shown = json.dumps(body)[:300] if body is not None else "(empty)"
        r["detail"] = "Request: %s %s (%s)\n%sAnswer: %s in %d ms\n%s" % (
            c["method"], url, {"token": "valid token", "none": "no token", "wrong": "wrong token"}[c["auth"]],
            ("Body: %s\n" % json.dumps(c["body"])) if c["body"] is not None and c["method"] != "GET" else "",
            words(resp.status_code), r["ms"], shown)
        problems = []
        if resp.status_code not in c["expect"]:
            want = " or ".join(words(s) for s in c["expect"])
            if c["auth"] != "token" and resp.status_code < 300:
                problems.append("SECURITY: the API answered %s %s - it should refuse (%s). The answer was: %s"
                                % (words(resp.status_code), "without a login" if c["auth"] == "none" else "to a wrong token", want, shown[:160]))
            elif resp.status_code >= 500:
                problems.append("The server crashed: expected status %s, got %s." % (want, words(resp.status_code)))
            else:
                problems.append("Expected status %s, got %s. The answer was: %s" % (want, words(resp.status_code), shown[:160]))
        elif c.get("schema") and resp.status_code < 300:
            problems += ["The answer does not match the documented shape: " + p for p in shape_problems(body, c["schema"])[:5]]
        if c.get("saves_id") and resp.status_code < 300:
            new = find_id(body)
            if new is not None:
                created[c["group"]] = new
        if c["method"] == "DELETE" and resp.status_code < 300 and c["path_values"]:
            deleted.add(addr)
        if r["ms"] > slow_ms and not problems:
            r["note"] = "slow: %d ms" % r["ms"]
        if problems:
            r.update(status="Failed", message=" ".join(problems[:1]) + (" (+%d more)" % (len(problems) - 1) if len(problems) > 1 else ""),
                     detail=r["detail"] + "\n" + "\n".join(problems))
    return done, created, deleted


def cleanup(meta, base, token, created, deleted):
    """Delete records this run created and did not delete itself."""
    import requests
    schemes = meta.get("security") or []
    removed = []
    for group, rid in created.items():
        addr = None
        for c in meta["cases"]:
            if c["method"] == "DELETE" and c["group"] == group and any(str(v).startswith("@created:") for v in c["path_values"].values()):
                addr = fill_path(c, created)
        if addr and addr not in deleted:
            try:
                requests.delete(base.rstrip("/") + addr, headers=headers_for("token", schemes, token), timeout=30)
                removed.append(addr)
            except requests.RequestException:
                pass
    return removed


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def write_junit(results, path):
    total = sum(r["ms"] for r in results) / 1000.0
    fails = sum(1 for r in results if r["status"] == "Failed")
    skips = sum(1 for r in results if r["status"] == "Skipped")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    out = ['<?xml version="1.0" encoding="utf-8"?>',
           '<testsuites><testsuite name="api" tests="%d" failures="%d" skipped="%d" time="%.3f" timestamp="%s">' % (len(results), fails, skips, total, stamp)]
    for r in results:
        c = r["case"]
        name = "test_%s_%s" % (c["id"].replace("-", "_"), slug(c["title"]))
        cls = "api.test_%s" % slug(c["area"])
        out.append('<testcase classname=%s name=%s time="%.3f">' % (quoteattr(cls), quoteattr(name), r["ms"] / 1000.0))
        if r["status"] == "Failed":
            out.append('<failure message=%s>%s</failure>' % (quoteattr(r["message"]), escape(r["detail"])))
        elif r["status"] == "Skipped":
            out.append('<skipped message=%s/>' % quoteattr(r["message"]))
        out.append("</testcase>")
    out.append("</testsuite></testsuites>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main():
    ap = argparse.ArgumentParser(description="Run API test cases and build the report")
    ap.add_argument("cases", help="the .json file api_cases.py wrote")
    ap.add_argument("--base-url", help="API address (default: the first server in the description)")
    ap.add_argument("--token-env", default="API_TOKEN", help="environment / .env variable holding the token (default API_TOKEN)")
    ap.add_argument("--allow-writes", action="store_true", help="also run POST / PUT / PATCH / DELETE (QA only)")
    ap.add_argument("--only", help="only these ids, e.g. API-01,API-05")
    ap.add_argument("--slow-ms", type=int, default=2000, help="note answers slower than this (default 2000 ms)")
    ap.add_argument("--out", default="reports/api")
    ap.add_argument("--title")
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        meta = json.load(f)
    base = args.base_url or (meta.get("servers") or [None])[0]
    if not base:
        sys.exit("No --base-url and no server in the API description.")
    try:
        import yaml
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}
    except FileNotFoundError:
        sys.exit("qa-toolkit safety guard: no qa-toolkit.yaml in this folder - nothing is allowed.")
    ok, why = check(base, settings)
    if not ok:
        sys.exit("qa-toolkit safety guard BLOCKED %s: %s" % (base, why))
    token = read_env(args.token_env)
    if not token and any(c["auth"] == "token" for c in meta["cases"]) and meta.get("security"):
        print("Note: no %s in the environment or .env - tests that need a login will fail with 401." % args.token_env)
    if args.only:
        keep = {x.strip().upper() for x in args.only.split(",")}
        meta["cases"] = [c for c in meta["cases"] if c["id"] in keep]

    print("API: %s  (%d test cases, writes %s)" % (base, len(meta["cases"]), "ALLOWED" if args.allow_writes else "off - GET only"))
    results, created, deleted = run(meta, base, token, args.allow_writes, args.slow_ms)
    removed = cleanup(meta, base, token, created, deleted) if args.allow_writes else []
    for r in results:
        icon = {"Passed": "✅", "Failed": "❌", "Skipped": "⏭"}[r["status"]]
        print("  %s %s %-6s %s%s" % (icon, r["case"]["id"], r["case"]["method"], r["case"]["title"][:70],
                                     ("  -> " + r["message"][:110]) if r["status"] != "Passed" else ("  (" + r["note"] + ")" if r.get("note") else "")))
    if removed:
        print("Cleaned up %d record(s) this run created: %s" % (len(removed), ", ".join(removed)))
    os.makedirs(args.out, exist_ok=True)
    xml = os.path.join(args.out, "results.xml")
    write_junit(results, xml)
    with open(os.path.join(args.out, "run.json"), "w") as f:   # for the release dashboard
        json.dump({"sheet": meta["sheet"], "base_url": base, "writes": args.allow_writes,
                   "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, f, indent=1)
    c = {k: sum(1 for r in results if r["status"] == k) for k in ("Passed", "Failed", "Skipped")}
    print("\n%d passed, %d failed, %d skipped" % (c["Passed"], c["Failed"], c["Skipped"]), flush=True)
    subprocess.run([sys.executable, os.path.join(HERE, "make_report.py"), "--cases", meta["sheet"], "--results", xml,
                    "--out", os.path.join(args.out, "api-report.html"), "--title", args.title or meta["title"],
                    "--site", base], check=False)


if __name__ == "__main__":
    main()
