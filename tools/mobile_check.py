#!/usr/bin/env python3
"""Comprueba que ninguna pantalla se sale de un móvil. Requiere Playwright (no está en requirements-dev).

    pip install playwright && playwright install chromium     # una vez
    EVBRO_URL=http://127.0.0.1:8080 EVBRO_USER=juan EVBRO_PASSWORD=... python tools/mobile_check.py

Recorre todas las pestañas y subpestañas de Ajustes a 360, 390 y 430 px y falla (código 1) si algún
elemento visible sobrepasa el borde derecho. Los dos tests de abajo existían como dolor de cabeza
real: la cabecera medía 444 px en un móvil de 390 y las tablas de Viajes y Mantenimiento 1.400–1.600 px.

Con `--shots CARPETA` guarda además una captura de cada pantalla para revisarla a ojo. Las capturas
siguen siendo necesarias: este script detecta desbordes, no que algo se vea feo.
"""
from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright

TABS = ["overview", "charges", "climate", "drives", "refuels", "more", "vehicle", "maintenance", "geofences", "settings"]
SETTINGS = ["account", "polling", "notifs", "theme", "system"]
WIDTHS = [360, 390, 430]

# Elementos visibles cuyo borde derecho pasa del viewport, salvo los que se desplazan a propósito
# (subpestañas, mapas, matriz de mantenimiento) o están ocultos de forma accesible.
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
    ap.add_argument("--shots", help="carpeta donde guardar una captura por pantalla")
    args = ap.parse_args()
    base = os.environ.get("EVBRO_URL", "http://127.0.0.1:8080").rstrip("/")
    user, password = os.environ.get("EVBRO_USER", ""), os.environ.get("EVBRO_PASSWORD", "")
    if not (user and password):
        print("Define EVBRO_USER y EVBRO_PASSWORD", file=sys.stderr)
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
                print(f"Login fallido ({login.status})", file=sys.stderr)
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
                    failures.append(f"{width}px · {name}: página +{page_wide}px · {bad}")
                if args.shots:
                    page.screenshot(path=f"{args.shots}/{width}-{name}.png", full_page=True)
            ctx.close()
        browser.close()

    for f in failures:
        print("✘", f)
    print(f"{len(failures)} pantalla(s) con desborde" if failures else "✔ ninguna pantalla se sale en 360, 390 y 430 px")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
