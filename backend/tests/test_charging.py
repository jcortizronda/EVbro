"""Programación de carga: horas locales↔UTC, planes y la decisión de cuándo pausar o liberar."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.charge_manager import HYSTERESIS, MODE_FREE, MODE_HOLD, MODE_SCHEDULED, PlanState, decide
from app.vehicle import charging as c

MADRID = ZoneInfo("Europe/Madrid")
UTC = datetime.timezone.utc
INVIERNO = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=UTC)     # España = UTC+1
VERANO = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=UTC)       # España = UTC+2


# ───────────────────────── hora local ↔ UTC ─────────────────────────
@pytest.mark.parametrize("now,local_h,utc_h", [(INVIERNO, 3, 2), (VERANO, 3, 1), (INVIERNO, 0, 23), (VERANO, 1, 23)])
def test_local_a_utc_sigue_el_horario_vigente(now, local_h, utc_h) -> None:
    """Verificado en campo por la integración de HA: el backend interpreta startTime en UTC."""
    assert c.local_minutes_to_utc(local_h * 60, MADRID, now) == utc_h * 60


@pytest.mark.parametrize("now", [INVIERNO, VERANO])
def test_ida_y_vuelta(now) -> None:
    for minutos in (0, 59, 180, 1439):
        assert c.utc_minutes_to_local(c.local_minutes_to_utc(minutos, MADRID, now), MADRID, now) == minutos


# ───────────────────────── planes ─────────────────────────
def test_plan_normal_convierte_el_inicio_y_no_la_duracion() -> None:
    plan = c.build_plan(22 * 60, 8 * 60, 1, MADRID, INVIERNO)
    assert plan == {"cycleData": [1, 2, 3, 4, 5, 6, 7], "startTime": 21 * 60, "switchStatus": 1, "timeConsuming": 480}


def test_la_duracion_minima_del_coche_se_fuerza() -> None:
    assert c.build_plan(0, 10, 1, MADRID, INVIERNO)["timeConsuming"] == 60      # menos de 1 h da code 89


def test_el_plan_de_pausa_tiene_la_ventana_ya_terminada() -> None:
    plan = c.build_stop_plan(INVIERNO)                                          # 12:00 UTC
    inicio, dur = plan["startTime"], plan["timeConsuming"]
    assert (inicio, dur) == (10 * 60 + 30, 60)                                  # [10:30, 11:30] UTC: terminó hace 30 min
    ahora = 12 * 60
    assert not (inicio <= ahora < inicio + dur), "«ahora» no puede caer dentro de la ventana"


def test_el_plan_de_pausa_pasa_la_medianoche_sin_salirse_del_dia() -> None:
    plan = c.build_stop_plan(datetime.datetime(2026, 1, 15, 0, 30, tzinfo=UTC))
    assert plan["startTime"] == (30 - 90) % 1440 == 1380


# ───────────────────────── renovar la ventana de pausa ─────────────────────────
def test_minutos_hasta_la_proxima_apertura() -> None:
    assert c.minutes_until_start(10 * 60 + 30, INVIERNO) == 22 * 60 + 30         # ventana de pausa recién puesta
    assert c.minutes_until_start(13 * 60, INVIERNO) == 60
    assert c.minutes_until_start(11 * 60, INVIERNO) == 23 * 60                   # ya pasó hoy → mañana


def test_la_pausa_se_renueva_antes_de_que_la_ventana_vuelva_a_abrirse() -> None:
    reciente = c.build_stop_plan(INVIERNO)["startTime"]
    assert not c.hold_needs_refresh(reciente, INVIERNO)
    assert not c.hold_needs_refresh(reciente, INVIERNO + datetime.timedelta(hours=18))     # a 4 h 30 de abrirse
    assert c.hold_needs_refresh(reciente, INVIERNO + datetime.timedelta(hours=19))         # a 3 h 30: se renueva
    assert c.hold_needs_refresh(None, INVIERNO)                                            # sin dato: por prudencia


def test_renovar_deja_la_ventana_otra_vez_lejos() -> None:
    antes = c.build_stop_plan(INVIERNO)["startTime"]
    ahora = INVIERNO + datetime.timedelta(hours=19)
    despues = c.build_stop_plan(ahora)["startTime"]
    assert c.hold_needs_refresh(antes, ahora) and not c.hold_needs_refresh(despues, ahora)


# ───────────────────────── lectura de lo que tiene el coche ─────────────────────────
def test_parse_schedule_vuelve_a_hora_local() -> None:
    payload = {"mainSwitch": 1, "chargeAppointPlans": [{"cycleData": [1, 2], "startTime": 21 * 60, "switchStatus": 1, "timeConsuming": 480}]}
    s = c.parse_schedule(payload, MADRID, INVIERNO)
    assert s.enabled and s.start_minutes == 22 * 60 and s.end_minutes == 6 * 60 and s.days == (1, 2)


def test_parse_schedule_exige_los_dos_interruptores() -> None:
    plan = {"startTime": 0, "switchStatus": 0, "timeConsuming": 60}
    assert not c.parse_schedule({"mainSwitch": 1, "chargeAppointPlans": [plan]}, MADRID, INVIERNO).enabled
    assert not c.parse_schedule({"mainSwitch": 0, "chargeAppointPlans": [{**plan, "switchStatus": 1}]}, MADRID, INVIERNO).enabled


@pytest.mark.parametrize("basura", [None, "x", [], {}, {"chargeAppointPlans": "mal"}])
def test_parse_schedule_tolera_basura(basura) -> None:
    s = c.parse_schedule(basura, MADRID, INVIERNO)
    assert s is None or (not s.enabled and s.start_minutes is None)


def test_duracion_entre_dos_horas() -> None:
    assert c.duration_between(22 * 60, 6 * 60) == 8 * 60          # cruza la medianoche
    assert c.duration_between(1 * 60, 5 * 60) == 4 * 60
    assert c.duration_between(60, 60) == 0                        # inicio = fin: inválido


# ───────────────────────── decisión del gestor ─────────────────────────
def libre(target=80, **kw): return PlanState(target_pct=target, mode=MODE_FREE, **kw)
def en_pausa(reason, target=80, start=None):
    return PlanState(target_pct=target, mode=MODE_HOLD, hold_reason=reason,
                     hold_start_utc=c.build_stop_plan(INVIERNO)["startTime"] if start is None else start)


def d(state, soc=None, plugged=True, charging=False, now=INVIERNO):
    return decide(state, soc=soc, plugged=plugged, charging_now=charging, now=now)


def test_con_objetivo_100_el_gestor_no_hace_nada() -> None:
    assert d(libre(100), soc=100, charging=True) is None
    assert d(PlanState(), soc=99, charging=True) is None                     # estado por defecto: inerte


def test_al_alcanzar_el_objetivo_cargando_se_pausa() -> None:
    assert d(libre(80), soc=80, charging=True) == "hold_target"
    assert d(libre(80), soc=83, charging=True) == "hold_target"
    assert d(PlanState(target_pct=80, mode=MODE_SCHEDULED), soc=80, charging=True) == "hold_target"


def test_sin_alcanzar_el_objetivo_o_sin_cargar_no_se_pausa() -> None:
    assert d(libre(80), soc=79, charging=True) is None
    assert d(libre(80), soc=95, charging=False) is None                       # no está cargando: nada que parar


@pytest.mark.parametrize("soc", [None, 0, -1])
def test_un_soc_desconocido_nunca_cuenta(soc) -> None:
    """Con la alta tensión apagada el coche manda 0: no es «objetivo alcanzado» ni «batería baja»."""
    assert d(libre(80), soc=soc, charging=True) is None
    assert d(en_pausa("target"), soc=soc) is None


def test_la_pausa_por_objetivo_se_libera_cuando_hay_hueco_y_esta_enchufado() -> None:
    assert d(en_pausa("target", 80), soc=75) == "release"                      # 5 puntos por debajo del objetivo
    assert d(en_pausa("target", 80), soc=40) == "release"
    assert d(en_pausa("target", 80), soc=76) is None                          # histéresis de 5 puntos: no oscila
    assert HYSTERESIS == 5
    assert d(en_pausa("target", 80), soc=40, plugged=False) is None           # sin cable no hay nada que liberar


def test_subir_el_objetivo_a_100_libera_la_pausa() -> None:
    assert d(en_pausa("target", 100), soc=95) == "release"
    assert d(en_pausa("target", 100), soc=None, plugged=False) == "release"


def test_la_pausa_del_usuario_no_se_libera_sola() -> None:
    assert d(en_pausa("user", 80), soc=10) is None
    assert d(en_pausa("user", 100), soc=10) is None


def test_la_pausa_se_renueva_cuando_la_ventana_esta_cerca() -> None:
    cerca = en_pausa("user", start=(12 * 60 + 120) % 1440)                                           # abre en 2 h
    assert d(cerca) == "refresh_hold"
    assert d(en_pausa("user")) is None                                                              # recién puesta


def test_liberar_tiene_prioridad_sobre_renovar() -> None:
    cerca = en_pausa("target", 80, start=(12 * 60 + 120) % 1440)
    assert d(cerca, soc=50) == "release"
