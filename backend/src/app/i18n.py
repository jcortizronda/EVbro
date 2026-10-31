"""The language of what a person reads: Spanish is the source text, English and Portuguese come from dictionaries.

Every user-facing text in the code stays written in Spanish; the dictionaries map that exact text (whitespace collapsed) to the
other languages, so a missing entry simply shows the Spanish text. There are two sets of dictionaries:

  * `backend/src/app/locales/<lang>.json`: texts the server produces (API errors, notices, the car book PDF). `translate()`
    looks them up in the language of the current request (a context variable set by the middleware in main.py from the
    `evbro_lang` cookie, which mirrors `app_users.language`) or in the language passed explicitly (background jobs).
  * `frontend/static/i18n/<lang>.json`: texts of the web app. The page gets the one of its language inlined as a JSON data block
    and `core/i18n.js` applies it (see docs/i18n.md).

`tools/i18n_extract.py` lists the web texts and a test checks that no key is missing in either set.
"""
from __future__ import annotations

import contextvars
import json
import logging
import pathlib
import re
from typing import Any, Dict, Optional

from app.paths import frontend_dir

SUPPORTED = ("es", "en", "pt")
DEFAULT = "es"
COOKIE_NAME = "evbro_lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
NAMES = {"es": "Español", "en": "English", "pt": "Português"}
LOCALES_DIR = pathlib.Path(__file__).parent / "locales"

_LOGGER = logging.getLogger(__name__)
_WS = re.compile(r"\s+")
_current: contextvars.ContextVar[str] = contextvars.ContextVar("language", default=DEFAULT)
_server_dicts: Dict[str, Dict[str, str]] = {}
_page_json: Dict[str, str] = {}


class _Params(dict):
    """`str.format_map` that leaves an unknown `{name}` in place instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def normalize(lang: Optional[str]) -> str:
    """`pt-BR` → `pt`; anything unsupported → Spanish."""
    code = (lang or "").strip().lower()[:2]
    return code if code in SUPPORTED else DEFAULT


def from_accept_language(header: Optional[str]) -> str:
    """The first supported language of an `Accept-Language` header (quality values respected in their order)."""
    if not header:
        return DEFAULT
    ranked = []
    for i, part in enumerate(header.split(",")):
        piece = part.strip().split(";")
        code = piece[0].strip().lower()[:2]
        q = 1.0
        for p in piece[1:]:
            p = p.strip()
            if p.startswith("q="):
                try:
                    q = float(p[2:])
                except ValueError:
                    q = 0.0
        if code in SUPPORTED and q > 0:
            ranked.append((-q, i, code))
    return sorted(ranked)[0][2] if ranked else DEFAULT


def pick(cookies: Dict[str, str], accept_language: Optional[str]) -> str:
    """The language of a request: the cookie set by the app when the account chose one, otherwise the browser's."""
    cookie = cookies.get(COOKIE_NAME)
    if cookie and cookie.lower() in SUPPORTED:
        return cookie.lower()
    return from_accept_language(accept_language)


def set_language(lang: Optional[str]) -> contextvars.Token:
    return _current.set(normalize(lang))


def reset_language(token: contextvars.Token) -> None:
    _current.reset(token)


def get_language() -> str:
    return _current.get()


def _load(path: pathlib.Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as ex:
        _LOGGER.error("Dictionary %s is not valid JSON: %s", path, ex)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def server_dictionary(lang: str) -> Dict[str, str]:
    """What the server translates with: its own `locales/` plus the web dictionary underneath, so a label that the page already
    knows (a part category, a plan step, a tariff name) reads the same in an API answer, a notice or the car book PDF."""
    lang = normalize(lang)
    if lang == DEFAULT:
        return {}
    if lang not in _server_dicts:
        merged = _load(pathlib.Path(frontend_dir()) / "static" / "i18n" / f"{lang}.json")
        merged.update(_load(LOCALES_DIR / f"{lang}.json"))
        _server_dicts[lang] = merged
    return _server_dicts[lang]


def translate(text: str, lang: Optional[str] = None, **params: Any) -> str:
    """The text in the language asked for (or the request's); `{name}` placeholders are filled with `params` afterwards."""
    out = server_dictionary(lang or get_language()).get(_WS.sub(" ", text).strip(), text)
    return out.format_map(_Params(params)) if params else out


_ = translate


class Message:
    """A text whose placeholders stay unresolved until it is rendered in the language of each recipient (notifications go to
    several accounts, each with its own language). `params` may be other messages, which are rendered in the same language."""

    __slots__ = ("text", "params")

    def __init__(self, text: str, **params: Any) -> None:
        self.text, self.params = text, params

    def render(self, lang: Optional[str] = None) -> str:
        lang = normalize(lang or get_language())
        params = {k: (v.render(lang) if isinstance(v, Message) else v) for k, v in self.params.items()}
        return translate(self.text, lang, **params) if params else translate(self.text, lang)

    def __str__(self) -> str:
        return self.render(DEFAULT)

    @classmethod
    def join(cls, sep: str, parts) -> "Message":
        """Several messages (or plain strings) glued with `sep`, each translated on its own; empty parts are dropped."""
        kept = [p for p in parts if p is not None and p != ""]
        return cls(sep.join("{p%d}" % i for i in range(len(kept))), **{"p%d" % i: p for i, p in enumerate(kept)})


def M(text: str, **params: Any) -> Message:
    return Message(text, **params)


def render(value: Any, lang: Optional[str] = None) -> str:
    """A `Message` in that language; anything else as text."""
    return value.render(lang) if isinstance(value, Message) else str(value)


def page_dictionary_json(lang: str) -> str:
    """The web dictionary of a language as JSON for the `<script type="application/json">` block of index.html ("{}" for Spanish).
    `</` is escaped so no text can close the block early."""
    lang = normalize(lang)
    if lang == DEFAULT:
        return "{}"
    if lang not in _page_json:
        data = _load(pathlib.Path(frontend_dir()) / "static" / "i18n" / f"{lang}.json")
        _page_json[lang] = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return _page_json[lang]


def clear_cache() -> None:
    """Tests edit dictionaries on the fly."""
    _server_dicts.clear()
    _page_json.clear()
