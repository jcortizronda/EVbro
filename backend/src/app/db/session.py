"""Configuración del motor de base de datos y sesiones asíncronas."""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.models import Base

_LOGGER = logging.getLogger(__name__)

# Configuración del motor asíncrono
database_url = settings.DATABASE_URL
# Si se usa postgresql:// estándar, convertir a postgresql+asyncpg://
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    database_url,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Brings the database to the current schema and runs the one-time data migrations.

    Alembic owns the schema (`db/migrate.py`, `db/migrations/versions/`). A database it does not track yet (empty, or created before
    Alembic) is built by the legacy path below and stamped at the baseline; from then on only the pending revisions run. **New schema
    changes go in a revision, never in `_legacy_schema`.**"""
    from app.db import migrate
    if await migrate.is_tracked(engine):
        await migrate.upgrade_to_head()
        _LOGGER.info("Esquema de la base de datos al día (Alembic, revisión %s).", migrate.head_revision())
    else:
        await _legacy_schema()
        await migrate.stamp_head()
        _LOGGER.info("Base de datos marcada en la revisión base de Alembic (%s); a partir de aquí migra con revisiones.", migrate.head_revision())
    await _data_migrations()


async def _legacy_schema() -> None:
    """Pre-Alembic schema path (frozen on 2026-10-29): `create_all` plus the columns that were added by hand over time. It only
    runs on databases Alembic does not track yet; it must stay equivalent to the `0001_baseline` revision."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS invoice_filename VARCHAR(255);"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS invoice_original_name VARCHAR(255);"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS invoice_content_type VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS invoice_filesize INTEGER;"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS revision_km INTEGER;"))
            await conn.execute(text("ALTER TABLE maintenance_items ADD COLUMN IF NOT EXISTS origin VARCHAR(12) NOT NULL DEFAULT 'custom';"))
            await conn.execute(text("ALTER TABLE drives ADD COLUMN IF NOT EXISTS start_estimated BOOLEAN NOT NULL DEFAULT FALSE;"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS title VARCHAR(150);"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS category VARCHAR(50);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS registration_date TIMESTAMP;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS delivery_date TIMESTAMP;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS battery_capacity_kwh DOUBLE PRECISION NOT NULL DEFAULT 19.27;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS fuel_tank_liters DOUBLE PRECISION NOT NULL DEFAULT 50;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS model_year INTEGER;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS purchase_price DOUBLE PRECISION;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS purchase_odometer DOUBLE PRECISION NOT NULL DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS insurance_company VARCHAR(100);"))
            await conn.execute(text("ALTER TABLE maintenance_logs ADD COLUMN IF NOT EXISTS parts_used JSON;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS role VARCHAR(10) NOT NULL DEFAULT 'member';"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS fuel_low_threshold INTEGER NOT NULL DEFAULT 15;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS low_battery_threshold INTEGER NOT NULL DEFAULT 20;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
            await conn.execute(text("ALTER TABLE notification_logs ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS parent_id INTEGER;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS read_only BOOLEAN NOT NULL DEFAULT FALSE;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS vehicle_limit INTEGER NOT NULL DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS member_limit INTEGER NOT NULL DEFAULT 0;"))
            await conn.execute(text("ALTER TABLE invites ADD COLUMN IF NOT EXISTS vehicle_limit INTEGER;"))
            await conn.execute(text("ALTER TABLE invites ADD COLUMN IF NOT EXISTS member_limit INTEGER;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS owner_id INTEGER;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS is_manual BOOLEAN NOT NULL DEFAULT FALSE;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS owner_id INTEGER;"))
            await conn.execute(text("ALTER TABLE ebro_accounts ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
            await conn.execute(text("ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS user_id INTEGER;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS invited_by INTEGER;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS insurance_policy VARCHAR(60);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS insurance_coverage VARCHAR(40);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS insurance_renewal_date TIMESTAMP;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS insurance_assistance_phone VARCHAR(30);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS trim VARCHAR(60);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS color VARCHAR(40);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS env_label VARCHAR(10);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tyre_front_size VARCHAR(30);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tyre_rear_size VARCHAR(30);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tyre_front_pressure_bar DOUBLE PRECISION;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tyre_rear_pressure_bar DOUBLE PRECISION;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tyre_pressure_tolerance_pct DOUBLE PRECISION NOT NULL DEFAULT 10;"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS photo_filename VARCHAR(120);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS chassis_number VARCHAR(32);"))
            await conn.execute(text("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS photo_size INTEGER;"))
            await conn.execute(text("ALTER TABLE app_users ADD COLUMN IF NOT EXISTS storage_limit_mb INTEGER;"))
            await conn.execute(text("ALTER TABLE invites ADD COLUMN IF NOT EXISTS storage_limit_mb INTEGER;"))
            # La fecha de entrega que se guardaba en el plan de mantenimiento pasa a la configuración del vehículo
            await conn.execute(text(
                "UPDATE vehicles v SET delivery_date = p.baseline_date FROM maintenance_plans p "
                "WHERE p.vin = v.vin AND v.delivery_date IS NULL AND p.baseline_date IS NOT NULL;"))

            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS notify_enter BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS notify_exit BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS is_charging_spot BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_mode VARCHAR(30) DEFAULT 'fixed';"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_valle FLOAT DEFAULT 0.08;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_llano FLOAT DEFAULT 0.14;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_punta FLOAT DEFAULT 0.22;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS pvpc_zone VARCHAR(30) DEFAULT 'Peninsula';"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_margin FLOAT DEFAULT 0.0;"))
            await conn.execute(text("ALTER TABLE geofences ADD COLUMN IF NOT EXISTS cost_fallback FLOAT DEFAULT 0.15;"))

            await conn.execute(text("ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS cost_mode VARCHAR(50);"))
            await conn.execute(text("ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS effective_cost_per_kwh FLOAT;"))
            await conn.execute(text("ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS start_range_km FLOAT;"))
            await conn.execute(text("ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS end_range_km FLOAT;"))
            await conn.execute(text("ALTER TABLE charging_sessions ADD COLUMN IF NOT EXISTS kwh_used FLOAT;"))
            await conn.execute(text("ALTER TABLE charge_points ADD COLUMN IF NOT EXISTS range_km FLOAT;"))

            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_fuel_low BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS fuel_low_threshold INTEGER DEFAULT 15;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_vehicle_faults BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_windows_open BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_charge_interrupted BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_tyre_pressure BOOLEAN DEFAULT TRUE;"))
            await conn.execute(text("ALTER TABLE notification_configs ADD COLUMN IF NOT EXISTS notify_renewals BOOLEAN DEFAULT TRUE;"))
        except Exception as e:
            _LOGGER.warning("Aviso al verificar columnas en base de datos: %s", e)
    _LOGGER.info("Tablas de base de datos inicializadas correctamente.")


async def _data_migrations() -> None:
    """One-time data migrations, each idempotent and in its own transaction (a flag in `app_settings` marks the ones already done)."""
    # Secretos en reposo: columnas a TEXT (PostgreSQL) y cifrado de lo que estuviera en claro.
    # Transacción aparte: un fallo aquí no debe arrastrar las migraciones de arriba.
    from app.db.secrets_migration import encrypt_legacy_secrets, widen_columns_to_text
    try:
        async with engine.begin() as conn:
            await widen_columns_to_text(conn)
            await encrypt_legacy_secrets(conn)
    except Exception:
        _LOGGER.exception(
            "No se pudieron cifrar los secretos guardados. Siguen legibles pero sin cifrar; "
            "revisa ENCRYPTION_KEY / DATA_DIR y reinicia."
        )

    # Una sola vez: la «energía de la red» que se guardaba era un cálculo (añadida / 0,95), no un dato. Se borra para que la
    # interfaz muestre «desconocida» hasta que el usuario indique los kWh de su contador.
    try:
        async with engine.begin() as conn:
            done = (await conn.execute(text("SELECT 1 FROM app_settings WHERE key = 'migration.kwh_used_cleared'"))).first()
            if not done:
                await conn.execute(text("UPDATE charging_sessions SET kwh_used = NULL"))
                await conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('migration.kwh_used_cleared', '1')"))
    except Exception as e:
        _LOGGER.warning("Aviso al limpiar la energía de red estimada: %s", e)

    # Una sola vez: los umbrales de combustible y batería eran de la configuración global de avisos; ahora son del coche.
    try:
        async with engine.begin() as conn:
            done = (await conn.execute(text("SELECT 1 FROM app_settings WHERE key = 'migration.thresholds_to_vehicle'"))).first()
            if not done:
                has_cols = engine.dialect.name == "postgresql" and (await conn.execute(text("SELECT 1 FROM information_schema.columns WHERE table_name = 'notification_configs' "
                                                    "AND column_name = 'fuel_low_threshold'"))).first()
                if has_cols:
                    await conn.execute(text("UPDATE vehicles SET fuel_low_threshold = COALESCE((SELECT fuel_low_threshold FROM notification_configs "
                                            "ORDER BY id LIMIT 1), 15), low_battery_threshold = COALESCE((SELECT low_battery_threshold FROM "
                                            "notification_configs ORDER BY id LIMIT 1), 20)"))
                await conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('migration.thresholds_to_vehicle', '1')"))
    except Exception as e:
        _LOGGER.warning("Aviso al mover los umbrales de avisos al coche: %s", e)

    await _migrate_accounts_and_owners()

    # Once: uploaded files that were stored in plaintext are encrypted with the server key (and old photos get their size).
    from app.db.files_migration import encrypt_legacy_files
    try:
        await encrypt_legacy_files()
    except Exception:
        _LOGGER.exception("No se pudieron cifrar los ficheros subidos que estaban sin cifrar; se reintentará en el próximo arranque.")


async def _migrate_accounts_and_owners() -> None:
    """One-time data migration of the account model (PostgreSQL), in its OWN transaction: if a step fails nothing else is rolled back
    with it (the columns added above are already committed) and the failure is reported, not hidden.

    * the administrator is a separate account that owns nothing (it lives in the admin console), so the accounts that were "admin"
      (they own the cars) become managers; the administrator is created from the console with the setup code;
    * a username only has to be unique per kind of account (administrator / everyone else);
    * whatever existed before ownership belongs to the oldest manager."""
    if engine.dialect.name != "postgresql":
        return
    try:
        async with engine.begin() as conn:
            if not (await conn.execute(text("SELECT 1 FROM app_settings WHERE key = 'migration.admin_accounts_to_managers'"))).first():
                await conn.execute(text("UPDATE app_users SET role = 'manager', is_admin = FALSE WHERE role = 'admin';"))
                await conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('migration.admin_accounts_to_managers', '1')"))
            if not (await conn.execute(text("SELECT 1 FROM app_settings WHERE key = 'migration.username_unique_per_kind'"))).first():
                await conn.execute(text("ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_username_key;"))
                await conn.execute(text("DROP INDEX IF EXISTS ix_app_users_username;"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_app_users_username ON app_users (username);"))
                await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_app_users_username_kind ON app_users (username, is_admin);"))
                await conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('migration.username_unique_per_kind', '1')"))
            # Accounts created with a fixed id (the old first-user setup used id=1) leave the id sequence behind: the next insert would
            # collide with an existing primary key. Put the sequence after the highest id (harmless if it is already there).
            await conn.execute(text("SELECT setval(pg_get_serial_sequence('app_users', 'id'), COALESCE((SELECT MAX(id) FROM app_users), 1), "
                                    "(SELECT MAX(id) IS NOT NULL FROM app_users));"))
            owner = "(SELECT MIN(id) FROM app_users WHERE role = 'manager')"
            for table, column in (("vehicles", "owner_id"), ("geofences", "owner_id"), ("ebro_accounts", "user_id"), ("push_subscriptions", "user_id"),
                                  ("notification_configs", "user_id"), ("notification_logs", "user_id")):
                await conn.execute(text(f"UPDATE {table} SET {column} = {owner} WHERE {column} IS NULL AND {owner} IS NOT NULL;"))
    except Exception as e:
        _LOGGER.error("FALLÓ la migración de cuentas y propietarios (no se ha aplicado nada de ella): %s", e)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Inyector de dependencias para FastAPI."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
