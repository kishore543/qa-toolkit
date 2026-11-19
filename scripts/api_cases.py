"""API test cases from an OpenAPI / Swagger description - the same sheet format as /qa-toolkit:cases.

    python3 <toolkit>/scripts/api_cases.py openapi.json --name tasks
    python3 <toolkit>/scripts/api_cases.py https://qa.example.com/openapi.json --name tasks   (site must be in allowed_hosts)

For every endpoint (method + path) it writes test cases a QA engineer would:
  works with valid data      the documented success status, and the answer has the documented shape
  refused without login      401/403 when the endpoint needs a login
  missing required field     400/422 (one case per required field, up to 3)
  wrong type / bad value     400/422 (a number where text is expected, a value outside the allowed list, too long)
  unknown id                 404 for endpoints with an id in the address
  gone after delete          the deleted record answers 404
Writes test-cases/api/<name>-api-test-cases.csv (for people and the report) and .json (for api_run.py).
Nothing is sent to the API by this script (except reading the description from a URL).
"""
import argparse
import copy
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BAD_INPUT = [400, 422]


def load_spec(src):
    if re.match(r"https?://", src):
        import yaml
        from check_url import check
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            ok, why = check(src, yaml.safe_load(f) or {})
        if not ok:
            sys.exit("qa-toolkit safety guard BLOCKED %s: %s" % (src, why))
        import requests
        text = requests.get(src, timeout=30).text
    else:
        with open(src, encoding="utf-8") as f:
            text = f.read()
    try:
        return json.loads(text)
    except ValueError:
        import yaml
        return yaml.safe_load(text)


def resolve(node, spec, depth=0):
    """Replace $ref with the real schema (components/schemas/...), recursively."""
    if depth > 12:
        return node
    if isinstance(node, dict):
        if "$ref" in node:
            target = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target.get(part, {})
            return resolve(target, spec, depth + 1)
        return {k: resolve(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, spec, depth + 1) for v in node]
    return node


def sample(schema, name=""):
    """A valid example value for a schema."""
    schema = schema or {}
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    t = schema.get("type") or ("object" if "properties" in schema else "string")
    if t == "object":
        props = schema.get("properties", {})
        return {k: sample(v, k) for k, v in props.items()}
    if t == "array":
        return [sample(schema.get("items", {}), name)]
    if t == "integer":
        return max(1, schema.get("minimum", 1))
    if t == "number":
        return max(1.5, schema.get("minimum", 1.5))
    if t == "boolean":
        return True
    fmt = schema.get("format", "")
    if fmt == "email" or "email" in name.lower():
        return "qa.api.test@example.com"
    if fmt == "uuid":
        return "00000000-0000-4000-8000-000000000001"
    if fmt in ("date", "date-time"):
        return "2027-01-15" if fmt == "date" else "2027-01-15T10:00:00Z"
    text = "QA API test %s" % name if name else "QA API test"
    if schema.get("maxLength"):
        text = text[:schema["maxLength"]]
    return text


def wrong_value(schema):
    t = schema.get("type")
    return {"string": 12345, "integer": "abc", "number": "abc", "boolean": "yes", "array": "not-a-list", "object": "not-an-object"}.get(t)


def json_body_schema(op, spec):
    content = ((op.get("requestBody") or {}).get("content") or {})
    for ctype, c in content.items():
        if "json" in ctype:
            return resolve(c.get("schema", {}), spec)
    return None


def response_schema(op, code, spec):
    content = ((op.get("responses") or {}).get(str(code)) or {}).get("content") or {}
    for ctype, c in content.items():
        if "json" in ctype:
            return resolve(c.get("schema", {}), spec)
    return None


def documented(op, codes):
    have = [int(c) for c in (op.get("responses") or {}) if str(c).isdigit()]
    return [c for c in codes if c in have] or codes


def shape_words(schema):
    if not schema:
        return ""
    if schema.get("type") == "array":
        inner = schema.get("items", {})
        return "a list of records with %s" % ", ".join(inner.get("required") or list(inner.get("properties", {}))[:5])
    fields = schema.get("required") or list(schema.get("properties", {}))[:6]
    return "a record with %s" % ", ".join(fields) if fields else ""


def build(spec, name):
    cases = []
    paths = spec.get("paths", {})
    post_collections = {p for p, item in paths.items() if "post" in item and "{" not in p.rsplit("/", 1)[-1]}
    global_security = spec.get("security") or []
    has_delete_get = {p for p, item in paths.items() if "delete" in item and "get" in item}

    for path, item in sorted(paths.items()):
        shared_params = item.get("parameters", [])
        for method in ("post", "get", "put", "patch", "delete"):
            op = item.get(method)
            if not op:
                continue
            op = resolve(op, spec)
            M = method.upper()
            area = (op.get("tags") or [path.strip("/").split("/")[0] or "api"])[0]
            summary = op.get("summary") or "%s %s" % (M, path)
            secured = bool(op.get("security", global_security))
            params = {p["name"]: resolve(p, spec) for p in shared_params + (op.get("parameters") or []) if p.get("in") == "path"}
            collection = path.rsplit("/", 1)[0] if params and path.endswith("}") else None
            from_create = collection in post_collections if collection else False
            path_values = {}
            for pname, p in params.items():
                if from_create and path.endswith("{%s}" % pname):
                    path_values[pname] = "@created:" + collection
                else:
                    path_values[pname] = sample(p.get("schema", {}), pname)
            body_schema = json_body_schema(op, spec)
            ok_codes = sorted(int(c) for c in (op.get("responses") or {}) if str(c).startswith("2")) or [200]
            ok = ok_codes[0]
            rschema = response_schema(op, ok, spec)
            body = sample(body_schema) if body_schema else None
            req = "%s %s" % (M, path)
            phase = {"POST": 0, "GET": 1, "PUT": 3, "PATCH": 3, "DELETE": 8}[M]
            write = M != "GET"

            def add(title, typ, prio, expect, auth="token", body_=None, pv=None, check_schema=None, ph=None, note="", saves=False):
                cases.append({"title": "%s - %s" % (summary, title), "area": area, "requirement": req, "type": typ,
                              "priority": prio, "method": M, "path": path, "path_values": pv if pv is not None else dict(path_values),
                              "body": body_, "auth": auth, "expect": expect, "schema": check_schema, "write": write,
                              "phase": ph if ph is not None else phase, "group": collection or path, "note": note,
                              "saves_id": saves})

            add("works with valid data", "Positive", "High", [ok], body_=copy.deepcopy(body), check_schema=rschema,
                saves=(M == "POST" and path in post_collections))
            if M == "DELETE" and path in has_delete_get:
                cases.append(dict(cases[-1], title="%s - deleted record is gone" % summary, method="GET", type="Positive", priority="High",
                                  expect=[404], schema=None, write=False, phase=9, body=None, requirement=req, saves_id=False,
                                  note="GET the same address after the delete"))
            if secured:
                add("refused without login", "Security", "High", [401, 403], auth="none", body_=copy.deepcopy(body),
                    ph=2, note="no token sent")
                add("refused with a wrong token", "Security", "High", [401, 403], auth="wrong", body_=copy.deepcopy(body), ph=2)
            if body_schema and body_schema.get("type", "object") == "object":
                props = body_schema.get("properties", {})
                for field in (body_schema.get("required") or [])[:3]:
                    b = copy.deepcopy(body)
                    b.pop(field, None)
                    add("missing required field '%s'" % field, "Negative", "Medium", documented(op, BAD_INPUT), body_=b, ph=2)
                typed = [(k, v) for k, v in props.items() if wrong_value(v) is not None]
                if typed:
                    k, v = typed[0]
                    b = copy.deepcopy(body)
                    b[k] = wrong_value(v)
                    add("wrong type for '%s' (%s)" % (k, json.dumps(b[k])), "Negative", "Medium", documented(op, BAD_INPUT), body_=b, ph=2)
                enums = [(k, v) for k, v in props.items() if v.get("enum")]
                if enums:
                    k, v = enums[0]
                    b = copy.deepcopy(body)
                    b[k] = "not-allowed-value"
                    add("'%s' outside the allowed values" % k, "Negative", "Medium", documented(op, BAD_INPUT), body_=b, ph=2)
                longs = [(k, v) for k, v in props.items() if v.get("maxLength")]
                if longs:
                    k, v = longs[0]
                    b = copy.deepcopy(body)
                    b[k] = "x" * (v["maxLength"] + 1)
                    add("'%s' one character too long (%d)" % (k, v["maxLength"] + 1), "Boundary", "Medium", documented(op, BAD_INPUT), body_=b, ph=2)
            if params:
                pv = {k: "qa-does-not-exist-000" for k in params}
                add("unknown id", "Negative", "Medium", [404], pv=pv, body_=copy.deepcopy(body), ph=2)

    cases.sort(key=lambda c: (c["group"], c["phase"]))
    for n, c in enumerate(cases, 1):
        c["id"] = "API-%02d" % n
        c["steps"], c["expected_text"] = words(c)
    return cases


def words(c):
    auth = {"token": "with a valid token", "none": "WITHOUT a token", "wrong": "with a WRONG token"}[c["auth"]]
    addr = c["path"]
    for k, v in c["path_values"].items():
        addr = addr.replace("{%s}" % k, "<id of the record created by API create step>" if str(v).startswith("@created:") else str(v))
    steps = ["Send %s %s %s" % (c["method"], addr, auth)]
    if c["body"] is not None and c["method"] != "GET":
        steps.append("Body: %s" % json.dumps(c["body"]))
    if c["note"]:
        steps.append(c["note"].capitalize())
    exp = "Status %s" % " or ".join(str(s) for s in c["expect"])
    shape = shape_words(c.get("schema"))
    if shape:
        exp += "; the answer is %s, as documented" % shape
    return steps, exp


def write(cases, name, spec, src):
    os.makedirs(os.path.join("test-cases", "api"), exist_ok=True)
    base = os.path.join("test-cases", "api", "%s-api-test-cases" % name)
    with open(base + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Requirement", "Area", "Title", "Type", "Priority", "Preconditions", "Steps", "Test data", "Expected result", "Status"])
        for c in cases:
            w.writerow([c["id"], c["requirement"], c["area"], c["title"], c["type"], c["priority"],
                        "API reachable; a valid token in .env" if c["auth"] != "none" else "API reachable",
                        "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(c["steps"])),
                        json.dumps(c["body"]) if c["body"] is not None else "-", c["expected_text"], "Ready"])
    meta = {"title": spec.get("info", {}).get("title", name), "source": src, "sheet": base + ".csv",
            "servers": [s.get("url") for s in spec.get("servers", [])], "security": security_schemes(spec), "cases": cases}
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return base


def security_schemes(spec):
    out = []
    for name, s in (spec.get("components", {}).get("securitySchemes") or spec.get("securityDefinitions") or {}).items():
        if s.get("type") == "http" and s.get("scheme", "").lower() == "bearer":
            out.append({"kind": "bearer"})
        elif s.get("type") == "apiKey" and s.get("in") == "header":
            out.append({"kind": "header", "name": s.get("name")})
        elif s.get("type") == "http" and s.get("scheme", "").lower() == "basic":
            out.append({"kind": "basic"})
    return out


def main():
    ap = argparse.ArgumentParser(description="Write API test cases from an OpenAPI / Swagger description")
    ap.add_argument("spec", help="openapi.json / .yaml file, or its URL")
    ap.add_argument("--name", help="short name for the files (default: from the API title)")
    args = ap.parse_args()
    spec = load_spec(args.spec)
    if not spec.get("paths"):
        sys.exit("No paths in this description - is it an OpenAPI / Swagger file?")
    name = args.name or re.sub(r"[^a-z0-9]+", "-", spec.get("info", {}).get("title", "api").lower()).strip("-")
    cases = build(spec, name)
    base = write(cases, name, spec, args.spec)
    by = {}
    for c in cases:
        by[c["type"]] = by.get(c["type"], 0) + 1
    ops = len({c["requirement"] for c in cases})
    print("%d test cases for %d endpoints: %s" % (len(cases), ops, ", ".join("%s %d" % kv for kv in sorted(by.items()))))
    print("%d change data (POST/PUT/PATCH/DELETE) - they run only with --allow-writes." % sum(1 for c in cases if c["write"]))
    print("Sheet: %s.csv   (for people and the report)" % base)
    print("Runner file: %s.json" % base)


if __name__ == "__main__":
    main()
