---
name: api
description: API testing from an OpenAPI / Swagger description - writes the API test cases (works with valid data, refused without login or with a wrong token, missing / wrong-type / out-of-range fields, unknown id 404, gone after delete, answer matches the documented shape), runs them directly against the API (GET only unless writes are allowed; only deletes what it created), and builds the same plain-language report. Use when someone asks for API tests, REST / backend / endpoint testing, Swagger or OpenAPI testing, or contract checks.
---

# API testing (QA engineer style)

API tests check the requests the screens send to the server - directly, without a browser.
They are fast, and they catch what the screen hides: wrong data, missing login checks,
server crashes on bad input.

Paths: the toolkit folder is two levels above this skill's folder.
- Cases: `<toolkit>/scripts/api_cases.py` (OpenAPI -> test case sheet, sends nothing)
- Run: `<toolkit>/scripts/api_run.py` (runs the cases, then builds the report)
- Practice API: `<toolkit>/demo/api_demo_app.py` (4 bugs planted on purpose)

## Rules

- **The API address must be in `qa-toolkit.yaml` `allowed_hosts`.** Production is refused.
- **GET only by default.** POST / PUT / PATCH / DELETE change data: run them only on QA/staging,
  with `--allow-writes`, after the person says yes. The runner changes and deletes ONLY records it
  created in the same run (create -> read -> update -> delete), and deletes leftovers at the end.
- **The token stays in `.env`** (`API_TOKEN=` by default, `--token-env` for another name). The
  person puts it there; never ask them to paste it into chat; never print it.
- **The API description is the source of truth**, like the test case sheet. If the API and the
  description disagree, it is a bug in one of them - say which one you think and let the team decide.
  Never change an expected status to make a test pass.
- **Verify each failure by hand** before calling it a bug (re-send that one request with `curl`
  or `--only API-07`) and read the answer. Security failures (data returned without a login) first.

## Steps

1. **Find the API description.** Ask the developers for the OpenAPI / Swagger file or its URL
   (often `/openapi.json`, `/swagger.json`, `/v3/api-docs`, `/api-docs`). No description?
   Ask for one - guessing endpoints from the browser is not reliable (apps like Next.js use hidden
   "server actions" that change on every deploy and are not an API).

2. **Write the cases:**
   ```
   python <toolkit>/scripts/api_cases.py <openapi.json or URL> --name <short-name>
   ```
   Output: `test-cases/api/<name>-api-test-cases.csv` (opens in Excel - show it to the person;
   remove or add cases there) and `.json` (what the runner uses). Explain the counts per type.

3. **Run, read-only first:**
   ```
   python <toolkit>/scripts/api_run.py test-cases/api/<name>-api-test-cases.json --base-url https://qa.example.com/api
   ```
   Then, after the person agrees and only on QA, add `--allow-writes` for create / update / delete.
   Options: `--only API-03,API-07`, `--token-env MY_TOKEN`, `--slow-ms 1000`, `--out reports/api`.

4. **Check every failure by hand**, then open `reports/api/api-report.html` (+ `.xlsx`) and explain:
   each failure in plain words - crash (5xx), wrong status, SECURITY (answered without a login),
   answer not matching the documented shape - and what was skipped (writes not allowed).

5. **Next:** confirmed bugs -> `/qa-toolkit:bug` (`bug_draft.py --results reports/api/results.xml
   --cases test-cases/api/<name>-api-test-cases.csv`); the release dashboard shows an API area.

## Practice first

```
python3 <toolkit>/demo/api_demo_app.py          # http://localhost:8800 , token: practice-token
```
In a practice folder with `qa-toolkit.yaml` (`allowed_hosts: [localhost]`) and `.env`
(`API_TOKEN=practice-token`), run the two commands above with `--allow-writes`. It should find all
4 planted bugs: a crash on a missing field, 200 instead of 404, user details without a login,
and true/false sent as text.
