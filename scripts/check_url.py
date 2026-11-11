"""qa-toolkit safety guard: is this URL safe to test?

Run it from the project folder that has qa-toolkit.yaml:
    python3 check_url.py https://staging.example.com/login

Answers ALLOWED (exit code 0) or BLOCKED (exit code 1).
"""
import sys
from urllib.parse import urlparse

import yaml


def matches(host, rule):
    """'qa.example.com' must match exactly. '*.example.com' matches any sub-domain."""
    if rule.startswith("*."):
        return host.endswith(rule[1:])
    return host == rule


def check(url, settings):
    # 1. Get the host (website name) out of the URL.
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False, "not a valid http/https URL"
    host = parsed.hostname.lower()

    # 2. Blocked list first - it always wins.
    for rule in settings.get("blocked_hosts") or []:
        if matches(host, rule.lower()):
            return False, host + " is in blocked_hosts"

    # 3. Allowed list.
    for rule in settings.get("allowed_hosts") or []:
        if matches(host, rule.lower()):
            return True, host + " is in allowed_hosts"

    # 4. Not on any list - refused.
    return False, host + " is not in allowed_hosts"


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 check_url.py <url>")
        return 1

    try:
        with open("qa-toolkit.yaml", encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print("BLOCKED: no qa-toolkit.yaml in this folder")
        return 1

    allowed, reason = check(sys.argv[1], settings)
    print(("ALLOWED: " if allowed else "BLOCKED: ") + reason)
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
