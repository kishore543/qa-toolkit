"""Exploratory agent: walk through the app like a curious tester and report what looks broken - read-only.

    python3 <toolkit>/scripts/explore.py https://qa.example.com/ [--state .auth/state.json] [--max-pages 40]

What it does on every page (only links and tabs - it never submits a form or clicks an action button):
  - opens the page and waits for it to settle; clicks each tab (role=tab) to see hidden panels
  - watches for: JavaScript crashes (uncaught errors), console errors, server errors (5xx),
    failed calls (4xx), broken links (a page that answers 404), broken images, slow pages,
    and tell-tale text on screen: "Something went wrong", "Application error", "undefined", "NaN",
    "[object Object]", "Invalid Date", "null" shown as a value
  - follows links on the same site; addresses that only differ by an id (/tenants/<id>/config) are
    visited at most twice, so 200 records do not mean 200 pages
  - never opens links that look risky: logout / sign out / delete / remove / suspend / deactivate /
    revoke / reset (change with --deny); never leaves qa-toolkit.yaml allowed_hosts
Writes reports/explore/explore-report.html + findings.json (bug_draft.py can draft bugs from it).
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
from urllib.parse import urldefrag, urljoin, urlparse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_url import check  # noqa: E402

DENY = r"log-?out|sign-?out|delete|remove|suspend|deactivat|revoke|reset|destroy|unsubscribe|\.pdf$|\.zip$|\.csv$|download|export"
BAD_TEXT = [  # (pattern on visible text, what it means, severity)
    (r"something went wrong|application error|unexpected error|internal server error", "The page shows an error message", "high"),
    (r"\bundefined\b", "'undefined' is shown on screen - a value is missing", "medium"),
    (r"\bNaN\b", "'NaN' (not a number) is shown on screen - a calculation failed", "medium"),
    (r"\[object Object\]", "'[object Object]' is shown on screen - data is printed wrongly", "medium"),
    (r"Invalid Date", "'Invalid Date' is shown on screen", "medium"),
    (r"(?:^|\s)null(?:\s|$)", "'null' is shown on screen as a value", "low"),
    (r"\b404\b.{0,40}(not found|could not be found)|page not found", "The page says it was not found", "medium"),
]
SEV = {"high": ("High", "crit", 0), "medium": ("Medium", "warn", 1), "low": ("Low", "none", 2)}
ID_PART = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f-]{27,}|\d{2,}|[0-9a-f]{16,}|[a-z0-9-]*\d[a-z0-9-]{6,})(?=/|$)", re.I)


def pattern_of(url):
    p = urlparse(url)
    return p.netloc + ID_PART.sub("/<id>", p.path.rstrip("/") or "/")


def visible_text_problems(page):
    try:
        text = page.inner_text("body", timeout=3000)
    except Exception:
        return []
    found = []
    for pat, meaning, sev in BAD_TEXT:
        m = re.search(pat, text, re.I | re.M)
        if m:
            start = max(0, m.start() - 60)
            found.append((meaning, sev, "...%s..." % text[start:m.end() + 60].replace("\n", " ").strip()))
    return found


def main():
    ap = argparse.ArgumentParser(description="Exploratory agent - read-only walk through the app")
    ap.add_argument("start", nargs="+", help="where to start (URLs)")
    ap.add_argument("--state", help="saved sign-in (Playwright storage state) for pages behind a login")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--per-pattern", type=int, default=2, help="pages per address pattern (default 2)")
    ap.add_argument("--slow-ms", type=int, default=4000, help="a page slower than this is reported (default 4000)")
    ap.add_argument("--deny", default=DENY, help="regex of addresses never opened")
    ap.add_argument("--no-tabs", action="store_true", help="do not click tabs")
    ap.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    ap.add_argument("--out", default="reports/explore")
    ap.add_argument("--title", default="Exploratory check")
    args = ap.parse_args()

    if not os.path.isfile("qa-toolkit.yaml"):
        sys.exit("qa-toolkit safety guard: no qa-toolkit.yaml in this folder.")
    with open("qa-toolkit.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}
    for u in args.start:
        ok, why = check(u, settings)
        if not ok:
            sys.exit("qa-toolkit safety guard BLOCKED %s: %s" % (u, why))
    deny = re.compile(args.deny, re.I)
    shots = os.path.join(args.out, "shots")
    os.makedirs(shots, exist_ok=True)
    for f in os.listdir(shots):
        os.remove(os.path.join(shots, f))

    from playwright.sync_api import sync_playwright
    queue = [(u, "start") for u in args.start]
    seen_urls, per_pattern, pages, findings, skipped = set(), {}, [], [], {}
    started = datetime.datetime.now()
    with sync_playwright() as p:
        browser = getattr(p, args.browser).launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, **({"storage_state": args.state} if args.state else {}))
        page = ctx.new_page()
        events = []
        blocked = []

        def guard(route):
            req = route.request
            if req.is_navigation_request() and req.frame == page.main_frame and not check(req.url, settings)[0]:
                blocked.append(req.url)
                route.abort()
            else:
                route.continue_()
        page.route("**/*", guard)
        page.on("console", lambda m: events.append(("console", m.text)) if m.type == "error" else None)
        page.on("pageerror", lambda e: events.append(("crash", str(e))))
        page.on("response", lambda r: events.append(("http", (r.status, r.request.method, r.url, r.request.resource_type)))
                if r.status >= 400 and r.request.resource_type in ("document", "fetch", "xhr", "image", "script", "stylesheet") else None)

        while queue and len(pages) < args.max_pages:
            url, came_from = queue.pop(0)
            url = urldefrag(url)[0]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            pat = pattern_of(url)
            if per_pattern.get(pat, 0) >= args.per_pattern:
                skipped[pat] = skipped.get(pat, 0) + 1
                continue
            per_pattern[pat] = per_pattern.get(pat, 0) + 1
            events.clear()
            n = len(pages) + 1
            print("[%d] %s" % (n, url), flush=True)
            info = {"n": n, "url": url, "from": came_from, "findings": 0}
            t0 = time.time()
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                info["ms"] = int((time.time() - t0) * 1000)
                info["status"] = resp.status if resp else None
                info["final"] = page.url
                info["title"] = page.title()
            except Exception as e:
                info.update(ms=int((time.time() - t0) * 1000), status=None, error=str(e).splitlines()[0][:200])
                findings.append({"kind": "Page did not open", "severity": "high", "page": url, "detail": info["error"], "from": came_from})
                pages.append(info)
                continue
            if page.url != url and not check(page.url, settings)[0]:
                pages.append(info)
                continue
            shot = os.path.join(shots, "page-%02d.png" % n)

            def add(kind, sev, detail):
                findings.append({"kind": kind, "severity": sev, "page": url, "detail": detail[:400], "from": came_from, "shot": shot})
                info["findings"] += 1

            if info["status"] and info["status"] >= 400:
                add("Broken link" if info["status"] == 404 else "Page answered with an error", "high" if info["status"] >= 500 else "medium",
                    "%s answered %s (linked from %s)" % (url, info["status"], came_from))
            if info["ms"] > args.slow_ms:
                add("Slow page", "low", "took %.1f s to settle" % (info["ms"] / 1000.0))
            # tabs: open each one, look again (tabs only show content, they do not change data)
            tab_names = []
            # text seen on the page first, then on each tab - each kind of wrong text is reported once per page
            seen_text = set()
            for meaning, sev, ctx_text in visible_text_problems(page):
                if info["status"] == 404 and "not found" in meaning:
                    continue  # already reported as a broken link
                seen_text.add(meaning)
                add("Wrong text on screen", sev, "%s: %s" % (meaning, ctx_text))
            if not args.no_tabs:
                try:
                    tabs = page.get_by_role("tab")
                    for i in range(min(tabs.count(), 8)):
                        t = tabs.nth(i)
                        name = (t.inner_text(timeout=2000) or "").strip().replace("\n", " ")[:40]
                        if deny.search(name):
                            continue
                        t.click(timeout=3000)
                        page.wait_for_timeout(700)
                        tab_names.append(name)
                        for meaning, sev, ctx_text in visible_text_problems(page):
                            if meaning not in seen_text:
                                seen_text.add(meaning)
                                add("Wrong text on screen", sev, "%s (tab '%s'): %s" % (meaning, name, ctx_text))
                except Exception:
                    pass
            info["tabs"] = tab_names
            try:
                broken = page.evaluate("() => [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.src).map(i => i.src).slice(0, 5)")
            except Exception:
                broken = []
            for src in broken:
                add("Broken image", "medium", src)
            if not info.get("title"):
                add("Page has no title", "low", "the browser tab shows no page name")
            for kind, data in list(events):
                if kind == "crash":
                    add("JavaScript crash", "high", data)
                elif kind == "console":
                    if "Failed to load resource" not in data:   # those are the failed calls, reported below
                        add("Console error", "medium", data)
                elif kind == "http":
                    status, method, rurl, rtype = data
                    if rtype == "document" and rurl.rstrip("/") == url.rstrip("/"):
                        continue  # already reported as the page status
                    if rtype == "image" and rurl in broken:
                        continue  # already reported as a broken image
                    add("Server error (%s)" % status if status >= 500 else "Failed call (%s)" % status, "high" if status >= 500 else "medium",
                        "%s %s (%s)" % (method, rurl[:200], rtype))
            try:
                page.screenshot(path=shot)
            except Exception:
                pass
            # new links
            try:
                hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            except Exception:
                hrefs = []
            for h in hrefs:
                h = urldefrag(urljoin(url, h))[0]
                if not h.startswith("http") or h in seen_urls:
                    continue
                if deny.search(h):
                    skipped["(risky) " + pattern_of(h)] = skipped.get("(risky) " + pattern_of(h), 0) + 1
                    continue
                if check(h, settings)[0]:
                    queue.append((h, url))
            pages.append(info)
        version = browser.version
        browser.close()

    # merge the same finding seen on many pages
    merged = {}
    for f in findings:
        key = (f["kind"], re.sub(r"\d+", "#", f["detail"][:120]))
        m = merged.setdefault(key, dict(f, pages=[]))
        if f["page"] not in m["pages"]:
            m["pages"].append(f["page"])
    items = sorted(merged.values(), key=lambda f: (SEV[f["severity"]][2], f["kind"]))
    data = {"title": args.title, "started": started.isoformat(timespec="seconds"), "browser": "%s %s" % (args.browser, version),
            "signed_in": bool(args.state), "pages": pages, "findings": items, "skipped": skipped, "blocked": blocked,
            "limit_hit": bool(queue) and len(pages) >= args.max_pages}
    with open(os.path.join(args.out, "findings.json"), "w") as f:
        json.dump(data, f, indent=1)
    with open(os.path.join(args.out, "explore-report.html"), "w", encoding="utf-8") as f:
        f.write(build_html(data))
    by = {s: sum(1 for f in items if f["severity"] == s) for s in SEV}
    print("\nVisited %d page(s): %d high, %d medium, %d low finding(s)%s" % (
        len(pages), by["high"], by["medium"], by["low"], " (page limit reached - use --max-pages)" if data["limit_hit"] else ""))
    for f in items[:15]:
        print("  %-6s %-24s %s" % (SEV[f["severity"]][0], f["kind"], f["detail"][:100]))
    print("Report: " + os.path.abspath(os.path.join(args.out, "explore-report.html")))


def esc(t):
    return html.escape(str(t if t is not None else ""))


def img(path):
    import base64
    if path and os.path.isfile(path):
        return "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
    return None


def build_html(d):
    by = {s: sum(1 for f in d["findings"] if f["severity"] == s) for s in SEV}
    verdict = ("fail", "PROBLEMS FOUND") if by["high"] else ("warnv", "THINGS TO LOOK AT") if by["medium"] else ("pass", "NOTHING BROKEN FOUND")
    cards = []
    for f in d["findings"]:
        label, css, _ = SEV[f["severity"]]
        shot = img(f.get("shot"))
        cards.append('<article class="card f %s"><div class="fh"><span class="pill %s">%s</span><h3>%s</h3><span class="muted">%d page%s</span></div>'
                     '<div class="grid"><div><p class="detail">%s</p><p class="muted small">Seen on: %s</p></div>%s</div></article>' % (
                         css, css, label, esc(f["kind"]), len(f["pages"]), "" if len(f["pages"]) == 1 else "s", esc(f["detail"]),
                         esc(", ".join(f["pages"][:5]) + (" ..." if len(f["pages"]) > 5 else "")),
                         ('<a href="%s" target="_blank"><img src="%s" alt="Screen of %s" loading="lazy"></a>' % (shot, shot, esc(f["page"]))) if shot else ""))
    rows = "".join('<tr><td>%d</td><td><a href="%s">%s</a>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        p["n"], esc(p["url"]), esc(urlparse(p["url"]).path or "/"), ('<div class="muted small">%s</div>' % esc(p.get("title"))) if p.get("title") else "",
        esc(p.get("status") or "-"), "%.1f s" % (p.get("ms", 0) / 1000.0), esc(", ".join(p.get("tabs") or []) or "-"),
        ('<b>%d</b>' % p["findings"]) if p["findings"] else "✅") for p in d["pages"])
    skipped = "".join("<li>%s <span class='muted'>(%d)</span></li>" % (esc(k), v) for k, v in sorted(d["skipped"].items()))
    meta = "".join("<span>%s</span>" % esc(x) for x in [d["started"].replace("T", " ")[:16], "%d pages" % len(d["pages"]), d["browser"],
                                                       "signed in" if d["signed_in"] else "signed out", "read-only"])
    return TEMPLATE.replace("$title", esc(d["title"])).replace("$meta", meta).replace("$vcss", verdict[0]).replace("$vword", verdict[1]).replace(
        "$vreason", esc("%d high, %d medium, %d low finding(s) on %d page(s)%s" % (by["high"], by["medium"], by["low"], len(d["pages"]),
                                                                                   " - page limit reached" if d["limit_hit"] else ""))).replace(
        "$cards", "".join(cards) or '<div class="card f">✅ Nothing looked broken on the pages visited.</div>').replace(
        "$rows", rows).replace("$skipped", ("<ul>%s</ul>" % skipped) if skipped else "<p class='muted'>Nothing.</p>").replace(
        "$generated", datetime.datetime.now().strftime("%d %b %Y %H:%M"))


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Exploratory check - $title</title>
<style>
 :root { --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9; --ring:rgba(11,11,11,.10);
  --good:#0ca30c; --warn:#fab219; --warn-bg:#fdf3dc; --crit:#d03b3b; --crit-bg:#f8e3e3; --none:#c3c2b7; --none-bg:#efeee9; --accent:#2a78d6;
  --hero:#0f1b2d; --hero2:#1d3557; }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7;
  --ink3:#9a998f; --line:#2c2c2a; --ring:rgba(255,255,255,.10); --warn-bg:#352a10; --crit-bg:#3a1a1a; --none-bg:#242422; --none:#4a4a46; --accent:#3987e5; } }
 * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 a { color:var(--accent); } .wrap { max-width:1160px; margin:0 auto; padding:0 16px; } .muted { color:var(--ink3); } .small { font-size:12.5px; }
 header { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:28px 0 30px; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; } h1 { margin:4px 0 10px; font-size:30px; }
 .meta { display:flex; flex-wrap:wrap; gap:8px; } .meta span { background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.14); padding:4px 10px; border-radius:999px; font-size:13px; }
 .verdict { margin-top:14px; display:inline-flex; gap:12px; padding:12px 16px; border-radius:12px; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.16); }
 .vdot { width:14px; height:14px; border-radius:50%; margin-top:5px; } .fail .vdot { background:var(--crit); } .warnv .vdot { background:var(--warn); } .pass .vdot { background:var(--good); }
 .vh { font-weight:800; font-size:18px; } .vr { font-size:14px; opacity:.9; }
 h2 { font-size:21px; margin:28px 0 8px; } .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; }
 .f { padding:14px 18px; margin-bottom:12px; border-left:4px solid var(--none); } .f.crit { border-left-color:var(--crit); } .f.warn { border-left-color:var(--warn); }
 .fh { display:flex; flex-wrap:wrap; gap:10px; align-items:center; } .fh h3 { margin:0; font-size:16px; flex:1; }
 .pill { font-size:12.5px; font-weight:700; padding:2px 9px; border-radius:999px; } .pill.crit { background:var(--crit-bg); } .pill.warn { background:var(--warn-bg); } .pill.none { background:var(--none-bg); }
 .grid { display:grid; grid-template-columns:1fr minmax(0, 360px); gap:16px; margin-top:8px; } .detail { margin:0 0 6px; word-break:break-word; }
 .grid img { width:100%; max-height:220px; object-fit:cover; object-position:top; border:1px solid var(--line); border-radius:8px; }
 table { width:100%; border-collapse:collapse; font-size:13.5px; } th { text-align:left; font-size:12.5px; color:var(--ink2); padding:9px; border-bottom:1px solid var(--line); }
 td { padding:8px 9px; border-bottom:1px solid var(--line); vertical-align:top; } .tw { overflow-x:auto; }
 .note { padding:14px 18px; color:var(--ink2); font-size:13.5px; } footer { color:var(--ink3); font-size:12.5px; padding:24px 0 40px; }
 @media (max-width:720px) { .grid { grid-template-columns:1fr; } }
</style></head><body>
<header><div class="wrap"><div class="eyebrow">Exploratory check (read-only)</div><h1>$title</h1><div class="meta">$meta</div>
<div class="verdict $vcss"><span class="vdot"></span><div><div class="vh">$vword</div><div class="vr">$vreason</div></div></div></div></header>
<main class="wrap"><h2>What looked broken</h2>$cards
<h2>Pages visited</h2><div class="card tw"><table><thead><tr><th>#</th><th>Page</th><th>Status</th><th>Load</th><th>Tabs opened</th><th>Findings</th></tr></thead><tbody>$rows</tbody></table></div>
<h2>Not opened</h2><div class="card note"><p>Risky-looking links (logout, delete, suspend ...) and repeats of the same page with another id:</p>$skipped</div>
<div class="card note" style="margin-top:12px"><b>What this does not do:</b> it never fills or submits forms and never clicks action buttons, so flows that change data
(create, invite, suspend) are not explored - those are covered by the automated tests. Check each finding by hand before filing it.</div>
<footer>Made by qa-toolkit · $generated</footer></main></body></html>
"""


if __name__ == "__main__":
    main()
