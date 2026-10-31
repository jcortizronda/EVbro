"""Your data: the car book PDF, the full export/import round trip, privacy consent on registration, the legal page and the admin audit."""
from __future__ import annotations

import datetime
import io
import json
import zipfile

import httpx
import pytest
from sqlalchemy import select

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth
from app.config import settings
from app.db.models import (
    AdminAudit,
    AppUser,
    ChargePoint,
    ChargingSession,
    Drive,
    DrivePosition,
    Expense,
    Geofence,
    MaintenanceItem,
    MaintenanceLog,
    Refuel,
    Vehicle,
    VehicleDocument,
    VehiclePart,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import uploads

VIN = "LSJA0000000000011"
D = datetime.datetime
ORIGIN = {"Origin": "http://testserver"}
PDF_MAGIC = b"%PDF-1."


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, data_dir):
    monkeypatch.setattr(settings, "SETUP_CODE", "test-setup-code")
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    yield
    auth.reset_setup_code()


async def _client(user_id: int):
    async with async_session_factory() as s:
        user = await s.get(AppUser, user_id)
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.fixture
async def world(db):
    """Ana (2) owns a car full of history; Bea (3) owns nothing; Mia (4) is Ana's member."""
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=2, role="manager", username="ana", vehicle_limit=-1, password_hash=h),
                   AppUser(id=3, role="manager", username="bea", vehicle_limit=1, password_hash=h),
                   AppUser(id=4, role="member", username="mia", parent_id=2, password_hash=h)])
        v = Vehicle(owner_id=2, vin=VIN, name="Mi Ebro", brand="Ebro", model="S900 PHEV", license_plate="1234 ABC", odometer=15000, purchase_price=32000,
                    insurance_company="Mapfre", tyre_front_size="235/55 R19")
        s.add(v)
        gf = Geofence(owner_id=2, name="Casa", latitude=40.0, longitude=-3.0, radius_meters=100)
        s.add(gf)
        await s.flush()
        d = Drive(vin=VIN, start_time=D(2026, 10, 1, 8), end_time=D(2026, 10, 1, 9), start_address="Casa", end_address="Trabajo", distance_km=25.5,
                  duration_seconds=3600, energy_consumed_kwh=4.2, start_battery_pct=80, end_battery_pct=60, end_geofence_id=gf.id, speed_max_kmh=110)
        c = ChargingSession(vin=VIN, start_time=D(2026, 10, 1, 20), end_time=D(2026, 10, 1, 23), address="Casa", kwh_added=12.3, charge_cost=1.8,
                            duration_seconds=10800, start_battery_pct=30, end_battery_pct=95, geofence_id=gf.id, max_charge_power_kw=3.6)
        item = MaintenanceItem(vin=VIN, name="Aceite", category="Motor", interval_km=15000)
        s.add_all([d, c, item, Refuel(vin=VIN, timestamp=D(2026, 10, 2), odometer=15100, liters=40, total_cost=60, full_tank=True),
                   Expense(vin=VIN, date=D(2026, 10, 3), category="tax", title="IVTM", amount=85.5),
                   VehiclePart(vin=VIN, category="engine_oil", name="Aceite", brand="Castrol", reference="0W-20", spec="0W-20", quantity=4.5)])
        await s.flush()
        s.add(DrivePosition(drive_id=d.id, timestamp=D(2026, 10, 1, 8, 10), latitude=40.01, longitude=-3.01, speed_kmh=50))
        s.add(ChargePoint(charging_session_id=c.id, timestamp=D(2026, 10, 1, 21), voltage=230, current=16, power_kw=3.6, battery_pct=50))
        s.add(MaintenanceLog(item_id=item.id, service_date=D(2026, 9, 1), odometer=14000, cost=145, workshop="Taller", title="Cambio de aceite"))
        doc_name = uploads.store(uploads.DOCUMENTS_FOLDER, b"%PDF-1.4 factura", ".pdf")
        s.add(VehicleDocument(vin=VIN, kind="purchase_invoice", title="Factura", filename=doc_name, original_name="factura.pdf", content_type="application/pdf", size=16))
        await s.commit()
    ana, bea, mia = await _client(2), await _client(3), await _client(4)
    yield {"ana": ana, "bea": bea, "mia": mia}
    for c in (ana, bea, mia):
        await c.aclose()


# ───────────────────────── libro del coche ─────────────────────────
async def test_the_car_book_is_a_pdf_with_the_chosen_sections(world) -> None:
    ana, mia = world["ana"], world["mia"]
    r = await ana.post(f"/api/vehicles/{VIN}/book", json={})
    assert r.status_code == 200 and r.content.startswith(PDF_MAGIC) and r.headers["content-type"] == "application/pdf"
    assert "libro-1234-ABC-" in r.headers["content-disposition"] and len(r.content) > 5000
    r2 = await ana.post(f"/api/vehicles/{VIN}/book", json={"sections": ["sheet", "refuels"]})
    assert r2.status_code == 200 and r2.content.startswith(PDF_MAGIC) and len(r2.content) < len(r.content)
    assert (await ana.post(f"/api/vehicles/{VIN}/book", json={"sections": ["nope"]})).status_code == 422
    assert (await mia.post(f"/api/vehicles/{VIN}/book", json={})).status_code == 404          # Mia has no link to the car
    assert (await ana.post("/api/vehicles/NOEXISTE/book", json={})).status_code == 404


# ───────────────────────── exportar e importar ─────────────────────────
async def test_export_has_everything_without_secrets_and_import_rebuilds_it_elsewhere(world) -> None:
    ana, bea = world["ana"], world["bea"]
    r = await ana.get("/api/account/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    payload = json.loads(zf.read("evbro-export.json"))
    assert payload["format"] == "evbro-account/1" and payload["username"] == "ana" and len(payload["vehicles"]) == 1
    car = payload["vehicles"][0]
    assert car["vehicle"]["vin"] == VIN and car["vehicle"]["insurance_company"] == "Mapfre" and "owner_id" not in car["vehicle"]
    assert len(car["drives"]) == 1 and len(car["drive_positions"]) == 1 and len(car["charges"]) == 1 and len(car["charge_points"]) == 1
    assert len(car["refuels"]) == 1 and len(car["expenses"]) == 1 and len(car["parts"]) == 1 and len(car["maintenance_logs"]) == 1
    assert car["documents"][0]["file"] == f"files/documents/{VIN}/{car['documents'][0]['original_name'] and 1}" or car["documents"][0]["file"].startswith("files/documents/")
    assert zf.read(car["documents"][0]["file"]) == b"%PDF-1.4 factura"
    text = r.content.decode("latin-1")
    assert "password" not in text.lower().replace("password_hash", "") and "api_token" not in text and "token_data" not in text
    # Bea imports it on the SAME server: the VIN already exists (it is Ana's), so the car is skipped; her geofence «Casa» is new
    r = await bea.post("/api/account/import", files={"file": ("ana.zip", r.content, "application/zip")})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["vehicles"] == [] and rep["skipped"] == [{"vin": VIN, "reason": "Ya existe un coche con ese bastidor en este servidor."}]
    assert rep["geofences"] == 1


async def test_import_skips_existing_vins_respects_the_limit_and_rejects_junk(world) -> None:
    ana, bea = world["ana"], world["bea"]
    export = (await ana.get("/api/account/export")).content
    # the same server: the VIN exists → skipped, nothing duplicated
    rep = (await ana.post("/api/account/import", files={"file": ("ana.zip", export, "application/zip")})).json()
    assert rep["vehicles"] == [] and rep["skipped"][0]["vin"] == VIN
    async with async_session_factory() as s:
        assert len((await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalars().all()) == 1
    # a different VIN into Bea's account (limit 1): the first fits, a second one does not
    payload = json.loads(zipfile.ZipFile(io.BytesIO(export)).read("evbro-export.json"))
    for i, vin in enumerate(("LSJA0000000000021", "LSJA0000000000022")):
        payload["vehicles"][0]["vehicle"]["vin"] = vin
        payload["vehicles"][0]["documents"] = []
        payload["vehicles"][0]["photo"] = None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("evbro-export.json", json.dumps(payload))
        rep = (await bea.post("/api/account/import", files={"file": ("x.zip", buf.getvalue(), "application/zip")})).json()
        if i == 0:
            assert rep["vehicles"][0]["vin"] == vin and rep["vehicles"][0]["charges"] == 1
        else:
            assert rep["skipped"][0]["reason"] == "La cuenta no admite más coches."
    async with async_session_factory() as s:
        imported = (await s.execute(select(Vehicle).where(Vehicle.vin == "LSJA0000000000021"))).scalar_one()
        assert imported.owner_id == 3 and imported.is_manual is True and imported.insurance_company == "Mapfre"
        drive = (await s.execute(select(Drive).where(Drive.vin == "LSJA0000000000021"))).scalar_one()
        assert drive.end_geofence_id is not None and drive.distance_km == 25.5
    assert (await bea.post("/api/account/import", files={"file": ("x.zip", b"not a zip", "application/zip")})).status_code == 422
    assert (await world["mia"].get("/api/account/export")).status_code == 403                   # members cannot export their manager's space


# ───────────────────────── consentimiento, legal, registro ─────────────────────────
async def test_registration_needs_the_privacy_consent_and_records_it(db) -> None:
    console = httpx.AsyncClient(transport=httpx.ASGITransport(app=admin_app), base_url="http://testserver", headers=ORIGIN)
    assert (await console.post("/api/auth/setup", json={"username": "admin", "password": "una-contraseña-larga-1", "setup_code": "test-setup-code"})).status_code == 200
    token = (await console.post("/api/admin/invites", json={"note": "x"})).json()["token"]
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=ORIGIN)
    body = {"username": "nuevo", "password": "una-contraseña-larga-9", "invite_token": token}
    r = await anon.post("/api/auth/register", json=body)
    assert r.status_code == 422 and "aceptar" in r.json()["detail"]
    assert (await anon.post("/api/auth/register", json={**body, "accept_privacy": True})).status_code == 200
    async with async_session_factory() as s:
        u = (await s.execute(select(AppUser).where(AppUser.username == "nuevo"))).scalar_one()
        assert u.privacy_accepted_at is not None
        audit = (await s.execute(select(AdminAudit).order_by(AdminAudit.id))).scalars().all()
    assert [a.action for a in audit] == ["invite.create"] and audit[0].username == "admin"
    log = (await console.get("/api/admin/audit")).json()
    assert log[0]["action"] == "invite.create" and log[0]["username"] == "admin"
    legal = await anon.get("/legal")
    assert legal.status_code == 200 and "Aviso legal y privacidad" in legal.text and "style=" not in legal.text
    assert "default-src 'self'" in legal.headers.get("content-security-policy", "")
    await console.aclose()
    await anon.aclose()
