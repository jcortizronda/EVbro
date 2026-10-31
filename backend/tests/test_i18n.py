"""The language of the texts (app.i18n + core/i18n.js): the account setting, the cookie, the page and the dictionaries."""
from __future__ import annotations

import importlib.util
import json
import re

import httpx
import pytest

from app import i18n
from app.db.models import AppUser
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from tests.paths import BACKEND, FRONTEND, SRC


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _fresh_dictionaries():
    i18n.clear_cache()
    yield
    i18n.clear_cache()


@pytest.fixture
async def client(db):
    async with async_session_factory() as s:
        user = AppUser(id=1, role="manager", username="juan", vehicle_limit=-1, password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"Origin": "http://testserver"})
    c.cookies.set(auth.COOKIE_NAME, token)
    yield c
    await c.aclose()


def test_translate_falls_back_to_the_spanish_text_and_fills_placeholders() -> None:
    i18n._server_dicts["en"] = {"Quedan {n} días": "{n} days left"}
    assert i18n.translate("Quedan {n} días", "en", n=3) == "3 days left"
    assert i18n.translate("Quedan  {n}  días ", "en", n=3) == "3 days left"          # whitespace does not matter in the key
    assert i18n.translate("Sin traducir {x}", "en", x=1) == "Sin traducir 1"
    assert i18n.translate("Quedan {n} días", "es", n=3) == "Quedan 3 días"
    assert i18n.translate("Hola {quien}", "en") == "Hola {quien}"                     # no params: the placeholder stays


def test_a_notification_message_is_rendered_in_the_language_of_each_recipient() -> None:
    """`Message` keeps the Spanish text and its values apart until `_dispatch_to_user` knows the account's language."""
    msg = i18n.Message.join("\n", [i18n.M("Carga iniciada: {p0}", p0="S900"), None, i18n.M("Ubicación: {p0}", p0="Casa")])
    assert msg.render("es") == "Carga iniciada: S900\nUbicación: Casa"
    assert msg.render("en") == "Charging started: S900\nLocation: Casa"                 # from locales/en.json
    assert msg.render("pt") == "Carregamento iniciado: S900\nLocalização: Casa"
    nested = i18n.M("Faltan {p0}{p1} para {p2}.", p0=i18n.M("{p0} y {p1}", p0=i18n.M("{p0} km", p0=500), p1=i18n.M("{p0} días", p0=3)), p1="", p2="aceite")
    assert nested.render("en") == "500 km and 3 days left until aceite."
    assert str(nested) == "Faltan 500 km y 3 días para aceite."
    assert i18n.render("texto plano", "en") == "texto plano"


def test_the_browser_language_is_used_until_the_account_chooses_one() -> None:
    assert i18n.from_accept_language("pt-PT,pt;q=0.9,en;q=0.8") == "pt"
    assert i18n.from_accept_language("fr-FR,fr;q=0.9,en;q=0.5,es;q=0.7") == "es"
    assert i18n.from_accept_language("de") == "es"
    assert i18n.from_accept_language(None) == "es"
    assert i18n.pick({"evbro_lang": "en"}, "pt") == "en"
    assert i18n.pick({"evbro_lang": "xx"}, "pt") == "pt"
    assert i18n.normalize("pt-BR") == "pt" and i18n.normalize("it") == "es"


async def test_the_account_language_is_saved_mirrored_in_the_cookie_and_served(client) -> None:
    r = await client.get("/api/auth/status")
    assert r.json()["language"] == "es"
    r = await client.put("/api/account/language", json={"language": "en"})
    assert r.status_code == 200, r.text
    assert r.cookies.get(i18n.COOKIE_NAME) == "en"
    async with async_session_factory() as s:
        assert (await s.get(AppUser, 1)).language == "en"
    assert (await client.get("/api/auth/status")).json()["language"] == "en"
    assert (await client.put("/api/account/language", json={"language": "fr"})).status_code == 422
    # the page comes out in that language, with its dictionary inlined
    client.cookies.set(i18n.COOKIE_NAME, "en")
    page = (await client.get("/")).text
    assert '<html lang="en">' in page
    block = re.search(r'<script type="application/json" id="i18nData">(.*?)</script>', page, re.S).group(1)
    assert json.loads(block)["Ajustes"] == "Settings"
    # and the login hands the cookie back, so a new browser starts in the account's language
    r = await client.post("/api/auth/login", json={"username": "juan", "password": "una-contraseña-larga-1"})
    assert r.status_code == 200 and r.cookies.get(i18n.COOKIE_NAME) == "en"


async def test_in_spanish_the_page_carries_an_empty_dictionary(client) -> None:
    page = (await client.get("/", headers={"Accept-Language": "es-ES"})).text
    assert '<html lang="es">' in page and 'id="i18nData">{}</script>' in page


async def test_api_errors_are_translated_to_the_language_of_the_request(client) -> None:
    i18n._server_dicts["en"] = {"Para no pedir nunca el PIN hay que tener el PIN guardado en la cuenta.": "To never ask for the PIN, the PIN must be saved in the account."}
    client.cookies.set(i18n.COOKIE_NAME, "en")
    r = await client.put("/api/account/pin-policy", json={"policy": "never"})
    assert r.status_code == 409 and r.json()["detail"].startswith("To never ask")
    client.cookies.set(i18n.COOKIE_NAME, "es")
    r = await client.put("/api/account/pin-policy", json={"policy": "never"})
    assert r.json()["detail"].startswith("Para no pedir")


async def test_the_admin_console_is_served_in_the_administrators_language(db) -> None:
    """The console (another app, same cookie) inlines the dictionary like the app and lets the administrator choose the language."""
    from app.admin_app import admin_app
    async with async_session_factory() as s:
        admin = AppUser(id=9, role="admin", is_admin=True, username="root", password_hash=passwords.hash_password("contraseña-del-admin-1"))
        s.add(admin)
        await s.commit()
        token = await auth.create_session(s, admin, _Req())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers={"Origin": "http://testserver"}) as c:
        c.cookies.set(auth.ADMIN_COOKIE_NAME, token)
        page = (await c.get("/", headers={"Accept-Language": "pt-PT"})).text
        assert '<html lang="pt">' in page and 'src="/static/js/core/i18n.js"' in page
        assert json.loads(re.search(r'id="i18nData">(.*?)</script>', page, re.S).group(1))["Gestores"] == "Gestores"
        r = await c.put("/api/auth/language", json={"language": "en"})
        assert r.status_code == 200 and r.cookies.get(i18n.COOKIE_NAME) == "en"
        assert (await c.put("/api/auth/language", json={"language": "de"})).status_code == 422
        assert '<html lang="en">' in (await c.get("/")).text
        assert (await c.get("/static/js/core/i18n.js")).status_code == 200


def _extractor():
    spec = importlib.util.spec_from_file_location("i18n_extract", BACKEND.parent / "tools" / "i18n_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_web_text_has_its_english_and_portuguese() -> None:
    """`python tools/i18n_extract.py --missing` lists what is missing."""
    ex = _extractor()
    keys = ex.all_keys()
    assert len(keys) > 500
    for lang in ("en", "pt"):
        missing = ex.missing(lang, keys)
        assert not missing, f"{lang}: {len(missing)} web texts without translation, e.g. {missing[:8]}"


def test_every_server_text_has_its_english_and_portuguese() -> None:
    """Every `_("…")`, `translate("…")`, `M("…")` and static `detail="…"` of the server (app and admin console) must be in both
    server dictionaries."""
    pattern = re.compile(r"""(?<![\w.])(?:_|translate|M)\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")
    detail = re.compile(r'detail=\s*((?:"(?:[^"\\]|\\.)*"\s*)+)')
    keys = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            keys.add(re.sub(r"\s+", " ", (m.group(1) if m.group(1) is not None else m.group(2))).strip())
        for m in detail.finditer(text):
            keys.add(re.sub(r"\s+", " ", "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1)))).strip())
    keys.discard("")
    for lang in ("en", "pt"):
        # the server looks in its locales and, underneath, in the web dictionary (i18n.server_dictionary)
        d = json.loads((FRONTEND / "static" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        d.update(json.loads((SRC / "locales" / f"{lang}.json").read_text(encoding="utf-8")))
        missing = sorted(k for k in keys if not d.get(k))
        assert not missing, f"{lang}: {len(missing)} server texts without translation, e.g. {missing[:8]}"


def test_the_dictionaries_keep_the_placeholders_of_their_keys() -> None:
    """A translation that drops or renames a `{name}` would show the raw placeholder."""
    for path in [*(FRONTEND / "static" / "i18n").glob("*.json"), *(SRC / "locales").glob("*.json")]:
        for key, value in json.loads(path.read_text(encoding="utf-8")).items():
            assert set(re.findall(r"\{(\w+)\}", key)) == set(re.findall(r"\{(\w+)\}", value)), (path.name, key, value)
