"""Shared setup for all automated tests (pytest reads this file automatically).

Made by qa-toolkit. It does three things for every test:
  1. Opens a fresh browser page (add --headed to watch it).
     --qa-browser picks the browser: chromium (default), chrome, msedge,
     firefox, webkit (Safari's engine), iphone, ipad, android.
  2. Safety guard: if a test tries to open a website that qa-toolkit.yaml
     does not allow, the page is stopped and the test fails.
  3. If a test fails, saves a screenshot to reports/screenshots/<ID>.png
     so the report can show it.
"""
import os
import re
from urllib.parse import urlparse

import pytest
import yaml
from playwright.sync_api import sync_playwright

SETTINGS_FILE = "qa-toolkit.yaml"

# --qa-browser name -> (Playwright engine, installed channel or None, phone/tablet to imitate or None)
BROWSERS = {
    "chromium": ("chromium", None, None),
    "chrome": ("chromium", "chrome", None),
    "msedge": ("chromium", "msedge", None),
    "firefox": ("firefox", None, None),
    "webkit": ("webkit", None, None),
    "iphone": ("webkit", None, "iPhone 13"),
    "ipad": ("webkit", None, "iPad Pro 11"),
    "android": ("chromium", None, "Pixel 7"),
}
CONTEXT_OPTIONS = {}  # screen size / touch of the imitated device, filled by the browser fixture
SCREENSHOTS = os.environ.get("QA_SCREENSHOTS", "reports/screenshots")  # browser_run.py sets one folder per browser


# ---------- safety guard ----------

def load_settings():
    if not os.path.isfile(SETTINGS_FILE):
        pytest.exit("qa-toolkit safety guard: no %s in this folder." % SETTINGS_FILE, returncode=1)
    with open(SETTINGS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def host_matches(host, rule):
    rule = str(rule).lower()
    return host.endswith(rule[1:]) if rule.startswith("*.") else host == rule


def is_allowed(url, settings):
    host = (urlparse(url).hostname or "").lower()
    if any(host_matches(host, r) for r in settings.get("blocked_hosts") or []):
        return False
    return any(host_matches(host, r) for r in settings.get("allowed_hosts") or [])


# ---------- options ----------

def pytest_addoption(parser):
    parser.addoption("--headed", action="store_true", help="show the browser window")
    parser.addoption("--qa-browser", default=os.environ.get("QA_BROWSER", "chromium"), choices=sorted(BROWSERS),
                     help="browser to run the tests in (default chromium)")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Remember each test's result so the page fixture knows if it failed."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, "result_" + report.when, report)


# ---------- browser ----------

@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def browser(request):
    engine, channel, device = BROWSERS[request.config.getoption("--qa-browser")]
    with sync_playwright() as p:
        CONTEXT_OPTIONS.clear()
        if device:
            CONTEXT_OPTIONS.update(p.devices[device])
            CONTEXT_OPTIONS.pop("default_browser_type", None)
        launch = {"headless": not request.config.getoption("--headed")}
        if channel:
            launch["channel"] = channel
        browser = getattr(p, engine).launch(**launch)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, settings, request):
    context = browser.new_context(**CONTEXT_OPTIONS)
    page = context.new_page()
    blocked = []

    def guard(route):
        req = route.request
        if req.is_navigation_request() and req.frame == page.main_frame and not is_allowed(req.url, settings):
            blocked.append(req.url)
            route.abort()
        else:
            route.continue_()

    page.route("**/*", guard)
    yield page

    result = getattr(request.node, "result_call", None)
    if result is not None and result.failed:
        match = re.match(r"test_([A-Za-z][A-Za-z0-9]*)_(\d+)", request.node.name)  # test_SA_01_... -> SA-01
        if match:
            os.makedirs(SCREENSHOTS, exist_ok=True)
            tc_id = "%s-%s" % (match.group(1).upper(), match.group(2))
            page.screenshot(path=os.path.join(SCREENSHOTS, tc_id + ".png"), full_page=True)
    context.close()
    if blocked:
        pytest.fail("qa-toolkit safety guard BLOCKED a page that is not in allowed_hosts: "
                    + ", ".join(blocked), pytrace=False)
