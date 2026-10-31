"""Offline mode of the PWA (service worker + banner) and the Android app's assetlinks route."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.main import app
from tests.paths import FRONTEND

SW = (FRONTEND / "static" / "sw.js").read_text(encoding="utf-8")
HTML = (FRONTEND / "templates" / "index.html").read_text(encoding="utf-8")


def test_the_service_worker_keeps_the_last_good_api_reads_and_marks_them_offline() -> None:
    assert "const API_CACHE" in SW and "X-EVBro-Offline" in SW and "apiNetworkFirst" in SW
    assert "/static/js/core/offline.js" in SW                      # precached like every script
    assert "key !== CACHE_NAME && key !== API_CACHE" in SW          # activating a new version keeps the offline copies
    assert 'id="offlineBanner"' in HTML and "core/offline.js" in HTML


@pytest.fixture
async def anon():
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    yield c
    await c.aclose()


async def test_assetlinks_is_served_only_when_configured(anon, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANDROID_ASSETLINKS_JSON", "")
    assert (await anon.get("/.well-known/assetlinks.json")).status_code == 404
    monkeypatch.setattr(settings, "ANDROID_ASSETLINKS_JSON",
                        '[{"relation": ["delegate_permission/common.handle_all_urls"], "target": {"namespace": "android_app", '
                        '"package_name": "es.example.evbro", "sha256_cert_fingerprints": ["AA:BB"]}}]')
    r = await anon.get("/.well-known/assetlinks.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.json()[0]["target"]["package_name"] == "es.example.evbro"
    monkeypatch.setattr(settings, "ANDROID_ASSETLINKS_JSON", "{not json")
    assert (await anon.get("/.well-known/assetlinks.json")).status_code == 500
