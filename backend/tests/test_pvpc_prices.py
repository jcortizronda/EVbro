"""PVPC from Red Eléctrica by date and zone (calculations audit, 27-11-2026).

The old source only gave today's table, so the hours of a charge before midnight took the next day's prices; it asked for Ceuta
and Melilla for Canarias; and when it did not answer, the fallback price was used with nothing to say so.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from app.services import electricity_tariffs as et


def _ree_answer(date: str, geo: str, base: float) -> dict:
    """The shape apidatos.ree.es answered on 27-11-2026: a «PVPC» series, hourly, €/MWh, local time with its offset (winter: +01:00)."""
    values = [{"value": base + h, "datetime": f"{date}T{h:02d}:00:00.000+01:00"} for h in range(24)]
    return {"included": [{"type": "PVPC", "attributes": {"title": "PVPC", "values": values}},
                         {"type": "Precio mercado spot", "attributes": {"title": "Precio mercado spot", "values": []}}]}


class _FakeSession:
    calls: list = []
    answers: dict = {}

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    def get(self, url, params=None, headers=None, timeout=None):
        _FakeSession.calls.append((params["start_date"][:10], params["geo_limit"]))
        answer = _FakeSession.answers.get((params["start_date"][:10], params["geo_limit"]))
        return _FakeResponse(answer)


class _FakeResponse:
    def __init__(self, data): self.data, self.status = data, (200 if data else 500)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def json(self): return self.data


@pytest.fixture(autouse=True)
def fake_ree(monkeypatch):
    et._PVPC_CACHE.clear()
    et._PVPC_FAILED.clear()
    _FakeSession.calls, _FakeSession.answers = [], {}
    monkeypatch.setattr(et.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(et.settings if hasattr(et, "settings") else __import__("app.config", fromlist=["settings"]).settings, "TIMEZONE", "Europe/Madrid")
    yield
    et._PVPC_CACHE.clear()
    et._PVPC_FAILED.clear()


async def test_a_charge_across_midnight_takes_each_days_own_prices() -> None:
    _FakeSession.answers = {("2026-11-25", "peninsular"): _ree_answer("2026-11-25", "peninsular", 100.0),
                            ("2026-11-26", "peninsular"): _ree_answer("2026-11-26", "peninsular", 200.0)}
    gf = SimpleNamespace(cost_mode="pvpc", pvpc_zone="Peninsula", cost_margin=0.0, cost_fallback=None)
    # 22:00 → 02:00 local (21:00 → 01:00 UTC): 2 h at 100+22, 100+23 €/MWh and 2 h at 200+0, 200+1 €/MWh, 1 kWh each
    res = await et.calculate_charging_session_cost(gf, datetime.datetime(2026, 11, 25, 21, 0), datetime.datetime(2026, 11, 26, 1, 0), 4.0)
    assert res["total_cost"] == round((122 + 123 + 200 + 201) / 1000, 2)
    assert sorted(set(_FakeSession.calls)) == [("2026-11-25", "peninsular"), ("2026-11-26", "peninsular")]
    assert "estimado" not in res["details"]


async def test_canarias_asks_for_canarias_and_a_free_text_zone_for_the_mainland() -> None:
    assert et.pvpc_geo("Canarias") == "canarias" and et.pvpc_geo("Baleares") == "baleares" and et.pvpc_geo("Ceuta") == "ceuta"
    assert et.pvpc_geo("Melilla") == "melilla" and et.pvpc_geo("Península") == "peninsular" and et.pvpc_geo("<script>") == "peninsular"


async def test_without_the_days_prices_the_fallback_is_used_and_said_and_not_asked_again_at_once() -> None:
    gf = SimpleNamespace(cost_mode="pvpc", pvpc_zone="Peninsula", cost_margin=0.01, cost_fallback=0.2)
    res = await et.calculate_charging_session_cost(gf, datetime.datetime(2026, 11, 25, 10, 0), datetime.datetime(2026, 11, 25, 12, 0), 2.0)
    assert res["total_cost"] == round(2 * 0.21, 2) and "estimado" in res["details"]
    assert len(_FakeSession.calls) == 1                                          # the second hour did not ask the dead API again
