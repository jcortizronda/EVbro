"""Central configuration of EVBro."""
from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # docker-compose passes `VAR=` (empty) when the user does not set it: it must mean "default",
        # not break the startup (e.g. an empty COOKIE_SECURE is not a valid boolean).
        env_ignore_empty=True,
    )

    # PostgreSQL database
    DATABASE_URL: str = "postgresql+asyncpg://evbro:evbro_password@localhost:5432/evbro"

    # Ebro account credentials
    EBRO_PHONE: str = ""
    EBRO_PASSWORD: str = ""
    EBRO_PIN: str = ""
    EBRO_AREA_CODE: str = "34"
    EBRO_VIN: Optional[str] = None  # if empty, discovered on login

    # Ebro servers (Europe by default)
    EBRO_BFF_HOST: str = "https://legend.ebroauto.com/api"
    EBRO_TSP_HOST: str = "https://tspconsole-eu.ebroauto.com"
    EBRO_CHANNEL_ID: str = "4"

    # smart polling intervals (in minutes; 0 = do not poll)
    POLL_INTERVAL_PARKED_MIN: int = 60
    POLL_INTERVAL_PLUGGED_MIN: int = 30
    POLL_INTERVAL_CHARGING_MIN: int = 2
    POLL_INTERVAL_DRIVING_MIN: int = 1
    POLL_INTERVAL_DRIVING_IDLE_MIN: int = 3

    # Web server
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    DEBUG: bool = False

    # Time zone used to read and write the scheduled charge times (the car stores them in UTC).
    # Mainland Spain by default; use an IANA name (e.g. Atlantic/Canary).
    TIMEZONE: str = "Europe/Madrid"

    # Contact declared to the Web Push services (Google FCM, Mozilla, Apple) when sending notices.
    # Apple rejects obviously fake addresses: use an email of yours if you use an iPhone.
    VAPID_SUBJECT: str = "mailto:evbro@example.com"

    # Frontend folder (templates + static files). Empty = `frontend/` is looked up next to the backend.
    FRONTEND_DIR: str = ""

    # On-disk persistence (tokens, taskId, invoices, encryption key). Empty = ./data
    DATA_DIR: str = ""

    # Encryption of secrets in the database (Ebro password and PIN, tokens, notification
    # channels). Empty = a random key is generated in DATA_DIR/secret.key. If set, it takes
    # precedence (at least 16 characters; e.g. `openssl rand -base64 32`). Losing it means
    # logging in to the Ebro account again and reconfiguring the channels.
    ENCRYPTION_KEY: Optional[str] = None

    # Access to EVBro (own login, cookie session)
    SESSION_DAYS: int = 30                 # session lifetime; renewed with use
    # Admin console: a second web interface on its own port, for the server administrator. 0 = disabled. The container listens on
    # ADMIN_BIND; what makes it local-only is how the port is published (deploy/docker-compose.yml): never put it behind a public proxy.
    ADMIN_PORT: int = 8082
    ADMIN_BIND: str = "0.0.0.0"
    # Webhooks of the managers may point to private addresses (e.g. Home Assistant on the LAN)? Off by default: a webhook is made
    # by the server, so it could otherwise be used to reach the server's own network.
    ALLOW_PRIVATE_WEBHOOKS: bool = False
    INVITE_DEFAULT_DAYS: int = 14
    # Limits a new manager starts with (-1 unlimited, 0 none); the admin can change them per account.
    DEFAULT_VEHICLE_LIMIT: int = 2
    DEFAULT_MEMBER_LIMIT: int = 5
    # MB of files (documents, maintenance invoices, car photos) a manager may keep when their account has no limit of its own (-1 unlimited).
    DEFAULT_STORAGE_MB: int = 50
    COOKIE_SECURE: Optional[bool] = None   # None = automatic (Secure if the request came over HTTPS)
    ALLOWED_ORIGINS: str = ""              # extra origins allowed for POST/WS, comma-separated
    SETUP_CODE: Optional[str] = None       # code to create the first user; empty = generated and printed in the log
    # Fingerprint login (passkeys/WebAuthn). Empty = the name the browser opens the site with (the proxy's Host header).
    WEBAUTHN_RP_ID: Optional[str] = None   # e.g. evbro.example.com (no scheme or port)
    WEBAUTHN_ORIGIN: Optional[str] = None  # e.g. https://evbro.example.com (several, comma-separated)

    # Optional MQTT integration
    MQTT_ENABLED: bool = False
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_USER: Optional[str] = None
    MQTT_PASSWORD: Optional[str] = None
    MQTT_TOPIC_PREFIX: str = "evbro"
    # Whose cars go to that broker: "owner" = only the cars of the oldest manager (the person who runs the server); "all" = every
    # manager's cars (only for a server where everybody trusts whoever reads the broker: positions and state travel through it).
    MQTT_PUBLISH_SCOPE: str = "owner"
    # Home Assistant: describe the car through MQTT discovery (same entities as the Home Assistant integration) and accept its commands
    MQTT_HA_DISCOVERY: bool = True
    MQTT_HA_PREFIX: str = "homeassistant"

    # The CAR's MQTT channel (Chery push: doors, locks, cable, engine and command confirmations).
    # Not to be confused with MQTT_*, which PUBLISHES the telemetry to your own broker.
    # It only works with the vehicle's OWNER account. Off by default: the clientId is the same
    # one the Home Assistant integration uses, and two clients with the same clientId kick each
    # other out. Turn it on only if that integration is not connected to the same account.
    CAR_MQTT_ENABLED: bool = False
    CAR_MQTT_HOST: str = "tspemqx-app-eu.ebroauto.com"
    CAR_MQTT_PORT: int = 8083
    CAR_MQTT_CERTS_SRC: str = ""   # folder with ca.pem/client.pem/client.key if the bundle is not used

    # Reverse geocoding (OpenStreetMap Nominatim)
    NOMINATIM_USER_AGENT: str = "EVBro/1.0"
    LEGAL_OPERATOR: str = ""               # who runs this server (name shown in /legal); empty = "el administrador de este servidor"
    LEGAL_CONTACT: str = ""                # how to reach them for data requests (e-mail); shown in /legal
    ANDROID_ASSETLINKS_JSON: str = ""      # /.well-known/assetlinks.json for the Android app (TWA); empty = not served
    NOMINATIM_ENABLED: bool = True

    # Shared consumables catalogue: the server a bare key (`ebc_…`) belongs to. A full link (`https://server#key`) always wins.
    CATALOG_DEFAULT_URL: str = "https://catalog.evbro.app"

    # Anonymous usage statistics and error reports (services/telemetry.py, docs/telemetry.md). Nothing is sent until the
    # administrator turns it on in the console; empty = never sent and the console says so.
    TELEMETRY_URL: str = "https://data.evbro.app"
    # This server RECEIVES the reports of other installations (only the project's own server). Off everywhere else.
    TELEMETRY_RECEIVER: bool = False


settings = Settings()


def get_timezone():
    """Configured time zone (UTC if the name does not exist)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(settings.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def get_data_dir() -> str:
    """Persistent data folder (Docker volume `evbro_data` mounted at /app/data)."""
    return settings.DATA_DIR or os.path.join(os.getcwd(), "data")
