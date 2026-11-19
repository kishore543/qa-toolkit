"""Accessibility report in plain words, from what a11y_check.py saved.

    python3 <toolkit>/scripts/a11y_report.py [--out reports/a11y]

Reads <out>/a11y-results.json, writes <out>/a11y-report.html and <out>/a11y-report.xlsx.
Problems are grouped by rule across all pages: what is wrong, who it hurts, the WCAG rule,
how to fix it, and examples with a screenshot (failing elements outlined in red).
"""
import argparse
import base64
import datetime
import html
import json
import os
import re
import sys

SEVERITY = [  # key, label, css, icon, meaning
    ("critical", "Critical", "crit", "⛔", "blocks some people completely - fix first"),
    ("serious", "Serious", "serious", "❗", "very hard for some people - fix before release"),
    ("moderate", "Moderate", "warn", "⚠️", "harder than it should be - fix soon"),
    ("minor", "Minor", "none", "ℹ️", "small annoyance - fix when convenient"),
]
SEV_RANK = {s[0]: i for i, s in enumerate(SEVERITY)}

WHO = {
    "cat.color": "People with low vision or colour blindness",
    "cat.text-alternatives": "Blind people using a screen reader",
    "cat.forms": "Screen reader and voice-control users filling in forms",
    "cat.keyboard": "People who use only a keyboard (no mouse)",
    "cat.name-role-value": "Screen reader and voice-control users",
    "cat.aria": "Screen reader users",
    "cat.structure": "Screen reader users finding their way around the page",
    "cat.semantics": "Screen reader users finding their way around the page",
    "cat.language": "Screen reader users (words read out in the wrong accent)",
    "cat.tables": "Screen reader users reading tables",
    "cat.time-and-media": "Deaf or hard-of-hearing people, and people who need more time",
    "cat.sensory-and-visual-cues": "People with low vision",
    "cat.parsing": "People using assistive technology",
}

PLAIN = {  # axe rule -> (plain title, how to fix)
    "color-contrast": ("Text is too faint against its background",
                       "Darken the text or lighten the background until the contrast is at least 4.5:1 (3:1 for large text). The details below give the exact colours."),
    "image-alt": ("Images have no text description", "Add an alt text that says what the image shows - or alt=\"\" if it is only decoration."),
    "label": ("Form fields have no label", "Give every field a visible <label for=...>, or an aria-label if a visible label is impossible."),
    "button-name": ("Buttons have no name a screen reader can read", "Put text inside the button, or add aria-label (icon buttons: aria-label=\"Close\", \"Search\" ...)."),
    "link-name": ("Links have no readable text", "Put text inside the link, or aria-label on icon links."),
    "html-has-lang": ("The page language is not set", "Add lang=\"en\" (or the right language) to the <html> tag."),
    "document-title": ("The page has no title", "Add a <title> that says which page this is."),
    "region": ("Some content is outside the page's landmarks", "Put all content inside <header>, <nav>, <main> or <footer>."),
    "landmark-one-main": ("The page has no main area", "Wrap the main content in a <main> element."),
    "page-has-heading-one": ("The page has no main heading", "Give the page one <h1> that names it."),
    "heading-order": ("Headings skip levels", "Use headings in order - h1, then h2, then h3 - without jumping levels."),
    "empty-heading": ("Headings with no text", "Remove empty headings or put text in them."),
    "nested-interactive": ("Clickable things inside other clickable things", "Do not put a button or link inside another button or link."),
    "target-size": ("Click / tap targets are too small", "Make buttons and links at least 24 x 24 px, or leave space around them."),
    "scrollable-region-focusable": ("A scrolling area cannot be reached with the keyboard", "Add tabindex=\"0\" to the scrolling area so keyboard users can scroll it."),
    "aria-hidden-focus": ("Hidden elements can still get keyboard focus", "Remove aria-hidden, or make the elements inside unfocusable (tabindex=\"-1\" / inert)."),
    "meta-viewport": ("Zooming is blocked on phones", "Remove user-scalable=no and maximum-scale from the viewport meta tag."),
    "select-name": ("Drop-downs have no label", "Give every <select> a label."),
    "input-button-name": ("Input buttons have no name", "Give the button a value or aria-label."),
    "aria-dialog-name": ("Dialogs have no name", "Add aria-labelledby (pointing at the dialog title) or aria-label to the dialog."),
    "aria-input-field-name": ("Custom fields have no name", "Add aria-label or aria-labelledby."),
    "aria-toggle-field-name": ("Switches / checkboxes have no name", "Add aria-label or a visible label."),
    "aria-command-name": ("Custom buttons have no name", "Add aria-label or text inside."),
    "list": ("Lists are built wrongly", "<ul>/<ol> may only contain <li> items directly."),
    "listitem": ("List items outside a list", "Put <li> inside a <ul> or <ol>."),
    "duplicate-id-aria": ("The same id is used twice for labels", "Make every id unique."),
    "frame-title": ("Frames have no title", "Add a title to every <iframe>."),
    "link-in-text-block": ("Links inside text are told apart by colour only", "Underline links inside text, or make the contrast with the text at least 3:1."),
    "autocomplete-valid": ("Wrong autocomplete values", "Use standard autocomplete values (email, name, tel ...)."),
    "aria-allowed-attr": ("ARIA attributes not allowed on this element", "Remove the ARIA attributes that do not belong on this element."),
    "aria-required-children": ("ARIA widgets miss required parts", "Add the required child roles (e.g. a list needs listitems)."),
    "aria-required-parent": ("ARIA parts outside their widget", "Put the element inside its required parent role."),
    "aria-valid-attr-value": ("ARIA attributes have wrong values", "Fix the values (e.g. aria-controls must point at an existing id)."),
    "landmark-unique": ("Landmarks cannot be told apart", "Give repeated landmarks (two <nav>s ...) different aria-labels."),
    "landmark-complementary-is-top-level": ("Side areas are nested inside other landmarks", "Move <aside> to the top level."),
    "bypass": ("No way to skip to the main content", "Add a 'Skip to main content' link or a <main> landmark."),
}

SC_NAMES = {
    "1.1.1": "Non-text content", "1.2.2": "Captions", "1.3.1": "Info and relationships", "1.3.5": "Identify input purpose",
    "1.4.1": "Use of colour", "1.4.3": "Contrast (minimum)", "1.4.4": "Resize text", "1.4.10": "Reflow", "1.4.11": "Non-text contrast",
    "1.4.12": "Text spacing", "2.1.1": "Keyboard", "2.1.2": "No keyboard trap", "2.2.1": "Timing adjustable", "2.4.1": "Bypass blocks",
    "2.4.2": "Page titled", "2.4.3": "Focus order", "2.4.4": "Link purpose", "2.4.7": "Focus visible", "2.5.3": "Label in name",
    "2.5.8": "Target size (minimum)", "3.1.1": "Language of page", "3.3.2": "Labels or instructions", "4.1.1": "Parsing",
    "4.1.2": "Name, role, value",
}


def esc(t):
    return html.escape(str(t if t is not None else "")).replace("\n", "<br>")


def img(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def wcag_of(tags):
    """['wcag2aa', 'wcag143'] -> ('1.4.3 Contrast (minimum)', 'AA') ; best-practice -> ('Best practice', '')."""
    scs = []
    for t in tags:
        m = re.match(r"wcag(\d)(\d)(\d+)$", t)
        if m:
            sc = "%s.%s.%s" % m.groups()
            scs.append(sc + (" " + SC_NAMES[sc] if sc in SC_NAMES else ""))
    level = "AAA" if "wcag2aaa" in tags else "AA" if any(t in tags for t in ("wcag2aa", "wcag21aa", "wcag22aa")) else \
            "A" if any(t in tags for t in ("wcag2a", "wcag21a")) else ""
    if not scs:
        return "Best practice (not a WCAG rule)", ""
    return ", ".join(scs), level


def who_of(tags):
    return next((WHO[t] for t in tags if t in WHO), "People using assistive technology")


def plain_summary(text):
    text = (text or "").replace("Fix any of the following:", "Fix one of these:").replace("Fix all of the following:", "Fix all of these:")
    return re.sub(r"\s*\n\s*", "\n", text).strip()


# ---------- grouping ----------

def collect(data):
    """Group axe violations by rule across pages; add keyboard and reflow findings as extra issues."""
    issues = {}
    for p in data["pages"]:
        if p.get("error"):
            continue
        for v in p["axe"]["violations"]:
            it = issues.setdefault(v["id"], {"id": v["id"], "impact": v["impact"] or "minor", "tags": v["tags"], "help": v["help"],
                                             "url": v["helpUrl"], "pages": [], "count": 0, "examples": [], "shot": None, "source": "axe"})
            it["count"] += v["count"]
            it["pages"].append(p["name"])
            if SEV_RANK.get(v["impact"], 3) < SEV_RANK.get(it["impact"], 3):
                it["impact"] = v["impact"]
            for n in v["nodes"][:4]:
                if len(it["examples"]) < 6:
                    it["examples"].append({"page": p["name"], "html": n["html"], "summary": plain_summary(n["summary"]),
                                           "where": " > ".join(map(str, n["target"]))})
            if not it["shot"] and v.get("shot"):
                it["shot"], it["shot_page"] = v["shot"], p["name"]
        kb = p.get("keyboard")
        if kb:
            if kb.get("stuck"):
                add_custom(issues, "kb-trap", "critical", "Keyboard focus gets stuck", ["wcag2a", "wcag212", "cat.keyboard"], p,
                           [{"page": p["name"], "html": kb["stuck"]["html"], "summary": "Pressing Tab keeps focus on this element - keyboard users cannot move on.", "where": kb["stuck"]["name"]}],
                           "Make sure Tab and Shift+Tab can always leave the element (widgets that capture keys must release Tab).")
            if kb["no_visible_focus"]:
                add_custom(issues, "kb-focus", "serious", "You cannot see where the keyboard focus is", ["wcag2aa", "wcag247", "cat.keyboard"], p,
                           [{"page": p["name"], "html": x["html"], "summary": "Nothing on screen changed when this got focus.", "where": x["name"], "shot": x["shot"]} for x in kb["no_visible_focus"][:4]],
                           "Do not remove the focus outline (outline: none) without a replacement - add a clear :focus-visible style, e.g. a 2px outline.",
                           count=len(kb["no_visible_focus"]), check=True, caption="the element while it has focus - nothing shows it")
            if kb["missed_count"] and not kb["limit_hit"]:
                add_custom(issues, "kb-missed", "serious", "Some buttons, links or fields cannot be reached with Tab", ["wcag2a", "wcag211", "cat.keyboard"], p,
                           [{"page": p["name"], "html": "<%s> %s" % (m["tag"], m["name"]), "summary": "Visible and clickable, but Tab never reached it.", "where": m["name"]} for m in kb["missed"][:4]],
                           "Use real <button>/<a href> elements, or add tabindex=\"0\" and Enter/Space handling to custom ones.",
                           count=kb["missed_count"], check=True)
            if kb["fake_buttons"]:
                add_custom(issues, "kb-fake", "moderate", "Things look clickable but are not real buttons or links", ["wcag2a", "wcag211", "wcag412", "cat.keyboard"], p,
                           [{"page": p["name"], "html": f["html"], "summary": "Has a pointer cursor but no button/link role and no tabindex - a keyboard or screen reader user may not be able to use it.", "where": f["name"]} for f in kb["fake_buttons"][:4]],
                           "If it does something on click, make it a <button> (or <a href> if it opens a page).",
                           count=len(kb["fake_buttons"]), check=True)
        rf = p.get("reflow")
        if rf and rf["sideways"]:
            add_custom(issues, "reflow", "moderate", "The page scrolls sideways on a narrow screen", ["wcag21aa", "wcag1410", "cat.structure"], p,
                       [{"page": p["name"], "html": "", "summary": "At 320px wide the page is %dpx wide - people who zoom in to 400%% must scroll left and right to read." % rf["scroll_width"], "where": "whole page", "shot": rf["shot"]}],
                       "Use flexible layouts (wrap, max-width:100%, responsive tables) so content fits 320px without sideways scrolling.",
                       who="People with low vision who zoom in, and phone users", caption="the page at 320px wide")
    return sorted(issues.values(), key=lambda i: (SEV_RANK.get(i["impact"], 3), -i["count"]))


def add_custom(issues, key, impact, title, tags, page, examples, fix, count=None, check=False, who=None, caption=None):
    it = issues.setdefault(key, {"id": key, "impact": impact, "tags": tags, "help": title, "url": None, "pages": [], "count": 0,
                                 "examples": [], "shot": None, "source": "toolkit", "fix": fix, "check": check, "who": who,
                                 "caption": caption})
    it["count"] += count if count is not None else len(examples)
    it["pages"].append(page["name"])
    for e in examples:
        if len(it["examples"]) < 6:
            it["examples"].append(e)
        if not it["shot"] and e.get("shot"):
            it["shot"], it["shot_page"] = e["shot"], page["name"]


def verdict(issues, data):
    checked = [p for p in data["pages"] if not p.get("error")]
    by = {s[0]: sum(1 for i in issues if i["impact"] == s[0]) for s in SEVERITY}
    if not checked:
        return "blocked", "COULD NOT TEST", "No page could be opened - see the pages list.", by
    if by["critical"] or by["serious"]:
        parts = " and ".join("%d %s" % (by[k], k) for k in ("critical", "serious") if by[k])
        n = by["critical"] + by["serious"]
        return "fail", "NOT ACCESSIBLE YET", "%s problem%s %s or seriously slow down some people - fix before release." % (
            parts, "" if n == 1 else "s", "stops" if n == 1 else "stop"), by
    if by["moderate"] or by["minor"]:
        return "warnv", "NEEDS FIXES", "No critical or serious problems; %d smaller problem%s to fix." % (
            by["moderate"] + by["minor"], "" if by["moderate"] + by["minor"] == 1 else "s"), by
    return "pass", "NO PROBLEMS FOUND", "The automated checks found nothing - a short manual check is still needed (see the end).", by


# ---------- HTML ----------

def issue_card(i):
    sev = next(s for s in SEVERITY if s[0] == i["impact"])
    title, fix = PLAIN.get(i["id"], (i["help"], None))
    fix = i.get("fix") or fix or "See the rule page for how to fix it."
    wcag, level = wcag_of(i["tags"])
    pages = sorted(set(i["pages"]))
    shot = img(i.get("shot"))
    ex = "".join('<li><div class="ex-where"><b>%s</b> · %s</div>%s<div class="ex-why">%s</div></li>' % (
        esc(e["page"]), esc(e["where"]), '<code>%s</code>' % esc(e["html"]) if e.get("html") else "", esc(e["summary"]))
        for e in i["examples"])
    more = i["count"] - len(i["examples"])
    return (
        '<article class="card issue %s" data-sev="%s"><div class="ih"><span class="pill %s">%s %s</span>'
        '<h3>%s</h3><span class="count">%d element%s · %d page%s</span></div>'
        '<div class="igrid"><div><dl>'
        '<dt>Who it affects</dt><dd>%s</dd>'
        '<dt>WCAG rule</dt><dd>%s%s</dd>'
        '<dt>How to fix</dt><dd>%s%s</dd>'
        '<dt>Pages</dt><dd>%s</dd></dl>%s</div>%s</div>'
        '<details><summary>Examples (%d%s)</summary><ul class="ex">%s</ul></details></article>'
        % (sev[2], i["impact"], sev[2], sev[3], sev[1], esc(title), i["count"], "" if i["count"] == 1 else "s",
           len(pages), "" if len(pages) == 1 else "s", esc(i.get("who") or who_of(i["tags"])), esc(wcag), (" · level " + level) if level else "",
           esc(fix), ' <a href="%s" target="_blank" rel="noopener">Rule details ↗</a>' % esc(i["url"]) if i.get("url") else "",
           esc(", ".join(pages)),
           '<p class="checknote">🔎 Found by the toolkit\'s own check - confirm by hand before reporting.</p>' if i.get("check") else "",
           '<figure><img src="%s" alt="%s - failing element outlined in red" loading="lazy"><figcaption>%s - %s</figcaption></figure>' % (
               shot, esc(title), esc(i.get("shot_page", "")), esc(i.get("caption") or "outlined in red")) if shot else "",
           len(i["examples"]), (", %d more" % more) if more > 0 else "", ex))


def page_rows(data, issues):
    rows = []
    for p in data["pages"]:
        if p.get("error"):
            rows.append('<tr><td><b>%s</b><div class="muted small">%s</div></td><td colspan="5"><span class="pill none">⚠️ Could not check</span> %s</td></tr>'
                        % (esc(p["name"]), esc(p["url"]), esc(p["error"])))
            continue
        v = p["axe"]["violations"]
        sev = {s[0]: sum(x["count"] for x in v if x["impact"] == s[0]) for s in SEVERITY}
        kb, rf = p.get("keyboard"), p.get("reflow")
        moved = p.get("final_url") and p["final_url"].rstrip("/") != p["url"].rstrip("/")
        kb_txt = "-" if not kb else ("⛔ focus gets stuck" if kb["stuck"] else "reached %d of %d%s%s" % (
            kb["reached"], kb["interactive"], "" if not kb["no_visible_focus"] else " · %d without visible focus" % len(kb["no_visible_focus"]),
            " (stopped after %d Tabs)" % kb["tabs"] if kb["limit_hit"] else ""))
        rows.append('<tr><td><b>%s</b><div class="muted small">%s%s</div></td><td>%d rules passed</td><td class="sevs">%s</td><td>%s</td><td>%s</td></tr>' % (
            esc(p["name"]), esc(p["url"]), '<br>⚠️ ended on %s - signed in?' % esc(p["final_url"]) if moved else "",
            p["axe"]["passes"], " ".join('<span class="pill %s">%s %d</span>' % (s[2], s[3], sev[s[0]]) for s in SEVERITY if sev[s[0]]) or '<span class="pill good">✅ none</span>',
            esc(kb_txt), "-" if not rf else ("↔️ scrolls sideways" if rf["sideways"] else "✅ fits")))
    return "".join(rows)


def needs_review(data):
    seen = {}
    for p in data["pages"]:
        for v in (p.get("axe") or {}).get("incomplete", []):
            it = seen.setdefault(v["id"], {"help": v["help"], "count": 0, "pages": set(), "url": v["helpUrl"]})
            it["count"] += v["count"]
            it["pages"].add(p["name"])
    if not seen:
        return '<p class="muted">Nothing - axe could decide every check by itself.</p>'
    return '<ul class="slist card">%s</ul>' % "".join(
        '<li><b>%s</b> <span class="muted">· %d element%s on %s</span> <a href="%s" target="_blank" rel="noopener">↗</a></li>' % (
            esc(PLAIN.get(k, (v["help"],))[0]), v["count"], "" if v["count"] == 1 else "s", esc(", ".join(sorted(v["pages"]))), esc(v["url"]))
        for k, v in sorted(seen.items(), key=lambda kv: -kv[1]["count"]))


def build_html(data, issues):
    css, word, reason, by = verdict(issues, data)
    checked = [p for p in data["pages"] if not p.get("error")]
    passed_rules = sum(p["axe"]["passes"] for p in checked)
    failed_rules = sum(len(p["axe"]["violations"]) for p in checked)
    rate = int(round(100.0 * passed_rules / (passed_rules + failed_rules))) if passed_rules + failed_rules else 0
    try:
        when = datetime.datetime.fromisoformat(data["started"]).strftime("%d %b %Y, %H:%M")
    except ValueError:
        when = data["started"]
    meta = "".join("<span>%s</span>" % esc(x) for x in [when, data["standard"], "%d page%s" % (len(data["pages"]), "" if len(data["pages"]) == 1 else "s"),
                                                         data["browser"], "signed in" if data["signed_in"] else "signed out",
                                                         "axe-core " + (checked[0]["axe"]["version"] if checked else "")])
    kpis = "".join('<div class="kpi %s"><div class="k-lbl">%s %s</div><div class="k-num">%d</div><div class="k-sub">%s</div></div>' % (
        s[2], s[3], s[1], by[s[0]], esc(s[4])) for s in SEVERITY)
    filters = '<button class="btn on" data-f="all">All (%d)</button>' % len(issues) + "".join(
        '<button class="btn" data-f="%s">%s %s (%d)</button>' % (s[0], s[3], s[1], by[s[0]]) for s in SEVERITY if by[s[0]])
    cards = "".join(issue_card(i) for i in issues) or '<div class="card empty">✅ No problems found by the automated checks.</div>'
    return (TEMPLATE.replace("$title", esc(data["title"])).replace("$meta", meta).replace("$vcss", css).replace("$vword", esc(word))
            .replace("$vreason", esc(reason)).replace("$rate", str(rate)).replace("$passed", str(passed_rules)).replace("$failed", str(failed_rules))
            .replace("$kpis", kpis).replace("$filters", filters).replace("$cards", cards).replace("$pages", page_rows(data, issues))
            .replace("$review", needs_review(data)).replace("$generated", datetime.datetime.now().strftime("%d %b %Y %H:%M")))


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accessibility report - $title</title>
<style>
 :root {
  --bg:#f3f4f1; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --ink3:#6f6e69; --line:#e1e0d9;
  --ring:rgba(11,11,11,.10); --shadow:0 1px 2px rgba(0,0,0,.05), 0 4px 16px rgba(0,0,0,.04);
  --good:#0ca30c; --good-bg:#e6f5e6; --warn:#fab219; --warn-bg:#fdf3dc; --serious:#ec835a; --serious-bg:#fbeae2;
  --crit:#d03b3b; --crit-bg:#f8e3e3; --none:#c3c2b7; --none-bg:#efeee9; --accent:#2a78d6; --hero:#0f1b2d; --hero2:#1d3557;
 }
 @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#121211; --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --ink3:#9a998f; --line:#2c2c2a;
  --ring:rgba(255,255,255,.10); --shadow:none; --good-bg:#14301a; --warn-bg:#352a10; --serious-bg:#3a2218;
  --crit-bg:#3a1a1a; --none:#4a4a46; --none-bg:#242422; --accent:#3987e5; --hero:#0b1422; --hero2:#16294a; } }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
 a { color:var(--accent); }
 .wrap { max-width:1160px; margin:0 auto; padding:0 16px; } .muted { color:var(--ink3); } .small { font-size:12.5px; }
 h2 { font-size:22px; margin:0 0 4px; } .lead { color:var(--ink2); margin:0 0 14px; } section { padding:30px 0 4px; }
 .card { background:var(--surface); border:1px solid var(--ring); border-radius:14px; box-shadow:var(--shadow); }
 header.hero { background:linear-gradient(135deg, var(--hero), var(--hero2)); color:#f4f6fb; padding:30px 0 34px; }
 .hgrid { display:grid; grid-template-columns:1fr auto; gap:24px; align-items:center; }
 .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:12px; opacity:.75; font-weight:600; }
 .hero h1 { font-size:32px; margin:4px 0 10px; }
 .meta { display:flex; flex-wrap:wrap; gap:8px; }
 .meta span { background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.14); padding:4px 10px; border-radius:999px; font-size:13px; }
 .verdict { margin-top:16px; display:inline-flex; gap:12px; align-items:flex-start; padding:12px 16px; border-radius:12px;
   background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.16); max-width:720px; }
 .vdot { width:14px; height:14px; border-radius:50%; margin-top:5px; flex:none; box-shadow:0 0 0 4px rgba(255,255,255,.12); }
 .verdict.fail .vdot { background:var(--crit); } .verdict.warnv .vdot { background:var(--warn); }
 .verdict.blocked .vdot { background:var(--none); } .verdict.pass .vdot { background:var(--good); }
 .vh { font-weight:800; font-size:18px; letter-spacing:.02em; } .vr { opacity:.9; font-size:14px; }
 .gauge { width:150px; height:150px; border-radius:50%; display:grid; place-items:center;
   background:conic-gradient(var(--good) calc($rate * 3.6deg), rgba(255,255,255,.14) 0); }
 .gauge > div { width:118px; height:118px; border-radius:50%; background:var(--hero); display:grid; place-items:center; text-align:center; }
 .gauge b { font-size:32px; line-height:1; display:block; } .gauge small { font-size:11.5px; opacity:.85; }
 .kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-top:22px; }
 .kpi { background:var(--surface); border:1px solid var(--ring); border-radius:14px; padding:14px 16px 14px 18px; position:relative; overflow:hidden; box-shadow:var(--shadow); }
 .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:var(--line); }
 .kpi.crit::before { background:var(--crit); } .kpi.serious::before { background:var(--serious); } .kpi.warn::before { background:var(--warn); } .kpi.none::before { background:var(--none); }
 .k-lbl { color:var(--ink2); font-size:13px; font-weight:600; } .k-num { font-size:32px; font-weight:800; font-variant-numeric:tabular-nums; } .k-sub { color:var(--ink3); font-size:12.5px; }
 .pill { display:inline-flex; gap:5px; align-items:center; font-size:12.5px; font-weight:600; padding:2px 9px; border-radius:999px; white-space:nowrap; }
 .pill.good { background:var(--good-bg); } .pill.crit { background:var(--crit-bg); } .pill.serious { background:var(--serious-bg); }
 .pill.warn { background:var(--warn-bg); } .pill.none { background:var(--none-bg); }
 .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
 .btn { font:inherit; font-size:13px; border:1px solid var(--line); background:var(--surface); color:var(--ink); border-radius:8px; padding:6px 12px; cursor:pointer; }
 .btn.on { border-color:var(--ink); font-weight:600; }
 .issue { padding:16px 20px; margin-bottom:14px; border-left:4px solid var(--line); }
 .issue.crit { border-left-color:var(--crit); } .issue.serious { border-left-color:var(--serious); } .issue.warn { border-left-color:var(--warn); } .issue.none { border-left-color:var(--none); }
 .ih { display:flex; flex-wrap:wrap; gap:10px; align-items:center; } .ih h3 { margin:0; font-size:17px; flex:1; min-width:220px; }
 .count { color:var(--ink3); font-size:13px; }
 .igrid { display:grid; grid-template-columns:1fr minmax(0, 420px); gap:18px; margin-top:10px; }
 dl { margin:0; display:grid; grid-template-columns:130px 1fr; gap:6px 12px; font-size:14px; } dt { color:var(--ink3); font-weight:600; } dd { margin:0; }
 figure { margin:0; } figure img { width:100%; max-height:240px; object-fit:contain; object-position:left top; border-radius:8px; border:1px solid var(--line); background:#fff; }
 figcaption { font-size:12px; color:var(--ink3); margin-top:4px; }
 .checknote { font-size:13px; color:var(--ink2); margin:10px 0 0; }
 details { margin-top:10px; font-size:13.5px; } summary { cursor:pointer; color:var(--ink2); font-weight:600; }
 .ex { margin:8px 0 0; padding-left:18px; } .ex li { margin:0 0 10px; }
 .ex code { display:block; font:12px ui-monospace, Menlo, monospace; background:var(--none-bg); padding:6px 8px; border-radius:6px; margin:4px 0; white-space:pre-wrap; word-break:break-all; }
 .ex-why { white-space:pre-line; color:var(--ink2); font-size:13px; }
 .empty { padding:16px 20px; }
 .tablewrap { overflow-x:auto; } table { width:100%; border-collapse:collapse; font-size:13.5px; }
 th { text-align:left; font-size:12.5px; color:var(--ink2); padding:10px; border-bottom:1px solid var(--line); }
 td { padding:10px; border-bottom:1px solid var(--line); vertical-align:top; } .sevs .pill { margin:0 4px 4px 0; }
 .slist { list-style:none; margin:0; padding:6px 18px; } .slist li { padding:9px 0; border-bottom:1px solid var(--line); } .slist li:last-child { border:0; }
 .note { padding:16px 20px; } .note ul { margin:6px 0 0; padding-left:20px; } .note li { margin:4px 0; }
 footer { color:var(--ink3); font-size:12.5px; padding:30px 0 40px; }
 @media (max-width:760px) { .hgrid, .igrid { grid-template-columns:1fr; } .gauge { display:none; } dl { grid-template-columns:1fr; } .hero h1 { font-size:24px; } }
</style></head><body>
<header class="hero"><div class="wrap hgrid"><div>
 <div class="eyebrow">Accessibility report</div>
 <h1>$title</h1>
 <div class="meta">$meta</div>
 <div class="verdict $vcss"><span class="vdot"></span><div><div class="vh">$vword</div><div class="vr">$vreason</div></div></div>
 </div><div class="gauge" role="img" aria-label="$rate percent of automated rule checks passed"><div><div><b>$rate%</b><small>rule checks passed<br>$passed passed · $failed failed</small></div></div></div>
</div></header>
<main class="wrap">
 <div class="kpis">$kpis</div>
 <section><h2>Problems to fix</h2>
  <p class="lead">Grouped by problem across all pages, most serious first. Each one says who it hurts and how to fix it; open the examples for the exact elements.</p>
  <div class="filters">$filters</div>$cards</section>
 <section><h2>Pages checked</h2>
  <div class="tablewrap card"><table><thead><tr><th>Page</th><th>Automated rules</th><th>Problems (elements)</th><th>Keyboard (Tab)</th><th>Phone width 320px</th></tr></thead><tbody>$pages</tbody></table></div></section>
 <section><h2>Needs a human look</h2>
  <p class="lead">axe could not decide these by itself (for example text on a background image). Check them by eye.</p>$review</section>
 <section><div class="card note"><b>What this check covers - and what it does not</b><ul>
  <li>Automated tools find roughly a third to a half of accessibility problems. A clean result here is a good start, not proof the site is accessible.</li>
  <li><b>Still check by hand:</b> use the site with a screen reader (VoiceOver on Mac: Cmd+F5; NVDA on Windows) for one main journey; use it with the keyboard only; zoom the browser to 200%; check videos have captions; check error messages are clear.</li>
  <li>Only what is on screen when the page loads is checked - dialogs, menus and later steps need their own pages or a manual look.</li>
  <li>"Cannot see the focus" and "cannot be reached with Tab" come from the toolkit's own keyboard walk; confirm them by pressing Tab yourself.</li>
 </ul></div></section>
 <footer>Made by qa-toolkit with axe-core (Deque Systems, MPL-2.0) · $generated</footer>
</main>
<script>
 document.querySelectorAll(".filters .btn").forEach(function (b) {
  b.addEventListener("click", function () {
   document.querySelectorAll(".filters .btn").forEach(function (x) { x.classList.toggle("on", x === b); });
   document.querySelectorAll("article.issue").forEach(function (a) { a.hidden = b.dataset.f !== "all" && a.dataset.sev !== b.dataset.f; });
  });
 });
</script>
</body></html>
"""


# ---------- Excel ----------

def write_excel(path, data, issues):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return None
    fill = {"critical": "F8E3E3", "serious": "FBEAE2", "moderate": "FDF3DC", "minor": "EFEEE9"}
    wb = Workbook()
    ws = wb.active
    ws.title = "Problems"
    ws.append(["Severity", "Problem", "Who it affects", "WCAG rule", "Level", "Elements", "Pages", "How to fix", "Rule details"])
    for i in issues:
        title, fix = PLAIN.get(i["id"], (i["help"], None))
        wcag, level = wcag_of(i["tags"])
        ws.append([i["impact"].capitalize(), title, i.get("who") or who_of(i["tags"]), wcag, level, i["count"], ", ".join(sorted(set(i["pages"]))),
                   i.get("fix") or fix or "", i.get("url") or ""])
        ws.cell(ws.max_row, 1).fill = PatternFill("solid", fgColor=fill.get(i["impact"], "EFEEE9"))
    ws2 = wb.create_sheet("Elements")
    ws2.append(["Severity", "Problem", "Page", "Where", "HTML", "What is wrong"])
    for i in issues:
        title = PLAIN.get(i["id"], (i["help"],))[0]
        for e in i["examples"]:
            ws2.append([i["impact"].capitalize(), title, e["page"], e["where"], e.get("html", ""), e["summary"]])
    ws3 = wb.create_sheet("Pages")
    ws3.append(["Page", "URL", "Rules passed", "Problem rules", "Problem elements", "Keyboard reached", "Interactive elements", "No visible focus", "Scrolls sideways at 320px", "Note"])
    for p in data["pages"]:
        if p.get("error"):
            ws3.append([p["name"], p["url"], "", "", "", "", "", "", "", "Could not check: " + p["error"]])
            continue
        kb, rf = p.get("keyboard") or {}, p.get("reflow") or {}
        ws3.append([p["name"], p["url"], p["axe"]["passes"], len(p["axe"]["violations"]), sum(v["count"] for v in p["axe"]["violations"]),
                    kb.get("reached", ""), kb.get("interactive", ""), len(kb.get("no_visible_focus", [])) if kb else "",
                    ("Yes" if rf.get("sideways") else "No") if rf else "", ""])
    for sheet, widths in ((ws, [10, 44, 36, 30, 7, 9, 30, 60, 40]), (ws2, [10, 40, 18, 40, 60, 70]), (ws3, [22, 44, 12, 12, 14, 14, 14, 14, 18, 40])):
        for c in sheet[1]:
            c.font = Font(bold=True)
        for idx, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + idx)].width = w
        for row in sheet.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    wb.save(path)
    return path


def main():
    ap = argparse.ArgumentParser(description="Build the accessibility report")
    ap.add_argument("--out", default="reports/a11y")
    args = ap.parse_args()
    src = os.path.join(args.out, "a11y-results.json")
    if not os.path.isfile(src):
        sys.exit("No %s - run a11y_check.py first." % src)
    with open(src) as f:
        data = json.load(f)
    issues = collect(data)
    page = os.path.join(args.out, "a11y-report.html")
    with open(page, "w", encoding="utf-8") as f:
        f.write(build_html(data, issues))
    css, word, reason, by = verdict(issues, data)
    print("Verdict: %s - %s" % (word, reason))
    print("Report written: " + os.path.abspath(page))
    xl = write_excel(os.path.join(args.out, "a11y-report.xlsx"), data, issues)
    if xl:
        print("Excel report written: " + os.path.abspath(xl))


if __name__ == "__main__":
    main()
