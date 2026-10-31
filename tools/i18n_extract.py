#!/usr/bin/env python3
"""Collect every user-visible Spanish text of the web app, so the dictionaries in `frontend/static/i18n/` can be checked for gaps.

Sources (all keyed by the exact Spanish text, whitespace collapsed):
  * text nodes and translatable attributes (placeholder, title, aria-label, alt, data-tooltip) of `frontend/templates/index.html`;
  * `t('…')` / `t("…")` calls in `frontend/static/js/**/*.js`;
  * plain text between tags inside the template literals of those JS files (`>Fecha<`), which the DOM translator picks up at runtime.

Usage:  python tools/i18n_extract.py            # prints how many keys each source has and how many are missing per language
        python tools/i18n_extract.py --missing  # also prints the missing keys (to fill the dictionaries)
        python tools/i18n_extract.py --json     # dumps every key as a JSON object (key → "") on stdout
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "frontend" / "templates" / "index.html"
JS_DIR = ROOT / "frontend" / "static" / "js"
DICT_DIR = ROOT / "frontend" / "static" / "i18n"
LANGUAGES = ("en", "pt")
ATTRS = ("placeholder", "title", "aria-label", "alt", "data-tooltip", "data-empty")
SKIP_TAGS = {"script", "style", "code", "svg", "path", "noscript"}
_WS = re.compile(r"\s+")
_LETTERS = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]")
_T_CALL = re.compile(r"""\bt\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
# Plain quoted strings the JS hands to the DOM or to a dialog (showToast('Guardado'), el.textContent = 'Sin datos'): the DOM
# translator picks them up at runtime, so they need a dictionary entry too. Heuristic, see looks_like_ui_text().
_UI_STRING = re.compile(r"""(?<![\w$])(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")""")
_CODEISH = re.compile(r"[<>=/\\{}\[\]$#@|^~`]|\.js\b|\.css\b|\.png\b|\.svg\b|^[a-z]+(?:[A-Z][a-z]+)+$|^[a-z0-9_-]+$|^[A-Z0-9_]+$|^\d"
                      r"|^M[\d.]|^\([a-z-]+:")                 # SVG path data, media queries
_ESCAPES = {"n": "\n", "t": "\t", "'": "'", '"': '"', "\\": "\\"}


def _unescape_js(raw: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def looks_like_ui_text(text: str) -> bool:
    """A sentence or label for a person: letters, usually a space or Spanish punctuation, and nothing that smells of code."""
    t = text.strip()
    if len(t) < 3 or not _LETTERS.search(t) or _CODEISH.search(t):
        return False
    if " " not in t and not re.search(r"[áéíóúñÁÉÍÓÚÑ¿¡…]", t):
        return False                                      # single words without accents are mostly identifiers or CSS classes
    return t[0].isupper() or t[0] in "¿¡«(" or bool(re.search(r"[áéíóúñ]", t))
_TEMPLATE = re.compile(r"`((?:[^`\\]|\\.)*)`", re.S)
_BETWEEN_TAGS = re.compile(r">([^<>]*?)<")
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def translatable(text: str) -> bool:
    """Something a person reads: has letters and is not a bare placeholder, number or symbol."""
    t = norm(text)
    return bool(t) and bool(_LETTERS.search(t)) and not t.startswith("${") and "{{" not in t


_VOID = {"input", "img", "br", "hr", "meta", "link", "source", "wbr", "col", "area", "base", "track", "embed", "param"}


class _Collector(HTMLParser):
    """Text nodes and attributes outside <script>/<style>/<code>/<svg> and outside any element marked `data-i18n-skip`
    (the same rules as core/i18n.js)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.keys: dict[str, str] = {}
        self._stack: list[tuple[str, bool]] = []          # (tag, skipped) of the open elements

    @property
    def _skip(self) -> bool:
        return any(skipped for _, skipped in self._stack)

    def handle_starttag(self, tag, attrs):
        skipped = tag in SKIP_TAGS or any(name == "data-i18n-skip" for name, _ in attrs)
        if not self._skip:
            for name, value in attrs:
                if name in ATTRS and value and translatable(value):
                    self.keys.setdefault(norm(value), f"attr {name}")
        if tag not in _VOID:
            self._stack.append((tag, skipped))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self._stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if not self._skip and translatable(data):
            self.keys.setdefault(norm(data), "html")


ADMIN_INDEX = ROOT / "frontend" / "admin" / "index.html"
ADMIN_JS = ROOT / "frontend" / "admin" / "assets"


def html_keys() -> dict[str, str]:
    c = _Collector()
    for page in (INDEX, ADMIN_INDEX):                       # the app and the admin console share the dictionaries
        c.feed(_JINJA.sub("", page.read_text(encoding="utf-8")))
    return c.keys


def js_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for path in sorted([*JS_DIR.rglob("*.js"), *ADMIN_JS.glob("*.js")]):
        src = path.read_text(encoding="utf-8")
        for m in _T_CALL.finditer(src):
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            keys.setdefault(norm(_unescape_js(raw)), f"t() {path.name}")
        for m in _UI_STRING.finditer(src):
            line_start = src.rfind("\n", 0, m.start()) + 1
            if "console." in src[line_start:m.start()]:
                continue                                        # log lines are for the developer
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            text = _unescape_js(raw)
            if "<" in text and ">" in text:                     # HTML built in a plain string: its texts between tags
                for seg in _BETWEEN_TAGS.finditer(text):
                    piece = seg.group(1)
                    if translatable(piece) and not any(code in piece for code in ("(", ")", "=>", ";")):
                        keys.setdefault(norm(piece), f"string {path.name}")
            elif looks_like_ui_text(text):
                keys.setdefault(norm(text), f"string {path.name}")
        for tpl in _TEMPLATE.finditer(src):
            for seg in _BETWEEN_TAGS.finditer(tpl.group(1)):
                text = seg.group(1)
                if "${" in text or any(code in text for code in ("(", ")", "=>", ";", "?.", "&&", "||")) or not translatable(text):
                    continue                                        # a `>` of an arrow function, not markup
                keys.setdefault(norm(text), f"template {path.name}")
    return keys


BACKEND = ROOT / "backend" / "src" / "app"
# Server modules whose texts reach the web as data (labels of the maintenance plan, part categories, car events, tariff names…).
# The page translates them like any other text node, so they live in the web dictionaries too. Left out: what the server translates
# itself (API errors, notifications: backend/src/app/locales), the PDF, Home Assistant, logs and the admin console.
_PY_DIRS = ("services", "api", "security")
_PY_SKIP = {"car_book.py", "notification_service.py", "ha_discovery.py", "mqtt_publisher.py", "mqtt_hub.py", "poller.py", "catalog_remote.py",
            "account_transfer.py", "admin_stats.py", "admin_audit.py", "invites.py", "tenant.py", "storage.py", "crypto.py"}
_PY_STRING = re.compile(r'''(?<![\w])(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')''')
_PY_NOT_UI = re.compile(r"(_LOGGER|logger|logging)\.\w+\(|#|detail=|raise |Exception\(|Error\(|__doc__|\.format\(|tags=|title=")


def py_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    for sub in _PY_DIRS:
        for path in sorted((BACKEND / sub).rglob("*.py")):
            if path.name in _PY_SKIP or path.name.startswith("routes_admin"):
                continue
            src = re.sub(r'"""[\s\S]*?"""', '""', path.read_text(encoding="utf-8"))      # docstrings are for developers
            for m in _PY_STRING.finditer(src):
                before = src[src.rfind("\n", 0, m.start()) + 1:m.start()]
                if _PY_NOT_UI.search(before):
                    continue
                text = m.group(1) if m.group(1) is not None else m.group(2)
                if "%s" in text or "%d" in text:
                    continue                                        # a log format
                if looks_like_ui_text(text):
                    keys.setdefault(norm(text), f"py {path.name}")
    return keys


def all_keys() -> dict[str, str]:
    keys = html_keys()
    for source in (js_keys, py_keys):
        for k, v in source().items():
            keys.setdefault(k, v)
    return keys


def load_dict(lang: str) -> dict[str, str]:
    path = DICT_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def missing(lang: str, keys: dict[str, str]) -> list[str]:
    d = load_dict(lang)
    return [k for k in keys if not d.get(k)]


def main(argv: list[str]) -> int:
    keys = all_keys()
    if "--json" in argv:
        json.dump({k: "" for k in keys}, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    by_source: dict[str, int] = {}
    for src in keys.values():
        by_source[src.split(" ")[0]] = by_source.get(src.split(" ")[0], 0) + 1
    print(f"{len(keys)} keys: " + ", ".join(f"{k} {v}" for k, v in sorted(by_source.items())))
    rc = 0
    for lang in LANGUAGES:
        miss = missing(lang, keys)
        print(f"{lang}: {len(miss)} missing")
        if miss and "--missing" in argv:
            for k in miss:
                print(f"  [{keys[k]}] {k}")
        rc = rc or (1 if miss else 0)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
