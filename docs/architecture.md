# Arquitectura

EVBro es una plataforma **autoalojada** para monitorizar, registrar y controlar vehículos Ebro (telemática Chery), con el mismo protocolo que la app oficial. Objetivo y alcance: [README](../README.md); estado y futuro: [roadmap.md](roadmap.md).

## Mapa del repositorio

```
/
├── README.md  LICENSE  AGENTS.md  CLAUDE.md
├── docs/        esta documentación
├── backend/     FastAPI + SQLAlchemy (Python 3.11)
│   ├── src/app/{api,core,vehicle,services,db,security}/ · main.py · config.py
│   ├── tests/            pytest (BD SQLite temporal; nunca toca PostgreSQL)
│   ├── Dockerfile · requirements*.txt · pyproject.toml
├── frontend/    PWA sin compilación: templates/index.html · static/{css,js,icons,images,sw.js,manifest.json}
├── deploy/      docker-compose.yml · .env.example
└── tools/       mobile_check.py (desbordes en móvil) · utilidades de desarrollo
```

Capas del backend: `core/` (protocolo Ebro) → `vehicle/` (canal MQTT del coche) → `services/` (sondeo, detectores, gestor de carga, notificaciones) → `api/` (rutas REST/WebSocket) y `security/` + `db/` transversales. El frontend lo sirve FastAPI desde `frontend/` (mismo origen: la cookie de sesión y la protección CSRF por `Origin` dependen de ello).


```
              ┌────────────────────────────────────────────────────────┐
              │               Nube Oficial Ebro / Chery                │
              │  - legend.ebroauto.com/api (OAuth2 + BFF)              │
              │  - tspconsole-eu.ebroauto.com (TSP: telemetría/control)│
              └───────────────▲────────────────────────▲───────────────┘
                              │ OAuth2 / BFF           │ REST firmado (SHA-256) + taskId
              ┌───────────────┴────────────────────────┴───────────────┐
              │                  core/ (protocolo)                     │
              │  login AES · firma BFF · firma TSP · SM4 · taskId      │
              │  PinLockout (anti-bloqueo del PIN, por vehículo)       │
              └───────────────────────▲────────────────────────────────┘
                                      │
              ┌───────────────────────┴────────────────────────────────┐
              │     FastAPI (Py 3.11) — poller + detectores + API      │
              │  seguridad: sesión por cookie · cifrado · command_policy│
              └───▲───────────────▲───────────────▲───────────────▲────┘
                  │               │               │               │
        ┌─────────┴───┐  ┌────────┴────────┐ ┌────┴────────┐ ┌────┴─────────┐
        │ PostgreSQL  │  │ Volumen /app/data│ │ Notificac.  │ │ MQTT (opc.)  │
        │ 16 (datos,  │  │ tokens, facturas,│ │ WebPush·Tg │ │ publicación  │
        │ secretos    │  │ clave de cifrado │ │ Pushover·   │ │ de telemetría│
        │ cifrados)   │  │                  │ │ webhook     │ │              │
        └─────────────┘  └──────────────────┘ └─────────────┘ └──────────────┘
                                      ▲
              ┌───────────────────────┴────────────────────────────────┐
              │ PWA (HTML + CSS + JS puro) · auth.js · Leaflet         │
              │ → futura APK (TWA o Capacitor)                         │
              └────────────────────────────────────────────────────────┘
```

## Servidor Backend
- **FastAPI** sobre Uvicorn; E/S asíncrona con `asyncio`, `asyncpg` y SQLAlchemy 2.0. El código bloqueante del core (red con `requests`/`urllib`) se ejecuta en hilos (`run_in_executor`).
- **Poller (`services/poller.py`):** un `VehiclePoller` singleton con un `CoreCtx` por vehículo, el bucle de sondeo adaptativo, los detectores de viajes/cargas y el despacho de alertas.
- **WebSocket (`/api/ws`):** emite la telemetría a los clientes conectados. **Exige sesión y origen propio** antes de aceptar la conexión (cierra con código 4401).
- **Ciclo de vida (`lifespan`):** crea/migra el esquema, avisa en el log si falta el primer usuario, y arranca el poller y el publicador MQTT.
- **`/docs`, `/redoc` y `/openapi.json` solo existen con `DEBUG=true`.**

## Capa de Almacenamiento
1. **PostgreSQL 16** (esquema con SQLAlchemy; migraciones con **Alembic** desde el 29-10-2026: `db/migrate.py` y `db/migrations/versions/`, aplicadas por la app al arrancar; el camino antiguo de `ALTER TABLE` queda en `db/session.py:_legacy_schema` solo para bases de datos anteriores, ver [development.md](development.md)). Tablas:
   - Vehículo y telemetría: `vehicles`, `telemetry_snapshots`.
   - Viajes y cargas: `drives`, `drive_positions`, `charging_sessions`, `charge_points`.
   - `geofences` (solo **círculos**: lat/lon + radio) con su tarifa.
   - `maintenance_plans`, `maintenance_items`, `maintenance_logs` (ver [maintenance.md](maintenance.md)); `refuels` y `app_settings` (ver [costs.md](costs.md)).
   - `geofence_events` (entradas y salidas con hora) y `climate_schedules` (climatización programada); ver [trips.md](trips.md) y [security.md](security.md).
   - `traccar_links` y `gps_positions` (localizador GPS del coche vía servidor Traccar; ver [traccar.md](traccar.md)).
   - `command_audit`: cada orden (y cada intento rechazado: PIN incorrecto, parámetros inválidos) con IP, código y resultado.
   - `ebro_accounts`: credenciales de la cuenta Ebro. **Cifradas** (`password`, `pin`, `token_data`).
   - `notification_configs` (secretos **cifrados**: token de Telegram, claves de Pushover, URL del webhook) y `notification_logs`; `push_subscriptions` (dispositivos de Web Push: suscripción **cifrada**, etiqueta, último envío) y `charge_plans` ([ebro-protocol.md](ebro-protocol.md), «Control de la carga»): objetivo y modo de carga por coche).
   - `app_users` y `auth_sessions`: acceso a EVBro (ver [security.md](security.md)).
2. **Volumen `/app/data`** (`DATA_DIR`, volumen Docker `evbro_data`, distinto del de PostgreSQL):
   - `<vin>/token.json`: tokens OAuth del vehículo (0600). Los lee y renueva el core.
   - `<vin>/taskid.txt`: **solo lectura** y opcional (un taskId externo que el core usaría con prioridad). **Nada lo escribe.** El `taskId` en uso se cachea **en memoria** durante 600 s (`CoreCtx.taskid_ttl`).
   - `invoices/`: facturas subidas (PDF/imagen).
   - `vehicle_documents/`: documentos del coche (factura de compra, póliza, permiso de circulación…), con nombre aleatorio; ver [costs.md](costs.md), «Documentos del coche».
   - `certs/`: certificados mutual-TLS del MQTT del coche (0600; se generan desde `vehicle/certs/store.json`).
   - `vapid_private.pem`: clave VAPID de Web Push (0600, se genera sola). **Si se pierde hay que volver a activar las notificaciones en cada dispositivo.**
   - `secret.key`: clave de cifrado si no se define `ENCRYPTION_KEY` (0600). **Hay que hacerle copia.**
   - Con `umask 077` al arrancar, todo lo que cree la app es privado.

## Stack tecnológico
| Componente | Tecnología | Versión | Propósito |
| :--- | :--- | :--- | :--- |
| Runtime | Python | 3.11 (imagen Docker) | Backend |
| Web | FastAPI + Uvicorn | ≥ 0.110 / ≥ 0.28 | API REST y WebSocket |
| ORM | SQLAlchemy (asyncio) | ≥ 2.0.28 | Acceso a datos |
| Drivers BD | asyncpg (app) · psycopg2-binary | ≥ 0.29 / ≥ 2.9.9 | PostgreSQL |
| BD | PostgreSQL | 16-alpine | Datos |
| Cifrado de campos | PyCryptodome (AES-256-GCM + HKDF) | ≥ 3.20 | Secretos en reposo |
| Hash de contraseñas | `hashlib.scrypt` (stdlib) | — | Login de EVBro |
| Cifrado del protocolo Ebro | PyCryptodome (AES-128-CBC) + **SM4 propio en Python puro** (`core/ebro_auth.py`) | — | Login y PIN hacia Chery. *PyCryptodome no trae SM4.* |
| Validación / config | Pydantic v2 · pydantic-settings | ≥ 2.6 / ≥ 2.2.1 | DTOs y variables de entorno |
| HTTP hacia Chery | `requests` (login, BFF) y `urllib` (comandos); `aiohttp` (PVPC, notificaciones, Nominatim) | — | Clientes |
| MQTT | paho-mqtt | ≥ 2.0 | Publicación opcional |
| Mapas | Leaflet | 1.9.4 | Mapa y geocercas |
| Tests | pytest · pytest-asyncio · syrupy · freezegun · httpx · aiosqlite | ver `requirements-dev.txt` | Suite (BD de tests: SQLite) |
| Contenedores | Docker + Compose v2 | — | Despliegue |

---

## Frontend
Ver [frontend.md](frontend.md).

## Futura APK para Android
Dos vías: **TWA con Bubblewrap** (empaqueta la URL del servidor sin barras de navegador) o **Capacitor** (embebe `web/static` y da acceso a APIs nativas: huella para autorizar comandos, geolocalización en segundo plano). Pendiente.

---
