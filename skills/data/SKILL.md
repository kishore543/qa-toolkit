---
name: data
description: Test data - generate realistic rows plus edge cases (empty, spaces, too long, non-Latin letters, script text, quotes, invalid / duplicate emails, bad phones, past / future / impossible dates, negative / huge numbers, values not in the list) as CSV or Excel for imports and data-driven tests; track every record the tests create; and clean them up safely (dry run first, only tracked records, only with a cleanup rule). Use when someone asks for test data, sample / bulk / import files, edge-case data, or to clean up test data.
---

# Test data (QA engineer style)

Good test data finds bugs; leftover test data causes them. This skill makes both easy.

Script: `<toolkit>/scripts/test_data.py` (toolkit = two levels above this skill's folder).

## Rules
- **Never real personal data.** Names are made up; emails use `example.com` (can never receive
  mail) unless the person gives a test-mailbox domain (`--email-domain`).
- **Cleanup touches only records in `test_data/created.jsonl`** (what the tests created), only
  with a rule in `qa-toolkit.yaml`, only on allowed hosts, and is a dry run until `--yes` - which
  needs the person's yes. Records the app cannot delete are listed for the team, never forced.

## Steps
1. **Make data** - ask which fields (or read them from the import template / the form):
   ```
   python <toolkit>/scripts/test_data.py make --fields "name:name, email:email, phone:phone, joined:date, role:choice(Learner|Manager)" --rows 10 --edge --out test_data/generated/people.xlsx
   ```
   Each edge row changes ONE field of a normal row and says so in `_case`, so a failure points
   at that value. Use it for import tests (expected: normal rows imported, each edge row accepted
   or refused with a clear message - write the expected result per `_case` in the test cases).
2. **Track** what tests create - add a tiny helper to the project's fixtures (ask first):
   ```python
   def track_created(kind, name="", rid="", url=""):
       import datetime, json, os
       os.makedirs("test_data", exist_ok=True)
       with open("test_data/created.jsonl", "a") as f:
           f.write(json.dumps({"kind": kind, "name": name, "id": str(rid), "url": url, "note": "",
                               "created": datetime.datetime.now().isoformat(timespec="seconds"), "cleaned": None}) + "\n")
   ```
   or from the command line: `test_data.py track --kind tenant --name "QA Test Org 1119" --id <id>`.
3. **See what is left:** `python <toolkit>/scripts/test_data.py list`.
4. **Clean up** - add a rule per kind the app can delete (API / admin endpoint), then dry run:
   ```yaml
   test_data:
     cleanup:
       task: {method: DELETE, url: "https://qa.example.com/api/tasks/{id}", token_env: API_TOKEN}
   ```
   `test_data.py cleanup [--kind task] [--older-than 1d]` shows the plan; `--yes` after the person agrees.
   Kinds without a rule: give the list to the developers (e.g. a QA-only cleanup script on their side).
