"""La configuración debe tolerar lo que docker-compose pasa cuando una variable no está definida."""
from __future__ import annotations

from app.config import Settings


def test_variables_vacias_valen_por_defecto(monkeypatch) -> None:
    # Es lo que genera `- COOKIE_SECURE=${COOKIE_SECURE:-}` en docker-compose.yml
    for nombre in ("COOKIE_SECURE", "ENCRYPTION_KEY", "SETUP_CODE", "ALLOWED_ORIGINS", "MQTT_USER",
                   "MQTT_PASSWORD", "EBRO_VIN", "DATA_DIR"):
        monkeypatch.setenv(nombre, "")
    for nombre in ("SESSION_DAYS", "POLL_INTERVAL_PLUGGED_MIN"):
        monkeypatch.setenv(nombre, "")
    cfg = Settings(_env_file=None)
    assert cfg.COOKIE_SECURE is None
    assert cfg.SESSION_DAYS == 30 and cfg.POLL_INTERVAL_PLUGGED_MIN == 30
    assert not cfg.ENCRYPTION_KEY and not cfg.SETUP_CODE


def test_valores_definidos_se_respetan(monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SESSION_DAYS", "7")
    cfg = Settings(_env_file=None)
    assert cfg.COOKIE_SECURE is True and cfg.SESSION_DAYS == 7
