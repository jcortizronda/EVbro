"""The configuration must tolerate what docker-compose passes when a variable is not defined."""
from __future__ import annotations

from app.config import Settings


def test_empty_variables_fall_back_to_defaults(monkeypatch) -> None:
    # This is what `- COOKIE_SECURE=${COOKIE_SECURE:-}` in docker-compose.yml produces
    for name in ("COOKIE_SECURE", "ENCRYPTION_KEY", "SETUP_CODE", "ALLOWED_ORIGINS", "MQTT_USER",
                 "MQTT_PASSWORD", "EBRO_VIN", "DATA_DIR"):
        monkeypatch.setenv(name, "")
    for name in ("SESSION_DAYS", "POLL_INTERVAL_PLUGGED_MIN"):
        monkeypatch.setenv(name, "")
    cfg = Settings(_env_file=None)
    assert cfg.COOKIE_SECURE is None
    assert cfg.SESSION_DAYS == 30 and cfg.POLL_INTERVAL_PLUGGED_MIN == 30
    assert not cfg.ENCRYPTION_KEY and not cfg.SETUP_CODE


def test_defined_values_are_respected(monkeypatch) -> None:
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("SESSION_DAYS", "7")
    cfg = Settings(_env_file=None)
    assert cfg.COOKIE_SECURE is True and cfg.SESSION_DAYS == 7
