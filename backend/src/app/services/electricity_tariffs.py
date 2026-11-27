"""Charging cost engine (fixed tariff, 2.0TD off-peak/mid/peak and real-time PVPC)."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional, Tuple

import aiohttp

from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

# PVPC from Red Eléctrica's open data (apidatos.ree.es): asked BY DATE AND ZONE, every hour with its instant. The previous source
# (api.preciodelaluz.org) only gave today's table, so a charge started before midnight took the next day's prices; it mapped
# Canarias to Ceuta y Melilla; and on 27-11-2026 its name no longer resolved, so every PVPC charge was being priced at the
# fallback without saying so (calculations audit, 27-11-2026). Checked that day: «PVPC» series, hourly, €/MWh, ISO time with offset.
PVPC_URL = "https://apidatos.ree.es/es/datos/mercados/precios-mercados-tiempo-real"
_PVPC_GEO = {"peninsula": "peninsular", "canarias": "canarias", "baleares": "baleares", "ceuta": "ceuta", "melilla": "melilla"}
# {(local date, geo): {UTC hour start (naive): €/kWh}}, bounded; and when a day last failed, so a dead API is not asked every hour
_PVPC_CACHE: Dict[Tuple[str, str], Dict[datetime.datetime, float]] = {}
_PVPC_FAILED: Dict[Tuple[str, str], datetime.datetime] = {}
PVPC_CACHE_DAYS = 62
PVPC_RETRY_AFTER = datetime.timedelta(minutes=10)


def pvpc_geo(zone: Optional[str]) -> str:
    """The zone as the API names it; anything unknown is the mainland (a free-text zone must not open a new query per value)."""
    import unicodedata
    key = unicodedata.normalize("NFKD", (zone or "")).encode("ascii", "ignore").decode().strip().lower()
    return next((geo for name, geo in _PVPC_GEO.items() if key.startswith(name[:5])), "peninsular")


# National holidays that make the whole day off-peak under 2.0TD (CNMC Circular 3/2020): the national ones of fixed date that cannot be
# moved, plus 6 January. Regional holidays, moved ones (a Monday after a Sunday holiday) and Good Friday (no fixed date) do not count.
TOU_HOLIDAYS = frozenset({(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)})


def to_local(dt: datetime.datetime, zone: Optional[str] = None) -> datetime.datetime:
    """The tariffs follow the local clock. Naive datetimes are UTC (how EVbro stores times); the Canary Islands use their own clock."""
    from zoneinfo import ZoneInfo

    from app.config import get_timezone
    tz = ZoneInfo("Atlantic/Canary") if zone and "canaria" in zone.lower() else get_timezone()
    aware = dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt
    return aware.astimezone(tz)


def get_time_of_use_period(dt: datetime.datetime, zone: Optional[str] = None) -> Tuple[str, str]:
    """
    The tariff period under Spain's official 2.0TD regulation, on the local clock (`dt` naive = UTC):
    - Weekends and national holidays (`TOU_HOLIDAYS`): 24 h off-peak (Valle, P3).
    - Working days:
        * Valle (P3): 00:00 to 08:00
        * Llano (P2): 08:00 to 10:00, 14:00 to 18:00 and 22:00 to 24:00
        * Punta (P1): 10:00 to 14:00 and 18:00 to 22:00
    """
    dt = to_local(dt, zone)
    # 5: Saturday, 6: Sunday
    if dt.weekday() >= 5:
        return "valle", "Valle (Fin de semana 24h)"
    if (dt.month, dt.day) in TOU_HOLIDAYS:
        return "valle", "Valle (Festivo nacional 24h)"

    hour = dt.hour
    if 0 <= hour < 8:
        return "valle", "Valle (00:00 - 08:00)"
    elif (8 <= hour < 10) or (14 <= hour < 18) or (22 <= hour < 24):
        return "llano", "Llano (08:00-10:00, 14:00-18:00, 22:00-24:00)"
    else:
        return "punta", "Punta (10:00-14:00, 18:00-22:00)"


def get_time_of_use_price(geofence: Any, dt: datetime.datetime) -> Tuple[float, str]:
    """The price that applies and its label under the time-of-use tariff."""
    period, label = get_time_of_use_period(dt)

    if period == "valle":
        price = geofence.cost_valle if getattr(geofence, "cost_valle", None) is not None else 0.08
    elif period == "llano":
        price = geofence.cost_llano if getattr(geofence, "cost_llano", None) is not None else 0.14
    else:
        price = geofence.cost_punta if getattr(geofence, "cost_punta", None) is not None else 0.22

    return float(price), f"Tarifa 2.0TD - {label}"


async def _pvpc_day(local_date: datetime.date, geo: str) -> Optional[Dict[datetime.datetime, float]]:
    """The PVPC of one local day in one zone, {UTC hour: €/kWh}, or None when the API did not give it."""
    key = (local_date.isoformat(), geo)
    if key in _PVPC_CACHE:
        return _PVPC_CACHE[key]
    failed = _PVPC_FAILED.get(key)
    if failed is not None and utcnow() - failed < PVPC_RETRY_AFTER:
        return None
    params = {"start_date": f"{local_date.isoformat()}T00:00", "end_date": f"{local_date.isoformat()}T23:59", "time_trunc": "hour",
              "geo_limit": geo}
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(PVPC_URL, params=params, headers={"Accept": "application/json"},
                                  timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json() if resp.status == 200 else None
        series = next((i for i in (data or {}).get("included", []) if (i.get("attributes") or {}).get("title") == "PVPC"), None)
        day: Dict[datetime.datetime, float] = {}
        for v in (series or {}).get("attributes", {}).get("values", []):
            at = datetime.datetime.fromisoformat(v["datetime"]).astimezone(datetime.timezone.utc).replace(tzinfo=None)
            day[at.replace(minute=0, second=0, microsecond=0)] = float(v["value"]) / 1000.0      # €/MWh → €/kWh
    except Exception as e:                                   # noqa: BLE001 — network or a changed answer: the fallback is used and said
        _LOGGER.warning("PVPC prices of %s (%s) not available: %s", local_date, geo, type(e).__name__)
        day = {}
    if not day:
        _PVPC_FAILED[key] = utcnow()
        return None
    _PVPC_FAILED.pop(key, None)
    _PVPC_CACHE[key] = day
    while len(_PVPC_CACHE) > PVPC_CACHE_DAYS * len(_PVPC_GEO):
        _PVPC_CACHE.pop(next(iter(_PVPC_CACHE)))
    return day


async def pvpc_price(dt: datetime.datetime, zone: str = "Peninsula", margin: float = 0.0,
                     fallback: float = 0.15) -> Tuple[float, str, bool]:
    """The PVPC per kWh of the hour `dt` (naive UTC) falls in, plus the margin: (price, label, estimated). `estimated` is True when
    the day's prices were not available and `fallback` was used instead."""
    utc_hour = (dt.astimezone(datetime.timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt).replace(minute=0, second=0, microsecond=0)
    day = await _pvpc_day(to_local(dt, zone).date(), pvpc_geo(zone))
    if day and utc_hour in day:
        p = round(day[utc_hour] + margin, 4)
        return p, f"PVPC REE ({p:.4f} €/kWh)", False
    fb = round(fallback + margin, 4)
    return fb, f"PVPC estimado, sin precios de ese día ({fb:.4f} €/kWh)", True


async def get_pvpc_hourly_price(dt: datetime.datetime, zone: str = "Peninsula", margin: float = 0.0,
                                fallback: float = 0.15) -> Tuple[float, str]:
    """`pvpc_price` without the estimated flag (the current-price route)."""
    price, label, _ = await pvpc_price(dt, zone=zone, margin=margin, fallback=fallback)
    return price, label


def _price(value, default: float = 0.15) -> float:
    """A price the user set, or the default when there is none. A price of 0 is free, not missing: `value or 0.15` charged
    a fixed tariff of 0 €/kWh at 0.15 (calculations audit, 27-11-2026)."""
    return default if value is None else float(value)


async def calculate_charging_session_cost(
    geofence: Optional[Any],
    start_time: datetime.datetime,
    end_time: Optional[datetime.datetime],
    kwh_added: float,
) -> Dict[str, Any]:
    """
    The total cost of a charging session from the geofence's settings.
    Supports: fixed, free, 2.0TD time-of-use and dynamic PVPC.
    """
    if not geofence or kwh_added <= 0.0:
        return {
            "total_cost": 0.0,
            "effective_kwh_cost": 0.0,
            "cost_mode": "free",
            "details": "Sin geocerca o sin coste registrado",
        }

    mode = (getattr(geofence, "cost_mode", "fixed") or "fixed").lower()

    # 1. free charge
    if mode == "free":
        return {
            "total_cost": 0.0,
            "effective_kwh_cost": 0.0,
            "cost_mode": "free",
            "details": "Recarga Gratuita",
        }

    # 2. fixed cost
    if mode == "fixed":
        fixed_p = _price(getattr(geofence, "cost_per_kwh", None))
        total = round(kwh_added * fixed_p, 2)
        return {
            "total_cost": total,
            "effective_kwh_cost": fixed_p,
            "cost_mode": "fixed",
            "details": f"Tarifa Fija ({fixed_p:.3f} €/kWh)",
        }

    # 3. time-of-use or PVPC (spread over the duration of the charge)
    finish_time = end_time or utcnow()
    total_seconds = max(60, int((finish_time - start_time).total_seconds()))

    # split the session hour by hour for an exact figure
    curr = start_time
    total_cost_accum = 0.0
    estimated_hours = 0

    while curr < finish_time:
        # end of the current hour
        next_hour = (curr.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1))
        segment_end = min(finish_time, next_hour)
        segment_seconds = max(1, int((segment_end - curr).total_seconds()))
        fraction = segment_seconds / total_seconds
        segment_kwh = kwh_added * fraction

        if mode == "time_of_use":
            p, _ = get_time_of_use_price(geofence, curr)
        elif mode == "pvpc":
            zone = getattr(geofence, "pvpc_zone", "Peninsula") or "Peninsula"
            margin = getattr(geofence, "cost_margin", 0.0) or 0.0
            fallback = _price(getattr(geofence, "cost_fallback", None))
            p, _, guessed = await pvpc_price(curr, zone=zone, margin=margin, fallback=fallback)
            estimated_hours += 1 if guessed else 0
        else:
            p = _price(getattr(geofence, "cost_per_kwh", None))

        total_cost_accum += segment_kwh * p
        curr = segment_end

    total_cost_rounded = round(total_cost_accum, 2)
    avg_price = round(total_cost_rounded / kwh_added, 4) if kwh_added > 0 else 0.0

    if mode == "time_of_use":
        period_name, _ = get_time_of_use_period(start_time)
        details_txt = f"Tarifa 2.0TD ({avg_price:.3f} €/kWh medio, inicio en {period_name})"
    elif mode == "pvpc":
        details_txt = f"Tarifa Regulada PVPC ({avg_price:.3f} €/kWh medio)"
        if estimated_hours:
            # Said in the charge, so it is not taken for the real PVPC (it used to be indistinguishable)
            details_txt += f" · estimado: sin precios de PVPC para {estimated_hours} h, se usó el precio de respaldo"
    else:
        details_txt = f"{avg_price:.3f} €/kWh"

    return {
        "total_cost": total_cost_rounded,
        "effective_kwh_cost": avg_price,
        "cost_mode": mode,
        "details": details_txt,
    }
