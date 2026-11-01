"""SQLAlchemy database models for EVBro."""
from __future__ import annotations

import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship

from app.db.types import EncryptedJSON, EncryptedText

# BIGINT on PostgreSQL; on SQLite (tests only) a BIGINT key does not autoincrement, so INTEGER
# is used there. Nothing changes in production.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Vehicle(Base):
    __tablename__ = "vehicles"
    owner_id = Column(Integer, ForeignKey("app_users.id", ondelete="SET NULL"), index=True, nullable=True)   # exactly one owner; None = nobody (fail closed)
    is_manual = Column(Boolean, default=False, nullable=False, server_default="false")   # no Ebro account: no polling, no commands

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), unique=True, index=True, nullable=False)
    name = Column(String(100), default="Ebro")
    model = Column(String(100), default="Ebro Auto")
    brand = Column(String(50), default="Ebro")
    license_plate = Column(String(20), nullable=True)
    chassis_number = Column(String(32), nullable=True)        # manual cars only, informative: their `vin` is an internal id and never changes

    # vehicle settings (Ajustes → Cuenta → Vehículo)
    registration_date = Column(DateTime, nullable=True)       # registration date: the ITV counts from it
    delivery_date = Column(DateTime, nullable=True)           # delivery date: the official plan's month intervals count from it
    battery_capacity_kwh = Column(Float, default=19.27, nullable=False, server_default="19.27")   # usable; estimates kWh from the battery %
    fuel_tank_liters = Column(Float, default=50.0, nullable=False, server_default="50")   # used to estimate a refuel's litres (tank % × capacity)
    # gasoline | diesel | phev | ev: what the interface shows (Energía and the battery only for phev/ev; Repostajes and the tank not for ev)
    fuel_type = Column(String(10), nullable=False, default="phev", server_default="phev")

    # vehicle profile (Ajustes → Vehículo)
    model_year = Column(Integer, nullable=True)
    purchase_price = Column(Float, nullable=True)             # € paid for the car
    purchase_odometer = Column(Float, default=0.0, nullable=False, server_default="0")    # km when bought (0 if new): base of the cost per km
    insurance_company = Column(String(100), nullable=True)
    insurance_policy = Column(String(60), nullable=True)
    insurance_coverage = Column(String(40), nullable=True)           # third party, extended third party, comprehensive…
    insurance_renewal_date = Column(DateTime, nullable=True)         # next renewal: EVBro sends a notice before it
    insurance_assistance_phone = Column(String(30), nullable=True)
    trim = Column(String(60), nullable=True)                         # version / trim level
    color = Column(String(40), nullable=True)
    env_label = Column(String(10), nullable=True)                    # DGT environmental label (0, ECO, C, B)
    tyre_front_size = Column(String(30), nullable=True)       # p. ej. 235/55 R19
    tyre_rear_size = Column(String(30), nullable=True)
    tyre_front_pressure_bar = Column(Float, nullable=True)    # reference pressure (cold)
    tyre_rear_pressure_bar = Column(Float, nullable=True)
    tyre_pressure_tolerance_pct = Column(Float, default=10.0, nullable=False, server_default="10")   # notice if it strays more than this % from the reference
    fuel_low_threshold = Column(Integer, default=15, nullable=False, server_default="15")      # car setting: warn at or below this fuel %
    low_battery_threshold = Column(Integer, default=20, nullable=False, server_default="20")    # car setting: warn at or below this battery %
    photo_filename = Column(String(120), nullable=True)       # in DATA_DIR/vehicle_photos
    photo_size = Column(Integer, nullable=True)               # plaintext bytes of the photo (storage quota of the owner)

    # current operating state (asleep, parked, driving, charging, offline)
    state = Column(String(30), default="parked")
    last_seen = Column(DateTime, nullable=True)

    # cache of the last known state
    odometer = Column(Float, default=0.0)
    battery_level = Column(Float, default=0.0)
    electric_range_km = Column(Float, default=0.0)
    total_range_km = Column(Float, default=0.0)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    address = Column(String(255), nullable=True)

    # vehicle-specific API credentials (cached tokens)
    car_token = Column(String(255), nullable=True)
    tuserid = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    @property
    def photo_url(self) -> str | None:
        """URL of the vehicle photo (the file name goes in `v` so the browser reloads it when it changes)."""
        return f"/api/vehicles/{self.vin}/photo?v={self.photo_filename}" if self.photo_filename else None

    # relationships
    snapshots = relationship("TelemetrySnapshot", back_populates="vehicle", cascade="all, delete-orphan")
    drives = relationship("Drive", back_populates="vehicle", cascade="all, delete-orphan")
    charging_sessions = relationship("ChargingSession", back_populates="vehicle", cascade="all, delete-orphan")
    commands = relationship("CommandAudit", back_populates="vehicle", cascade="all, delete-orphan")
    maintenance_items = relationship("MaintenanceItem", back_populates="vehicle", cascade="all, delete-orphan")


class TelemetrySnapshot(Base):
    """Periodic telemetry snapshots."""
    __tablename__ = "telemetry_snapshots"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, index=True, default=datetime.datetime.utcnow, nullable=False)

    online_status = Column(Boolean, default=False)
    state = Column(String(30), default="parked")

    # levels and range
    battery_level = Column(Float, nullable=True)      # % SOC
    electric_range_km = Column(Float, nullable=True)  # km
    total_range_km = Column(Float, nullable=True)     # km
    fuel_level = Column(Float, nullable=True)         # litres or %
    odometer_km = Column(Float, nullable=True)        # total km
    speed_kmh = Column(Float, default=0.0)            # km/h

    # GPS position
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    altitude = Column(Float, nullable=True)
    heading = Column(Float, nullable=True)

    # temperatures and climate
    outside_temp = Column(Float, nullable=True)
    inside_temp = Column(Float, nullable=True)
    climate_on = Column(Boolean, default=False)
    target_temp = Column(Float, nullable=True)

    # charging and electrical system
    battery_voltage = Column(Float, nullable=True)
    battery_current = Column(Float, nullable=True)
    charge_power_kw = Column(Float, nullable=True)
    is_charging = Column(Boolean, default=False)
    cable_connected = Column(Boolean, default=False)
    fast_charging = Column(Boolean, default=False)
    remaining_charge_time_min = Column(Integer, nullable=True)

    # body parts and locks
    is_locked = Column(Boolean, default=True)
    doors = Column(JSON, nullable=True)               # {'fl': 0, 'fr': 0, 'rl': 0, 'rr': 0, 'trunk': 0, 'hood': 0}
    windows = Column(JSON, nullable=True)             # {'fl': 0, 'fr': 0, 'rl': 0, 'rr': 0, 'sunroof': 0}
    tyre_pressures = Column(JSON, nullable=True)      # {'fl': 290, 'fr': 288, 'rl': 285, 'rr': 290} in kPa
    tyre_temps = Column(JSON, nullable=True)          # {'fl': 34, 'fr': 35, 'rl': 33, 'rr': 34} in °C

    raw_data = Column(JSON, nullable=True)            # raw payload from the backend/MQTT

    vehicle = relationship("Vehicle", back_populates="snapshots")


class Drive(Base):
    """Completed drive."""
    __tablename__ = "drives"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)

    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True, index=True)

    start_address = Column(String(255), nullable=True)
    end_address = Column(String(255), nullable=True)

    start_lat = Column(Float, nullable=True)
    start_lon = Column(Float, nullable=True)
    end_lat = Column(Float, nullable=True)
    end_lon = Column(Float, nullable=True)

    start_geofence_id = Column(Integer, ForeignKey("geofences.id", ondelete="SET NULL"), nullable=True)
    end_geofence_id = Column(Integer, ForeignKey("geofences.id", ondelete="SET NULL"), nullable=True)

    distance_km = Column(Float, default=0.0)
    duration_seconds = Column(Integer, default=0)

    speed_max_kmh = Column(Float, default=0.0)
    speed_avg_kmh = Column(Float, default=0.0)

    start_battery_pct = Column(Float, nullable=True)
    end_battery_pct = Column(Float, nullable=True)
    battery_used_pct = Column(Float, default=0.0)
    energy_consumed_kwh = Column(Float, default=0.0)

    start_fuel = Column(Float, nullable=True)
    end_fuel = Column(Float, nullable=True)
    fuel_used_l = Column(Float, default=0.0)

    outside_temp_avg = Column(Float, nullable=True)
    max_power_kw = Column(Float, default=0.0)
    climate_used = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    start_odometer = Column(Float, nullable=True)
    end_odometer = Column(Float, nullable=True)
    # the drive was detected with the car already moving: the start time, position, odometer and battery are those of the
    # last reading while parked (see services/trip_detector.py)
    start_estimated = Column(Boolean, default=False, nullable=False, server_default="false")

    positions = relationship("DrivePosition", back_populates="drive", cascade="all, delete-orphan", order_by="DrivePosition.timestamp")
    vehicle = relationship("Vehicle", back_populates="drives")


class DrivePosition(Base):
    """GPS points and telemetry along a drive, to draw the route."""
    __tablename__ = "drive_positions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    drive_id = Column(Integer, ForeignKey("drives.id", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)

    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    altitude = Column(Float, nullable=True)
    heading = Column(Float, nullable=True)
    speed_kmh = Column(Float, default=0.0)
    battery_pct = Column(Float, nullable=True)
    power_kw = Column(Float, nullable=True)

    drive = relationship("Drive", back_populates="positions")


class ChargingSession(Base):
    """Completed charging session."""
    __tablename__ = "charging_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)

    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True, index=True)

    address = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    geofence_id = Column(Integer, ForeignKey("geofences.id", ondelete="SET NULL"), nullable=True)

    start_battery_pct = Column(Float, nullable=True)
    end_battery_pct = Column(Float, nullable=True)
    start_range_km = Column(Float, nullable=True)
    end_range_km = Column(Float, nullable=True)
    kwh_added = Column(Float, default=0.0)
    kwh_used = Column(Float, nullable=True)
    duration_seconds = Column(Integer, default=0)

    max_charge_power_kw = Column(Float, default=0.0)
    charge_cost = Column(Float, default=0.0)
    cost_mode = Column(String(50), nullable=True)  # fixed, free, time_of_use, pvpc
    # AC (slow, the default) or DC fast charge: detected from the fast-charging gun flag or a power above AC levels, editable by hand
    charge_type = Column(String(4), nullable=True)
    effective_cost_per_kwh = Column(Float, nullable=True)

    points = relationship("ChargePoint", back_populates="charging_session", cascade="all, delete-orphan", order_by="ChargePoint.timestamp")
    vehicle = relationship("Vehicle", back_populates="charging_sessions")


class Expense(Base):
    """Car expense that is neither a maintenance intervention nor a refuel nor a charge: insurance, taxes, garage…"""
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    category = Column(String(20), nullable=False)         # insurance, tax, parking, tolls, fines, wash, accessories, other
    title = Column(String(150), nullable=False)
    amount = Column(Float, nullable=False)
    notes = Column(Text, nullable=True)
    next_due_date = Column(DateTime, nullable=True)       # when it is due again: EVBro sends a notice before it


class Journey(Base):
    """A named span of days (a holiday, a road trip): everything the car recorded between `start_time` and `end_time` is added up
    on demand (drives, charges, refuels, expenses, workshop visits), nothing is copied. services/journeys.py."""
    __tablename__ = "journeys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(120), nullable=False)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class VehiclePart(Base):
    """A part or consumable the car carries (oil, filters, brake pads, tyres...). Private to the vehicle's owner."""
    __tablename__ = "vehicle_parts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    category = Column(String(30), nullable=False, index=True)    # key of services.parts.PART_CATEGORIES
    position = Column(String(10), nullable=True)                  # front / rear / spare, when the category has positions
    name = Column(String(150), nullable=False)
    brand = Column(String(80), nullable=True)
    reference = Column(String(120), nullable=True)                # part number
    oem_reference = Column(String(120), nullable=True)            # the car maker's own part number («Ref. fabricante»)
    spec = Column(String(120), nullable=True)                     # viscosity, tyre size, capacity...
    quantity = Column(Float, nullable=True)                       # litres, units, sets
    attrs = Column(JSON, nullable=True)                           # tyres: {"season": ..., "dot": "2324"}
    notes = Column(Text, nullable=True)
    installed_at = Column(DateTime, nullable=True)
    installed_odometer = Column(Float, nullable=True)
    source_log_id = Column(Integer, nullable=True)                # maintenance log that fitted it (no FK: the log may be deleted)
    catalog_id = Column(Integer, nullable=True)                   # entry of the shared catalogue: my proposal, or the approved one it came from


class Refuel(Base):
    """Fuel refuel: detected by the tank level going up, or added by hand."""
    __tablename__ = "refuels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    odometer = Column(Float, nullable=True)
    fuel_before_pct = Column(Float, nullable=True)
    fuel_after_pct = Column(Float, nullable=True)
    liters = Column(Float, nullable=True)                 # estimated from tank % × capacity, or the real ones if you enter them
    price_per_liter = Column(Float, nullable=True)
    total_cost = Column(Float, nullable=True)             # None = no price yet
    station = Column(String(150), nullable=True)
    address = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    detected = Column(Boolean, default=True, nullable=False, server_default="true")    # False = added by hand
    full_tank = Column(Boolean, nullable=True)            # tank filled up? NULL = not stated (a detected one counts as full at >= 95 %)


class GeofenceEvent(Base):
    """One entry into or exit from a geofence, with its time: the history behind the geofence alerts."""
    __tablename__ = "geofence_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    geofence_id = Column(Integer, ForeignKey("geofences.id", ondelete="SET NULL"), nullable=True, index=True)
    geofence_name = Column(String(100), nullable=False)       # kept so the history survives a deleted geofence
    kind = Column(String(8), nullable=False)                  # "enter" | "exit"
    timestamp = Column(DateTime, nullable=False, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    battery_pct = Column(Float, nullable=True)
    odometer_km = Column(Float, nullable=True)


class CatalogEntry(Base):
    """Shared catalogue of consumables and references (which oil, filters, pads, tyres... fits each model).

    A manager proposes an entry from one of their parts; it is public only once an administrator approves it. The part keeps
    `catalog_id` so the owner sees the state of their proposal. Approved entries can be exported as JSON (docs/hosted-service.md)."""
    __tablename__ = "catalog_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model = Column(String(80), nullable=False, index=True)        # car model as the sheet says it (e.g. "S900 PHEV")
    make = Column(String(50), nullable=True)                      # car brand (Vehicle.brand), e.g. "Ebro"
    years = Column(String(20), nullable=True)                     # "2024-2026" or a single year, free text
    category = Column(String(30), nullable=False, index=True)     # key of services.parts.PART_CATEGORIES
    position = Column(String(10), nullable=True)
    name = Column(String(150), nullable=False)
    brand = Column(String(80), nullable=True)
    reference = Column(String(120), nullable=True)
    oem_reference = Column(String(120), nullable=True)            # the maker's part number of the first fitment
    spec = Column(String(120), nullable=True)
    quantity = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    source = Column(String(30), nullable=False, default="manual", server_default="manual")   # manual | invoice | photo
    status = Column(String(10), nullable=False, default="pending", server_default="pending", index=True)   # pending | approved | rejected
    reject_reason = Column(String(255), nullable=True)
    proposed_by = Column(Integer, ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True)
    proposed_at = Column(DateTime, default=datetime.datetime.utcnow)
    reviewed_by = Column(Integer, ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    confirmations = Column(Integer, nullable=False, default=0, server_default="0")
    # Online catalogue (services/catalog_remote.py): `remote` rows are a read-only mirror downloaded from the online server
    # (`remote_id` = its id there); a `local` proposal forwarded to that server keeps `forwarded_at`; a proposal that arrived from
    # another installation (this server being the online one) carries the note of the key it came with in `remote_note`.
    origin = Column(String(10), nullable=False, default="local", server_default="local", index=True)   # local | remote
    remote_id = Column(Integer, nullable=True)
    remote_note = Column(String(120), nullable=True)
    forwarded_at = Column(DateTime, nullable=True)


class CatalogKey(Base):
    """A key that lets another EVBro installation read this server's approved catalogue and send proposals to its queue
    (the «online catalogue», docs/hosted-service.md). Only the hash is stored; the key is shown once, as a link."""
    __tablename__ = "catalog_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    note = Column(String(120), nullable=True)                 # who it is for, e.g. "Luis, home server"
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)              # None = does not expire (a paid key would carry a date)
    revoked = Column(Boolean, default=False, nullable=False, server_default="false")
    last_used_at = Column(DateTime, nullable=True)
    downloads = Column(Integer, nullable=False, default=0, server_default="0")
    proposals = Column(Integer, nullable=False, default=0, server_default="0")


class CatalogRemote(Base):
    """Where this server takes its catalogue from: its own queue (`source = own`) or an online catalogue reached with a link
    (`https://servidor#clave`, kept encrypted). One row (id 1)."""
    __tablename__ = "catalog_remote"

    id = Column(Integer, primary_key=True)
    source = Column(String(10), nullable=False, default="own", server_default="own")     # own | remote
    link = Column(EncryptedText("catalog_remote.link"), nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(String(255), nullable=True)
    entries = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class CatalogFitment(Base):
    """One make/model (and years) a catalogue entry fits. A reference often serves several models: an entry has many fitments;
    the first one comes from the proposer's car and the administrator (or a confirming proposal) adds the rest."""
    __tablename__ = "catalog_fitments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(Integer, ForeignKey("catalog_entries.id", ondelete="CASCADE"), index=True, nullable=False)
    make = Column(String(50), nullable=True)
    model = Column(String(80), nullable=False, index=True)
    years = Column(String(20), nullable=True)
    oem_reference = Column(String(120), nullable=True)            # that make's own part number (Ebro's, Chery's...)


class MqttLink(Base):
    """A manager's own MQTT broker (usually the Mosquitto of their Home Assistant): their cars are published there with Home Assistant
    discovery and its command topics are obeyed (services/mqtt_hub.py). One per manager; the password is encrypted."""
    __tablename__ = "mqtt_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=True)                      # None = 1883, or 8883 with TLS
    username = Column(String(120), nullable=True)
    password = Column(EncryptedText("mqtt_links.password"), nullable=True)
    use_tls = Column(Boolean, default=False, nullable=False, server_default="false")
    topic_prefix = Column(String(60), nullable=False, default="evbro", server_default="evbro")
    ha_discovery = Column(Boolean, default=True, nullable=False, server_default="true")
    ha_prefix = Column(String(60), nullable=False, default="homeassistant", server_default="homeassistant")
    enabled = Column(Boolean, default=True, nullable=False, server_default="true")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class TraccarLink(Base):
    """A car followed through a Traccar Server (its own GPS tracker reports there): server, API token and device.

    Traccar pushes every fix over its WebSocket (services/traccar.py); the fixes go to `gps_positions` and, depending on
    `build_trips`, either build the drives (cars without an Ebro account) or only enrich the open drive's track."""
    __tablename__ = "traccar_links"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    host = Column(String(255), nullable=False)                 # host name, or a full http(s) URL
    port = Column(Integer, nullable=True)                      # default 8082
    use_ssl = Column(Boolean, nullable=False, default=False, server_default="false")
    verify_ssl = Column(Boolean, nullable=False, default=True, server_default="true")
    api_token = Column(EncryptedText("traccar_links.api_token"), nullable=False)
    device_id = Column(Integer, nullable=False)                # Traccar device id
    device_name = Column(String(120), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    build_trips = Column(Boolean, nullable=False, default=False, server_default="false")
    stop_minutes = Column(Integer, nullable=False, default=5, server_default="5")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_position_at = Column(DateTime, nullable=True)
    last_latitude = Column(Float, nullable=True)
    last_longitude = Column(Float, nullable=True)
    last_error = Column(String(255), nullable=True)


class GpsPosition(Base):
    """A fix received from Traccar for a car (raw history; the drive keeps its own copy of the track)."""
    __tablename__ = "gps_positions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    altitude = Column(Float, nullable=True)
    speed_kmh = Column(Float, nullable=True)
    course = Column(Float, nullable=True)
    accuracy_m = Column(Float, nullable=True)
    ignition = Column(Boolean, nullable=True)
    motion = Column(Boolean, nullable=True)
    battery_pct = Column(Float, nullable=True)


class AdminAudit(Base):
    """Append-only log of what the administrator did in the console (who, what, on whom, when, from where). Never secrets."""
    __tablename__ = "admin_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(Integer, nullable=True, index=True)
    username = Column(String(64), nullable=False)
    action = Column(String(60), nullable=False, index=True)
    target = Column(String(120), nullable=True)
    detail = Column(String(255), nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, index=True)


class ClimateSchedule(Base):
    """Pre-conditioning on a timetable: at `time_local` on the chosen weekdays EVBro sends `clima_on` with `temperature`.

    Runs with the account's stored PIN (like the charge manager); `last_run_on` guarantees one send per day."""
    __tablename__ = "climate_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    time_local = Column(String(5), nullable=False)            # "HH:MM" in the server's TIMEZONE
    days = Column(String(20), nullable=False, default="0,1,2,3,4", server_default="0,1,2,3,4")   # ISO weekdays, Monday = 0
    temperature = Column(Float, nullable=False, default=22.0, server_default="22")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    label = Column(String(60), nullable=True)
    last_run_on = Column(Date, nullable=True)
    last_result = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ChargePoint(Base):
    """Points of the charging curve (voltage, current, power over time)."""
    __tablename__ = "charge_points"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    charging_session_id = Column(Integer, ForeignKey("charging_sessions.id", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)

    battery_pct = Column(Float, nullable=False)
    voltage = Column(Float, nullable=True)
    current = Column(Float, nullable=True)
    power_kw = Column(Float, nullable=True)
    range_km = Column(Float, nullable=True)

    charging_session = relationship("ChargingSession", back_populates="points")


class Geofence(Base):
    """Geofence to identify common places (home, work, usual charger)."""
    __tablename__ = "geofences"
    owner_id = Column(Integer, nullable=True, index=True)      # manager space the geofence belongs to

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_meters = Column(Float, default=100.0)

    # purpose of the geofence and notifications
    notify_enter = Column(Boolean, default=True)
    notify_exit = Column(Boolean, default=True)
    is_charging_spot = Column(Boolean, default=True)

    # tariff model: 'fixed', 'free', 'time_of_use', 'pvpc'
    cost_mode = Column(String(30), default="fixed")

    # fixed tariff (€/kWh)
    cost_per_kwh = Column(Float, default=0.15)

    # time-of-use periods (off-peak / mid-peak / peak - Spanish 2.0TD tariff)
    cost_valle = Column(Float, default=0.08)
    cost_llano = Column(Float, default=0.14)
    cost_punta = Column(Float, default=0.22)

    # dynamic PVPC tariff (Red Eléctrica de España / ESIOS)
    pvpc_zone = Column(String(30), default="Peninsula")  # Peninsula, Canarias, Baleares, Ceuta, Melilla
    cost_margin = Column(Float, default=0.0)             # extra margin / tolls in €/kWh
    cost_fallback = Column(Float, default=0.15)          # fallback price if the external API does not answer

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class CommandAudit(Base):
    """Audit log of commands run on the vehicle."""
    __tablename__ = "command_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    command = Column(String(50), nullable=False)
    params = Column(JSON, nullable=True)
    success = Column(Boolean, default=False)
    code = Column(String(20), nullable=True)
    message = Column(Text, nullable=True)
    client_ip = Column(String(50), nullable=True)

    vehicle = relationship("Vehicle", back_populates="commands")


class MaintenanceItem(Base):
    """Periodic maintenance component or service."""
    __tablename__ = "maintenance_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(100), nullable=False)
    category = Column(String(50), default="General")  # engine, tyres, brakes, filters, ITV...

    last_service_date = Column(DateTime, nullable=True)
    last_service_odometer = Column(Float, nullable=True)

    interval_km = Column(Float, nullable=True)        # Ej. 15000 km
    interval_months = Column(Integer, nullable=True)   # e.g. 12 months

    next_due_odometer = Column(Float, nullable=True)
    next_due_date = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)
    # custom = the user's own task; official = 'container' for the official service records (not listed as a task)
    origin = Column(String(12), default="custom", nullable=False, server_default="custom")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    vehicle = relationship("Vehicle", back_populates="maintenance_items")
    logs = relationship("MaintenanceLog", back_populates="item", cascade="all, delete-orphan", order_by="desc(MaintenanceLog.service_date)")


class AppSetting(Base):
    """Application settings as key/value pairs (e.g. how many days the history is kept)."""
    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class MaintenancePlan(Base):
    """Maintenance plan of a car: EBRO's official one or a custom one (see services/maintenance_official.py)."""
    __tablename__ = "maintenance_plans"

    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), primary_key=True)
    mode = Column(String(12), default="official", nullable=False)          # official | custom
    # starting point of the official plan (steps are 15,000 km / 12 months from delivery)
    baseline_date = Column(DateTime, nullable=True)                         # delivery / registration date
    baseline_odometer = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class MaintenanceLog(Base):
    """History of services and interventions done."""
    __tablename__ = "maintenance_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("maintenance_items.id", ondelete="CASCADE"), index=True, nullable=False)
    service_date = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    odometer = Column(Float, nullable=False)
    cost = Column(Float, default=0.0)
    workshop = Column(String(150), nullable=True)
    notes = Column(Text, nullable=True)
    # official plan step it belongs to (15000, 30000…); None = intervention of an own task
    revision_km = Column(Integer, nullable=True)
    # what was done, for one-off interventions («Pastillas de freno»); the others show the task name
    title = Column(String(150), nullable=True)
    category = Column(String(50), nullable=True)           # category of the one-off intervention (brakes, tyres…)
    parts_used = Column(JSON, nullable=True)               # parts fitted in this intervention (services.parts.PartIn dumps)

    # attached invoice or receipt (PDF, JPG, PNG, WEBP)
    invoice_filename = Column(String(255), nullable=True)
    invoice_original_name = Column(String(255), nullable=True)
    invoice_content_type = Column(String(100), nullable=True)
    invoice_filesize = Column(Integer, nullable=True)

    item = relationship("MaintenanceItem", back_populates="logs")


class EbroAccount(Base):
    """Official Ebro/Chery account connected to the platform from the web interface."""
    __tablename__ = "ebro_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=True, index=True)       # the manager that connected it (owner of the cars it discovers)
    # secrets: encrypted in the DB (see db/types.py and security/crypto.py)
    password = Column(EncryptedText("ebro_accounts.password"), nullable=False)
    pin = Column(EncryptedText("ebro_accounts.pin"), nullable=True)
    area_code = Column(String(8), default="34")
    is_active = Column(Boolean, default=True)
    token_data = Column(EncryptedJSON("ebro_accounts.token_data"), nullable=True)
    last_login = Column(DateTime, default=datetime.datetime.utcnow)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AppUser(Base):
    """User who can log in to EVBro (not to be confused with the Ebro/Chery account)."""
    __tablename__ = "app_users"
    # The same person may have an administrator account and a manager account, even with the same username: they are different
    # accounts that live behind different doors (the admin console and the app), so the name only has to be unique per kind.
    __table_args__ = (UniqueConstraint("username", "is_admin", name="uq_app_users_username_kind"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), index=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False, server_default="false")     # mirrors role == "admin"; used by the unique name
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    # "admin" (runs the server from the admin console; owns nothing) | "manager" (owns cars, creates member accounts) | "member"
    role = Column(String(10), default="member", nullable=False, server_default="member")
    parent_id = Column(Integer, nullable=True)                 # manager that created this member account (None for admin/manager)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")    # False = cannot sign in (sessions are revoked)
    read_only = Column(Boolean, default=False, nullable=False, server_default="false") # never writes, whatever level a car grants
    expires_at = Column(DateTime, nullable=True)               # after this the account cannot sign in (None = never)
    vehicle_limit = Column(Integer, default=0, nullable=False, server_default="0")      # managers: cars they may own (-1 unlimited, 0 none)
    member_limit = Column(Integer, default=0, nullable=False, server_default="0")       # managers: member accounts they may create
    storage_limit_mb = Column(Integer, nullable=True)          # managers: MB of files of their cars (-1 unlimited, 0 none, None = server default)
    privacy_accepted_at = Column(DateTime, nullable=True)   # when the account accepted the privacy notice (registration with an invitation)
    # When orders ask for the PIN (services/command_policy.py): open = only those that open or unlock the car (default), always = every
    # order, never = none while a PIN is saved (the manager's explicit choice; see docs/security.md)
    pin_policy = Column(String(10), nullable=False, default="open", server_default="open")
    invited_by = Column(Integer, nullable=True)                                          # admin who issued the invitation used to register
    language = Column(String(5), nullable=False, default="es", server_default="es")       # es | en | pt (app.i18n): texts of the app and of the notices


class Invite(Base):
    """Single-use registration token handed out by an admin (hosted mode). Only the hash of the token is stored."""
    __tablename__ = "invites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    note = Column(String(120), nullable=True)                 # who it is for, e.g. "Maria (beta)"
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    vehicle_limit = Column(Integer, nullable=True)            # limits the new manager starts with (None = server defaults)
    member_limit = Column(Integer, nullable=True)
    storage_limit_mb = Column(Integer, nullable=True)         # None = the server default
    used_at = Column(DateTime, nullable=True)
    used_by = Column(Integer, nullable=True)
    revoked = Column(Boolean, default=False, nullable=False, server_default="false")


class DeviceAccess(Base):
    """Access of an account to a car it does not own: `view` < `edit` < `control`. The owner is `Vehicle.owner_id`.

    Links never cross manager spaces: a member can only be linked to cars of the manager that created it."""
    __tablename__ = "device_access"
    __table_args__ = (UniqueConstraint("user_id", "vin", name="uq_device_access_user_vin"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), index=True, nullable=False)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    level = Column(String(10), nullable=False)                 # view | edit | control
    granted_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class AuthSession(Base):
    """Session opened in a browser. The DB only stores the hash of the cookie token."""
    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)


class ChargePlan(Base):
    """Charge control of a vehicle (everything is done through the car's charge schedule; see services/charge_manager.py)."""
    __tablename__ = "charge_plans"

    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), primary_key=True)
    target_pct = Column(Integer, default=100, nullable=False)        # 100 = no limit
    mode = Column(String(12), default="free", nullable=False)        # free | scheduled | hold
    hold_reason = Column(String(12), nullable=True)                  # user | target
    hold_start_utc = Column(Integer, nullable=True)                  # UTC minute of the day when the pause window would open
    sched_enabled = Column(Boolean, default=False, nullable=False)
    sched_start_min = Column(Integer, nullable=True)                 # local wall clock
    sched_duration_min = Column(Integer, nullable=True)
    last_action = Column(String(30), nullable=True)
    last_action_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class NotificationConfig(Base):
    """Notification channels and alerts settings (Android, iOS, Web)."""
    __tablename__ = "notification_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, unique=True, index=True)       # one configuration per account: what *this* account wants to receive
    # channels
    telegram_enabled = Column(Boolean, default=False)
    telegram_bot_token = Column(EncryptedText("notification_configs.telegram_bot_token"), nullable=True)
    telegram_chat_id = Column(String(100), nullable=True)

    pushover_enabled = Column(Boolean, default=False)
    pushover_user_key = Column(EncryptedText("notification_configs.pushover_user_key"), nullable=True)
    pushover_api_token = Column(EncryptedText("notification_configs.pushover_api_token"), nullable=True)

    webhook_enabled = Column(Boolean, default=False)
    webhook_url = Column(EncryptedText("notification_configs.webhook_url"), nullable=True)  # usually carries a token in the URL

    # event triggers
    notify_geofence_enter = Column(Boolean, default=True)
    notify_geofence_exit = Column(Boolean, default=True)
    notify_fuel_low = Column(Boolean, default=True)
    notify_vehicle_faults = Column(Boolean, default=True)
    notify_windows_open = Column(Boolean, default=True)
    notify_maintenance_due = Column(Boolean, default=True)
    notify_charge_started = Column(Boolean, default=True)
    notify_charge_completed = Column(Boolean, default=True)
    notify_charge_interrupted = Column(Boolean, default=True)
    notify_tyre_pressure = Column(Boolean, default=True)
    notify_renewals = Column(Boolean, default=True)       # due dates: insurance renewal and dated expenses
    notify_security_alerts = Column(Boolean, default=True)
    notify_low_battery = Column(Boolean, default=True)

    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class PushSubscription(Base):
    """Device (browser or PWA) subscribed to Web Push. On Android, Chrome delivers through Google FCM.

    The subscription (endpoint + keys) allows encrypting and sending notices to that device, so it is
    stored encrypted; `endpoint_hash` only serves to find it and avoid duplicates.
    """
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint_hash = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=True, index=True)
    subscription = Column(EncryptedJSON("push_subscriptions.subscription"), nullable=False)
    label = Column(String(120), nullable=True)           # «Chrome en Android», to recognise it in the list
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_success = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)


class NotificationLog(Base):
    """History of notifications sent to Android, iOS and Web."""
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)       # the account it was sent to
    timestamp = Column(DateTime, default=datetime.datetime.utcnow, index=True)
    category = Column(String(50), nullable=False)   # geofence, maintenance, charging, security, test
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    channel = Column(String(50), nullable=False)    # telegram, webpush, pushover, webhook
    success = Column(Boolean, default=True)
    error = Column(Text, nullable=True)




class VehicleDocument(Base):
    """A file kept with the car: purchase invoice, insurance policy documents, registration certificate, technical sheet...

    The file lives in `DATA_DIR/vehicle_documents/` under a random name (`filename`); the name the user uploaded is only shown."""
    __tablename__ = "vehicle_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vin = Column(String(32), ForeignKey("vehicles.vin", ondelete="CASCADE"), index=True, nullable=False)
    kind = Column(String(30), nullable=False, index=True)       # purchase_invoice, insurance_policy, registration, technical_sheet, other
    title = Column(String(120), nullable=True)                  # «Condiciones particulares»
    filename = Column(String(80), nullable=False, unique=True)  # stored name (random), never the client's
    original_name = Column(String(200), nullable=False)
    content_type = Column(String(40), nullable=False)
    size = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True)


class PasskeyCredential(Base):
    """A passkey (WebAuthn credential) that lets an app account sign in with the phone's fingerprint, face or screen lock.

    Only the public key is stored: the fingerprint and the private key never leave the device. Admin accounts never get one.
    """
    __tablename__ = "passkey_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("app_users.id", ondelete="CASCADE"), index=True, nullable=False)
    credential_id = Column(String(1400), unique=True, index=True, nullable=False)   # base64url, as the browser sends it
    public_key = Column(LargeBinary, nullable=False)                               # COSE public key
    sign_count = Column(BigInteger, default=0, nullable=False)                     # clone detection (0 = the authenticator does not count)
    transports = Column(JSON, nullable=True)                                       # ["internal", "hybrid"...]: hints for the browser
    label = Column(String(120), nullable=True)                                     # «Chrome en Android», to recognise it in the list
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
