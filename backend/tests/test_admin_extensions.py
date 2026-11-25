"""The admin console's optional extensions (app/extensions) and the invitation link (`/#invite=…`) that any installation gets."""
from __future__ import annotations

import sys
import types

import httpx
import pytest

from app import extensions
from app.admin_app import admin_app, create_admin_app
from app.config import settings
from app.security import auth
from app.services import invites
from tests.test_admin_console import ADMIN, ADMIN_PW, SETUP_CODE, console


@pytest.fixture
async def admin(db, data_dir, monkeypatch):
    """A console client signed in as the first administrator."""
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    c = console()
    r = await c.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "setup_code": SETUP_CODE})
    assert r.status_code == 200, r.text
    yield c
    await c.aclose()
    auth.reset_setup_code()


def test_the_invitation_link_opens_the_sign_up_with_the_code(monkeypatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://evbro.example.com/")
    assert invites.invite_link("ebm_abc") == "https://evbro.example.com/#invite=ebm_abc"     # a fragment: it never reaches a server
    monkeypatch.setattr(settings, "PUBLIC_URL", None)
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", None)
    assert invites.invite_link("ebm_abc") is None


async def test_a_new_invitation_comes_with_its_link(admin, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_URL", "https://evbro.example.com")
    inv = (await admin.post("/api/admin/invites", json={"note": "Ana"})).json()
    assert inv["link"] == f"https://evbro.example.com/#invite={inv['token']}"


def test_a_broken_extension_is_skipped_and_the_console_still_starts(monkeypatch, tmp_path) -> None:
    (tmp_path / "zz_broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "zz_good.py").write_text("ADMIN_SCRIPT = 'zz_good.js'\n")
    (tmp_path / "zz_good.js").write_text("console.log('ok');\n")
    package = types.ModuleType("app.extensions_test")
    package.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, "app.extensions_test", package)
    monkeypatch.setattr(extensions, "HERE", str(tmp_path))
    monkeypatch.setattr(extensions, "__name__", "app.extensions_test")
    found = {e.name: e for e in extensions.discover()}
    assert "zz_broken" not in found and found["zz_good"].script_path.endswith("zz_good.js")
    assert create_admin_app() is not None


async def test_unknown_extension_scripts_are_not_served(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver") as c:
        assert (await c.get("/ext/nada.js")).status_code == 404
        assert (await c.get("/ext/..%2F..%2Fconfig.py")).status_code == 404


async def test_the_console_asks_for_its_files_with_the_build_in_the_address(admin) -> None:
    """A browser kept yesterday's admin.js after a deploy and mixed it with today's page (25-11-2026)."""
    page = (await admin.get("/")).text
    assert '<script src="/assets/admin.js?v=' in page and '<link rel="stylesheet" href="/assets/admin.css?v=' in page
    assert (await admin.get("/assets/admin.js?v=abc")).status_code == 200
