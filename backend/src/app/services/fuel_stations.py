"""Fuel stations and their prices in Spain (Repostajes → «Gasolineras cercanas», and the price of a detected refuel).

The source is the Ministry's open service (Geoportal de Gasolineras, MITECO): one JSON with every station in Spain (≈11 500), its
position, brand, address, opening hours and the price of each fuel, refreshed by the Ministry several times a day. No key.

It is about 12 MB, so it is downloaded at most once an hour and only when someone needs it (a look at the map, a refuel to
price): never on a timer. What was parsed is kept in memory and in DATA_DIR/fuel_stations.json, so a restart does not download it
again. When the download fails the last list stays in use.

Only Spain for now (the Ministry's list covers the peninsula, the islands, Ceuta and Melilla).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import aiohttp

from app.config import get_data_dir, settings
from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

SOURCE_URL = "https://sedeaplicaciones.minetur.gob.es/ServiciosRESTCarburantes/PreciosCarburantes/EstacionesTerrestres/"
FRESH = datetime.timedelta(hours=1)            # the map: not older than this (the Ministry refreshes it several times a day)
USABLE = datetime.timedelta(hours=12)          # a detected refuel: a price from this morning is still the day's price
DOWNLOAD_TIMEOUT = 60                          # seconds (12 MB)
CACHE_FILE = "fuel_stations.json"

# the petrol fuels a petrol hybrid takes: key → the Ministry's field; the first is the default (95 E5)
FUELS: Dict[str, str] = {
    "g95e5": "Precio Gasolina 95 E5",
    "g95e10": "Precio Gasolina 95 E10",
    "g95e5p": "Precio Gasolina 95 E5 Premium",
    "g98e5": "Precio Gasolina 98 E5",
    "g98e10": "Precio Gasolina 98 E10",
}
DEFAULT_FUEL = "g95e5"
# a refuel is priced with the station's 95 E5 or, when it does not sell it, its 95 E10
REFUEL_FUELS = ("g95e5", "g95e10")
# how far the car may be from the station's registered point to take it for the station where it refuelled (the point is often
# the entrance and the pumps are some metres in)
REFUEL_MATCH_M = 250


@dataclass
class Station:
    id: str
    name: str
    address: str
    town: str
    hours: str
    lat: float
    lon: float
    prices: Dict[str, float] = field(default_factory=dict)


_state: Dict = {"stations": None, "fetched_at": None, "source_time": None, "loaded": False}
_lock = asyncio.Lock()


def _number(text) -> Optional[float]:
    """«1,519» → 1.519; empty → None (the Ministry writes Spanish decimals as text)."""
    if text is None:
        return None
    s = str(text).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _tidy(text) -> str:
    return " ".join(str(text or "").split())


def parse(payload: Dict) -> List[Station]:
    """The Ministry's JSON → stations with a position and at least one petrol price (the rest are left out)."""
    out: List[Station] = []
    for row in payload.get("ListaEESSPrecio") or []:
        lat, lon = _number(row.get("Latitud")), _number(row.get("Longitud (WGS84)"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        prices = {key: p for key, source in FUELS.items() if (p := _number(row.get(source))) is not None and 0.3 < p < 5}
        if not prices:
            continue
        out.append(Station(id=str(row.get("IDEESS") or ""), name=_tidy(row.get("Rótulo")) or "Gasolinera", address=_tidy(row.get("Dirección")),
                           town=_tidy(row.get("Localidad") or row.get("Municipio")), hours=_tidy(row.get("Horario")), lat=lat, lon=lon,
                           prices=prices))
    return out


def _cache_path() -> str:
    return os.path.join(get_data_dir(), CACHE_FILE)


def _load_cache() -> None:
    """The list saved by an earlier run (once per process)."""
    _state["loaded"] = True
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            saved = json.load(f)
        _state["stations"] = [Station(**s) for s in saved["stations"]]
        _state["fetched_at"] = datetime.datetime.fromisoformat(saved["fetched_at"])
        _state["source_time"] = saved.get("source_time")
    except FileNotFoundError:
        pass
    except Exception as e:                                   # noqa: BLE001 — a broken cache is simply downloaded again
        _LOGGER.warning("The saved fuel station list could not be read: %s", e)


def _save_cache() -> None:
    try:
        path = _cache_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": _state["fetched_at"].isoformat(), "source_time": _state["source_time"],
                       "stations": [asdict(s) for s in _state["stations"]]}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:                                   # noqa: BLE001 — without the file it is only downloaded again after a restart
        _LOGGER.warning("The fuel station list could not be saved: %s", e)


async def _download() -> Dict:
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/json", "User-Agent": "EVbro"}) as client:
        async with client.get(SOURCE_URL) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            text = await resp.text(encoding="utf-8")
    payload = json.loads(text.lstrip("﻿"))
    if str(payload.get("ResultadoConsulta", "")).upper() != "OK":
        raise RuntimeError(f"ResultadoConsulta={payload.get('ResultadoConsulta')!r}")
    return payload


class Unavailable(Exception):
    """No list of stations: never downloaded and the source cannot be reached (or the feature is off)."""


async def stations(max_age: datetime.timedelta = FRESH) -> List[Station]:
    """Every station, downloading the list again when it is older than `max_age`. A failed download keeps the last list."""
    if not settings.FUEL_PRICES_ENABLED:
        raise Unavailable("off")
    async with _lock:
        if not _state["loaded"]:
            _load_cache()
        fetched = _state["fetched_at"]
        if _state["stations"] is not None and fetched is not None and utcnow() - fetched < max_age:
            return _state["stations"]
        try:
            payload = await _download()
            parsed = parse(payload)
            if not parsed:
                raise RuntimeError("empty list")
            _state.update(stations=parsed, fetched_at=utcnow(), source_time=_tidy(payload.get("Fecha")) or None)
            _save_cache()
            _LOGGER.info("Fuel station list downloaded: %d stations (%s)", len(parsed), _state["source_time"])
        except Exception as e:                               # noqa: BLE001 — the source failing must not break the map or the poller
            _LOGGER.warning("The fuel station list could not be downloaded: %s", e, extra={"telemetry": False})
            if _state["stations"] is None:
                raise Unavailable(str(e)) from e
        return _state["stations"]


def info() -> Dict:
    """When the list in use was downloaded and the time the Ministry gives for it."""
    fetched = _state["fetched_at"]
    return {"fetched_at": fetched.isoformat() + "Z" if fetched else None, "source_time": _state["source_time"]}


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearby(all_stations: List[Station], lat: float, lon: float, radius_km: float, fuel: str, limit: int) -> List[Dict]:
    """The stations within `radius_km` that sell `fuel`, nearest first, each with its distance and that fuel's price."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    found = []
    for s in all_stations:
        if abs(s.lat - lat) > dlat or abs(s.lon - lon) > dlon or fuel not in s.prices:
            continue
        d = distance_km(lat, lon, s.lat, s.lon)
        if d <= radius_km:
            found.append((d, s))
    found.sort(key=lambda x: x[0])
    return [{"id": s.id, "name": s.name, "address": s.address, "town": s.town, "hours": s.hours, "lat": s.lat, "lon": s.lon,
             "distance_km": round(d, 2), "price": s.prices[fuel]} for d, s in found[:limit]]


def at_station(all_stations: List[Station], lat: float, lon: float, within_m: float = REFUEL_MATCH_M) -> Optional[Station]:
    """The station the car is at: the nearest one within `within_m` metres, or None."""
    best, best_d = None, within_m / 1000.0
    for s in all_stations:
        if abs(s.lat - lat) > 0.01 or abs(s.lon - lon) > 0.015:
            continue
        d = distance_km(lat, lon, s.lat, s.lon)
        if d <= best_d:
            best, best_d = s, d
    return best


async def price_refuel(refuel) -> bool:
    """A detected refuel takes the price per litre (95 E5, or E10) and the name of the station where the car was, and its cost
    (estimated litres × price). Nothing changes when the car was not at a known station or the list cannot be had."""
    if refuel.latitude is None or refuel.longitude is None or refuel.price_per_liter is not None:
        return False
    try:
        all_stations = await asyncio.wait_for(stations(USABLE), timeout=DOWNLOAD_TIMEOUT + 10)
    except (Unavailable, asyncio.TimeoutError):
        return False
    station = at_station(all_stations, refuel.latitude, refuel.longitude)
    if station is None:
        return False
    price = next((station.prices[f] for f in REFUEL_FUELS if f in station.prices), None)
    if price is None:
        return False
    refuel.price_per_liter = price
    refuel.station = refuel.station or station.name[:150]
    if refuel.liters:
        refuel.total_cost = round(refuel.liters * price, 2)
    return True


def _reset_for_tests() -> None:
    _state.update(stations=None, fetched_at=None, source_time=None, loaded=False)
