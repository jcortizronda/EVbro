"""Tests for `core/ebro_login.py` — OAuth2 phone+password login against the BFF."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import ebro_login


def _respuesta(json_data=None, *, status: int = 200, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_data is None:
        r.json.side_effect = ValueError("no es JSON")
    else:
        r.json.return_value = json_data
    return r


# ───────────────────────── password encryption (pure) ─────────────────────────


def test_aes_encrypt_password_es_determinista(snapshot: SnapshotAssertion) -> None:
    """AES-CBC with fixed key == IV and no nonce → same text, same output, always.

    It is a property of the protocol, not a coincidence: the backend expects exactly this
    value. Freezing it in a snapshot catches any change of key or padding.
    """
    cifrada = ebro_login.aes_encrypt_password("contrasena-de-prueba")

    assert cifrada == ebro_login.aes_encrypt_password("contrasena-de-prueba")
    assert cifrada == snapshot


def test_aes_encrypt_password_is_base64_and_block_multiple() -> None:
    crudo = base64.b64decode(ebro_login.aes_encrypt_password("hola"))
    assert len(crudo) % 16 == 0


def test_aes_encrypt_password_supports_non_ascii() -> None:
    """Padding applies to the utf-8 BYTES, not to the characters."""
    assert base64.b64decode(ebro_login.aes_encrypt_password("contraseña-ñ"))


# ───────────────────────── headers (pure) ─────────────────────────


def test_headers(snapshot: SnapshotAssertion) -> None:
    assert ebro_login._headers("34") == snapshot


def test_headers_area_code_goes_in_dept_id() -> None:
    assert ebro_login._headers("39")["dept-id"] == "39"
    # the tenant is the login one (numeric) and does NOT depend on the country
    assert ebro_login._headers("39")["tenant-code"] == ebro_login.TENANT_CODE


# ───────────────────────── response normaliser (pure) ─────────────────────────


def test_extract_from_data() -> None:
    tok = ebro_login._extract(
        {"data": {"access_token": "AT", "refresh_token": "RT", "expires_in": 43200}}
    )
    assert tok["access_token"] == "AT"
    assert tok["refresh_token"] == "RT"
    assert tok["expires_in"] == 43200


def test_extract_from_the_root() -> None:
    """The BFF sometimes answers with the token at the top level and not under `data`."""
    tok = ebro_login._extract({"access_token": "AT"})
    assert tok["access_token"] == "AT"
    assert tok["refresh_token"] is None


def test_extract_keeps_the_raw_json() -> None:
    """`raw` is what the config flow writes to token.json: it must not be lost."""
    j = {"access_token": "AT", "extra": "valor"}
    assert ebro_login._extract(j)["raw"] is j


@pytest.mark.parametrize(
    "j",
    [{}, {"data": {}}, {"msg": "credenciales erróneas"}, {"data": {"access_token": ""}}],
)
def test_extract_without_token(j) -> None:
    assert ebro_login._extract(j) is None


# ───────────────────────── password_login (mocked network) ─────────────────────────


def test_password_login_ok() -> None:
    resp = _respuesta({"data": {"access_token": "AT", "refresh_token": "RT"}})

    with patch.object(ebro_login.requests, "post", return_value=resp) as post:
        ok, tok = ebro_login.password_login("600000000", "secreta", area_code="34")

    assert ok is True
    assert tok["access_token"] == "AT"

    _, kwargs = post.call_args
    # username = "<areaCode>_<mobile>" and grant_type/scope go in the QUERY STRING
    assert kwargs["params"] == {
        "username": "34_600000000",
        "grant_type": "password",
        "scope": "server",
    }
    # the password travels encrypted in the BODY, never in plain text
    assert kwargs["data"] == {"password": ebro_login.aes_encrypt_password("secreta")}
    assert "secreta" not in str(kwargs["data"])


def test_password_login_wrong_credentials() -> None:
    resp = _respuesta({"msg": "usuario o contraseña incorrectos"}, status=200)

    with patch.object(ebro_login.requests, "post", return_value=resp):
        ok, detalle = ebro_login.password_login("600000000", "mala")

    assert ok is False
    assert "usuario o contraseña incorrectos" in detalle


def test_password_login_non_json_response() -> None:
    """A down gateway returns HTML: the parser exception must not propagate."""
    resp = _respuesta(None, status=502, text="<html>Bad Gateway</html>")

    with patch.object(ebro_login.requests, "post", return_value=resp):
        ok, detalle = ebro_login.password_login("600000000", "x")

    assert ok is False
    assert detalle.startswith("HTTP 502:")


def test_refresh_token_ok() -> None:
    resp = _respuesta({"access_token": "AT2", "refresh_token": "RT2"})

    with patch.object(ebro_login.requests, "post", return_value=resp) as post:
        ok, tok = ebro_login.refresh_token("RT1")

    assert ok is True
    assert tok["access_token"] == "AT2"
    # the refresh does NOT send a password: only the grant in the query string
    _, kwargs = post.call_args
    assert kwargs["params"]["grant_type"] == "refresh_token"
    assert kwargs["params"]["refresh_token"] == "RT1"
    assert "data" not in kwargs


def test_refresh_token_rejected() -> None:
    resp = _respuesta({"error": "invalid_grant"}, status=400)

    with patch.object(ebro_login.requests, "post", return_value=resp):
        ok, detalle = ebro_login.refresh_token("caducado")

    assert ok is False
    assert "invalid_grant" in detalle
