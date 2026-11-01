"""Tests for `core/ebro_auth.py` — the app signature + SM4 encoder.

All the crypto in this module is deterministic (SM4-ECB with a fixed key, SHA-256), so
a direct snapshot works. The ONLY source of non-determinism is `sign_post`'s default
timestamp, which is pinned by passing `ts_ms` explicitly or freezing the clock.
"""

from __future__ import annotations

import hashlib

from freezegun.api import FrozenDateTimeFactory
import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import ebro_auth
from app.core.context import CoreCtx

TS = 1700000000000


# ───────────────────────── SM4 (deterministic) ─────────────────────────


def test_sm4_vectores(snapshot: SnapshotAssertion) -> None:
    """SM4-ECB with a fixed key and no IV: same text → same output, always."""
    assert {
        "plain": ebro_auth.sm4_code("1234"),
        "padRight32": ebro_auth.sm4_code("1234", "padRight32"),
        "padLeft32": ebro_auth.sm4_code("1234", "padLeft32"),
        "bloque_exacto": ebro_auth.sm4_ecb_encrypt_pkcs7(b"0123456789abcdef").hex(),
    } == snapshot


def test_sm4_is_stable_across_calls() -> None:
    assert ebro_auth.sm4_code("1234") == ebro_auth.sm4_code("1234")


def test_sm4_pkcs7_adds_a_full_block_when_already_a_multiple() -> None:
    """Canonical PKCS#7: 16 bytes in → 32 out, not 16."""
    assert len(ebro_auth.sm4_ecb_encrypt_pkcs7(b"0123456789abcdef")) == 32
    assert len(ebro_auth.sm4_ecb_encrypt_pkcs7(b"corto")) == 16


@pytest.mark.parametrize(
    ("transform", "longitud"),
    [("plain", 4), ("padRight32", 32), ("padLeft32", 32)],
)
def test_sm4_padding_transforms(transform, longitud) -> None:
    """Padding changes the plaintext, and therefore the ciphertext too."""
    s = "1234"
    esperado = {"plain": s, "padRight32": s.ljust(32), "padLeft32": s.rjust(32)}[transform]
    assert len(esperado) == longitud
    assert ebro_auth.sm4_code(s, transform) == ebro_auth.sm4_code(esperado)


def test_sm4_accepts_non_strings() -> None:
    assert ebro_auth.sm4_code(1234) == ebro_auth.sm4_code("1234")


# ───────────────────────── sign_post ─────────────────────────


def test_sign_post_with_explicit_timestamp() -> None:
    """SHA256(secret + nonce + url + ts) in lower-case hex."""
    sig, ts = ebro_auth.sign_post("/tsp/v1/app/auth/login", ts_ms=TS)

    esperado = hashlib.sha256(
        f"{ebro_auth.SIGN_SECRET}{ebro_auth.SIGN_NONCE}/tsp/v1/app/auth/login{TS}".encode()
    ).hexdigest()

    assert ts == TS
    assert sig == esperado
    assert sig == sig.lower()
    assert len(sig) == 64


def test_sign_post_uses_the_clock_without_ts(freezer: FrozenDateTimeFactory) -> None:
    """It is the module's only non-determinism: that is why the `headers_post` tests
    freeze the clock."""
    freezer.move_to("2026-01-15 12:00:00+00:00")
    _, ts = ebro_auth.sign_post("/x")

    assert ts == 1768478400000


def test_sign_post_depends_on_path_and_ts() -> None:
    base, _ = ebro_auth.sign_post("/a", ts_ms=TS)
    assert ebro_auth.sign_post("/b", ts_ms=TS)[0] != base
    assert ebro_auth.sign_post("/a", ts_ms=TS + 1)[0] != base


# ───────────────────────── headers_post ─────────────────────────


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
def test_headers_post(snapshot: SnapshotAssertion) -> None:
    """`headers_post` never receives `ts_ms`, so a frozen clock is mandatory here."""
    assert ebro_auth.headers_post("/tsp/v1/app/vmc/queryList") == snapshot


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
def test_headers_post_takes_the_region_from_the_context() -> None:
    """Two entries with different regions must not step on each other: that is why `ctx` wins over the
    module defaults, which only serve command-line diagnostics."""
    ctx = CoreCtx(channel_id="9", country_id="7", tenant_code="9999999")

    h = ebro_auth.headers_post("/x", ctx=ctx)

    assert h["channelId"] == "9"
    assert h["countryId"] == "7"
    assert h["TENANT-ID"] == "9999999"
    assert h["TENANT-CODE"] == "9999999"


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
def test_headers_post_without_context_uses_module_defaults() -> None:
    h = ebro_auth.headers_post("/x")

    assert h["channelId"] == ebro_auth.CHANNEL_ID
    assert h["TENANT-CODE"] == ebro_auth.TENANT_CODE


@pytest.mark.freeze_time("2026-01-15 12:00:00+00:00")
def test_headers_post_dept_id_is_the_country_prefix() -> None:
    assert ebro_auth.headers_post("/x")["DEPT-ID"] == "34"
    assert ebro_auth.headers_post("/x", dept_id="39")["DEPT-ID"] == "39"


def test_region_constants_are_read_at_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`BFF`/`TENANT_CODE`/`CHANNEL_ID` are resolved from the environment when the module is imported.

    Consequence: `monkeypatch.setenv` is useless afterwards; the attribute must be
    patched. It is asserted so whoever writes new tests does not lose an afternoon over it.
    """
    monkeypatch.setenv("EBRO_TENANT_CODE", "0000000")

    import importlib

    assert ebro_auth.TENANT_CODE != "0000000"
    # and patching the attribute does change it
    monkeypatch.setattr(ebro_auth, "TENANT_CODE", "0000000")
    assert ebro_auth.headers_post("/x")["TENANT-CODE"] == "0000000"
    del importlib
