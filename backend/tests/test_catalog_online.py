"""Online catalogue: keys issued from the console, the public export/propose routes, and a self-hosted server mirroring the catalogue and
forwarding proposals. Both roles run in this one application: the «remote» calls are routed back into it in process."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth, routes_catalog_online
from app.config import settings
from app.db.models import AppUser, CatalogEntry, CatalogFitment, CatalogKey, Vehicle, VehiclePart
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import catalog_remote, telemetry

VIN = "LSJA0000000000041"
SETUP_CODE = "test-setup-code"
ORIGIN = {"Origin": "http://testserver"}
REMOTE = "https://catalogo.ejemplo.es"


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", SETUP_CODE)
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle, routes_catalog_online.ip_throttle):
        t._fails.clear()
    routes_catalog_online._signups_by_ip.clear()
    yield
    auth.reset_setup_code()


@pytest.fixture
def in_process_http(monkeypatch):
    """`catalog_remote._http` talks to this very application: the «online server» and the «self-hosted server» share the process."""
    async def fake(method, url, key, payload=None):
        assert url.startswith(REMOTE)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            r = await c.request(method, url[len(REMOTE):], json=payload, headers={"Authorization": f"Bearer {key}"})
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
    monkeypatch.setattr(catalog_remote, "_http", fake)


@pytest.fixture
async def world(db):
    """Ana (2) owns a car with one approved entry in the local catalogue and one part to propose; the console admin is created by setup."""
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add(AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h))
        s.add(Vehicle(owner_id=2, vin=VIN, name="A", brand="Ebro", model="S900 PHEV"))
        await s.flush()
        approved = CatalogEntry(model="S900 PHEV", make="Ebro", category="engine_oil", name="Aceite", brand="Castrol", reference="EDGE-0W20", spec="0W-20",
                                quantity=4.5, status="approved", proposed_by=2, confirmations=2)
        s.add(approved)
        await s.flush()
        s.add(CatalogFitment(entry_id=approved.id, make="Ebro", model="S900 PHEV", oem_reference="E-1000"))
        part = VehiclePart(vin=VIN, category="cabin_filter", name="Filtro de habitáculo", brand="Mann", reference="CUK-2545")
        s.add(part)
        await s.commit()
        part_id = part.id
    console = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN)
    assert (await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": SETUP_CODE})).status_code == 200
    async with async_session_factory() as s:
        token = await auth.create_session(s, await s.get(AppUser, 2), _Req())
    ana = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    ana.cookies.set(auth.COOKIE_NAME, token)
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    yield {"console": console, "ana": ana, "anon": anon, "part_id": part_id}
    for c in (console, ana, anon):
        await c.aclose()


async def test_keys_are_issued_as_links_and_open_the_public_routes(world) -> None:
    console, anon = world["console"], world["anon"]
    # without a public URL the key comes alone; with it, as a link
    r = await console.post("/api/admin/catalog/keys", json={"note": "Luis, servidor de casa"})
    assert r.status_code == 200 and r.json()["key"].startswith("ebc_") and r.json()["link"] is None
    assert (await console.put("/api/admin/catalog/keys/public-url", json={"public_url": "ftp://x"})).status_code == 422
    assert (await console.put("/api/admin/catalog/keys/public-url", json={"public_url": REMOTE + "/"})).json()["public_url"] == REMOTE
    r = await console.post("/api/admin/catalog/keys", json={"note": "Marta", "days": 30})
    key, link = r.json()["key"], r.json()["link"]
    assert link == f"{REMOTE}#{key}" and r.json()["expires_at"] is not None
    listed = (await console.get("/api/admin/catalog/keys")).json()
    assert listed["public_url"] == REMOTE and [k["note"] for k in listed["keys"]] == ["Marta", "Luis, servidor de casa"]
    assert all("key" not in k and "key_hash" not in k for k in listed["keys"])            # the plain key is never listed again
    async with async_session_factory() as s:
        stored = (await s.execute(select(CatalogKey.key_hash))).scalars().all()
    assert key not in stored and all(len(x) == 64 for x in stored)

    # public routes: no key → 401, bad key → 401 and throttled, good key → the approved catalogue
    assert (await anon.get("/api/catalog/online/export")).status_code == 401
    assert (await anon.get("/api/catalog/online/export", headers={"Authorization": "Bearer ebc_nope-nope-nope-nope"})).status_code == 401
    r = await anon.get("/api/catalog/online/export", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["format"] == "evbro-catalog/2" and [e["reference"] for e in r.json()["entries"]] == ["EDGE-0W20"]
    assert "status" not in r.json()["entries"][0] and "origin" not in r.json()["entries"][0]
    # a proposal from another installation lands in the queue tagged with the key's note, with no person behind it
    r = await anon.post("/api/catalog/online/propose", json={"category": "brake_pads", "name": "Pastillas", "brand": "Brembo", "reference": "P85020", "notes": "privado",
                                                              "fitments": [{"make": "Ebro", "model": "S800 PHEV", "years": "2025", "oem_reference": "S8-77"}]},
                        headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    queue = (await console.get("/api/admin/catalog?status=pending")).json()
    assert len(queue) == 1 and queue[0]["remote_note"] == "Marta" and queue[0]["proposed_by"] is None and queue[0]["fitments"][0]["model"] == "S800 PHEV"
    assert queue[0]["fitments"][0]["oem_reference"] == "S8-77" and queue[0]["oem_reference"] == "S8-77"
    assert queue[0]["notes"] is None                                                                                  # an older server's notes are not published                  # the maker's number travels
    assert (await anon.post("/api/catalog/online/propose", json={"category": "nope", "fitments": [{"model": "x"}]}, headers={"Authorization": f"Bearer {key}"})).status_code == 422
    keys = (await console.get("/api/admin/catalog/keys")).json()["keys"]
    marta = next(k for k in keys if k["note"] == "Marta")
    assert marta["downloads"] == 1 and marta["proposals"] == 1 and marta["last_used_at"]
    # revoked → 401
    assert (await console.delete(f"/api/admin/catalog/keys/{marta['id']}")).status_code == 200
    assert (await anon.get("/api/catalog/online/export", headers={"Authorization": f"Bearer {key}"})).status_code == 401
    # after a few bad keys the IP waits
    for _ in range(10):
        await anon.get("/api/catalog/online/export", headers={"Authorization": "Bearer ebc_wrong-wrong-wrong-wrong"})
    assert (await anon.get("/api/catalog/online/export", headers={"Authorization": f"Bearer {key}"})).status_code == 429


async def test_a_self_hosted_server_mirrors_the_online_catalogue_and_forwards_proposals(world, in_process_http) -> None:
    console, ana, part_id = world["console"], world["ana"], world["part_id"]
    await console.put("/api/admin/catalog/keys/public-url", json={"public_url": REMOTE})
    key = (await console.post("/api/admin/catalog/keys", json={"note": "Yo mismo"})).json()["key"]
    # default: own catalogue, no link
    assert (await console.get("/api/admin/catalog/source")).json() == {"source": "own", "has_link": False, "server": None, "default_server": "https://catalog.evbro.app", "last_sync_at": None, "last_error": None, "entries": 0, "decided": False}
    assert (await console.put("/api/admin/catalog/source", json={"source": "remote"})).status_code == 422          # needs a link
    assert (await console.put("/api/admin/catalog/source", json={"source": "remote", "link": "https://x#clave-mala"})).status_code == 422
    r = await console.put("/api/admin/catalog/source", json={"source": "remote", "link": f"{REMOTE}#{key}"})
    assert r.status_code == 200 and r.json()["source"] == "remote" and r.json()["has_link"] and r.json()["server"] == REMOTE
    assert "link" not in r.json() and key not in r.text                                                               # the key is a secret
    # sync: the approved entry comes back as a read-only mirror next to the local one
    r = await console.post("/api/admin/catalog/source/sync")
    assert r.status_code == 200 and r.json()["entries"] == 1 and r.json()["last_error"] is None
    async with async_session_factory() as s:
        rows = (await s.execute(select(CatalogEntry).order_by(CatalogEntry.id))).scalars().all()
    assert [(e.origin, e.status, e.reference) for e in rows] == [("local", "approved", "EDGE-0W20"), ("remote", "approved", "EDGE-0W20")]
    assert rows[1].remote_id == rows[0].id and rows[1].proposed_by is None
    async with async_session_factory() as s:                                                                           # the mirror keeps the maker's number
        assert (await s.execute(select(CatalogFitment.oem_reference).where(CatalogFitment.entry_id == rows[1].id))).scalar_one() == "E-1000"
    # the app sees both when it looks up the model; the console queue and the export only show local ones
    visible = (await ana.get("/api/catalog", params={"model": "s900 phev", "category": "engine_oil"})).json()
    assert sorted(e["origin"] for e in visible) == ["local", "remote"]
    assert all(e["origin"] == "local" for e in (await console.get("/api/admin/catalog?status=all")).json())
    assert (await console.get("/api/admin/catalog/export")).json()["count"] == 1
    # a second sync replaces the mirror instead of piling up
    await console.post("/api/admin/catalog/source/sync")
    async with async_session_factory() as s:
        assert len((await s.execute(select(CatalogEntry).where(CatalogEntry.origin == "remote"))).scalars().all()) == 1
    # Ana proposes a part: it stays in the local queue and is forwarded to the online queue (the same queue here) with the key's note
    r = await ana.post(f"/api/parts/{part_id}/propose", json={})
    assert r.status_code == 200 and r.json()["forwarded_at"] is not None
    queue = (await console.get("/api/admin/catalog?status=pending")).json()
    assert {(q["proposed_by"], q["remote_note"]) for q in queue} == {("ana", None), (None, "Yo mismo")}
    # a revoked key: the sync reports the error and keeps the last mirror; back to «own» the mirror is still searchable until replaced
    key_id = (await console.get("/api/admin/catalog/keys")).json()["keys"][0]["id"]
    await console.delete(f"/api/admin/catalog/keys/{key_id}")
    r = await console.post("/api/admin/catalog/source/sync")
    assert r.status_code == 401 and "clave" in r.json()["detail"].lower()
    assert (await console.get("/api/admin/catalog/source")).json()["last_error"].startswith("La clave")
    back = (await console.put("/api/admin/catalog/source", json={"source": "own", "link": ""})).json()
    assert back["source"] == "own" and back["has_link"] is False and back["last_error"] is None and back["entries"] == 1


async def test_a_bare_key_uses_the_default_catalogue_server(world, monkeypatch) -> None:
    """Pasting only `ebc_…` stores it with CATALOG_DEFAULT_URL; a full link keeps its own server; no default = the link is required."""
    console = world["console"]
    monkeypatch.setattr(settings, "CATALOG_DEFAULT_URL", "https://catalog.evbro.app/")
    key = "ebc_" + "a" * 30
    r = await console.put("/api/admin/catalog/source", json={"source": "remote", "link": key})
    assert r.status_code == 200, r.text
    assert r.json()["server"] == "https://catalog.evbro.app" and r.json()["default_server"] == "https://catalog.evbro.app"
    async with async_session_factory() as s:
        assert (await catalog_remote.get_config(s)).link == f"https://catalog.evbro.app#{key}"

    r = await console.put("/api/admin/catalog/source", json={"source": "remote", "link": f"{REMOTE}#{key}"})
    assert r.json()["server"] == REMOTE

    monkeypatch.setattr(settings, "CATALOG_DEFAULT_URL", "")
    r = await console.put("/api/admin/catalog/source", json={"source": "remote", "link": key})
    assert r.status_code == 422 and "enlace completo" in r.json()["detail"]


async def test_the_project_server_hands_out_keys_that_work_alone(world) -> None:
    """When this server's public URL is the default catalogue, a new key is shown alone (that is all the other side has to paste)."""
    console = world["console"]
    await console.put("/api/admin/catalog/keys/public-url", json={"public_url": settings.CATALOG_DEFAULT_URL})
    k = (await console.post("/api/admin/catalog/keys", json={"note": "Luis"})).json()
    assert k["key_alone"] is True and k["key"].startswith("ebc_")
    await console.put("/api/admin/catalog/keys/public-url", json={"public_url": REMOTE})
    assert (await console.post("/api/admin/catalog/keys", json={"note": "Eva"})).json()["key_alone"] is False


async def test_sign_up_is_only_open_on_the_project_server_and_is_capped(world, monkeypatch) -> None:
    console, anon = world["console"], world["anon"]
    form = {"format": catalog_remote.SIGNUP_FORMAT}
    assert (await anon.post("/api/catalog/online/signup", json=form)).status_code == 404                  # off by default
    monkeypatch.setattr(settings, "CATALOG_OPEN_SIGNUP", True)
    assert (await anon.post("/api/catalog/online/signup", json={"format": "x"})).status_code == 422
    r = await anon.post("/api/catalog/online/signup", json=form)
    key = r.json()["key"]
    assert r.status_code == 200 and key.startswith("ebc_")
    assert (await anon.get("/api/catalog/online/export", headers={"Authorization": f"Bearer {key}"})).status_code == 200
    listed = (await console.get("/api/admin/catalog/keys")).json()["keys"]
    assert listed[0]["auto"] is True and listed[0]["note"].startswith("Alta automática")
    # a self-service key has a lower daily proposal cap
    monkeypatch.setattr(catalog_remote, "MAX_PROPOSALS_PER_AUTO_KEY_DAY", 1)
    part = {"category": "brake_pads", "brand": "Brembo", "fitments": [{"model": "S800 PHEV"}]}
    assert (await anon.post("/api/catalog/online/propose", json=part, headers={"Authorization": f"Bearer {key}"})).status_code == 200
    assert (await anon.post("/api/catalog/online/propose", json=part, headers={"Authorization": f"Bearer {key}"})).status_code == 429
    # a few per IP and day, and a cap for everybody together
    assert (await anon.post("/api/catalog/online/signup", json=form)).status_code == 200
    assert (await anon.post("/api/catalog/online/signup", json=form)).status_code == 200
    assert (await anon.post("/api/catalog/online/signup", json=form)).status_code == 429
    routes_catalog_online._signups_by_ip.clear()
    monkeypatch.setattr(catalog_remote, "MAX_SIGNUPS_PER_DAY", 3)
    assert (await anon.post("/api/catalog/online/signup", json=form)).status_code == 429


async def test_one_click_connects_to_the_project_catalogue(world, in_process_http, monkeypatch) -> None:
    console = world["console"]
    monkeypatch.setattr(settings, "CATALOG_DEFAULT_URL", REMOTE)
    # the project's server does not take sign-ups: the error is clear and nothing changes
    r = await console.post("/api/admin/catalog/source/connect")
    assert r.status_code == 502 and "clave" in r.json()["detail"]
    state = (await console.get("/api/admin/catalog/source")).json()
    assert state["source"] == "own" and state["has_link"] is False and state["decided"] is False
    monkeypatch.setattr(settings, "CATALOG_OPEN_SIGNUP", True)
    r = await console.post("/api/admin/catalog/source/connect")
    assert r.status_code == 200
    state = r.json()
    assert state["source"] == "remote" and state["has_link"] and state["server"] == REMOTE and state["entries"] == 1 and state["decided"] is True
    assert "ebc_" not in r.text                                                                                        # the key stays a secret
    keys = (await console.get("/api/admin/catalog/keys")).json()["keys"]
    assert [k["auto"] for k in keys] == [True]
    # connecting again reuses the stored key instead of signing up twice
    assert (await console.post("/api/admin/catalog/source/connect")).status_code == 200
    assert len((await console.get("/api/admin/catalog/keys")).json()["keys"]) == 1
    audit = [a["action"] for a in (await console.get("/api/admin/audit")).json()]
    assert "catalog.connect" in audit


async def test_keeping_the_own_catalogue_stops_the_question(world) -> None:
    console = world["console"]
    assert (await console.get("/api/admin/catalog/source")).json()["decided"] is False
    r = await console.post("/api/admin/catalog/source/keep-own")
    assert r.status_code == 200 and r.json()["decided"] is True and r.json()["source"] == "own"


@pytest.mark.parametrize("signup_open", [True, False])
async def test_the_first_run_form_applies_both_answers(db, in_process_http, monkeypatch, signup_open) -> None:
    monkeypatch.setattr(settings, "CATALOG_DEFAULT_URL", REMOTE)
    monkeypatch.setattr(settings, "CATALOG_OPEN_SIGNUP", signup_open)
    monkeypatch.setattr(settings, "TELEMETRY_URL", "https://data.ejemplo.es")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN) as console:
        status = (await console.get("/api/auth/status")).json()
        assert status["setup_required"] and status["catalog_server"] == REMOTE and status["telemetry_available"] is True
        r = await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": SETUP_CODE,
                                                        "catalog": "online", "telemetry": True})
        assert r.status_code == 200                                                                                    # the account exists either way
        source = (await console.get("/api/admin/catalog/source")).json()
        if signup_open:
            assert "catalog_error" not in r.json() and source["source"] == "remote" and source["decided"] is True
        else:
            assert "clave" in r.json()["catalog_error"] and source["source"] == "own" and source["decided"] is False
        tel = (await console.get("/api/admin/telemetry")).json()
        assert tel["enabled"] is True and tel["decided"] is True
        assert "catalog_server" not in (await console.get("/api/auth/status")).json()                                   # only before the setup
    telemetry.set_enabled_cache(False)


async def test_the_first_run_form_can_keep_everything_local(db) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN) as console:
        r = await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": SETUP_CODE,
                                                        "catalog": "own", "telemetry": False})
        assert r.status_code == 200
        assert (await console.get("/api/admin/catalog/source")).json()["decided"] is True
        tel = (await console.get("/api/admin/telemetry")).json()
        assert tel["enabled"] is False and tel["decided"] is True
        assert (await console.post("/api/auth/setup", json={"username": "x", "password": "y", "setup_code": SETUP_CODE, "catalog": "maybe"})).status_code in (409, 422)
