"""File confirmed bug drafts in Jira - dry run unless you say --live.

    python3 <toolkit>/scripts/bug_file.py check                      # can we reach Jira? (no changes)
    python3 <toolkit>/scripts/bug_file.py file --ids B-01 B-03       # DRY RUN: shows what would happen
    python3 <toolkit>/scripts/bug_file.py file --ids B-01 B-03 --live   # creates them (asks to confirm)

Settings - qa-toolkit.yaml (no secrets there):
    jira:
      project_key: ABC          # letters before the dash, e.g. ABC-123
      issue_type: Bug
      labels: [lurniq]          # added to every bug (optional)
      env_file: .env            # where the login is (optional; default .env). Can be ~/qa-configs/x/.env
Login - in that .env file, typed by you, never printed:
    JIRA_BASE_URL=https://your-company.atlassian.net
    JIRA_EMAIL=you@company.com
    JIRA_API_TOKEN=...           (https://id.atlassian.com/manage-profile/security/api-tokens)

No duplicates: each draft carries a label qa-fp-<fingerprint>. If an OPEN issue with that label exists,
a "Seen again" comment is added to it instead of a new bug (same labels as qa-bug-reporter uses).
The screenshot is attached. Results are written back to bugs.json (status: filed / commented, Jira key).
"""
import argparse
import datetime
import json
import os
import sys
from urllib.parse import urlparse

import yaml


def load_settings():
    if not os.path.isfile("qa-toolkit.yaml"):
        sys.exit("No qa-toolkit.yaml in this folder - run from the project root.")
    with open("qa-toolkit.yaml", encoding="utf-8") as f:
        conf = (yaml.safe_load(f) or {}).get("jira") or {}
    if not conf.get("project_key"):
        sys.exit("qa-toolkit.yaml has no jira: section with project_key yet. Add:\n"
                 "jira:\n  project_key: ABC\n  issue_type: Bug\n  env_file: .env")
    env_file = os.path.expanduser(conf.get("env_file") or ".env")
    login = read_env(env_file)
    missing = [k for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN") if not login.get(k)]
    if missing:
        sys.exit("%s is missing %s. Add them to that file yourself (never paste the token into chat)." % (env_file, ", ".join(missing)))
    url = urlparse(login["JIRA_BASE_URL"])
    local = url.hostname in ("localhost", "127.0.0.1")  # a practice / fake Jira on this computer
    if not url.hostname or (url.scheme != "https" and not local):
        sys.exit("JIRA_BASE_URL must be an https:// address, e.g. https://your-company.atlassian.net")
    login["JIRA_BASE_URL"] = login["JIRA_BASE_URL"].rstrip("/")
    return conf, login


def read_env(path):
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        values.setdefault(k, os.environ.get(k, ""))
    return values


# ---------- Jira calls ----------

class Jira:
    def __init__(self, login):
        import requests
        self.r = requests
        self.base = login["JIRA_BASE_URL"]
        self.auth = (login["JIRA_EMAIL"], login["JIRA_API_TOKEN"])

    def call(self, method, path, **kw):
        resp = self.r.request(method, self.base + path, auth=self.auth, timeout=30, **kw)
        if resp.status_code == 401:
            raise SystemExit("Jira said 401 - the login was refused. The API token has probably expired or was revoked "
                             "(or JIRA_EMAIL is not the account that made it). Make a new token at "
                             "https://id.atlassian.com/manage-profile/security/api-tokens and put it in JIRA_API_TOKEN yourself.")
        if resp.status_code >= 400:
            raise RuntimeError("Jira said %s on %s: %s" % (resp.status_code, path, resp.text[:400]))
        return resp.json() if resp.text.strip() else {}

    def me(self):
        return self.call("GET", "/rest/api/3/myself")["displayName"]

    def project(self, key):
        return self.call("GET", "/rest/api/3/project/%s" % key)

    def find_open(self, project_key, label):
        jql = 'project = "%s" AND labels = "%s" AND statusCategory != Done ORDER BY created DESC' % (project_key, label)
        found = self.call("POST", "/rest/api/3/search/jql", json={"jql": jql, "maxResults": 1, "fields": ["summary"]})
        issues = found.get("issues", [])
        return issues[0]["key"] if issues else None

    def create(self, fields):
        return self.call("POST", "/rest/api/3/issue", json={"fields": fields})["key"]

    def comment(self, key, text):
        self.call("POST", "/rest/api/3/issue/%s/comment" % key, json={"body": adf_text(text)})

    def attach(self, key, path):
        with open(path, "rb") as f:
            self.call("POST", "/rest/api/3/issue/%s/attachments" % key, headers={"X-Atlassian-Token": "no-check"},
                      files={"file": (os.path.basename(path), f, "image/png")})


def adf_text(text):
    """Plain text -> Atlassian Document Format: headings for *Bold lines*, numbered steps, code for {noformat}."""
    blocks, code, buf = [], None, []

    def para(t):
        return {"type": "paragraph", "content": [{"type": "text", "text": t}] if t else []}
    lines = (text or "").split("\n")
    ordered = []
    for line in lines + [""]:
        if code is not None:
            if line.strip() == "{noformat}":
                blocks.append({"type": "codeBlock", "attrs": {}, "content": [{"type": "text", "text": "\n".join(code) or " "}]})
                code = None
            else:
                code.append(line)
            continue
        if line.strip() == "{noformat}":
            if ordered:
                blocks.append({"type": "orderedList", "content": [{"type": "listItem", "content": [para(i)]} for i in ordered]})
                ordered = []
            if buf:
                blocks.append({"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in buf]})
                buf = []
            code = []
            continue
        num = line[:4].split(".")[0]
        if num.isdigit() and line.startswith(num + ". "):
            ordered.append(line[len(num) + 2:])
            continue
        if ordered:
            blocks.append({"type": "orderedList", "content": [{"type": "listItem", "content": [para(i)]} for i in ordered]})
            ordered = []
        if line.startswith("*") and line.rstrip().endswith("*") and len(line.strip()) > 2:
            blocks.append({"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": line.strip("* ")}]})
        elif line.startswith("- "):
            buf.append(line[2:])
            continue
        elif line.startswith("*") and "*" in line[1:]:   # "*Label:* text" -> bold label + text
            label, rest = line[1:].split("*", 1)
            blocks.append({"type": "paragraph", "content": [{"type": "text", "text": label, "marks": [{"type": "strong"}]}]
                           + ([{"type": "text", "text": rest}] if rest.strip() else [])})
        elif line.strip():
            blocks.append(para(line))
        if buf:
            blocks.append({"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in buf]})
            buf = []
    if buf:
        blocks.append({"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in buf]})
    return {"type": "doc", "version": 1, "content": blocks or [para(" ")]}


def fields_for(bug, conf):
    labels = list(dict.fromkeys(bug["labels"] + list(conf.get("labels") or []) + ["severity-" + bug["severity"].lower()]))
    return {"project": {"key": conf["project_key"]}, "issuetype": {"name": conf.get("issue_type") or "Bug"},
            "summary": bug["title"][:250], "labels": [l.replace(" ", "-") for l in labels],
            "description": adf_text("*Severity:* %s\n%s" % (bug["severity"], bug["description"])),
            "priority": {"name": bug["priority"]}}


# ---------- commands ----------

def cmd_check(conf, login):
    j = Jira(login)
    print("Jira: %s" % login["JIRA_BASE_URL"])
    print("Connected as: %s" % j.me())
    p = j.project(conf["project_key"])
    types = [t["name"] for t in p.get("issueTypes", [])]
    print("Project: %s (%s)" % (p.get("name"), conf["project_key"]))
    want = conf.get("issue_type") or "Bug"
    print("Issue type '%s': %s" % (want, "OK" if want in types else "NOT FOUND - this project has: " + ", ".join(types)))


def cmd_file(args, conf, login, data, path):
    bugs = {b["id"]: b for b in data["bugs"]}
    ids = list(bugs) if args.all else [i.upper() for i in args.ids or []]
    if not ids:
        sys.exit("Say which bugs to file: --ids B-01 B-03 (only the ones you confirmed by hand), or --all.")
    unknown = [i for i in ids if i not in bugs]
    if unknown:
        sys.exit("Not in bugs.json: " + ", ".join(unknown))
    chosen = [bugs[i] for i in ids]
    done = [b["id"] for b in chosen if b.get("status") in ("filed", "commented")]
    if done:
        print("Already sent before (skipped): " + ", ".join("%s -> %s" % (i, bugs[i].get("jira")) for i in done))
        chosen = [b for b in chosen if b["id"] not in done]
    if not chosen:
        return
    refused = [b["id"] for b in chosen if b.get("status") == "not-a-bug"]
    if refused:
        print("Marked NOT A BUG after the hand check (never filed): " + ", ".join(refused))
        chosen = [b for b in chosen if b["id"] not in refused]
        if not chosen:
            return
    unsure = [b["id"] for b in chosen if b.get("check_first")]
    if unsure:
        print("Marked 'check by hand first': %s - file only if you confirmed them." % ", ".join(unsure))

    j = Jira(login)
    plan = []
    for b in chosen:
        label = "qa-fp-" + b["fingerprint"]
        existing = j.find_open(conf["project_key"], label)
        plan.append((b, existing))
    print("\n%s - project %s on %s:" % ("LIVE" if args.live else "DRY RUN (nothing will be sent)", conf["project_key"], login["JIRA_BASE_URL"]))
    for b, existing in plan:
        if existing:
            print("  %s  comment 'Seen again' on %s (open bug with the same fingerprint)  - %s" % (b["id"], existing, b["title"][:70]))
        else:
            print("  %s  CREATE %s / %s / %s%s  - %s" % (b["id"], conf.get("issue_type") or "Bug", b["severity"], b["priority"],
                                                     " + screenshot" if b.get("screenshot") and os.path.isfile(b["screenshot"]) else "", b["title"][:70]))
    if not args.live:
        print("\nTo send them, run the same command with --live.")
        return
    if not args.yes:
        answer = input("\nSend these %d to Jira now? Type yes: " % len(plan)).strip().lower()
        if answer != "yes":
            print("Nothing sent.")
            return
    stamp = datetime.datetime.now().strftime("%d %b %Y %H:%M")
    for b, existing in plan:
        try:
            if existing:
                j.comment(existing, "Seen again by qa-toolkit on %s (%s %s).\n%s" % (stamp, b["source"], b["ref"], b.get("actual", "")[:500]))
                b.update(status="commented", jira=existing)
                print("  %s  commented on %s" % (b["id"], existing))
                continue
            fields = fields_for(b, conf)
            try:
                key = j.create(fields)
            except RuntimeError as e:
                if "priority" in str(e).lower():
                    fields.pop("priority")
                    key = j.create(fields)
                    print("  (priority could not be set in this project - filed without it)")
                else:
                    raise
            if b.get("screenshot") and os.path.isfile(b["screenshot"]):
                j.attach(key, b["screenshot"])
            b.update(status="filed", jira=key)
            print("  %s  created %s  %s/browse/%s" % (b["id"], key, login["JIRA_BASE_URL"], key))
        except Exception as e:
            b.update(status="error", error_filing=str(e)[:300])
            print("  %s  FAILED: %s" % (b["id"], str(e)[:300]))
        with open(path, "w") as f:
            json.dump(data, f, indent=1)


def main():
    ap = argparse.ArgumentParser(description="File confirmed bug drafts in Jira (dry run unless --live)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="check the Jira login and project (changes nothing)")
    f = sub.add_parser("file", help="file bugs from bugs.json")
    f.add_argument("--ids", nargs="+", help="draft ids to file, e.g. B-01 B-03")
    f.add_argument("--all", action="store_true", help="file every draft (still a dry run without --live)")
    f.add_argument("--live", action="store_true", help="really send to Jira (asks to confirm)")
    f.add_argument("--yes", action="store_true", help="skip the confirm question (only after the person agreed)")
    f.add_argument("--bugs", default="reports/bugs/bugs.json")
    args = ap.parse_args()
    conf, login = load_settings()
    if args.cmd == "check":
        cmd_check(conf, login)
        return
    if not os.path.isfile(args.bugs):
        sys.exit("No %s - run bug_draft.py first." % args.bugs)
    with open(args.bugs) as fh:
        data = json.load(fh)
    cmd_file(args, conf, login, data, args.bugs)


if __name__ == "__main__":
    main()
