"""GPX 1.1 export of a drive's track (what Traccar, Strava or any map tool imports)."""
from __future__ import annotations

import datetime
from typing import Iterable
from xml.sax.saxutils import escape


def _iso(ts: datetime.datetime) -> str:
    """Positions are stored as naive UTC; GPX wants an explicit Z."""
    return ts.replace(microsecond=0).isoformat() + "Z"


def build_gpx(name: str, positions: Iterable, description: str = "") -> str:
    points = []
    for p in positions:
        ele = f"<ele>{p.altitude:.1f}</ele>" if p.altitude is not None else ""
        ext = ""
        if p.speed_kmh is not None or p.battery_pct is not None:
            parts = []
            if p.speed_kmh is not None:
                parts.append(f"<evbro:speed_kmh>{p.speed_kmh:.1f}</evbro:speed_kmh>")
            if p.battery_pct is not None:
                parts.append(f"<evbro:battery_pct>{p.battery_pct:.1f}</evbro:battery_pct>")
            ext = "<extensions>" + "".join(parts) + "</extensions>"
        points.append(f'<trkpt lat="{p.latitude:.6f}" lon="{p.longitude:.6f}">{ele}<time>{_iso(p.timestamp)}</time>{ext}</trkpt>')
    desc = f"<desc>{escape(description)}</desc>" if description else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="EVBro" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:evbro="https://github.com/jcortizronda/EVbro">\n'
        f"<metadata><name>{escape(name)}</name>{desc}</metadata>\n"
        f"<trk><name>{escape(name)}</name><trkseg>\n" + "\n".join(points) + "\n</trkseg></trk>\n</gpx>\n"
    )
