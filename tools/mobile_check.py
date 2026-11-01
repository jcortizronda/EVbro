#!/usr/bin/env python3
"""Checks that no screen overflows a phone. Requires Playwright (not in requirements-dev).

    pip install playwright && playwright install chromium     # once
    EVBRO_URL=http://127.0.0.1:8080 EVBRO_USER=juan EVBRO_PASSWORD=... python tools/mobile_check.py

Walks through every tab and every Ajustes sub-tab at 360, 390 and 430 px and fails (exit code 1) if any
visible element goes past the right edge. The two checks below were a real headache: the header was
444 px wide on a 390 px phone and the Viajes and Mantenimiento tables 1,400–1,600 px.

With `--shots DIR` it also saves a screenshot of each screen to review by eye. Screenshots are still
needed: this script detects overflows, not whether something looks ugly.
"""
from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

TABS = ["overview", "charges", "climate", "drives", "refuels", "more", "vehicle", "maintenance", "geofences", "settings"]
SETTINGS = ["account", "accounts", "notifs", "connections", "theme", "system"]
WIDTHS = [360, 390, 430]

# Visible elements whose right edge goes past the viewport, except those that scroll on purpose
# (sub-tabs, maps, maintenance matrix) or are visually hidden for accessibility.
FIND_OVERFLOW = """(width) => {
  const bad = [];
  for (const e of document.querySelectorAll('body *')) {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (e.closest('.sub-nav-bar, .leaflet-container, thead, .table-responsive-matrix, .modal-overlay:not(.open)')) continue;
    if (r.right > width + 1) bad.push(`${e.tagName.toLowerCase()}#${e.id}.${String(e.className && e.className.baseVal === undefined ? e.className : '').trim().replace(/\\s+/g, '.')} → ${Math.round(r.right)}px`);
  }
  return bad.slice(0, 6);
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="folder where a screenshot of each screen is saved")
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
            ctx = browser.new_context(viewport={"width": width, "height": 844}, has_touch=True, locale="es-ES")
            login = ctx.request.post(f"{base}/api/auth/login", headers={"Origin": base},
                                     data={"username": user, "password": password})
            if not login.ok:
                print(f"Login failed ({login.status})", file=sys.stderr)
                return 2
            page = ctx.new_page()
            page.goto(base)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1200)
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
                    page.screenshot(path=f"{args.shots}/{width}-{name}.png", full_page=True)
            ctx.close()
        browser.close()

    for f in failures:
        print("✘", f)
    print(f"{len(failures)} screen(s) with overflow" if failures else "✔ no screen overflows at 360, 390 and 430 px")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
