"""Tests for `core/tsp_sign.py` — the Chery SDK's REST signature.

`ts_ms` is always an explicit argument, so the module is 100 % reproducible without
freezing the clock: it is signed against golden vectors with a fixed timestamp. If one day the
signature stops reproducing these values, every request to the TSP would stop being accepted —
it is exactly the failure this file must catch before it reaches the car.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import tsp_sign

TS = 1700000000000
"""Fixed timestamp of the golden vectors."""


def test_half_secret_known() -> None:
    """The documented HALF, verified against real captured requests."""
    assert tsp_sign.HALF == "EUEBROProd89ec59274d23491084af"
    assert tsp_sign.half_secret("abcdef") == "ace"


def test_vectores_dorados(snapshot: SnapshotAssertion) -> None:
    """Signature of a flat body and of one with a nested array, with a fixed timestamp."""
    plano = tsp_sign.build_sign({"vin": "LSJA0000000000001"}, TS)
    anidado = tsp_sign.build_sign(
        {
            "vin": "LSJA0000000000001",
            "mainSwitch": 1,
            "chargeAppointPlans": [
                {"switchStatus": 1, "startTime": 465, "duration": 360},
            ],
        },
        TS,
    )
    escalares = tsp_sign.build_sign({"vin": "V", "cycleData": [1, 2, 3, 4, 5, 6, 7]}, TS)

    assert {"plano": plano, "anidado": anidado, "escalares": escalares} == snapshot


def test_build_sign_is_upper_case_base64() -> None:
    """base64(sha256(base)).upper(), NOT hexdigest — the finding of the S23 capture."""
    sign = tsp_sign.build_sign({"vin": "V"}, TS)
    assert sign == sign.upper()
    assert sign.endswith("=")
    assert len(sign) == 44  # 32 bytes in base64


def test_build_sign_ignores_key_order() -> None:
    """`Arrays.sort` over the keys: the dict insertion order must not matter."""
    a = tsp_sign.build_sign({"vin": "V", "pin": "1234"}, TS)
    b = tsp_sign.build_sign({"pin": "1234", "vin": "V"}, TS)
    assert a == b


@pytest.mark.parametrize("vacio", [None, ""])
def test_build_sign_skips_empty_values(vacio) -> None:
    """`null` and `""` are skipped → signing with them equals leaving them out."""
    con = tsp_sign.build_sign({"vin": "V", "extra": vacio}, TS)
    sin = tsp_sign.build_sign({"vin": "V"}, TS)
    assert con == sin


def test_build_sign_depends_on_the_timestamp() -> None:
    assert tsp_sign.build_sign({"vin": "V"}, TS) != tsp_sign.build_sign({"vin": "V"}, TS + 1)


def test_flatten_scalars_without_separator() -> None:
    """`[1,2,3]` → `'123'`, verified byte by byte against real envelopes."""
    assert tsp_sign._flatten_value([1, 2, 3, 4, 5, 6, 7]) == "1234567"


def test_flatten_objects_sorts_and_skips_empty() -> None:
    plano = tsp_sign._flatten_value([{"b": 2, "a": 1, "c": "", "d": None}])
    assert plano == "a=1&b=2"


def test_flatten_obj_is_noop_for_flat_bodies() -> None:
    """Without arrays the historical algorithm stays identical (63/63 flat envelopes)."""
    plano = {"vin": "V", "pin": "1234", "n": 7}
    assert tsp_sign._flatten_obj(plano) == plano


def test_sign_body_keeps_the_real_array() -> None:
    """`build_sign` flattens a COPY: the body sent must carry the original list, not the
    flattened string — otherwise the backend would reject the request."""
    plans = [{"switchStatus": 1, "startTime": 465}]
    body = tsp_sign.sign_body({"vin": "V", "chargeAppointPlans": plans}, TS)

    assert body["chargeAppointPlans"] == plans
    assert body["appId"] == tsp_sign.APP_ID
    assert "sign" in body


def test_sign_body_signs_including_appid_but_not_sign() -> None:
    """`appId` goes into the signed parameters; `sign` is added AFTERWARDS."""
    body = tsp_sign.sign_body({"vin": "V"}, TS)
    esperado = tsp_sign.build_sign({"vin": "V", "appId": tsp_sign.APP_ID}, TS)
    assert body["sign"] == esperado


def test_sign_body_accepts_an_alternative_half() -> None:
    otra = tsp_sign.sign_body({"vin": "V"}, TS, half="OTRA")
    assert otra["sign"] != tsp_sign.sign_body({"vin": "V"}, TS)["sign"]


def test_auth_headers() -> None:
    assert tsp_sign.auth_headers("tok", TS) == {
        "Authorization": "tok",
        "timestamp": str(TS),
        "x-TenantId": "",
    }
    assert tsp_sign.auth_headers("tok", TS, tenant_id="euebro")["x-TenantId"] == "euebro"


def test_app_id_is_read_at_import_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """`APP_ID` is resolved from the environment when the module is imported.

    Practical consequence for test writers: `monkeypatch.setenv("TSP_APP_ID", …)` does
    nothing; the module attribute must be patched.
    """
    monkeypatch.setattr(tsp_sign, "APP_ID", "otro-app-id")
    assert tsp_sign.sign_body({"vin": "V"}, TS)["appId"] == "otro-app-id"
