"""Fuel stations a car has used: suggestions for the «Gasolinera» field so the same name is not typed every time.

No list to keep: the names come from the refuels themselves. docs/costs.md, «Gasolineras».
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from app.db.models import Refuel
from app.services.geofence import haversine_distance_meters

# A detected refuel this close to an earlier one with a name is at the same station (a forecourt is some tens of metres; the
# position of a detected refuel is that of the reading, which may come a little after the pump)
SAME_STATION_M = 150


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def used_stations(refuels: Iterable[Refuel]) -> List[Dict]:
    """[{name, count}] of the stations named in these refuels, most used first (then most recent); the spelling shown is the
    latest one, and «repsol» and «Repsol » are the same station."""
    groups: Dict[str, Dict] = {}
    for r in sorted((r for r in refuels if r.station and r.station.strip()), key=lambda r: r.timestamp):
        g = groups.setdefault(_key(r.station), {"count": 0})
        g.update(name=" ".join(r.station.split()), count=g["count"] + 1, last=r.timestamp)
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], -g["last"].timestamp()))
    return [{"name": g["name"], "count": g["count"]} for g in ordered]


def suggest_station(refuel: Refuel, refuels: Iterable[Refuel]) -> Optional[str]:
    """The name of the closest other refuel with a name within SAME_STATION_M of this one, for a refuel that has none."""
    if refuel.station or refuel.latitude is None or refuel.longitude is None:
        return None
    best = None
    for other in refuels:
        if other.id == refuel.id or not other.station or other.latitude is None or other.longitude is None:
            continue
        d = haversine_distance_meters(refuel.latitude, refuel.longitude, other.latitude, other.longitude)
        if d <= SAME_STATION_M and (best is None or d < best[0] or (d == best[0] and other.timestamp > best[1].timestamp)):
            best = (d, other)
    return " ".join(best[1].station.split()) if best else None
