"""Accessibility check of web pages (WCAG 2.1 / 2.2 level AA) - read-only.

    python3 <toolkit>/scripts/a11y_check.py https://qa.example.com/ "Login=https://qa.example.com/login"
    python3 <toolkit>/scripts/a11y_check.py --state .auth/state.json "Dashboard=https://qa.example.com/home"
    python3 <toolkit>/scripts/a11y_check.py --save-state .auth/state.json https://qa.example.com/login

For every page:
  1. axe-core (the industry-standard checker by Deque) - WCAG A/AA rules + best practices.
  2. Keyboard walk - presses Tab through the page: does focus move, can you SEE where it is,
     does it get stuck, and are there things that look clickable but Tab never reaches?
  3. Phone width (reflow, WCAG 1.4.10) - the page at 320px wide must not scroll sideways.
Nothing is clicked, typed or submitted; only the Tab key is pressed.

Pages behind a login: pass --state with a saved sign-in. Make one with --save-state (a browser
window opens, YOU sign in, press Enter here) - the file holds a session cookie: keep it out of git.

Writes <out>/a11y-results.json + screenshots, then builds <out>/a11y-report.html (+ .xlsx).
Only sites in qa-toolkit.yaml allowed_hosts are opened.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_url import check  # noqa: E402  (the same safety guard as the other tools)

AXE = os.path.join(HERE, "..", "vendor", "axe-core", "axe.min.js")
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

INTERACTIVE_JS = r"""
() => {
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [tabindex], [contenteditable=""], [contenteditable=true]';
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.closest('[inert],[aria-hidden=true]'); };
  const label = el => (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.getAttribute('alt') || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  let n = 0; const items = [];
  document.querySelectorAll(sel).forEach(el => {
    if (!visible(el) || el.disabled || el.getAttribute('tabindex') === '-1') return;
    el.setAttribute('data-qa-a11y', String(n)); items.push({id: n, tag: el.tagName.toLowerCase(), name: label(el)}); n++;
  });
  // looks clickable (pointer cursor) but is not a link/button/field and has no tabindex
  const native = 'a[href],button,input,select,textarea,summary,label,[tabindex],[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=radio],[role=switch],[role=option]';
  const fake = [];
  document.querySelectorAll('body *').forEach(el => {
    if (fake.length >= 15 || el.matches(native) || el.closest(native) || !visible(el)) return;
    if (getComputedStyle(el).cursor !== 'pointer') return;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer') return; // only the outer one
    if (el.querySelector(native)) return;
    const t = label(el); if (!t) return;
    fake.push({tag: el.tagName.toLowerCase(), name: t, html: el.outerHTML.slice(0, 160)});
  });
  return {items, fake};
}
"""

ACTIVE_JS = r"""
() => { const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const r = el.getBoundingClientRect();
  const listed = el.hasAttribute('data-qa-a11y');
  if (!listed) el.setAttribute('data-qa-a11y', 'w' + Math.random().toString(36).slice(2, 9)); // appeared later (e.g. more rows loaded)
  return {id: el.getAttribute('data-qa-a11y'), listed, tag: el.tagName.toLowerCase(),
          name: (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || '').trim().replace(/\s+/g, ' ').slice(0, 60),
          html: el.outerHTML.slice(0, 160), x: r.x, y: r.y, w: r.width, h: r.height}; }
"""


def settings():
    if not os.path.isfile("qa-toolkit.yaml"):
        sys.exit("qa-toolkit safety guard: no qa-toolkit.yaml in this folder - nothing is allowed.")
    with open("qa-toolkit.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_pages(items):
    pages = []
    for i, item in enumerate(items):
        name, url = (item.split("=", 1) if "=" in item and not item.startswith("http") else (None, item))
        pages.append({"name": (name or "").strip() or url.split("://", 1)[-1], "url": url.strip(), "slug": "p%02d" % (i + 1)})
    return pages


def guard_route(page, conf, blocked):
    def handler(route):
        req = route.request
        if req.is_navigation_request() and req.frame == page.main_frame and not check(req.url, conf)[0]:
            blocked.append(req.url)
            route.abort()
        else:
            route.continue_()
    page.route("**/*", handler)


def open_page(page, url):
    try:
        page.goto(url, wait_until="networkidle", timeout=45000)
    except Exception:
        page.goto(url, wait_until="load", timeout=45000)
    page.wait_for_timeout(1500)


def shot_of_element(page, selector, path, highlight_all=10):
    """Outline the failing elements in red and save a screenshot around the first one."""
    try:
        box = page.evaluate(r"""([sel, n]) => {
            const els = [...document.querySelectorAll(sel)].slice(0, n);
            if (!els.length) return null;
            els.forEach(e => { e.dataset.qaOutline = e.style.outline; e.style.outline = '3px solid #d03b3b'; e.style.outlineOffset = '2px'; });
            els[0].scrollIntoView({block: 'center', inline: 'nearest'});
            const r = els[0].getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height};
        }""", [selector, highlight_all])
        if not box or box["w"] * box["h"] > 0.6 * page.viewport_size["width"] * page.viewport_size["height"]:
            page.evaluate(r"""(sel) => document.querySelectorAll(sel).forEach(e => { e.style.outline = e.dataset.qaOutline || ''; e.style.outlineOffset = ''; delete e.dataset.qaOutline; })""", selector)
            return None  # the whole page (e.g. <html>) - a picture would not show anything useful
        vw, vh = page.viewport_size["width"], page.viewport_size["height"]
        pad = 70
        x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
        w, h = min(vw - x, max(box["w"] + 2 * pad, 360), 820), min(vh - y, max(box["h"] + 2 * pad, 160), 420)  # keep it readable
        page.screenshot(path=path, clip={"x": x, "y": y, "width": max(w, 50), "height": max(h, 50)})
        page.evaluate(r"""(sel) => document.querySelectorAll(sel).forEach(e => { e.style.outline = e.dataset.qaOutline || ''; e.style.outlineOffset = ''; delete e.dataset.qaOutline; })""", selector)
        return path
    except Exception:
        return None


def run_axe(page, axe_js, shots, slug):
    page.add_script_tag(content=axe_js)
    res = page.evaluate("""async (tags) => {
        const r = await axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations', 'incomplete']});
        const slim = v => ({id: v.id, impact: v.impact, help: v.help, description: v.description, helpUrl: v.helpUrl, tags: v.tags,
            nodes: v.nodes.slice(0, 25).map(n => ({target: n.target, html: (n.html || '').slice(0, 200), summary: n.failureSummary || ''})),
            count: v.nodes.length});
        return {violations: r.violations.map(slim), incomplete: r.incomplete.map(slim), passes: r.passes.length,
                inapplicable: r.inapplicable.length, version: axe.version};
    }""", WCAG_TAGS + ["best-practice"])
    for v in res["violations"]:
        target = v["nodes"][0]["target"] if v["nodes"] else None
        if target and isinstance(target[0], str) and len(target) == 1:
            sel = ", ".join(t[0] for t in (n["target"] for n in v["nodes"]) if len(t) == 1 and isinstance(t[0], str))
            if sel.strip().lower() not in ("html", "body", ""):  # page-wide problems get no picture
                v["shot"] = shot_of_element(page, sel, os.path.join(shots, "%s-%s.png" % (slug, v["id"])))
    return res


def keyboard_walk(page, max_tabs, shots, slug):
    found = page.evaluate(INTERACTIVE_JS)
    page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    stops, seen, no_focus, stuck = [], set(), [], None
    first, repeat, finished = None, 0, False
    for i in range(max_tabs):
        page.keyboard.press("Tab")
        page.wait_for_timeout(120)
        el = page.evaluate(ACTIVE_JS)
        if not el:
            stops.append({"tag": "(page)", "name": "focus left the page content"})
            if len(stops) > 2 and stops[-2]["tag"] == "(page)":
                finished = "focus left the page"
                break
            continue
        key = el["id"]
        if first is None:
            first = key
        elif key == first:
            finished = "went all the way round"
            break  # went all the way round
        if stops and stops[-1].get("key") == key:
            repeat += 1
            if repeat >= 3:
                stuck = el
                finished = "focus got stuck"
                break
            continue
        repeat = 0
        el["key"] = key
        # can you see where focus is? compare the element with focus and without it
        visible = True
        try:
            pad = 6
            clip = {"x": max(0, el["x"] - pad), "y": max(0, el["y"] - pad), "width": max(4, el["w"] + 2 * pad), "height": max(4, el["h"] + 2 * pad)}
            if el["y"] >= 0 and el["y"] + el["h"] <= page.viewport_size["height"]:
                with_focus = page.screenshot(clip=clip)
                page.keyboard.press("Shift+Tab")  # move focus off it the way a keyboard user would ...
                # hide the focus ring of the element that now has focus, so a neighbour's ring is not mistaken for this one's
                page.evaluate("""() => { const st = document.createElement('style'); st.id = 'qa-a11y-nofocus';
                    st.textContent = '*:focus, *:focus-visible, *:focus-within { outline: none !important; box-shadow: none !important; }';
                    document.head.appendChild(st); }""")
                page.wait_for_timeout(100)
                without = page.screenshot(clip=clip)
                page.evaluate("() => { const st = document.getElementById('qa-a11y-nofocus'); if (st) st.remove(); }")
                page.keyboard.press("Tab")        # ... and come back, so the Tab order is never disturbed
                page.wait_for_timeout(100)
                back = page.evaluate(ACTIVE_JS)
                if not back or back["id"] != el["id"]:
                    finished = "lost its place in the Tab order"
                    break
                visible = with_focus != without
                if not visible and len(no_focus) < 12:
                    path = os.path.join(shots, "%s-nofocus-%d.png" % (slug, len(no_focus)))
                    with open(path, "wb") as f:
                        f.write(with_focus)
                    no_focus.append({"tag": el["tag"], "name": el["name"], "html": el["html"], "shot": path})
        except Exception:
            pass
        if finished:
            break
        el["focus_visible"] = visible
        stops.append(el)
        if el["listed"]:
            seen.add(int(el["id"]))
    reached = [s for s in stops if s.get("tag") != "(page)"]
    missed = [it for it in found["items"] if it["id"] not in seen]
    # limit_hit: Tab presses ran out before the walk went all the way round - so "not reached" means nothing
    return {"interactive": len(found["items"]), "reached": len(seen), "tabs": i + 1, "limit_hit": not finished, "ended": finished or "ran out of Tab presses",
            "stuck": stuck, "no_visible_focus": no_focus, "missed": missed[:15], "missed_count": len(missed),
            "fake_buttons": found["fake"], "order": [{"tag": s["tag"], "name": s.get("name", ""), "focus_visible": s.get("focus_visible", True)} for s in reached[:60]]}


def reflow(page, url, shots, slug):
    size = page.viewport_size
    page.set_viewport_size({"width": 320, "height": 800})
    page.wait_for_timeout(800)
    info = page.evaluate("() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
    path = os.path.join(shots, "%s-320px.png" % slug)
    page.screenshot(path=path)
    page.set_viewport_size(size)
    page.wait_for_timeout(300)
    return {"width": 320, "scroll_width": info["sw"], "sideways": info["sw"] > info["cw"] + 2, "shot": path}


def save_state(path, url, conf):
    if not check(url, conf)[0]:
        sys.exit("qa-toolkit safety guard: %s is not in allowed_hosts." % url)
    from playwright.sync_api import sync_playwright
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(url)
        input("\nSign in in the browser window yourself. When you see the signed-in page, press Enter here ... ")
        ctx.storage_state(path=path)
        b.close()
    print("Saved the sign-in to %s - it holds a session cookie: keep it out of git (add it to .gitignore)." % path)


def main():
    ap = argparse.ArgumentParser(description="Accessibility check (WCAG 2.1/2.2 AA) - read-only")
    ap.add_argument("pages", nargs="+", help='URLs, or "Name=URL"')
    ap.add_argument("--state", help="saved sign-in (Playwright storage state JSON) for pages behind a login")
    ap.add_argument("--save-state", metavar="PATH", help="open a browser, let the person sign in, save the sign-in to PATH")
    ap.add_argument("--out", default="reports/a11y")
    ap.add_argument("--title", default="Accessibility check")
    ap.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    ap.add_argument("--tabs", type=int, default=60, help="how many Tab presses per page (default 60)")
    ap.add_argument("--no-keyboard", action="store_true")
    ap.add_argument("--no-reflow", action="store_true")
    args = ap.parse_args()
    conf = settings()

    if args.save_state:
        save_state(args.save_state, args.pages[0], conf)
        return
    if not os.path.isfile(AXE):
        sys.exit("axe-core is missing: %s (it ships with qa-toolkit in vendor/axe-core)." % AXE)
    with open(AXE, encoding="utf-8") as f:
        axe_js = f.read()

    pages = parse_pages(args.pages)
    for pg in pages:
        ok, why = check(pg["url"], conf)
        if not ok:
            sys.exit("qa-toolkit safety guard BLOCKED %s: %s" % (pg["url"], why))

    shots = os.path.join(args.out, "shots")
    os.makedirs(shots, exist_ok=True)
    for f in os.listdir(shots):
        os.remove(os.path.join(shots, f))

    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = getattr(p, args.browser).launch()
        ctx_opts = {"viewport": {"width": 1440, "height": 900}, "bypass_csp": True}
        if args.state:
            ctx_opts["storage_state"] = args.state
        for pg in pages:
            print("Checking %s ..." % pg["name"], flush=True)
            ctx = browser.new_context(**ctx_opts)
            page = ctx.new_page()
            blocked = []
            guard_route(page, conf, blocked)
            item = dict(pg)
            try:
                open_page(page, pg["url"])
                item.update(final_url=page.url, title=page.title())
                full = os.path.join(shots, "%s-page.png" % pg["slug"])
                page.screenshot(path=full)
                item["shot"] = full
                item["axe"] = run_axe(page, axe_js, shots, pg["slug"])
                if not args.no_keyboard:
                    open_page(page, pg["url"])
                    item["keyboard"] = keyboard_walk(page, args.tabs, shots, pg["slug"])
                if not args.no_reflow:
                    item["reflow"] = reflow(page, pg["url"], shots, pg["slug"])
                v = item["axe"]["violations"]
                print("  axe: %d problem rule(s), %d element(s)%s%s" % (
                    len(v), sum(x["count"] for x in v),
                    "" if args.no_keyboard else " | keyboard: reached %d of %d%s" % (
                        item["keyboard"]["reached"], item["keyboard"]["interactive"],
                        " (ran out of Tab presses - use --tabs for more)" if item["keyboard"]["limit_hit"] else ""),
                    "" if args.no_reflow else " | 320px: %s" % ("scrolls sideways" if item["reflow"]["sideways"] else "fits")), flush=True)
            except Exception as e:
                item["error"] = str(e).splitlines()[0][:300]
                print("  COULD NOT CHECK: " + item["error"], flush=True)
            if blocked:
                item["blocked"] = blocked
            ctx.close()
            results.append(item)
        version = browser.version
        browser.close()

    out = {"title": args.title, "started": datetime.datetime.now().isoformat(timespec="seconds"),
           "browser": "%s %s" % (args.browser, version), "signed_in": bool(args.state), "pages": results,
           "checks": {"keyboard": not args.no_keyboard, "reflow": not args.no_reflow}, "standard": "WCAG 2.1 / 2.2 level AA"}
    with open(os.path.join(args.out, "a11y-results.json"), "w") as f:
        json.dump(out, f, indent=1)
    subprocess.run([sys.executable, os.path.join(HERE, "a11y_report.py"), "--out", args.out], check=False)


if __name__ == "__main__":
    main()
