"""Portada: no afirmar lo que no se sabe (cierre, puertas, ventanas) y estado real de alarma y carga programada."""
from __future__ import annotations

import httpx
import pytest

from app.core import commands
from app.core.telemetry import parse_telemetry_data
from app.db.models import AppUser, Vehicle
from app.db.session import async_session_factory
from app.main import app
from app.security import auth, passwords
from app.services.poller import VehiclePoller

VIN = "LSJA0000000000001"


# ───────────────────────── el parser no inventa «cerrado» ─────────────────────────
def test_sin_datos_el_coche_no_se_da_por_cerrado() -> None:
    t = parse_telemetry_data({})
    assert t["is_locked"] is None
    assert all(v is None for v in t["doors"].values())
    assert all(v is None for v in t["windows"].values())
    assert t["appointment_charge_on"] is None


def test_con_datos_se_interpreta_igual_que_antes() -> None:
    cerrado = parse_telemetry_data({"doorLock": "0", "trunkDoor": "0", "frontLeftWindowState": "0"})
    assert cerrado["is_locked"] is True and cerrado["doors"]["trunk"] == 0 and cerrado["windows"]["fl"] == 0
    abierto = parse_telemetry_data({"doorLock": "1", "trunkDoor": "1", "sunroofState": "1"})
    assert abierto["is_locked"] is False and abierto["doors"]["trunk"] == 1 and abierto["windows"]["sunroof"] == 1
    # lo que no ha llegado sigue sin saberse aunque otros campos sí hayan llegado
    assert abierto["doors"]["fl"] is None and abierto["windows"]["fl"] is None


@pytest.mark.parametrize("raw,esperado", [("0", False), ("1", True), ("2", True), (None, None), ("", None)])
def test_carga_programada_activa(raw, esperado) -> None:
    datos = {} if raw is None else {"appointmentChargeState": raw}
    assert parse_telemetry_data(datos)["appointment_charge_on"] is esperado


def test_la_alarma_o_el_cierre_desconocidos_no_disparan_avisos_de_seguridad() -> None:
    """El poller avisa de «sin cerrar» solo si is_locked es False; None (sin dato) no debe contar."""
    t = parse_telemetry_data({"engineState": "0"})
    assert t["is_locked"] is None and (t.get("is_locked") is False) is False


# ───────────────────────── estado de la alarma ─────────────────────────
class _Req:
    client = type("C", (), {"host": "127.0.0.1"})()
    headers = {"user-agent": "t"}


async def _cliente(db):
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN, name="Ebro"))
        user = AppUser(id=1, role="manager", username="juan", password_hash=passwords.hash_password("una-contraseña-larga-1"))
        s.add(user)
        await s.commit()
        token = await auth.create_session(s, user, _Req())
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c.cookies.set(auth.COOKIE_NAME, token)
    return c


@pytest.mark.parametrize("devuelve,esperado", [(1, True), (0, False), (None, None)])
async def test_estado_de_la_alarma(db, monkeypatch, devuelve, esperado) -> None:
    monkeypatch.setattr(commands, "query_theft_switch", lambda ctx: devuelve)
    VehiclePoller._contexts = {}
    async with await _cliente(db) as c:
        r = await c.get(f"/api/vehicles/{VIN}/alarm")
    assert r.status_code == 200 and r.json() == {"enabled": esperado}


async def test_la_alarma_de_un_vehiculo_desconocido_da_404_y_exige_sesion(db) -> None:
    async with await _cliente(db) as c:
        assert (await c.get("/api/vehicles/LSJA9999999999999/alarm")).status_code == 404
        c.cookies.clear()
        assert (await c.get(f"/api/vehicles/{VIN}/alarm")).status_code == 401
