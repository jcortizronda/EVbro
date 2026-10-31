"""Frequent places: where the drives end, grouped by proximity.

Drive ends within `CELL_DEG` of each other (about 110 m) fall in the same cell; cells are ranked by visits. The most recent
address seen in the cell names the place, and a geofence that covers it is reported so the user can see which places still
have no geofence (and create one from the app).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List

CELL_DEG = 0.001


def _cell(lat: float, lon: float) -> tuple:
    return (round(lat / CELL_DEG), round(lon / CELL_DEG))


def frequent_places(drives: Iterable, limit: int = 8, min_visits: int = 2) -> List[Dict[str, Any]]:
    cells: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {"visits": 0, "lat": 0.0, "lon": 0.0, "last": None, "address": None, "geofence_id": None})
    for d in drives:
        if d.end_lat is None or d.end_lon is None:
            continue
        c = cells[_cell(d.end_lat, d.end_lon)]
        n = c["visits"]
        c["lat"] = (c["lat"] * n + d.end_lat) / (n + 1)      # running centroid
        c["lon"] = (c["lon"] * n + d.end_lon) / (n + 1)
        c["visits"] = n + 1
        when = d.end_time or d.start_time
        if c["last"] is None or (when and when > c["last"]):
            c["last"] = when
            if d.end_address:
                c["address"] = d.end_address
            if d.end_geofence_id:
                c["geofence_id"] = d.end_geofence_id
        elif c["address"] is None and d.end_address:
            c["address"] = d.end_address
    ranked = sorted((c for c in cells.values() if c["visits"] >= min_visits), key=lambda c: (-c["visits"], c["last"] or ""), reverse=False)
    return [{"latitude": round(c["lat"], 6), "longitude": round(c["lon"], 6), "visits": c["visits"],
             "last_visit": c["last"].isoformat() if c["last"] else None, "address": c["address"], "geofence_id": c["geofence_id"]}
            for c in ranked[:limit]]
