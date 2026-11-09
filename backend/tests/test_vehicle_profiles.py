"""A new car's sheet starts with the figures of its model and year; what the owner typed is never touched."""
from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from app.db.models import Drive, Vehicle
from app.db.session import async_session_factory
from app.services import vehicle_profiles as vp
from app.services.poller import VehiclePoller

# Made-up VINs: the 10th character carries the model year (T = 2026, S = 2025)
VIN_2026 = "TEST00000T0000001"
VIN_2025 = "TEST00000S0000002"


@pytest.mark.parametrize("model, expected", [("S900 PHEV", "S900 PHEV"), ("Ebro s700 phev", "S700 PHEV"), ("S800  PHEV", "S800 PHEV"),
                                             ("IX35", None), ("Ebro Auto", None), (None, None)])
def test_the_model_name_is_recognised_however_the_cloud_writes_it(model, expected) -> None:
    assert vp.normalize_model(model) == expected


@pytest.mark.parametrize("vin, year", [(VIN_2026, 2026), (VIN_2025, 2025), ("TEST00000A0000003", 2010), ("TEST000001000000", None),
                                       ("MANABCDEF0123456X", None), ("TEST00000O0000004", None)])
def test_the_model_year_comes_from_the_tenth_character_of_the_vin(vin, year) -> None:
    assert vp.model_year_from_vin(vin) == year


def test_an_untouched_sheet_takes_the_figures_of_its_model() -> None:
    car = Vehicle(vin=VIN_2026, brand="EBRO", model="S900 PHEV", battery_capacity_kwh=19.27, fuel_tank_liters=50.0)
    assert vp.fill_defaults(car) == ["model_year", "battery_capacity_kwh", "fuel_tank_liters", "env_label"]
    assert (car.model_year, car.battery_capacity_kwh, car.fuel_tank_liters, car.env_label) == (2026, 34.5, 70.0, "0")
    s800 = Vehicle(vin=VIN_2025, brand="Ebro", model="S800 PHEV", battery_capacity_kwh=19.27, fuel_tank_liters=50.0)
    vp.fill_defaults(s800)
    assert (s800.model_year, s800.battery_capacity_kwh, s800.fuel_tank_liters) == (2025, 18.3, 60.0)


def test_what_the_owner_typed_stays_and_other_makes_are_left_alone() -> None:
    edited = Vehicle(vin=VIN_2026, brand="EBRO", model="S900 PHEV", battery_capacity_kwh=34.0, fuel_tank_liters=50.0, model_year=2025, env_label="ECO")
    assert vp.fill_defaults(edited) == ["fuel_tank_liters"]                 # only the tank was still the generic default
    assert (edited.battery_capacity_kwh, edited.model_year, edited.env_label) == (34.0, 2025, "ECO")
    hyundai = Vehicle(vin="MANABCDEF0123456X", brand="Hyundai", model="IX35", is_manual=True, battery_capacity_kwh=19.27, fuel_tank_liters=50.0)
    assert vp.fill_defaults(hyundai) == [] and hyundai.fuel_tank_liters == 50.0
    unknown = Vehicle(vin=VIN_2026, brand="EBRO", model="Ebro Auto", battery_capacity_kwh=19.27, fuel_tank_liters=50.0)
    assert vp.fill_defaults(unknown) == ["model_year"]


async def test_at_start_up_the_poller_fills_the_sheets_and_re_estimates_the_energy(db, data_dir) -> None:
    async with async_session_factory() as s:
        s.add(Vehicle(owner_id=1, vin=VIN_2026, brand="EBRO", model="S900 PHEV", battery_capacity_kwh=19.27, fuel_tank_liters=50.0))
        s.add(Drive(vin=VIN_2026, start_time=datetime.datetime(2026, 11, 1, 10), end_time=datetime.datetime(2026, 11, 1, 11),
                    distance_km=40.0, battery_used_pct=20.0, energy_consumed_kwh=3.85))
        await s.commit()
    poller = VehiclePoller()
    poller._contexts = {}
    await poller.initialize()
    async with async_session_factory() as s:
        car = (await s.execute(select(Vehicle).where(Vehicle.vin == VIN_2026))).scalar_one()
        drive = (await s.execute(select(Drive).where(Drive.vin == VIN_2026))).scalar_one()
    assert (car.battery_capacity_kwh, car.fuel_tank_liters, car.model_year) == (34.5, 70.0, 2026)
    assert drive.energy_consumed_kwh == 6.9                                 # 20 points of 34.5 kWh, as saving the sheet by hand would do
