"""Repository paths for the tests (so `parents[n]` is not repeated everywhere)."""
from __future__ import annotations

import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SRC = BACKEND / "src" / "app"
FRONTEND = BACKEND.parent / "frontend"


def frontend_js() -> str:
    """All the interface's JavaScript (core/ + features/) as a single text, to check its contents."""
    return "\n".join(f.read_text(encoding="utf-8") for f in sorted((FRONTEND / "static" / "js").rglob("*.js")))


def frontend_css() -> str:
    """All the interface's CSS (by area) as a single text, in the cascade order of the HTML."""
    import re
    html = (FRONTEND / "templates" / "index.html").read_text(encoding="utf-8")
    sheets = re.findall(r'href="(/static/css/[^"?]+)', html)
    return "\n".join((FRONTEND / h.removeprefix("/")).read_text(encoding="utf-8") for h in sheets)
