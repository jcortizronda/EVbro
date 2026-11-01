"""Tests for `cert_bundle.py` — deobfuscation of the bundled mutual-TLS certificates.

Both functions swallow **all** exceptions and return a falsy value, so the error tests
assert on the returned value and never with `pytest.raises`.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.vehicle import cert_bundle


def test_available_regions_reads_the_bundled_store() -> None:
    regiones = cert_bundle.available_regions()
    assert regiones
    assert regiones == sorted(regiones)
    assert all(isinstance(r, str) for r in regiones)


def test_decrypt_region_returns_the_three_certificates() -> None:
    """Happy path against the real `certs/store.json` shipped with the integration."""
    host = cert_bundle.available_regions()[0]
    out = cert_bundle.decrypt_region(host)

    assert out is not None
    assert set(out) == set(cert_bundle._REQUIRED)
    assert all(isinstance(v, bytes) and v for v in out.values())
    # the deobfuscated certificates must be readable PEM: if the XOR were wrong, paho
    # would fail to load them at connect time, already in production.
    assert out["ca.pem"].startswith(b"-----BEGIN ")
    assert out["client.pem"].startswith(b"-----BEGIN ")
    assert out["client.key"].startswith(b"-----BEGIN ")


def test_decrypt_region_unknown_host() -> None:
    assert cert_bundle.decrypt_region("broker.que.no.existe") is None


def test_decrypt_region_missing_required_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the store lacks one of the three certificates, None is returned outright, not a
    partial dict: connecting with an incomplete set would fail later and worse."""
    store = {
        "ks": base64.b64encode(b"\x00" * 64).decode(),
        "regions": {"host": {"ca.pem": base64.b64encode(b"x").decode()}},
    }
    monkeypatch.setattr(cert_bundle, "_load", lambda: store)

    assert cert_bundle.decrypt_region("host") is None


def test_decrypt_region_ciphertext_longer_than_keystream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keystream has a fixed length: a longer blob would indicate a corrupt store."""
    store = {
        "ks": base64.b64encode(b"\x00" * 4).decode(),
        "regions": {
            "host": {
                name: base64.b64encode(b"demasiado-largo").decode()
                for name in cert_bundle._REQUIRED
            }
        },
    }
    monkeypatch.setattr(cert_bundle, "_load", lambda: store)

    assert cert_bundle.decrypt_region("host") is None


def test_xor_with_known_keystream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The algorithm is a length-preserving XOR, just like `libapp.so` does."""
    ks = bytes(range(16))
    claro = b"HOLA"
    cifrado = bytes(c ^ ks[i] for i, c in enumerate(claro))
    store = {
        "ks": base64.b64encode(ks).decode(),
        "regions": {
            "host": {
                name: base64.b64encode(cifrado).decode() for name in cert_bundle._REQUIRED
            }
        },
    }
    monkeypatch.setattr(cert_bundle, "_load", lambda: store)

    out = cert_bundle.decrypt_region("host")

    assert out == dict.fromkeys(cert_bundle._REQUIRED, claro)


def test_unreadable_store_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing or corrupt store degrades to an empty list / None, never to an exception that
    would break the entry setup."""

    def _boom() -> dict:
        raise OSError("store ilegible")

    monkeypatch.setattr(cert_bundle, "_load", _boom)

    assert cert_bundle.available_regions() == []
    assert cert_bundle.decrypt_region("cualquiera") is None


def test_the_bundled_store_is_valid_json() -> None:
    """Guard against a `store.json` truncated by a merge: without it, the failure would only
    show up when connecting MQTT, already at the user's home."""
    with open(cert_bundle._STORE, encoding="utf-8") as fh:
        store = json.load(fh)

    assert "ks" in store
    assert store["regions"]
    for host, reg in store["regions"].items():
        assert set(cert_bundle._REQUIRED) <= set(reg), host
