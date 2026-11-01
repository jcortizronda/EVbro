"""Los secretos de la BD se guardan cifrados y la migración cifra los que estaban en claro."""
from __future__ import annotations

import json

from sqlalchemy import text

from app.db.models import EbroAccount, NotificationConfig
from app.db.secrets_migration import encrypt_legacy_secrets, encrypted_columns
from app.db.session import async_session_factory
from app.security import crypto


async def _raw(engine, sql: str, **params):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).mappings().all()


async def test_la_cuenta_se_guarda_cifrada_y_se_lee_en_claro(db, data_dir) -> None:
    async with async_session_factory() as s:
        s.add(EbroAccount(phone="600123456", password="pass-secreta", pin="4321",
                          token_data={"access_token": "AT", "refresh_token": "RT"}))
        await s.commit()

    (fila,) = await _raw(db, "SELECT password, pin, token_data FROM ebro_accounts")
    for valor in (fila["password"], fila["pin"], fila["token_data"]):
        assert crypto.is_encrypted(valor)
    volcado = json.dumps(dict(fila))
    assert "pass-secreta" not in volcado and "4321" not in volcado and "access_token" not in volcado

    async with async_session_factory() as s:
        acc = (await s.execute(text("SELECT id FROM ebro_accounts"))).first()
        cuenta = await s.get(EbroAccount, acc[0])
        assert cuenta.password == "pass-secreta"
        assert cuenta.pin == "4321"
        assert cuenta.token_data == {"access_token": "AT", "refresh_token": "RT"}


async def test_canales_de_notificacion_cifrados(db, data_dir) -> None:
    async with async_session_factory() as s:
        s.add(NotificationConfig(telegram_bot_token="123:ABC", pushover_user_key="u-privada",
                                 webhook_url="https://hooks.example/x/secreto", telegram_chat_id="999"))
        await s.commit()
    (fila,) = await _raw(db, "SELECT telegram_bot_token, pushover_user_key, webhook_url, telegram_chat_id FROM notification_configs")
    assert crypto.is_encrypted(fila["telegram_bot_token"])
    assert crypto.is_encrypted(fila["pushover_user_key"])
    assert crypto.is_encrypted(fila["webhook_url"])
    assert fila["telegram_chat_id"] == "999"            # no es secreto


async def test_migracion_cifra_los_datos_en_claro_de_una_instalacion_anterior(db, data_dir) -> None:
    async with db.begin() as conn:
        await conn.execute(text(
            "INSERT INTO ebro_accounts (phone, password, pin, token_data, area_code, is_active) "
            "VALUES ('600', 'vieja-pass', '1111', :tok, '34', 1)"),
            {"tok": json.dumps({"access_token": "VIEJO"})})

    # Before migrating, the ORM already reads the legacy (plain-text) data
    async with async_session_factory() as s:
        cuenta = (await s.execute(text("SELECT id FROM ebro_accounts"))).first()
        c = await s.get(EbroAccount, cuenta[0])
        assert (c.password, c.pin, c.token_data) == ("vieja-pass", "1111", {"access_token": "VIEJO"})

    async with db.begin() as conn:
        assert await encrypt_legacy_secrets(conn) == 3
    async with db.begin() as conn:
        assert await encrypt_legacy_secrets(conn) == 0        # idempotent

    (fila,) = await _raw(db, "SELECT password, pin, token_data FROM ebro_accounts")
    assert all(crypto.is_encrypted(v) for v in fila.values())
    async with async_session_factory() as s:
        cuenta = (await s.execute(text("SELECT id FROM ebro_accounts"))).first()
        c = await s.get(EbroAccount, cuenta[0])
        assert (c.password, c.pin, c.token_data) == ("vieja-pass", "1111", {"access_token": "VIEJO"})


async def test_con_la_clave_equivocada_no_se_rompe_la_consulta(db, data_dir) -> None:
    async with async_session_factory() as s:
        s.add(EbroAccount(phone="600", password="x", pin="1234"))
        await s.commit()
    (data_dir / crypto.KEY_FILENAME).unlink()
    crypto.reset_key_cache()
    async with async_session_factory() as s:
        cuenta = (await s.execute(text("SELECT id FROM ebro_accounts"))).first()
        c = await s.get(EbroAccount, cuenta[0])
        assert c.pin is None and c.password is None            # no value, but no exception either


def test_todas_las_columnas_secretas_estan_registradas() -> None:
    nombres = {f"{t.name}.{c.name}" for t, _pk, cols in encrypted_columns() for c in cols}
    assert nombres == {
        "ebro_accounts.password", "ebro_accounts.pin", "ebro_accounts.token_data",
        "notification_configs.telegram_bot_token", "push_subscriptions.subscription",
        "notification_configs.pushover_user_key", "notification_configs.pushover_api_token",
        "notification_configs.webhook_url",
        "traccar_links.api_token",                       # the Traccar API token (docs/traccar.md)
        "catalog_remote.link",                           # the online catalogue link carries that server's key
        "mqtt_links.password",                           # a manager's own broker (Home Assistant)
    }
