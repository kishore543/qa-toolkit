"""qa-toolkit hook: stop the Playwright browser from opening a URL that is not allowed.

Claude Code runs this automatically BEFORE every browser "navigate" step
(see hooks/hooks.json). It receives the step as JSON on standard input.

Exit code 0 = let the browser go there.
Exit code 2 = stop it; the message printed to stderr is shown to Claude.
"""
import json
import os
import sys

import yaml

# Re-use the same rules as check_url.py (same folder).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_url import check  # noqa: E402


def block(message):
    print("qa-toolkit safety guard BLOCKED this: " + message, file=sys.stderr)
    sys.exit(2)


def main():
    event = json.load(sys.stdin)
    url = (event.get("tool_input") or {}).get("url", "")
    project_folder = event.get("cwd") or os.getcwd()
    settings_file = os.path.join(project_folder, "qa-toolkit.yaml")

    if not os.path.isfile(settings_file):
        block("no qa-toolkit.yaml in %s. Copy the template there and list the "
              "allowed test websites first." % project_folder)

    with open(settings_file, encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}

    allowed, reason = check(url, settings)
    if not allowed:
        block("%s (%s). Only test websites listed in qa-toolkit.yaml may be opened. "
              "Do not try another way to reach it." % (url, reason))
    sys.exit(0)


if __name__ == "__main__":
    main()
