"""qa-toolkit: load test - many virtual users at the same time, described in load.yaml.

Run from the project folder (it needs load.yaml and qa-toolkit.yaml there):
    python3 load_test.py                         # the full test from load.yaml
    python3 load_test.py --users 5 --duration 30 # a short try-out first

How it works: Locust pretends to be many users by sending the same requests a browser
would send - without opening a browser - so one computer can act as 100 users.
Each virtual user logs in once (the `login` steps, if any), then walks through the `steps`
in load.yaml, top to bottom, again and again, pausing like a real person between steps.
Secret values (${LEARNER_EMAIL}, ${OTP}...) come from .env or users_file, never from load.yaml.

Writes reports/load/results.json (read by load_report.py).
"""
# Locust must be imported first: it switches Python into its "many users at once" mode.
try:
    import gevent
    from locust import HttpUser, events, task
    from locust.env import Environment
    from locust.log import setup_logging
except ImportError:
    raise SystemExit("Locust is not installed. Run:  python3 -m pip install --user locust")

import argparse
import csv
import datetime
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_url import check  # noqa: E402  (the same safety guard as the browser tests)

MAX_USERS = 100            # the toolkit never runs more than this
SAMPLE_EVERY_SECONDS = 2   # how often we note users / speed / errors for the chart


def load_settings(path):
    if not os.path.isfile(path):
        raise SystemExit("No %s in this folder. Copy templates/load.yaml from the toolkit and edit it." % path)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def safety_check(load, project_settings_path="qa-toolkit.yaml"):
    """Refuse to load any site that qa-toolkit.yaml does not allow."""
    if not os.path.isfile(project_settings_path):
        raise SystemExit("qa-toolkit safety guard: no qa-toolkit.yaml in this folder - nothing is allowed.")
    with open(project_settings_path, encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}
    # A load test is much heavier than a browser test, so a site needs a second, stricter "yes":
    # it must also be in load_test_hosts. Without that list, only your own computer is allowed.
    load_hosts = settings.get("load_test_hosts") or ["localhost", "127.0.0.1"]
    every_step = (load.get("login") or []) + load["steps"]
    urls = [load["base_url"]] + [s["path"] for s in every_step if str(s["path"]).startswith("http")]
    for url in urls:
        allowed, reason = check(url, settings)
        if not allowed:
            raise SystemExit("qa-toolkit safety guard BLOCKED this load test: %s (%s)" % (url, reason))
        if not check(url, {"allowed_hosts": load_hosts, "blocked_hosts": settings.get("blocked_hosts")})[0]:
            raise SystemExit(
                "qa-toolkit safety guard BLOCKED this load test: %s is not in load_test_hosts of qa-toolkit.yaml.\n"
                "Load tests need their own list, because 100 users can slow a site down for everyone.\n"
                "Add only your own staging/QA servers - after telling the developers - like this:\n"
                "  load_test_hosts:\n    - %s" % (url, urlparse(url).hostname))


# ---------- values: ${NAME} comes from the user's row, saved values, or .env ----------

PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def read_env_file(path=".env"):
    """KEY=value lines from .env (passwords, OTPs - never in load.yaml). Real environment wins."""
    values = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values


def fill(value, known):
    """Put the real values into ${NAME} placeholders - in text, and inside json/headers."""
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: str(known.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: fill(v, known) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, known) for v in value]
    return value


def read_users_file(path):
    """A CSV of test accounts (header row, e.g. email). Virtual user 1 gets row 1, and so on."""
    if not path:
        return []
    if not os.path.isfile(path):
        raise SystemExit("users_file %s not found." % path)
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("users_file %s has no rows." % path)
    return rows


def check_values(load, env_values, user_rows):
    """Before starting: every ${NAME} must have a value somewhere - stop early with a clear message."""
    saved = {name for step in load.get("login", []) + load["steps"] for name in (step.get("save") or {})}
    from_rows = set(user_rows[0]) if user_rows else set()
    needed = set(PLACEHOLDER.findall(json.dumps(load.get("login", []) + load["steps"])))
    missing = sorted(needed - saved - from_rows - set(env_values))
    if missing:
        raise SystemExit("These values are used in load.yaml but not set: %s\n"
                         "Add them to .env (KEY=value), or to the users_file columns." % ", ".join(missing))


def pick(response, where):
    """A value from the reply: 'data.access_token' = from the JSON reply,
    're:"redirectUrl":"([^"]+)"' = the first (...) of a pattern in the reply text."""
    if where.startswith("re:"):
        found = re.search(where[3:], response.text or "")
        return found.group(1) if found else None
    try:
        return dig(response.json(), where)
    except ValueError:
        return None


def text_problem(step, text):
    """Many apps answer 200 OK even when something went wrong, with the error inside the text.
    expect_text: words that must be in the reply. fail_if_text: words that must NOT be.
    Both also take a pattern: 're:...' - and if it has (...), the report quotes that part,
    e.g. the app's own error sentence."""
    # the app's own error message first - it says WHY
    for words in as_list(step.get("fail_if_text")):
        found = find(words, text)
        if found:
            return "The app said: %s" % found if words.startswith("re:") else "The answer contained %r" % words
    for words in as_list(step.get("expect_text")):
        if not find(words, text):
            return "The answer did not contain %r" % (words[3:] if words.startswith("re:") else words)
    return None


def find(words, text):
    """The matched text (or its (...) part) - empty if not found."""
    if words.startswith("re:"):
        m = re.search(words[3:], text, re.MULTILINE)
        return (m.group(1) if m.groups() else m.group(0)) if m else ""
    return words if words in text else ""


def as_list(value):
    return value if isinstance(value, list) else ([value] if value else [])


def dig(data, path):
    """Pick a value out of a JSON reply: 'data.access_token' -> reply['data']['access_token']."""
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit():
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


# ---------- one virtual user ----------

def make_user_class(load):
    steps = load["steps"]
    login = load.get("login") or []
    think_min, think_max = load.get("think_time_seconds", [1, 3])
    env_values = read_env_file()
    user_rows = read_users_file(load.get("users_file"))
    check_values(load, env_values, user_rows)
    counter = {"next": 0}

    class JourneyUser(HttpUser):
        """One virtual user: logs in once (if load.yaml has login steps), then walks the steps
        in order, again and again, pausing between them."""
        host = load["base_url"].rstrip("/")

        def wait_time(self):
            return random.uniform(think_min, think_max)

        def on_start(self):
            # This user's own values: their row from users_file (if any) + .env
            row = user_rows[counter["next"] % len(user_rows)] if user_rows else {}
            counter["next"] += 1
            self.known = dict(env_values, **row)
            self.logged_in = not login

        def do_step(self, step):
            """Send one request; check the answer; remember any values listed under save:."""
            expected = step.get("expect_status")
            path = fill(step["path"], self.known)
            if path.startswith("http") and urlparse(path).hostname != urlparse(self.host).hostname:
                # A ${value} filled in at run time pointed to another site - never send it.
                self.environment.events.request.fire(
                    request_type=step.get("method", "GET"), name=step["name"], response_time=0,
                    response_length=0, exception=Exception("Blocked: %s is not the site under test" % path))
                return False
            # multipart: the same fields a browser sends from a <form> with FormData
            multipart = {k: (None, str(v)) for k, v in (fill(step.get("multipart"), self.known) or {}).items()}
            with self.client.request(step.get("method", "GET"), path,
                                     name=step["name"], json=fill(step.get("json"), self.known),
                                     data=fill(step.get("form"), self.known),
                                     files=multipart or None,
                                     headers=fill(step.get("headers"), self.known),
                                     allow_redirects=step.get("follow_redirects", True),
                                     catch_response=True) as response:
                code = response.status_code
                if expected and code != expected:
                    response.failure("Expected answer %s, got %s" % (expected, code or "no answer"))
                    return False
                if not expected and not (200 <= code < 400):
                    response.failure("Got answer %s" % (code or "no answer (could not connect)"))
                    return False
                problem = text_problem(step, response.text or "")
                if problem:
                    response.failure(problem)
                    return False
                for name, where in (step.get("save") or {}).items():
                    value = pick(response, where)
                    if value is None:
                        response.failure("Could not find %s in the answer (save: %s)" % (where, name))
                        return False
                    self.known[name] = value
                response.success()
                return True

        @task
        def journey(self):
            if not self.logged_in:
                # Log in once. If it fails, wait and try again next time - the failure is counted.
                for step in login:
                    if not self.do_step(step):
                        return
                self.logged_in = True
            for step in steps:
                if not self.do_step(step):
                    break      # a real person can't go on after a failed step either
                self.wait()    # think time, like a real person

    return JourneyUser


def step_stats(entry):
    """Numbers for one step (or the total) in milliseconds."""
    slowest = round(entry.max_response_time or 0)
    # Locust rounds percentiles (e.g. 326 ms -> 330); never show "95% within" above the slowest.
    return {
        "requests": entry.num_requests,
        "failures": entry.num_failures,
        "median_ms": min(round(entry.median_response_time or 0), slowest),
        "p95_ms": min(round(entry.get_response_time_percentile(0.95) or 0), slowest),
        "max_ms": slowest,
        "avg_ms": round(entry.avg_response_time or 0),
    }


def run(load, users, duration, ramp):
    setup_logging("WARNING")
    login = load.get("login") or []
    env = Environment(user_classes=[make_user_class(load)], events=events)
    runner = env.create_local_runner()
    spawn_rate = max(users / max(ramp, 1), 0.1)   # new users per second
    timeline = []
    started = time.time()

    def sample():
        """Every few seconds: how many users, how fast, how many errors so far."""
        while True:
            total = env.stats.total
            timeline.append({
                "second": round(time.time() - started),
                "users": runner.user_count,
                "p95_ms": round(total.get_current_response_time_percentile(0.95) or 0),
                "requests_per_second": round(total.current_rps or 0, 1),
                "failures_so_far": total.num_failures,
            })
            gevent.sleep(SAMPLE_EVERY_SECONDS)

    print("Starting: %d users (adding %.1f a second), %d seconds, on %s"
          % (users, spawn_rate, duration, load["base_url"]))
    sampler = gevent.spawn(sample)
    runner.start(users, spawn_rate=spawn_rate)
    gevent.spawn_later(duration, runner.quit)
    runner.greenlet.join()
    sampler.kill()

    return {
        "name": load.get("name", "Load test"),
        "base_url": load["base_url"],
        "run_at": datetime.datetime.now().strftime("%d %b %Y, %H:%M"),
        "users": users, "ramp_up_seconds": ramp, "duration_seconds": duration,
        "limits": load.get("limits", {}),
        "steps": [dict(name=s["name"], method=s.get("method", "GET"), path=s["path"], login=s in login,
                       **step_stats(env.stats.get(s["name"], s.get("method", "GET"))))
                  for s in login + load["steps"]],
        "total": step_stats(env.stats.total),
        "errors": [{"step": e.name, "error": str(e.error), "count": e.occurrences}
                   for e in env.stats.errors.values()],
        "timeline": timeline,
    }


def main():
    parser = argparse.ArgumentParser(description="Load test from load.yaml")
    parser.add_argument("--config", default="load.yaml", help="load settings file")
    parser.add_argument("--users", type=int, help="override users (for a short try-out)")
    parser.add_argument("--duration", type=int, help="override duration_seconds")
    parser.add_argument("--out", default="reports/load/results.json", help="where to save the numbers")
    args = parser.parse_args()

    load = load_settings(args.config)
    safety_check(load)
    users = args.users or load.get("users", 10)
    if users > MAX_USERS:
        print("Asked for %d users - the toolkit's limit is %d. Using %d." % (users, MAX_USERS, MAX_USERS))
        users = MAX_USERS
    duration = args.duration or load.get("duration_seconds", 60)
    ramp = min(load.get("ramp_up_seconds", 30), duration)

    results = run(load, users, duration, ramp)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    t = results["total"]
    print("\nDone: %d requests, %d failed. Typical answer %d ms, 95%% within %d ms, slowest %d ms."
          % (t["requests"], t["failures"], t["median_ms"], t["p95_ms"], t["max_ms"]))
    print("Numbers saved: " + os.path.abspath(args.out))


if __name__ == "__main__":
    main()
