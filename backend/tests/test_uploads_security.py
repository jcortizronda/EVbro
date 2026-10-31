"""Uploaded files (car documents, maintenance invoices, car photo): type sniffing, size while reading, encryption at rest, the one-time
migration of plaintext files, response headers, the storage quota per manager and who can reach each file."""
from __future__ import annotations

import datetime

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.admin_app import admin_app
from app.api import routes_admin_auth, routes_auth, routes_maintenance
from app.config import settings
from app.db import files_migration
from app.db.models import (
    AppSetting,
    AppUser,
    DeviceAccess,
    MaintenanceItem,
    MaintenanceLog,
    Vehicle,
    VehicleDocument,
)
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, crypto, passwords
from app.services import storage, uploads

VIN, OTHER_VIN = "LSJD0000000000001", "LSJD0000000000002"
PW = "una-contraseña-larga-1"
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
HTML = b"<html><body><script>alert(document.cookie)</script></body></html>"
KB = 1024
OWNER, STRANGER, EDITOR, VIEWER = 1, 2, 3, 4


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


def _client(asgi=app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://testserver")


@pytest.fixture
async def world(db, data_dir):
    """Manager 1 owns VIN (member 3 with `edit`, member 4 with `view`); manager 2 owns OTHER_VIN. Each car has one maintenance log.
    Returns {"clients": {user id: client}, "log": log id of VIN, "other_log": log id of OTHER_VIN}."""
    h = passwords.hash_password(PW)
    async with async_session_factory() as s:
        s.add_all([AppUser(id=OWNER, role="manager", username="juan", password_hash=h),
                   AppUser(id=STRANGER, role="manager", username="otro", password_hash=h),
                   AppUser(id=EDITOR, role="member", username="editor", parent_id=OWNER, password_hash=h),
                   AppUser(id=VIEWER, role="member", username="mirón", parent_id=OWNER, password_hash=h)])
        s.add_all([Vehicle(owner_id=OWNER, vin=VIN, name="Ebro"), Vehicle(owner_id=STRANGER, vin=OTHER_VIN, name="Otro")])
        await s.flush()
        s.add_all([DeviceAccess(user_id=EDITOR, vin=VIN, level="edit", granted_by=OWNER), DeviceAccess(user_id=VIEWER, vin=VIN, level="view", granted_by=OWNER)])
        items = [MaintenanceItem(vin=v, name="Revisión", category="General", origin="custom") for v in (VIN, OTHER_VIN)]
        s.add_all(items)
        await s.flush()
        logs = [MaintenanceLog(item_id=i.id, service_date=datetime.datetime(2026, 1, 5), odometer=900.0) for i in items]
        s.add_all(logs)
        await s.commit()
        clients = {}
        for uid in (OWNER, STRANGER, EDITOR, VIEWER):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = _client()
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
        world = {"clients": clients, "log": logs[0].id, "other_log": logs[1].id}
    yield world
    for c in clients.values():
        await c.aclose()


def disk(data_dir, folder):
    path = data_dir / folder
    return sorted(p for p in path.iterdir() if not p.name.startswith(".")) if path.exists() else []


async def upload_invoice(client, log_id, data, name="factura.pdf", ctype="application/pdf"):
    return await client.post(f"/api/maintenance/logs/{log_id}/invoice", files={"file": (name, data, ctype)})


async def upload_document(client, data, name="factura.pdf", ctype="application/pdf", vin=VIN):
    return await client.post(f"/api/vehicles/{vin}/documents", data={"kind": "other"}, files={"file": (name, data, ctype)})


async def upload_photo(client, data, name="coche.jpg", ctype="image/jpeg", vin=VIN):
    return await client.post(f"/api/vehicles/{vin}/photo", files={"file": (name, data, ctype)})


async def set_limit(user_id, mb):
    async with async_session_factory() as s:
        (await s.get(AppUser, user_id)).storage_limit_mb = mb
        await s.commit()


async def used(client):
    r = await client.get("/api/account/storage")
    assert r.status_code == 200, r.text
    return r.json()


# ───────────────────────── type sniffing and size ─────────────────────────
async def test_an_invoice_is_judged_by_its_content_not_by_its_name_or_content_type(world, data_dir) -> None:
    owner, log = world["clients"][OWNER], world["log"]
    for data, name, ctype in ((HTML, "factura.pdf", "application/pdf"), (HTML, "f.html", "text/html"), (b"MZ\x90\x00" + b"\x00" * 64, "f.jpg", "image/jpeg")):
        r = await upload_invoice(owner, log, data, name, ctype)
        assert r.status_code == 415, (name, r.status_code)
    assert (await upload_invoice(owner, log, b"", "vacia.pdf")).status_code == 422
    assert disk(data_dir, "invoices") == []
    r = await upload_invoice(owner, log, PDF, name="foto.jpg", ctype="image/jpeg")          # a PDF named like an image is a PDF
    assert r.status_code == 200 and r.json()["content_type"] == "application/pdf" and r.json()["filename"].endswith(".pdf")
    got = await owner.get(r.json()["invoice_url"])
    assert got.headers["content-type"] == "application/pdf"


async def test_the_photo_only_takes_images(world) -> None:
    owner = world["clients"][OWNER]
    assert (await upload_photo(owner, PDF, "coche.jpg", "image/jpeg")).status_code == 415
    assert (await upload_photo(owner, HTML, "coche.png", "image/png")).status_code == 415
    r = await upload_photo(owner, PNG, "coche.jpg", "image/jpeg")                            # the content decides: a PNG
    assert r.status_code == 200 and (await owner.get(f"/api/vehicles/{VIN}/photo")).headers["content-type"] == "image/png"


async def test_the_size_is_checked_while_reading_and_reading_stops_early() -> None:
    class Endless:
        reads = 0

        async def read(self, n):
            self.reads += 1
            return b"x" * n

    upload = Endless()
    with pytest.raises(HTTPException) as exc:
        await uploads.read_limited(upload, 10 * uploads.CHUNK_BYTES, "demasiado")
    assert exc.value.status_code == 413 and exc.value.detail == "demasiado" and upload.reads == 11

    class Empty:
        async def read(self, n):
            return b""

    with pytest.raises(HTTPException) as exc:
        await uploads.read_limited(Empty(), 100, "x")
    assert exc.value.status_code == 422


async def test_a_too_big_invoice_is_refused_without_writing_anything(world, data_dir, monkeypatch) -> None:
    monkeypatch.setattr(routes_maintenance, "MAX_FILE_SIZE", 4 * KB)
    r = await upload_invoice(world["clients"][OWNER], world["log"], PDF + b"0" * (5 * KB))
    assert r.status_code == 413 and disk(data_dir, "invoices") == []


# ───────────────────────── encryption at rest ─────────────────────────
async def test_files_are_encrypted_on_disk_and_download_as_they_were(world, data_dir) -> None:
    owner = world["clients"][OWNER]
    inv = (await upload_invoice(owner, world["log"], PDF)).json()
    doc = (await upload_document(owner, PDF)).json()
    assert (await upload_photo(owner, JPG)).status_code == 200
    for folder in ("invoices", "vehicle_documents", "vehicle_photos"):
        files = disk(data_dir, folder)
        assert len(files) == 1, folder
        raw = files[0].read_bytes()
        assert raw.startswith(uploads.MAGIC) and b"%PDF" not in raw and not raw.startswith(b"\xff\xd8"), folder
    assert (await owner.get(inv["invoice_url"])).content == PDF
    assert (await owner.get(doc["url"])).content == PDF
    assert (await owner.get(f"/api/vehicles/{VIN}/photo")).content == JPG


async def test_a_file_cannot_be_swapped_for_another_one(world, data_dir) -> None:
    """The stored name is authenticated data: copying one encrypted file over another does not decrypt."""
    owner = world["clients"][OWNER]
    a = (await upload_document(owner, PDF)).json()
    b = (await upload_document(owner, JPG, name="b.jpg", ctype="image/jpeg")).json()
    files = disk(data_dir, "vehicle_documents")
    async with async_session_factory() as s:
        name_b = (await s.get(VehicleDocument, b["id"])).filename
    target = next(f for f in files if f.name == name_b)
    source = next(f for f in files if f.name != name_b)
    target.write_bytes(source.read_bytes())
    r = await owner.get(b["url"])
    assert r.status_code == 500 and "descifrar" in r.json()["detail"] and b"%PDF" not in r.content
    assert (await owner.get(a["url"])).content == PDF


async def test_a_file_encrypted_with_another_key_is_an_error_never_the_raw_bytes(world, data_dir, monkeypatch) -> None:
    owner = world["clients"][OWNER]
    doc = (await upload_document(owner, PDF)).json()
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "otra-clave-distinta-de-la-buena")
    crypto.reset_key_cache()
    r = await owner.get(doc["url"])
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert "clave de cifrado" in r.json()["detail"] and uploads.MAGIC not in r.content


async def test_legacy_plaintext_files_still_download(world, data_dir) -> None:
    folder = data_dir / "vehicle_documents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("a" * 32 + ".pdf")).write_bytes(PDF)
    async with async_session_factory() as s:
        doc = VehicleDocument(vin=VIN, kind="other", filename="a" * 32 + ".pdf", original_name="viejo.pdf", content_type="application/pdf", size=len(PDF))
        s.add(doc)
        await s.commit()
        doc_id = doc.id
    r = await world["clients"][OWNER].get(f"/api/documents/{doc_id}")
    assert r.status_code == 200 and r.content == PDF


async def test_the_migration_encrypts_old_files_once_and_is_idempotent(world, data_dir) -> None:
    owner = world["clients"][OWNER]
    plain = {"vehicle_documents": ("d" * 32 + ".pdf", PDF), "invoices": ("factura_log_1_abc.png", PNG), "vehicle_photos": (f"{VIN}-abc.jpg", JPG)}
    for folder, (name, data) in plain.items():
        (data_dir / folder).mkdir(parents=True, exist_ok=True)
        (data_dir / folder / name).write_bytes(data)
    (data_dir / "invoices" / ".tmp-leftover").write_bytes(b"half a file")      # leftovers of an interrupted write are not touched
    already = (await upload_document(owner, JPG, name="nuevo.jpg", ctype="image/jpeg")).json()     # already encrypted: skipped
    async with async_session_factory() as s:
        s.add(VehicleDocument(vin=VIN, kind="other", filename=plain["vehicle_documents"][0], original_name="viejo.pdf", content_type="application/pdf", size=len(PDF)))
        log = await s.get(MaintenanceLog, world["log"])
        log.invoice_filename, log.invoice_filesize, log.invoice_content_type = plain["invoices"][0], len(PNG), "image/png"
        vehicle = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN))).scalar_one()
        vehicle.photo_filename, vehicle.photo_size = plain["vehicle_photos"][0], None
        await s.commit()

    counts = await files_migration.encrypt_legacy_files()
    assert counts == {"encrypted": 3, "skipped": 1, "failed": 0, "photo_sizes": 1}
    for folder, (name, data) in plain.items():
        raw = (data_dir / folder / name).read_bytes()
        assert raw.startswith(uploads.MAGIC) and uploads.decrypt_bytes(folder, name, raw) == data
    assert (data_dir / "invoices" / ".tmp-leftover").read_bytes() == b"half a file"
    async with async_session_factory() as s:
        assert (await s.get(AppSetting, files_migration.FLAG)) is not None
        assert (await s.execute(select(Vehicle.photo_size).where(Vehicle.vin == VIN))).scalar_one() == len(JPG)
    assert (await owner.get(f"/api/maintenance/logs/{world['log']}/invoice")).content == PNG
    assert (await owner.get(f"/api/vehicles/{VIN}/photo")).content == JPG
    assert (await owner.get(already["url"])).content == JPG

    assert await files_migration.encrypt_legacy_files() == {}                 # the flag: it does not run again
    assert files_migration.encrypt_folders() == {"encrypted": 0, "skipped": 4, "failed": 0}      # and even without it, nothing changes


async def test_the_migration_keeps_a_file_it_cannot_verify_and_retries_later(world, data_dir, monkeypatch) -> None:
    (data_dir / "vehicle_documents").mkdir(parents=True, exist_ok=True)
    path = data_dir / "vehicle_documents" / "x.pdf"
    path.write_bytes(PDF)
    monkeypatch.setattr(uploads, "decrypt_bytes", lambda *a: b"something else")
    counts = await files_migration.encrypt_legacy_files()
    assert counts["failed"] == 1 and path.read_bytes() == PDF and disk(data_dir, "vehicle_documents") == [path]
    async with async_session_factory() as s:
        assert (await s.get(AppSetting, files_migration.FLAG)) is None


# ───────────────────────── headers ─────────────────────────
async def test_downloads_carry_safe_headers(world) -> None:
    owner = world["clients"][OWNER]
    inv = (await upload_invoice(owner, world["log"], PDF, name='fac"tura\r\nX-Evil: 1.pdf')).json()
    r = await owner.get(inv["invoice_url"])
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "private, no-store"
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline;") and "\n" not in cd and "x-evil" not in {k.lower() for k in r.headers}
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"   # only the framing rule: a sandbox would break Chrome's PDF viewer
    assert r.headers["x-frame-options"] == "SAMEORIGIN"                       # the app's own invoice viewer frames it

    img = (await upload_invoice(owner, world["log"], PNG, name="ticket.png", ctype="image/png")).json()
    r = await owner.get(img["invoice_url"])
    assert r.headers["content-type"] == "image/png" and r.headers["content-security-policy"] == "sandbox"
    assert (await upload_photo(owner, JPG)).status_code == 200
    r = await owner.get(f"/api/vehicles/{VIN}/photo")
    assert r.headers["content-security-policy"] == "sandbox" and r.headers["x-content-type-options"] == "nosniff"


async def test_an_old_invoice_row_with_a_client_content_type_is_served_with_the_sniffed_one(world, data_dir) -> None:
    folder = data_dir / "invoices"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "factura_log_1_old.pdf").write_bytes(HTML)                       # what the old upload accepted by extension
    async with async_session_factory() as s:
        log = await s.get(MaintenanceLog, world["log"])
        log.invoice_filename, log.invoice_content_type, log.invoice_original_name, log.invoice_filesize = "factura_log_1_old.pdf", "text/html", "x.pdf", len(HTML)
        await s.commit()
    r = await world["clients"][OWNER].get(f"/api/maintenance/logs/{world['log']}/invoice")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["x-content-type-options"] == "nosniff"


# ───────────────────────── storage quota ─────────────────────────
async def test_the_quota_blocks_uploads_over_the_limit_and_deleting_frees_space(world) -> None:
    owner = world["clients"][OWNER]
    await set_limit(OWNER, 1)
    half = PDF + b"0" * (600 * KB)
    first = await upload_document(owner, half)
    assert first.status_code == 200
    assert (await used(owner)) == {"used_bytes": len(half), "limit_mb": 1}
    r = await upload_document(owner, half)
    assert r.status_code == 409
    assert r.json()["detail"] == ("No queda espacio en tu cuenta: usas 0,6 de 1 MB. Borra documentos o pide más espacio al administrador.")
    assert (await upload_invoice(owner, world["log"], half)).status_code == 409
    assert (await upload_photo(owner, JPG + b"0" * (600 * KB))).status_code == 409
    assert (await owner.delete(first.json()["url"])).status_code == 200
    assert (await used(owner))["used_bytes"] == 0
    assert (await upload_invoice(owner, world["log"], half)).status_code == 200


async def test_unlimited_zero_and_the_server_default(world, monkeypatch) -> None:
    owner = world["clients"][OWNER]
    monkeypatch.setattr(settings, "DEFAULT_STORAGE_MB", 3)
    assert (await used(owner))["limit_mb"] == 3                                # NULL = the server default
    await set_limit(OWNER, 0)
    r = await upload_document(owner, PDF)
    assert r.status_code == 409 and "de 0 MB" in r.json()["detail"]
    await set_limit(OWNER, -1)
    monkeypatch.setattr(settings, "DEFAULT_STORAGE_MB", 0)
    assert (await upload_document(owner, PDF + b"0" * (2 * 1024 * KB))).status_code == 200
    assert (await used(owner))["limit_mb"] == -1


async def test_replacing_a_file_counts_only_the_difference(world) -> None:
    owner = world["clients"][OWNER]
    await set_limit(OWNER, 1)
    assert (await upload_photo(owner, JPG + b"0" * (700 * KB))).status_code == 200
    assert (await upload_photo(owner, JPG + b"0" * (800 * KB))).status_code == 200     # 800 KB replaces 700 KB: fits in 1 MB
    assert (await used(owner))["used_bytes"] == len(JPG) + 800 * KB
    assert (await owner.delete(f"/api/vehicles/{VIN}/photo")).status_code == 200
    assert (await used(owner))["used_bytes"] == 0

    assert (await upload_invoice(owner, world["log"], PDF + b"0" * (700 * KB))).status_code == 200
    assert (await upload_invoice(owner, world["log"], PDF + b"0" * (800 * KB))).status_code == 200
    assert (await used(owner))["used_bytes"] == len(PDF) + 800 * KB
    assert (await owner.delete(f"/api/maintenance/logs/{world['log']}/invoice")).status_code == 200
    assert (await used(owner))["used_bytes"] == 0


async def test_a_member_upload_is_charged_to_the_cars_owner(world) -> None:
    owner, editor, stranger = world["clients"][OWNER], world["clients"][EDITOR], world["clients"][STRANGER]
    assert (await upload_invoice(editor, world["log"], PDF)).status_code == 200
    assert (await used(owner))["used_bytes"] == len(PDF)
    assert (await used(editor)) == (await used(owner))                         # a member sees its manager's space
    assert (await used(stranger))["used_bytes"] == 0
    await set_limit(OWNER, 0)
    await set_limit(EDITOR, -1)                                                # a member's own column means nothing: the owner pays
    assert (await upload_invoice(editor, world["log"], PNG, ctype="image/png")).status_code == 409


async def test_storage_usage_counts_documents_invoices_and_photos_of_owned_cars_only(world) -> None:
    owner, stranger = world["clients"][OWNER], world["clients"][STRANGER]
    await upload_document(owner, PDF)
    await upload_invoice(owner, world["log"], PNG, ctype="image/png")
    await upload_photo(owner, JPG)
    await upload_document(stranger, PDF + b"x" * 999, vin=OTHER_VIN)
    async with async_session_factory() as s:
        assert await storage.storage_usage(s, OWNER) == len(PDF) + len(PNG) + len(JPG)
        assert await storage.storage_usage(s, STRANGER) == len(PDF) + 999


async def test_deleting_a_maintenance_task_removes_its_invoice_files(world, data_dir) -> None:
    owner = world["clients"][OWNER]
    assert (await upload_invoice(owner, world["log"], PDF)).status_code == 200
    async with async_session_factory() as s:
        item_id = (await s.get(MaintenanceLog, world["log"])).item_id
    assert (await owner.delete(f"/api/maintenance/{item_id}")).status_code == 200
    assert disk(data_dir, "invoices") == [] and (await used(owner))["used_bytes"] == 0


# ───────────────────────── admin console ─────────────────────────
@pytest.fixture
async def admin(db, data_dir, monkeypatch):
    monkeypatch.setattr(settings, "SETUP_CODE", "test-setup-code")
    auth.reset_setup_code()
    for t in (routes_auth.ip_throttle, routes_auth.pair_throttle, routes_admin_auth.ip_throttle, routes_admin_auth.pair_throttle):
        t._fails.clear()
    c = _client(admin_app)
    r = await c.post("/api/auth/setup", json={"username": "jefe", "password": PW, "setup_code": "test-setup-code"})
    assert r.status_code == 200, r.text
    yield c
    await c.aclose()
    auth.reset_setup_code()


async def test_the_admin_sets_the_storage_limit_on_create_patch_and_invitations(admin, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DEFAULT_STORAGE_MB", 500)
    m = (await admin.post("/api/admin/managers", json={"username": "ana", "password": PW})).json()
    assert (m["storage_limit_mb"], m["storage_limit_effective_mb"], m["storage_used_bytes"]) == (None, 500, 0)
    m2 = (await admin.post("/api/admin/managers", json={"username": "beto", "password": PW, "storage_limit_mb": 50})).json()
    assert (m2["storage_limit_mb"], m2["storage_limit_effective_mb"]) == (50, 50)

    d = (await admin.patch(f"/api/admin/users/{m['id']}", json={"storage_limit_mb": -1})).json()
    assert (d["storage_limit_mb"], d["storage_limit_effective_mb"]) == (-1, -1)
    d = (await admin.patch(f"/api/admin/users/{m['id']}", json={"vehicle_limit": 3})).json()
    assert d["storage_limit_mb"] == -1                                         # not sent: untouched
    d = (await admin.patch(f"/api/admin/users/{m['id']}", json={"storage_limit_mb": None})).json()
    assert (d["storage_limit_mb"], d["storage_limit_effective_mb"]) == (None, 500)    # null: back to the server default
    for bad in (-2, 1_000_001):
        assert (await admin.patch(f"/api/admin/users/{m['id']}", json={"storage_limit_mb": bad})).status_code == 422

    inv = (await admin.post("/api/admin/invites", json={"storage_limit_mb": 20})).json()
    assert inv["storage_limit_mb"] == 20
    async with _client() as c:
        assert (await c.post("/api/auth/register", json={"username": "carla", "password": PW, "invite_token": inv["token"], "accept_privacy": True})).status_code == 200
        assert (await c.get("/api/account/storage")).json() == {"used_bytes": 0, "limit_mb": 20}
    plain = (await admin.post("/api/admin/invites", json={})).json()
    async with _client() as c:
        assert (await c.post("/api/auth/register", json={"username": "dani", "password": PW, "invite_token": plain["token"], "accept_privacy": True})).status_code == 200
    async with async_session_factory() as s:
        dani = (await s.execute(select(AppUser).where(AppUser.username == "dani"))).scalar_one()
        assert dani.storage_limit_mb is None                                   # follows the server default


async def test_the_admin_sees_storage_numbers_but_never_the_files(admin) -> None:
    m = (await admin.post("/api/admin/managers", json={"username": "ana", "password": PW})).json()
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=m["id"], vin="MANSECRETO0000001", name="Coche secreto", photo_filename="x.jpg", photo_size=1234))
        await s.commit()
    row = next(u for u in (await admin.get("/api/admin/users")).json() if u["username"] == "ana")
    assert row["storage_used_bytes"] == 1234 and "MANSECRETO" not in str(row) and "x.jpg" not in str(row)
    stats = (await admin.get("/api/admin/stats")).json()
    assert next(r for r in stats["managers"] if r["username"] == "ana")["storage_bytes"] == 1234


# ───────────────────────── who reaches which file ─────────────────────────
async def test_nobody_without_access_to_the_car_reaches_its_files(world) -> None:
    owner, stranger = world["clients"][OWNER], world["clients"][STRANGER]
    doc = (await upload_document(owner, PDF)).json()
    inv = (await upload_invoice(owner, world["log"], PDF)).json()
    assert (await upload_photo(owner, JPG)).status_code == 200
    for method, url in (("GET", f"/api/vehicles/{VIN}/documents"), ("GET", doc["url"]), ("DELETE", doc["url"]), ("GET", inv["invoice_url"]),
                        ("DELETE", inv["invoice_url"]), ("GET", f"/api/vehicles/{VIN}/photo"), ("DELETE", f"/api/vehicles/{VIN}/photo")):
        assert (await stranger.request(method, url)).status_code == 404, (method, url)
    assert (await upload_invoice(stranger, world["log"], PDF)).status_code == 404
    assert (await upload_document(stranger, PDF)).status_code == 404
    assert (await upload_photo(stranger, JPG)).status_code == 404
    async with _client() as anon:
        for url in (doc["url"], inv["invoice_url"], f"/api/vehicles/{VIN}/photo"):
            assert (await anon.get(url)).status_code == 401
    assert (await owner.get(doc["url"])).content == PDF                         # nothing was touched


async def test_view_downloads_only_edit_handles_invoices_and_documents_and_photo_are_the_owners(world) -> None:
    owner, editor, viewer = world["clients"][OWNER], world["clients"][EDITOR], world["clients"][VIEWER]
    doc = (await upload_document(owner, PDF)).json()
    inv = (await upload_invoice(owner, world["log"], PDF)).json()
    assert (await upload_photo(owner, JPG)).status_code == 200
    for client in (viewer, editor):
        assert (await client.get(doc["url"])).content == PDF
        assert (await client.get(inv["invoice_url"])).content == PDF
        assert (await client.get(f"/api/vehicles/{VIN}/photo")).content == JPG
        assert (await upload_document(client, PDF)).status_code == 403 and (await client.delete(doc["url"])).status_code == 403
        assert (await upload_photo(client, JPG)).status_code == 403 and (await client.delete(f"/api/vehicles/{VIN}/photo")).status_code == 403
    assert (await upload_invoice(viewer, world["log"], PDF)).status_code == 403 and (await viewer.delete(inv["invoice_url"])).status_code == 403
    assert (await upload_invoice(editor, world["log"], PNG, ctype="image/png")).status_code == 200
    assert (await editor.delete(inv["invoice_url"])).status_code == 200


async def test_files_are_never_served_from_static_nor_from_the_data_folder(world, data_dir) -> None:
    owner = world["clients"][OWNER]
    doc = (await upload_document(owner, PDF)).json()
    name = disk(data_dir, "vehicle_documents")[0].name
    for url in (f"/static/{name}", f"/static/vehicle_documents/{name}", f"/static/../data/vehicle_documents/{name}", f"/static/..%2F..%2Fvehicle_documents%2F{name}",
                f"/data/vehicle_documents/{name}", f"/vehicle_documents/{name}", "/static/secret.key", "/secret.key", "/data/secret.key"):
        r = await owner.get(url)
        assert r.status_code == 404, (url, r.status_code)
    assert (await owner.get(doc["url"])).status_code == 200
