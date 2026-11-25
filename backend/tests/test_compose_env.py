"""Every setting of the server reaches the container through the Compose file, or is left out on purpose.

`deploy/docker-compose.yml` passes the variables one by one (`environment:`): one that is not listed never reaches the app, whatever
`.env` says. On 09-11-2026 `AUTO_LOCATE_ENABLED=true` was in the `.env` of a server for a whole deploy without any effect.
"""
from __future__ import annotations

import re

from app.config import Settings
from tests.paths import BACKEND

COMPOSE = (BACKEND.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

# Settings the container does not take from `.env`, and why. A new setting goes into the Compose file or here, never nowhere.
NOT_IN_COMPOSE = {
    "EBRO_PHONE": "Ebro account of the environment: only for running without Docker; in Docker the account is connected in the web",
    "EBRO_PASSWORD": "idem",
    "EBRO_PIN": "idem",
    "EBRO_AREA_CODE": "idem",
    "EBRO_VIN": "idem",
    "EBRO_BFF_HOST": "Ebro cloud endpoints: protocol constants, not user settings",
    "EBRO_TSP_HOST": "idem",
    "EBRO_CHANNEL_ID": "idem",
    "WEB_HOST": "the container always listens on the same address and port; the host port is mapped by Compose",
    "WEB_PORT": "idem",
    "ADMIN_BIND": "the console listens on every interface inside the container; ADMIN_BIND_ADDRESS limits it on the host",
    "DEBUG": "development only",
    "FRONTEND_DIR": "fixed in the image",
    "DATA_DIR": "fixed in the image (the volume is mounted at /app/data)",
    "CAR_MQTT_CERTS_SRC": "development only: the image carries the certificate bundle",
    "NOMINATIM_USER_AGENT": "the default identifies EVbro as OpenStreetMap asks",
    "NOMINATIM_ENABLED": "on by default; no install has needed to turn it off",
}
# Variables of the Compose file that are not EVbro settings: read by uvicorn itself
NOT_SETTINGS = {"FORWARDED_ALLOW_IPS"}


def compose_app_variables() -> set[str]:
    app_block = COMPOSE.split("\n  db:")[0]
    return set(re.findall(r"^\s*-\s*([A-Z][A-Z0-9_]*)=", app_block, re.M))


def test_every_setting_reaches_the_container_or_is_left_out_on_purpose() -> None:
    passed = compose_app_variables()
    missing = sorted(set(Settings.model_fields) - passed - set(NOT_IN_COMPOSE))
    assert not missing, f"Settings the container would never receive: {missing}. Add them to deploy/docker-compose.yml or to NOT_IN_COMPOSE"


def test_the_exceptions_are_real_settings_not_passed_and_compose_lists_nothing_unknown() -> None:
    passed = compose_app_variables()
    assert set(NOT_IN_COMPOSE) <= set(Settings.model_fields)
    assert not set(NOT_IN_COMPOSE) & passed, "a setting listed as left out is in the Compose file: take it off NOT_IN_COMPOSE"
    assert passed - set(Settings.model_fields) <= NOT_SETTINGS, passed - set(Settings.model_fields) - NOT_SETTINGS
