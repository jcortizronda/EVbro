# Desarrollo

## Entorno local

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.txt    # requirements.lock = versiones exactas de la imagen; requirements.txt = lo que la app necesita
export PYTHONPATH=src
export DATABASE_URL="postgresql+asyncpg://evbro:evbro_password@localhost:5432/evbro"
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```
El frontend se sirve solo (`frontend/` junto a `backend/`); `FRONTEND_DIR` lo cambia. Con `--reload` los cambios del backend se recargan; los del frontend basta con recargar el navegador (el HTML no se cachea).

## Tests

```bash
cd backend && . .venv/bin/activate
python -m pytest                 # ~550 tests, ~70 s
python -m pytest tests/core      # solo el protocolo
```
**Los tests nunca tocan PostgreSQL:** `tests/conftest.py` fija `DATABASE_URL` a un SQLite temporal antes de importar la app y las fixtures se niegan a hacer `drop_all` en cualquier otro motor. **No ejecutes scripts sueltos que importen `app.db.session` fuera de pytest con operaciones de escritura**: usarían la URL por defecto (PostgreSQL local).

Los tests de `tests/core/` y `tests/vehicle/` vienen de la integración de HA (snapshots incluidos): ver [ha-integration.md](ha-integration.md).

## Migraciones del esquema (Alembic)

El esquema lo lleva **Alembic** (`db/migrate.py`, revisiones en `db/migrations/versions/`). La app lo aplica sola al arrancar (`init_db`): una base de datos que Alembic no conoce (vacía o anterior al 29-10-2026) se construye por el camino antiguo (`_legacy_schema`: `create_all` + la lista de `ALTER TABLE … IF NOT EXISTS`) y se marca en la revisión base; a partir de ahí solo se ejecutan las revisiones pendientes.

**Un cambio de esquema nuevo = un modelo + una revisión.** Nunca se añade nada más a `_legacy_schema`.

```bash
cd backend && . .venv/bin/activate
alembic revision -m "añade columna x a vehicles"      # crea db/migrations/versions/<rev>_anade_columna_x_a_vehicles.py
# rellena upgrade()/downgrade() con op.add_column(...) etc.; usa `with op.batch_alter_table(...)` si SQLite lo necesita
alembic -x url=sqlite+aiosqlite:////tmp/prueba.db upgrade head    # probarla sobre una BD vacía
alembic current                                        # revisión de la BD de DATABASE_URL (en el servidor: dentro del contenedor)
```
`tests/test_migrations.py` comprueba que `upgrade head` sobre una BD vacía crea todas las tablas y columnas de los modelos, que la app marca y luego solo actualiza, y que no hay dos cabezas. Las migraciones **de datos** de una sola vez siguen en `_data_migrations` (marca en `app_settings`).

## Pruebas de navegador

`tools/e2e/` (Playwright) ejecuta la interfaz real contra un servidor de demostración aislado: ver [tools/e2e/README.md](../tools/e2e/README.md). Se usan al tocar el frontend; `tools/mobile_check.py` falla si alguna pantalla se sale a 360/390/430 px y `tools/desktop_check.py` comprueba la interfaz de ordenador (barra lateral, sin dock, sin desbordes a 1024/1366/1920 px). `python tools/inline_styles.py --check` vigila que no vuelva ningún `style="…"` (la CSP no lo admite; `--apply` lo convierte en clases).

## Calidad

```bash
cd backend && ruff check src tests      # configuración en pyproject.toml; core/ y vehicle/ excluidos (protocolo del coche, estilo propio)
```

## Datos de demostración

```bash
docker exec evbro python -m app.seed_demo
```

## Convenciones del código

- **Rutas nuevas** van en el router `protected` de `api/router.py`; un test recorre OpenAPI y falla si una ruta queda sin proteger.
- **Secretos nuevos en BD:** columna `EncryptedText`/`EncryptedJSON` y la migración de arranque los cifra sola.
- **Comandos que abren el coche** piden PIN en cada envío y los `params` pasan por lista blanca (`services/command_policy.py`).
- **Migraciones:** a mano (`ALTER TABLE … IF NOT EXISTS` en `db/session.py:init_db`); Alembic está instalado pero sin usar.
- **El protocolo se corrige en un sitio y se porta al otro** (EVBro ↔ integración de HA).
- Mensajes de usuario y comentarios en español.

## Variables de entorno

Todas en `deploy/.env.example`. Las de seguridad: `ENCRYPTION_KEY`, `SETUP_CODE`, `SESSION_DAYS`, `COOKIE_SECURE`, `ALLOWED_ORIGINS`, `FORWARDED_ALLOW_IPS`, `BIND_ADDRESS`. Zona horaria de la carga programada: `TIMEZONE` (por defecto `Europe/Madrid`). Contacto de Web Push: `VAPID_SUBJECT`. Canal del coche: `CAR_MQTT_ENABLED` (false), `CAR_MQTT_HOST`, `CAR_MQTT_PORT`, `CAR_MQTT_CERTS_SRC`. Una variable vacía (lo que pasa compose con `${X:-}`) vale «por defecto» (`env_ignore_empty`).
