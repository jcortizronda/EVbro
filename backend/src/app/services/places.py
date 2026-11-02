"""Frequent places: where the drives end, grouped by geofence or, outside them, by proximity.

- A drive that ends inside a geofence (by its `end_geofence_id`, or by its coordinates when the geofence came later) counts for
  that geofence, whatever cell it falls in: a large geofence is one place, never two «Casa».
- Outside the geofences, drive ends within `CELL_DEG` of each other (about 110 m) fall in the same cell. The cell is named after
  its most recent address that is not a geofence's name: a destination renamed «Casa» by hand on a drive whose end point is
  elsewhere (seen 2026-11-02: the cloud gave a stale position when a trip ended) must not make a second «Casa».
Places are ranked by visits; a geofence is reported so the user can see which places still have none (and create one).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Sequence

from app.services.geofence import haversine_distance_meters

CELL_DEG = 0.001


def _cell(lat: float, lon: float) -> tuple:
    return (round(lat / CELL_DEG), round(lon / CELL_DEG))


def _geofence_of(d, geofences: Sequence) -> Any:
    if d.end_geofence_id:
        return d.end_geofence_id
    for gf in geofences:
        if haversine_distance_meters(d.end_lat, d.end_lon, gf.latitude, gf.longitude) <= (gf.radius_meters or 0):
            return gf.id
    return None


def frequent_places(drives: Iterable, limit: int = 8, min_visits: int = 2, geofences: Sequence = ()) -> List[Dict[str, Any]]:
    names = {gf.id: gf.name for gf in geofences}
    taken = {n.strip().casefold() for n in names.values() if n}
    groups: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {"visits": 0, "lat": 0.0, "lon": 0.0, "last": None, "address": None,
                                                               "address_at": None, "geofence_id": None})
    for d in drives:
        if d.end_lat is None or d.end_lon is None:
            continue
        gf_id = _geofence_of(d, geofences)
        g = groups[("gf", gf_id) if gf_id else _cell(d.end_lat, d.end_lon)]
        n = g["visits"]
        g["lat"] = (g["lat"] * n + d.end_lat) / (n + 1)      # running centroid
        g["lon"] = (g["lon"] * n + d.end_lon) / (n + 1)
        g["visits"] = n + 1
        when = d.end_time or d.start_time
        if g["last"] is None or (when and when > g["last"]):
            g["last"] = when
        if gf_id:
            g["geofence_id"] = gf_id
        addr = d.end_address
        if addr and (gf_id or addr.strip().casefold() not in taken):
            if g["address"] is None or (when and (g["address_at"] is None or when > g["address_at"])):
                g["address"], g["address_at"] = addr, when
    for g in groups.values():
        if g["geofence_id"] in names:
            g["address"] = names[g["geofence_id"]]
    ranked = sorted((g for g in groups.values() if g["visits"] >= min_visits), key=lambda g: g["last"].timestamp() if g["last"] else 0, reverse=True)
    ranked.sort(key=lambda g: -g["visits"])                  # most visits first; on a tie, the most recent
    return [{"latitude": round(g["lat"], 6), "longitude": round(g["lon"], 6), "visits": g["visits"],
             "last_visit": g["last"].isoformat() if g["last"] else None, "address": g["address"], "geofence_id": g["geofence_id"]}
            for g in ranked[:limit]]
