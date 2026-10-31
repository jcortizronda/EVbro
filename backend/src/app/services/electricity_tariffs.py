"""Motor de cálculo de costes de recarga eléctrica (Tarifa Fija, 2.0TD Valle/Llano/Punta y PVPC en tiempo real)."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional, Tuple

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Caché en memoria para precios horarios del PVPC: {"YYYY-MM-DD_zone": {hour: price_eur_kwh}}
_PVPC_CACHE: Dict[str, Dict[int, float]] = {}


def get_time_of_use_period(dt: datetime.datetime) -> Tuple[str, str]:
    """
    Determina el periodo tarifario según la regulación oficial 2.0TD de España:
    - Fines de semana y festivos: 24h Valle (P3).
    - Días laborables:
        * Valle (P3): 00:00 a 08:00
        * Llano (P2): 08:00 a 10:00, 14:00 a 18:00 y 22:00 a 24:00
        * Punta (P1): 10:00 a 14:00 y 18:00 a 22:00
    """
    # 5: Sábado, 6: Domingo
    if dt.weekday() >= 5:
        return "valle", "Valle (Fin de semana 24h)"

    hour = dt.hour
    if 0 <= hour < 8:
        return "valle", "Valle (00:00 - 08:00)"
    elif (8 <= hour < 10) or (14 <= hour < 18) or (22 <= hour < 24):
        return "llano", "Llano (08:00-10:00, 14:00-18:00, 22:00-24:00)"
    else:
        return "punta", "Punta (10:00-14:00, 18:00-22:00)"


def get_time_of_use_price(geofence: Any, dt: datetime.datetime) -> Tuple[float, str]:
    """Devuelve el precio aplicable y la etiqueta según la tarifa con discriminación horaria."""
    period, label = get_time_of_use_period(dt)

    if period == "valle":
        price = geofence.cost_valle if getattr(geofence, "cost_valle", None) is not None else 0.08
    elif period == "llano":
        price = geofence.cost_llano if getattr(geofence, "cost_llano", None) is not None else 0.14
    else:
        price = geofence.cost_punta if getattr(geofence, "cost_punta", None) is not None else 0.22

    return float(price), f"Tarifa 2.0TD - {label}"


async def get_pvpc_hourly_price(
    dt: datetime.datetime,
    zone: str = "Peninsula",
    margin: float = 0.0,
    fallback: float = 0.15,
) -> Tuple[float, str]:
    """
    Obtiene el precio del kWh del mercado regulado PVPC para una hora específica.
    Consulta la API pública del operador del sistema y almacena en caché para las 24 horas del día.
    """
    date_key = dt.strftime("%Y-%m-%d")
    cache_key = f"{date_key}_{zone}"

    if cache_key in _PVPC_CACHE and dt.hour in _PVPC_CACHE[cache_key]:
        base_price = _PVPC_CACHE[cache_key][dt.hour]
        total_price = round(base_price + margin, 4)
        return total_price, f"PVPC REE ({total_price:.4f} €/kWh)"

    # Zona PCB: Península, CYM: Ceuta y Melilla, BALEARES, CANARIAS
    zone_code = "PCB"
    if "canaria" in zone.lower():
        zone_code = "CYM"
    elif "balea" in zone.lower():
        zone_code = "BAL"

    url = f"https://api.preciodelaluz.org/v1/prices/all?zone={zone_code}"

    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(url, timeout=4) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    day_dict = {}
                    for hour_str, item in data.items():
                        # hour_str suele ser "00-01", "01-02", etc.
                        try:
                            h = int(hour_str.split("-")[0])
                            # El precio suele venir en €/MWh o en €/kWh según la API
                            raw_p = item.get("price", 0.0)
                            # Si es mayor que 1.0 suele ser €/MWh (ej. 85.50 €/MWh -> 0.0855 €/kWh)
                            p_kwh = (raw_p / 1000.0) if raw_p > 2.0 else raw_p
                            day_dict[h] = p_kwh
                        except Exception:
                            pass

                    if day_dict:
                        _PVPC_CACHE[cache_key] = day_dict
                        if dt.hour in day_dict:
                            p = round(day_dict[dt.hour] + margin, 4)
                            return p, f"PVPC REE Oficial ({p:.4f} €/kWh)"
    except Exception as e:
        _LOGGER.debug(f"Aviso consultando PVPC en tiempo real para {date_key}: {e}")

    # Si la API no responde (sin conexión exterior o error), usar fallback seguro
    fb = round(fallback + margin, 4)
    return fb, f"PVPC Estimado / Respaldo ({fb:.4f} €/kWh)"


async def calculate_charging_session_cost(
    geofence: Optional[Any],
    start_time: datetime.datetime,
    end_time: Optional[datetime.datetime],
    kwh_added: float,
) -> Dict[str, Any]:
    """
    Calcula el coste total de una sesión de recarga según la configuración de la geocerca.
    Soporta: Fija, Gratuita, Discriminación Horaria 2.0TD y PVPC dinámico.
    """
    if not geofence or kwh_added <= 0.0:
        return {
            "total_cost": 0.0,
            "effective_kwh_cost": 0.0,
            "cost_mode": "free",
            "details": "Sin geocerca o sin coste registrado",
        }

    mode = (getattr(geofence, "cost_mode", "fixed") or "fixed").lower()

    # 1. Carga Gratuita
    if mode == "free":
        return {
            "total_cost": 0.0,
            "effective_kwh_cost": 0.0,
            "cost_mode": "free",
            "details": "Recarga Gratuita",
        }

    # 2. Coste Fijo
    if mode == "fixed":
        fixed_p = getattr(geofence, "cost_per_kwh", 0.15) or 0.15
        total = round(kwh_added * fixed_p, 2)
        return {
            "total_cost": total,
            "effective_kwh_cost": fixed_p,
            "cost_mode": "fixed",
            "details": f"Tarifa Fija ({fixed_p:.3f} €/kWh)",
        }

    # 3. Discriminación horaria o PVPC (distribuido a lo largo del tiempo de la carga)
    finish_time = end_time or datetime.datetime.utcnow()
    total_seconds = max(60, int((finish_time - start_time).total_seconds()))

    # Desglosar la sesión hora a hora para precisión exacta
    curr = start_time
    total_cost_accum = 0.0

    while curr < finish_time:
        # Fin de la hora actual
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
            fallback = getattr(geofence, "cost_fallback", 0.15) or 0.15
            p, _ = await get_pvpc_hourly_price(curr, zone=zone, margin=margin, fallback=fallback)
        else:
            p = getattr(geofence, "cost_per_kwh", 0.15) or 0.15

        total_cost_accum += segment_kwh * p
        curr = segment_end

    total_cost_rounded = round(total_cost_accum, 2)
    avg_price = round(total_cost_rounded / kwh_added, 4) if kwh_added > 0 else 0.0

    if mode == "time_of_use":
        period_name, _ = get_time_of_use_period(start_time)
        details_txt = f"Tarifa 2.0TD ({avg_price:.3f} €/kWh medio, inicio en {period_name})"
    elif mode == "pvpc":
        details_txt = f"Tarifa Regulada PVPC ({avg_price:.3f} €/kWh medio)"
    else:
        details_txt = f"{avg_price:.3f} €/kWh"

    return {
        "total_cost": total_cost_rounded,
        "effective_kwh_cost": avg_price,
        "cost_mode": mode,
        "details": details_txt,
    }
