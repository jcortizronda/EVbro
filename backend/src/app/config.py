"""Configuración central de EVBro."""
from __future__ import annotations

import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # docker-compose pasa `VAR=` (vacía) cuando el usuario no la define: debe valer «por defecto»,
        # no romper el arranque (p. ej. COOKIE_SECURE vacío no es un booleano válido).
        env_ignore_empty=True,
    )

    # Base de datos PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://evbro:evbro_password@localhost:5432/evbro"

    # Credenciales de cuenta Ebro
    EBRO_PHONE: str = ""
    EBRO_PASSWORD: str = ""
    EBRO_PIN: str = ""
    EBRO_AREA_CODE: str = "34"
    EBRO_VIN: Optional[str] = None  # Si está vacío, se autodescubre en login

    # Servidores Ebro (por defecto Europa)
    EBRO_BFF_HOST: str = "https://legend.ebroauto.com/api"
    EBRO_TSP_HOST: str = "https://tspconsole-eu.ebroauto.com"
    EBRO_CHANNEL_ID: str = "4"

    # Intervalos de sondeo inteligente (en minutos; 0 = no sondear)
    POLL_INTERVAL_PARKED_MIN: int = 60
    POLL_INTERVAL_PLUGGED_MIN: int = 30
    POLL_INTERVAL_CHARGING_MIN: int = 2
    POLL_INTERVAL_DRIVING_MIN: int = 1
    POLL_INTERVAL_DRIVING_IDLE_MIN: int = 3

    # Servidor Web
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8080
    DEBUG: bool = False

    # Zona horaria con la que se leen y escriben las horas de la carga programada (el coche las guarda en UTC).
    # Por defecto, España peninsular; usa un nombre IANA (p. ej. Atlantic/Canary).
    TIMEZONE: str = "Europe/Madrid"

    # Contacto que se declara a los servicios de Web Push (Google FCM, Mozilla, Apple) al enviar avisos.
    # Apple rechaza direcciones claramente falsas: pon un correo tuyo si usas iPhone.
    VAPID_SUBJECT: str = "mailto:evbro@example.com"

    # Carpeta del frontend (plantillas + estáticos). Vacío = se busca `frontend/` junto al backend.
    FRONTEND_DIR: str = ""

    # Persistencia en disco (tokens, taskId, facturas, clave de cifrado). Vacío = ./data
    DATA_DIR: str = ""

    # Cifrado de secretos en la base de datos (contraseña y PIN de Ebro, tokens, canales de
    # notificación). Vacío = se genera una clave aleatoria en DATA_DIR/secret.key. Si se define,
    # tiene preferencia (mínimo 16 caracteres; p. ej. `openssl rand -base64 32`). Perderla
    # implica volver a iniciar sesión con la cuenta Ebro y reconfigurar los canales.
    ENCRYPTION_KEY: Optional[str] = None

    # Acceso a EVBro (login propio, sesión por cookie)
    SESSION_DAYS: int = 30                 # vida de la sesión; se renueva con el uso
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
    COOKIE_SECURE: Optional[bool] = None   # None = automático (Secure si la petición llegó por HTTPS)
    ALLOWED_ORIGINS: str = ""              # orígenes extra permitidos para POST/WS, separados por comas
    SETUP_CODE: Optional[str] = None       # código para crear el primer usuario; vacío = se genera y se imprime en el log
    # Acceso con huella (passkeys/WebAuthn). Vacío = el nombre con el que el navegador abre la web (cabecera Host del proxy).
    WEBAUTHN_RP_ID: Optional[str] = None   # p. ej. evbro.midominio.es (sin esquema ni puerto)
    WEBAUTHN_ORIGIN: Optional[str] = None  # p. ej. https://evbro.midominio.es (varios, separados por comas)

    # Integración MQTT opcional
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

    # Canal MQTT del COCHE (push de Chery: puertas, cierre, cable, motor y confirmación de comandos).
    # No confundir con MQTT_*, que PUBLICA la telemetría hacia tu propio broker.
    # Solo funciona con la cuenta PROPIETARIA del vehículo. Desactivado por defecto: el clientId es
    # el mismo que usa la integración de Home Assistant, y dos clientes con el mismo clientId se
    # expulsan mutuamente. Actívalo solo si esa integración no está conectada a la misma cuenta.
    CAR_MQTT_ENABLED: bool = False
    CAR_MQTT_HOST: str = "tspemqx-app-eu.ebroauto.com"
    CAR_MQTT_PORT: int = 8083
    CAR_MQTT_CERTS_SRC: str = ""   # carpeta con ca.pem/client.pem/client.key si no se usa el bundle

    # Geocodificación inversa (OpenStreetMap Nominatim)
    NOMINATIM_USER_AGENT: str = "EVBro/1.0"
    LEGAL_OPERATOR: str = ""               # who runs this server (name shown in /legal); empty = "el administrador de este servidor"
    LEGAL_CONTACT: str = ""                # how to reach them for data requests (e-mail); shown in /legal
    ANDROID_ASSETLINKS_JSON: str = ""      # /.well-known/assetlinks.json for the Android app (TWA); empty = not served
    NOMINATIM_ENABLED: bool = True


settings = Settings()


def get_timezone():
    """Zona horaria configurada (UTC si el nombre no existe)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(settings.TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def get_data_dir() -> str:
    """Carpeta de datos persistentes (volumen Docker `evbro_data` montado en /app/data)."""
    return settings.DATA_DIR or os.path.join(os.getcwd(), "data")
