"""Documents of a car (purchase invoice, insurance policy, others): upload, list, download, delete, type sniffing and access."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.api import routes_documents
from app.config import settings
from app.db.models import AppUser, DeviceAccess, Vehicle, VehicleDocument
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services import tenant

VIN, OTHER_VIN = "LSJD0000000000001", "LSJD0000000000002"
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 100
EXE = b"MZ\x90\x00" + b"\x00" * 200


class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


@pytest.fixture
async def world(db, tmp_path, monkeypatch):
    """manager 1 owns VIN (member 3 linked with `view`); manager 2 owns OTHER_VIN. Returns {user id: client}."""
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    h = passwords.hash_password("una-contraseña-larga-1")
    async with async_session_factory() as s:
        s.add_all([AppUser(id=1, role="manager", username="juan", password_hash=h), AppUser(id=2, role="manager", username="otro", password_hash=h),
                   AppUser(id=3, role="member", username="miembro", parent_id=1, password_hash=h)])
        s.add_all([Vehicle(owner_id=1, vin=VIN, name="Ebro"), Vehicle(owner_id=2, vin=OTHER_VIN, name="Otro")])
        await s.flush()
        s.add(DeviceAccess(user_id=3, vin=VIN, level="view", granted_by=1))
        await s.commit()
        clients = {}
        for uid in (1, 2, 3):
            token = await auth.create_session(s, await s.get(AppUser, uid), _Req())
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
            c.cookies.set(auth.COOKIE_NAME, token)
            clients[uid] = c
    yield clients
    for c in clients.values():
        await c.aclose()


def stored_files(tmp_path):
    folder = tmp_path / "vehicle_documents"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


async def upload(client, data, name="factura.pdf", kind="purchase_invoice", title=None, ctype="application/pdf", vin=VIN):
    form = {"kind": kind, **({"title": title} if title is not None else {})}
    return await client.post(f"/api/vehicles/{vin}/documents", data=form, files={"file": (name, data, ctype)})


async def test_upload_list_download_and_delete(world, tmp_path) -> None:
    owner = world[1]
    r = await upload(owner, PDF, name="Factura compra.pdf")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["kind"] == "purchase_invoice" and doc["original_name"] == "Factura compra.pdf" and doc["content_type"] == "application/pdf"
    assert doc["size"] == len(PDF) and doc["url"] == f"/api/documents/{doc['id']}" and "filename" not in doc
    files = stored_files(tmp_path)
    assert len(files) == 1 and files[0].endswith(".pdf") and "Factura" not in files[0]           # random stored name

    listed = (await owner.get(f"/api/vehicles/{VIN}/documents")).json()
    assert [d["id"] for d in listed] == [doc["id"]]

    got = await owner.get(doc["url"])
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf" and got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-disposition"].startswith("inline;") and "filename*=UTF-8''Factura%20compra.pdf" in got.headers["content-disposition"]

    assert (await owner.delete(doc["url"])).json() == {"ok": True}
    assert stored_files(tmp_path) == [] and (await owner.get(doc["url"])).status_code == 404
    assert (await owner.get(f"/api/vehicles/{VIN}/documents")).json() == []


async def test_several_insurance_policy_documents_with_titles(world) -> None:
    owner = world[1]
    for title, data in (("Condiciones generales", PDF), ("Condiciones particulares", JPG), (None, PNG)):
        assert (await upload(owner, data, name="poliza.bin", kind="insurance_policy", title=title)).status_code == 200
    assert (await upload(owner, WEBP, name="permiso.webp", kind="registration", ctype="image/webp")).status_code == 200
    docs = (await owner.get(f"/api/vehicles/{VIN}/documents")).json()
    policy = [d for d in docs if d["kind"] == "insurance_policy"]
    assert sorted((d["title"] or "") for d in policy) == ["", "Condiciones generales", "Condiciones particulares"]
    assert sorted(d["content_type"] for d in policy) == ["application/pdf", "image/jpeg", "image/png"]
    assert [d["content_type"] for d in docs if d["kind"] == "registration"] == ["image/webp"]


async def test_the_type_comes_from_the_content_not_from_the_name(world, tmp_path) -> None:
    owner = world[1]
    r = await upload(owner, PDF, name="foto.jpg", ctype="image/jpeg")              # a PDF named like an image is a PDF
    assert r.status_code == 200 and r.json()["content_type"] == "application/pdf" and stored_files(tmp_path)[0].endswith(".pdf")
    for data, name, ctype in ((EXE, "factura.pdf", "application/pdf"), (b"<html><script>alert(1)</script>", "f.pdf", "application/pdf"),
                              (b"RIFF\x00\x00\x00\x00WAVEfmt ", "a.webp", "image/webp"), (b"", "vacio.pdf", "application/pdf")):
        res = await upload(owner, data, name=name, ctype=ctype)
        assert res.status_code in (415, 422), (name, res.status_code)
    assert len(stored_files(tmp_path)) == 1


async def test_size_kind_and_title_limits(world, tmp_path, monkeypatch) -> None:
    owner = world[1]
    big = PDF + b"0" * routes_documents.MAX_DOCUMENT_BYTES
    assert (await upload(owner, big)).status_code == 413
    assert (await upload(owner, PDF, kind="passport")).status_code == 422
    assert (await upload(owner, PDF, title="x" * 121)).status_code == 422
    monkeypatch.setattr(routes_documents, "MAX_DOCUMENTS_PER_VEHICLE", 1)
    assert (await upload(owner, PDF)).status_code == 200
    assert (await upload(owner, PDF)).status_code == 409
    assert len(stored_files(tmp_path)) == 1


async def test_the_client_name_never_reaches_the_path_nor_breaks_the_header(world, tmp_path) -> None:
    owner = world[1]
    r = await upload(owner, PDF, name='../../etc/pó"liza\r\nX-Evil: 1.pdf')
    assert r.status_code == 200
    doc = r.json()
    assert "/" not in doc["original_name"] and "\r" not in doc["original_name"] and "\n" not in doc["original_name"]
    assert len(stored_files(tmp_path)) == 1 and not (tmp_path.parent / "etc").exists()
    got = await owner.get(doc["url"])
    assert got.status_code == 200 and "x-evil" not in got.headers
    disposition = got.headers["content-disposition"]
    assert disposition.count('"') == 2 and "\n" not in disposition


async def test_access_by_level(world) -> None:
    owner, stranger, viewer = world[1], world[2], world[3]
    doc = (await upload(owner, PDF)).json()
    # another manager: the car and its documents do not exist
    assert (await stranger.get(f"/api/vehicles/{VIN}/documents")).status_code == 404
    assert (await stranger.get(doc["url"])).status_code == 404
    assert (await stranger.delete(doc["url"])).status_code == 404
    assert (await upload(stranger, PDF)).status_code == 404
    # a member with `view` reads but neither uploads nor deletes
    assert [d["id"] for d in (await viewer.get(f"/api/vehicles/{VIN}/documents")).json()] == [doc["id"]]
    assert (await viewer.get(doc["url"])).status_code == 200
    assert (await upload(viewer, PDF)).status_code == 403
    assert (await viewer.delete(doc["url"])).status_code == 403
    # even with `control` the papers stay the owner's
    async with async_session_factory() as s:
        link = (await s.execute(select(DeviceAccess).where(DeviceAccess.user_id == 3))).scalar_one()
        link.level = "control"
        await s.commit()
    assert (await upload(viewer, PDF)).status_code == 403 and (await viewer.delete(doc["url"])).status_code == 403
    # the owner of another car cannot attach to this one through its own documents either
    assert (await upload(owner, PDF, vin=OTHER_VIN)).status_code == 404
    assert (await owner.delete(doc["url"])).status_code == 200


async def test_deleting_the_car_removes_its_documents_and_files(world, tmp_path) -> None:
    owner, stranger = world[1], world[2]
    await upload(owner, PDF)
    await upload(owner, JPG, kind="insurance_policy", title="Condiciones particulares")
    other = (await upload(stranger, PDF, vin=OTHER_VIN)).json()
    assert len(stored_files(tmp_path)) == 3
    assert (await owner.delete(f"/api/vehicles/{VIN}")).status_code == 200
    async with async_session_factory() as s:
        left = (await s.execute(select(VehicleDocument))).scalars().all()
    assert [d.id for d in left] == [other["id"]]
    assert stored_files(tmp_path) == [left[0].filename]


async def test_deleting_a_manager_space_removes_the_files(world, tmp_path) -> None:
    await upload(world[1], PDF)
    async with async_session_factory() as s:
        await tenant.delete_manager_space(s, 1)
        await s.commit()
    assert stored_files(tmp_path) == []
