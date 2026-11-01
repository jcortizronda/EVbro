"""Demo script / test data for EVBro.

Fills the database with an 'Ebro S900 Demo' vehicle,
live telemetry, several drives with GPS track and fuel,
charging sessions, maintenance tasks and a service log.
"""
import asyncio
import datetime
import random

from sqlalchemy import select

from app.db.models import (
    ChargePoint,
    ChargingSession,
    Drive,
    DrivePosition,
    Geofence,
    MaintenanceItem,
    MaintenanceLog,
    TelemetrySnapshot,
    Vehicle,
)
from app.db.session import async_session_factory, init_db

# The demo is seeded before the first account exists; the setup flow always creates it with id 1, so the demo data belongs to it.
DEMO_OWNER_ID = 1

async def seed():
    print("Initialising the database and seeding full test data...")
    await init_db()

    async with async_session_factory() as session:
        # 1. geofences
        gf_stmt = select(Geofence)
        existing_gfs = (await session.execute(gf_stmt)).scalars().all()
        if not existing_gfs:
            gf_casa = Geofence(owner_id=DEMO_OWNER_ID, name="Casa (Garaje)", latitude=40.4168, longitude=-3.7038, radius_meters=120, cost_per_kwh=0.14)
            gf_trabajo = Geofence(owner_id=DEMO_OWNER_ID, name="Oficina / Sede Central", latitude=40.4530, longitude=-3.6883, radius_meters=150, cost_per_kwh=0.0)
            gf_super = Geofence(owner_id=DEMO_OWNER_ID, name="Supermercado Centro", latitude=40.4300, longitude=-3.6950, radius_meters=100, cost_per_kwh=0.25)
            session.add_all([gf_casa, gf_trabajo, gf_super])
            await session.flush()
        else:
            gf_casa = existing_gfs[0]
            gf_trabajo = existing_gfs[1] if len(existing_gfs) > 1 else existing_gfs[0]

        # 2. vehicle (remove any previous one for a clean re-seed)
        vin = "LSJA8888888888888"
        v_existing = (await session.execute(select(Vehicle).where(Vehicle.vin == vin))).scalar_one_or_none()
        if v_existing:
            await session.delete(v_existing)
            await session.flush()

        vehicle = Vehicle(
            owner_id=DEMO_OWNER_ID,
            vin=vin,
            name="Ebro Demo",
            model="Ebro Auto",
            brand="Ebro",
            license_plate="1234-EBR",
            state="parked",
            last_seen=datetime.datetime.utcnow(),
            odometer=14520.5,
            battery_level=78.0,
            electric_range_km=68.0,
            total_range_km=840.0,
            latitude=40.4168,
            longitude=-3.7038,
            address="Puerta del Sol, Madrid",
        )
        session.add(vehicle)
        await session.flush()

        # 3. telemetry snapshot
        snap = TelemetrySnapshot(
            vin=vin,
            online_status=True,
            state="parked",
            battery_level=78.0,
            electric_range_km=68.0,
            total_range_km=840.0,
            fuel_level=42.0,
            odometer_km=14520.5,
            speed_kmh=0.0,
            latitude=40.4168,
            longitude=-3.7038,
            altitude=655.0,
            heading=180.0,
            outside_temp=19.5,
            inside_temp=21.0,
            climate_on=False,
            target_temp=21.5,
            battery_voltage=330.2,
            battery_current=0.0,
            charge_power_kw=0.0,
            is_charging=False,
            cable_connected=False,
            fast_charging=False,
            is_locked=True,
            doors={"fl": 0, "fr": 0, "rl": 0, "rr": 0, "trunk": 0, "hood": 0},
            windows={"fl": 0, "fr": 0, "rl": 0, "rr": 0, "sunroof": 0},
            tyre_pressures={"fl": 245.0, "fr": 244.0, "rl": 240.0, "rr": 241.0},
            tyre_temps={"fl": 22.0, "fr": 23.0, "rl": 21.0, "rr": 22.0},
            raw_data={"demo": True},
        )
        session.add(snap)

        now = datetime.datetime.utcnow()

        # 4. several test drives (with fuel and metrics)
        drives_data = [
            {
                "start": now - datetime.timedelta(days=1, hours=2),
                "dur_min": 18,
                "dist": 14.5,
                "start_addr": "Paseo de la Castellana 120, Madrid",
                "end_addr": "Puerta del Sol 1, Madrid",
                "start_soc": 82.0, "end_soc": 65.0,
                "fuel_used": 0.4,
                "temp": 18.5,
                "spd_avg": 38.0, "spd_max": 78.0, "pwr_max": 95.0,
                "climate": True,
            },
            {
                "start": now - datetime.timedelta(days=2, hours=5),
                "dur_min": 35,
                "dist": 38.2,
                "start_addr": "Puerta del Sol 1, Madrid",
                "end_addr": "Carretera M-40 km 15, Madrid",
                "start_soc": 95.0, "end_soc": 55.0,
                "fuel_used": 1.2,
                "temp": 16.0,
                "spd_avg": 65.5, "spd_max": 112.0, "pwr_max": 135.0,
                "climate": True,
            },
            {
                "start": now - datetime.timedelta(days=3, hours=4),
                "dur_min": 12,
                "dist": 6.8,
                "start_addr": "Calle Serrano 45, Madrid",
                "end_addr": "Parque del Retiro, Madrid",
                "start_soc": 68.0, "end_soc": 61.0,
                "fuel_used": 0.0,
                "temp": 20.0,
                "spd_avg": 26.0, "spd_max": 52.0, "pwr_max": 45.0,
                "climate": False,
            },
        ]

        for idx, dd in enumerate(drives_data):
            drive_start = dd["start"]
            drive_end = drive_start + datetime.timedelta(minutes=dd["dur_min"])
            bat_used = round(dd["start_soc"] - dd["end_soc"], 1)
            kwh = round((bat_used / 100.0) * 19.27, 2)

            d = Drive(
                vin=vin,
                start_time=drive_start,
                end_time=drive_end,
                start_address=dd["start_addr"],
                end_address=dd["end_addr"],
                start_lat=40.4530 if idx == 0 else 40.4168,
                start_lon=-3.6883 if idx == 0 else -3.7038,
                end_lat=40.4168,
                end_lon=-3.7038,
                distance_km=dd["dist"],
                duration_seconds=dd["dur_min"] * 60,
                speed_avg_kmh=dd["spd_avg"],
                speed_max_kmh=dd["spd_max"],
                max_power_kw=dd["pwr_max"],
                start_battery_pct=dd["start_soc"],
                end_battery_pct=dd["end_soc"],
                battery_used_pct=bat_used,
                energy_consumed_kwh=kwh,
                fuel_used_l=dd["fuel_used"],
                outside_temp_avg=dd["temp"],
                climate_used=dd["climate"],
                start_odometer=14520.5 - dd["dist"],
                end_odometer=14520.5,
            )
            session.add(d)
            await session.flush()

            # GPS points for the first drive
            if idx == 0:
                positions = []
                steps = 15
                for i in range(steps):
                    ratio = i / (steps - 1)
                    t = drive_start + datetime.timedelta(seconds=int(ratio * dd["dur_min"] * 60))
                    lat = 40.4530 - (40.4530 - 40.4168) * ratio
                    lon = -3.6883 - (-3.6883 - (-3.7038)) * ratio
                    spd = 45.0 + random.uniform(-10, 15) if 0 < i < steps - 1 else 0.0
                    bat = dd["start_soc"] - (bat_used * ratio)
                    positions.append(
                        DrivePosition(
                            drive_id=d.id,
                            timestamp=t,
                            latitude=lat,
                            longitude=lon,
                            altitude=660 - (10 * ratio),
                            speed_kmh=round(spd, 1),
                            battery_pct=round(bat, 1),
                        )
                    )
                session.add_all(positions)

        # 5. incomplete drive (to try the Incomplete Drives section)
        inc_drive = Drive(
            vin=vin,
            start_time=now - datetime.timedelta(minutes=45),
            end_time=None,  # incomplete
            start_address="Aeropuerto Madrid-Barajas T4",
            end_address=None,
            start_lat=40.4900,
            start_lon=-3.5900,
            distance_km=18.4,
            duration_seconds=45 * 60,
            start_battery_pct=72.0,
            end_battery_pct=None,
            start_odometer=14502.1,
            speed_max_kmh=85.0,
            speed_avg_kmh=42.0,
        )
        session.add(inc_drive)

        # 6. test charging session
        charge_start = now - datetime.timedelta(days=1, hours=8)
        charge_end = charge_start + datetime.timedelta(hours=2, minutes=15)
        charge = ChargingSession(
            vin=vin,
            start_time=charge_start,
            end_time=charge_end,
            address="Puerta del Sol (Cargador Garaje Casa)",
            latitude=40.4168,
            longitude=-3.7038,
            geofence_id=gf_casa.id,
            start_battery_pct=25.0,
            end_battery_pct=95.0,
            kwh_added=13.5,
            duration_seconds=135 * 60,
            max_charge_power_kw=6.6,
            charge_cost=round(13.5 * 0.14, 2),
        )
        session.add(charge)
        await session.flush()

        charge_pts = []
        for i in range(10):
            ratio = i / 9.0
            t = charge_start + datetime.timedelta(seconds=int(ratio * 135 * 60))
            pct = 25.0 + (70.0 * ratio)
            charge_pts.append(
                ChargePoint(
                    charging_session_id=charge.id,
                    timestamp=t,
                    battery_pct=round(pct, 1),
                    voltage=230.0,
                    current=28.5 if pct < 85 else 12.0,
                    power_kw=6.6 if pct < 85 else 2.8,
                )
            )
        session.add_all(charge_pts)

        # 7. maintenance tasks and components (Ebro PHEV maintenance)
        maint_items = [
            MaintenanceItem(
                vin=vin,
                name="Cambio de Aceite y Filtro Motor",
                category="Motor",
                interval_km=15000.0,
                interval_months=12,
                last_service_odometer=10000.0,
                last_service_date=now - datetime.timedelta(days=110),
                next_due_odometer=25000.0,
                next_due_date=now + datetime.timedelta(days=255),
                notes="Aceite sintético 0W-20 según especificación Ebro.",
            ),
            MaintenanceItem(
                vin=vin,
                name="Filtro de Habitáculo / Aire Acondicionado",
                category="Filtros",
                interval_km=20000.0,
                interval_months=12,
                last_service_odometer=0.0,
                last_service_date=now - datetime.timedelta(days=340),
                next_due_odometer=20000.0,
                next_due_date=now + datetime.timedelta(days=25),  # coming due soon
                notes="Filtro de partículas y carbón activo.",
            ),
            MaintenanceItem(
                vin=vin,
                name="Rotación y Alineación de Neumáticos",
                category="Neumáticos",
                interval_km=10000.0,
                interval_months=12,
                last_service_odometer=5000.0,
                last_service_date=now - datetime.timedelta(days=200),
                next_due_odometer=15000.0,  # ~480 km to go
                next_due_date=now + datetime.timedelta(days=165),
                notes="Revisar desgaste interior en eje delantero.",
            ),
            MaintenanceItem(
                vin=vin,
                name="Líquido de Frenos DOT 4",
                category="Frenos",
                interval_km=30000.0,
                interval_months=24,
                last_service_odometer=0.0,
                last_service_date=now - datetime.timedelta(days=180),
                next_due_odometer=30000.0,
                next_due_date=now + datetime.timedelta(days=550),
                notes="Comprobar humedad y nivel del circuito.",
            ),
            MaintenanceItem(
                vin=vin,
                name="Inspección Técnica Vehicular (ITV)",
                category="Legal",
                interval_km=None,
                interval_months=48,
                last_service_odometer=0.0,
                last_service_date=now - datetime.timedelta(days=365),
                next_due_odometer=None,
                next_due_date=now + datetime.timedelta(days=1095),
                notes="Primera ITV a los 4 años desde la matriculación.",
            ),
        ]
        session.add_all(maint_items)
        await session.flush()

        # 8. log of services already done
        log1 = MaintenanceLog(
            item_id=maint_items[0].id,
            service_date=now - datetime.timedelta(days=110),
            odometer=10000.0,
            cost=145.0,
            workshop="Concesionario Oficial Ebro",
            notes="Cambio de aceite Castrol 0W-20 y filtro original.",
        )
        log2 = MaintenanceLog(
            item_id=maint_items[2].id,
            service_date=now - datetime.timedelta(days=200),
            odometer=5000.0,
            cost=40.0,
            workshop="Taller Neumáticos Rápido",
            notes="Rotación cruzada y equilibrado de 4 ruedas.",
        )
        session.add_all([log1, log2])

        await session.commit()
        print("Database seeded with advanced data:")
        print("   • Drives with electric and petrol consumption, efficiencies and GPS tracks.")
        print("   • Scheduled maintenance tasks with health tracking.")
        print("   • History of interventions and costs.")


if __name__ == "__main__":
    asyncio.run(seed())
