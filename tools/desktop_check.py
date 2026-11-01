#!/usr/bin/env python3
"""Checks the DESKTOP interface (mouse, wide window): the sidebar is there, the dock is not, nothing overflows.

    pip install playwright && playwright install chromium     # once
    EVBRO_URL=http://127.0.0.1:8080 EVBRO_USER=juan EVBRO_PASSWORD=... python tools/desktop_check.py [--shots DIR] [--theme dark]

Walks every section (and the Ajustes sub-sections) at 1024, 1366 and 1920 px with a mouse pointer and fails (exit 1)
if the sidebar is missing, the bottom dock shows, or a visible element passes the right edge. `--shots DIR` saves a
full-page screenshot per screen: the script finds overflows, not ugliness, so look at them.
"""
from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

TABS = ["overview", "charges", "climate", "drives", "timeline", "stats", "refuels", "maintenance", "expenses", "costs", "vehicle", "geofences", "settings"]
SETTINGS = ["account", "accounts", "notifs", "theme", "system"]
WIDTHS = [1024, 1366, 1920]

FIND_OVERFLOW = """(width) => {
  const bad = [];
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (e.closest('.leaflet-container, thead, .table-responsive-matrix, .modal-overlay:not(.open)')) continue;
    if (r.right > width + 1) bad.push(`${e.tagName.toLowerCase()}#${e.id}.${String(e.className && e.className.baseVal === undefined ? e.className : '').trim().replace(/\\s+/g, '.')} → ${Math.round(r.right)}px`);
  }
  return bad.slice(0, 6);
}"""
VISIBLE = "(sel) => { const e = document.querySelector(sel); return !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0; }"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="folder for one screenshot per screen")
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    args = ap.parse_args()
    base = os.environ.get("EVBRO_URL", "http://127.0.0.1:8080").rstrip("/")
    user, password = os.environ.get("EVBRO_USER", ""), os.environ.get("EVBRO_PASSWORD", "")
    if not (user and password):
        print("Set EVBRO_USER and EVBRO_PASSWORD", file=sys.stderr)
        return 2
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)

    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in WIDTHS:
            ctx = browser.new_context(viewport={"width": width, "height": 900}, has_touch=False, locale="es-ES",
                                      color_scheme=args.theme)
            login = ctx.request.post(f"{base}/api/auth/login", headers={"Origin": base},
                                     data={"username": user, "password": password})
            if not login.ok:
                print(f"Login failed ({login.status})", file=sys.stderr)
                return 2
            page = ctx.new_page()
            page.add_init_script(f"try {{ localStorage.setItem('evbro_theme', '{args.theme}'); }} catch (e) {{}}")
            page.goto(base)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1200)
            if not page.evaluate(VISIBLE, ".desktop-sidebar"):
                failures.append(f"{width}px: the sidebar is not visible")
            if page.evaluate(VISIBLE, ".native-bottom-dock"):
                failures.append(f"{width}px: the bottom dock is visible with a mouse")
            views = [(t, None) for t in TABS if t != "settings"] + [("settings", s) for s in SETTINGS]
            for tab, sub in views:
                page.evaluate(f"switchTab('{tab}')")
                if sub:
                    page.evaluate(f"switchSettingsSubTab('{sub}')")
                page.wait_for_timeout(700)
                name = f"{tab}{'-' + sub if sub else ''}"
                page_wide = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                bad = page.evaluate(FIND_OVERFLOW, width)
                if page_wide > 0 or bad:
                    failures.append(f"{width}px · {name}: page +{page_wide}px · {bad}")
                if args.shots:
                    page.screenshot(path=f"{args.shots}/{width}-{args.theme}-{name}.png", full_page=True)
            ctx.close()
        browser.close()

    for f in failures:
        print("✘", f)
    print(f"{len(failures)} problem(s)" if failures else "✔ desktop correct at 1024, 1366 and 1920 px")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
