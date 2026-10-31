"""Rutas del repositorio para los tests (para no repetir `parents[n]` por todas partes)."""
from __future__ import annotations

import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SRC = BACKEND / "src" / "app"
FRONTEND = BACKEND.parent / "frontend"


def frontend_js() -> str:
    """Todo el JavaScript de la interfaz (core/ + features/) como un solo texto, para comprobar su contenido."""
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted((FRONTEND / "static" / "js").rglob("*.js")))


def frontend_css() -> str:
    """Todo el CSS de la interfaz (por áreas) como un solo texto, en el orden de la cascada del HTML."""
    import re
    html = (FRONTEND / "templates" / "index.html").read_text(encoding="utf-8")
    hojas = re.findall(r'href="(/static/css/[^"?]+)', html)
    return "\n".join((FRONTEND / h.removeprefix("/")).read_text(encoding="utf-8") for h in hojas)
