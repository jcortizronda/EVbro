"""Cifrado de secretos en reposo."""
from __future__ import annotations

import os
import stat

import pytest

from app.config import settings
from app.security import crypto


def test_ida_y_vuelta(data_dir) -> None:
    token = crypto.encrypt("mi-contraseña-ñ", "ebro_accounts.password")
    assert token.startswith(crypto.PREFIX)
    assert "mi-contraseña" not in token
    assert crypto.decrypt(token, "ebro_accounts.password") == "mi-contraseña-ñ"


def test_dos_cifrados_del_mismo_valor_son_distintos(data_dir) -> None:
    assert crypto.encrypt("1234", "x") != crypto.encrypt("1234", "x")


def test_el_contexto_impide_copiar_un_valor_a_otro_campo(data_dir) -> None:
    token = crypto.encrypt("1234", "ebro_accounts.pin")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, "ebro_accounts.password")


def test_un_valor_manipulado_se_rechaza(data_dir) -> None:
    token = crypto.encrypt("1234", "x")
    manipulado = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(manipulado, "x")


def test_texto_plano_heredado_se_devuelve_tal_cual(data_dir) -> None:
    assert crypto.decrypt("1234", "ebro_accounts.pin") == "1234"
    assert not crypto.is_encrypted("1234")


def test_la_clave_se_genera_una_vez_con_permisos_privados(data_dir) -> None:
    crypto.encrypt("a", "x")
    path = data_dir / crypto.KEY_FILENAME
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    contenido = path.read_text()
    crypto.reset_key_cache()
    token = crypto.encrypt("b", "x")
    assert path.read_text() == contenido            # no se regenera
    assert crypto.decrypt(token, "x") == "b"


def test_otra_clave_no_descifra(data_dir) -> None:
    token = crypto.encrypt("secreto", "x")
    (data_dir / crypto.KEY_FILENAME).unlink()
    crypto.reset_key_cache()
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, "x")


def test_encryption_key_del_entorno_tiene_preferencia(data_dir, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "una-clave-de-prueba-bastante-larga")
    crypto.reset_key_cache()
    token = crypto.encrypt("secreto", "x")
    assert not (data_dir / crypto.KEY_FILENAME).exists()      # no se crea archivo
    assert crypto.decrypt(token, "x") == "secreto"


def test_encryption_key_demasiado_corta(data_dir, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "corta")
    crypto.reset_key_cache()
    with pytest.raises(RuntimeError, match="demasiado corta"):
        crypto.encrypt("x", "x")
