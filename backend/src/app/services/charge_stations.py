"""Public charging points in Spain (Carga → «Cargadores cercanos»).

The source is the official list: the National Access Point of the DGT publishes, with the data the charging operators must send to
the Ministry (MITERD), one DATEX II v3 file with every public charging site in Spain (about 12 000 sites and 43 000 connectors):
name, operator, address, position, opening hours, and for each connector its type and power, plus how one pays. Free of charge,
no key, republished once a day. It carries **no prices and no live availability** (whether a point is free or taken).

The file is about 85 MB of XML (under 3 MB on the wire), so it is downloaded at most once a day and only when someone opens the
map: never on a timer. It goes straight to a file in DATA_DIR and is read from there piece by piece, never whole in memory. What
was parsed is kept in memory and in DATA_DIR/charge_stations.json, so a restart does not download it again. When the download
fails the last list stays in use.

Only Spain (it is Spain's access point).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple
from xml.etree import ElementTree

import aiohttp

from app.config import get_data_dir, settings
from app.timeutil import utcnow

_LOGGER = logging.getLogger(__name__)

SOURCE_URL = "https://nap.dgt.es/datex2/v3/miterd/EnergyInfrastructureTablePublication/electrolineras.xml"
FRESH = datetime.timedelta(hours=24)           # the access point republishes the file once a day
DOWNLOAD_TIMEOUT = 180                         # seconds
MAX_BYTES = 400 * 1024 * 1024                  # the file is ~85 MB uncompressed: anything far beyond that is not the list
CACHE_FILE = "charge_stations.json"
DOWNLOAD_FILE = "charge_stations.xml.part"

_NS = {"fac": "http://datex2.eu/schema/3/facilities", "com": "http://datex2.eu/schema/3/common",
       "loc": "http://datex2.eu/schema/3/locationReferencing", "locx": "http://datex2.eu/schema/3/locationExtension",
       "egi": "http://datex2.eu/schema/3/energyInfrastructure"}
_SITE = f"{{{_NS['egi']}}}energyInfrastructureSite"
_PUBLISHED = f"{{{_NS['com']}}}publicationTime"

# DATEX II connector type → the name used here. What an Ebro plug-in hybrid takes is `type2` (AC) and `ccs2` (DC).
PLUGS: Dict[str, str] = {
    "iec62196T2": "type2", "iec62196T2COMBO": "ccs2", "chademo": "chademo", "iec62196T1": "type1", "iec62196T1COMBO": "ccs1",
    "domesticF": "schuko", "domesticE": "schuko",
}
OTHER_PLUG = "other"
# What the map can be asked for: every connector the car can use, only the fast ones, or only the slow ones
KINDS: Dict[str, Tuple[str, ...]] = {"all": ("type2", "ccs2"), "dc": ("ccs2",), "ac": ("type2",)}
DEFAULT_KIND = "all"
PAYMENTS = ("apps", "rfid", "creditCard", "debitCard", "nfc", "pinpad")


@dataclass
class Station:
    id: str
    name: str
    operator: str
    address: str
    town: str
    hours: str
    lat: float
    lon: float
    plugs: List[List] = field(default_factory=list)      # [plug, kW, how many], the most powerful first
    pay: List[str] = field(default_factory=list)


_state: Dict = {"stations": None, "fetched_at": None, "source_time": None, "loaded": False}
_lock = asyncio.Lock()


def _tidy(text) -> str:
    return " ".join(str(text or "").split())


def _text(node, path: str) -> str:
    found = node.find(path, _NS) if node is not None else None
    return _tidy(found.text) if found is not None and found.text else ""


def _float(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _kw(text: str) -> Optional[float]:
    """The connector's power in kW. The file gives watts («22000.0»); a figure that small that it can only be kW is taken as kW."""
    watts = _float(text)
    if watts is None or watts <= 0:
        return None
    kw = watts / 1000.0 if watts >= 1000 else watts
    return round(kw, 1) if 1 <= kw <= 1000 else None


_DAYS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_DAY_SHORT = ("L", "M", "X", "J", "V", "S", "D")
_HOURS = re.compile(r"([A-Za-zÁÉÍÓÚáéíóúñ]+)\s*\(\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*\)")


def tidy_hours(label: str) -> str:
    """The file's opening hours («Jueves (08:30 - 17:30) Lunes (00:00 - 23:59) …», in any order) → «24 h» when it never closes,
    or the week in order with equal consecutive days joined («L-V 08:30-17:30 · S-D 24 h»). Anything else is left as it comes."""
    days: Dict[int, str] = {}
    for name, start, end in _HOURS.findall(label or ""):
        key = name.lower().replace("é", "e").replace("á", "a")
        if key not in _DAYS:
            return _tidy(label)
        days[_DAYS.index(key)] = "24 h" if (start in ("00:00", "0:00") and end in ("23:59", "24:00")) else f"{start}-{end}"
    if not days:
        return _tidy(label)
    if len(days) == 7 and set(days.values()) == {"24 h"}:
        return "24 h"
    runs: List[List] = []                                     # [first day, last day, hours]
    for d in sorted(days):
        if runs and runs[-1][1] == d - 1 and runs[-1][2] == days[d]:
            runs[-1][1] = d
        else:
            runs.append([d, d, days[d]])
    return " · ".join(f"{_DAY_SHORT[a]}{'-' + _DAY_SHORT[b] if b > a else ''} {hours}" for a, b, hours in runs)


def _site(node) -> Optional[Station]:
    """One site of the file → a station, or None without a usable position or without any connector with a known power."""
    lat = _float(_text(node, "fac:locationReference/loc:coordinatesForDisplay/loc:latitude"))
    lon = _float(_text(node, "fac:locationReference/loc:coordinatesForDisplay/loc:longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    counts: Dict[Tuple[str, float], int] = {}
    for c in node.iterfind(".//egi:connector", _NS):
        kw = _kw(_text(c, "egi:maxPowerAtSocket"))
        if kw is None:
            continue
        key = (PLUGS.get(_text(c, "egi:connectorType"), OTHER_PLUG), kw)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    lines = {}
    for line in node.iterfind("fac:locationReference//locx:addressLine", _NS):
        label, _sep, value = _text(line, "locx:text/com:values/com:value").partition(":")
        if value:
            lines[label.strip().lower()[:3]] = _tidy(value)      # «Dirección», «Municipio», «Provincia»… by their first letters
    pay = {_tidy(m.text) for m in node.iterfind(".//egi:authenticationAndIdentificationMethods", _NS) if m.text}
    return Station(
        id=node.get("id") or "", name=_text(node, "fac:name/com:values/com:value") or "Punto de recarga",
        operator=_text(node, "fac:operator/fac:name/com:values/com:value"), address=lines.get("dir", ""), town=lines.get("mun", ""),
        hours=tidy_hours(_text(node, "fac:operatingHours/fac:label")), lat=lat, lon=lon,
        plugs=[[plug, kw, n] for (plug, kw), n in sorted(counts.items(), key=lambda item: (-item[0][1], item[0][0]))],
        pay=[p for p in PAYMENTS if p in pay])


def parse(source) -> Tuple[List[Station], Optional[str]]:
    """The DATEX II file (a path or a file object) → the stations and the publication time the file states. It is read one site at
    a time and each is dropped once read: the whole document is never held in memory."""
    out: List[Station] = []
    published: Optional[str] = None
    root = None
    for event, node in ElementTree.iterparse(source, events=("start", "end")):
        if event == "start":
            if root is None:
                root = node
            continue
        if node.tag == _PUBLISHED and published is None:
            published = _tidy(node.text) or None
        elif node.tag == _SITE:
            station = _site(node)
            if station is not None:
                out.append(station)
            node.clear()
            if root is not None:
                root.clear()                                  # the sites already read, so the tree does not grow
    return out, published


def _path(name: str) -> str:
    return os.path.join(get_data_dir(), name)


def _load_cache() -> None:
    """The list saved by an earlier run (once per process)."""
    _state["loaded"] = True
    try:
        with open(_path(CACHE_FILE), encoding="utf-8") as f:
            saved = json.load(f)
        _state["stations"] = [Station(**s) for s in saved["stations"]]
        _state["fetched_at"] = datetime.datetime.fromisoformat(saved["fetched_at"])
        _state["source_time"] = saved.get("source_time")
    except FileNotFoundError:
        pass
    except Exception as e:                                   # noqa: BLE001 — a broken cache is simply downloaded again
        _LOGGER.warning("The saved charging point list could not be read: %s", e)


def _save_cache() -> None:
    try:
        path = _path(CACHE_FILE)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": _state["fetched_at"].isoformat(), "source_time": _state["source_time"],
                       "stations": [asdict(s) for s in _state["stations"]]}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception as e:                                   # noqa: BLE001 — without the file it is only downloaded again after a restart
        _LOGGER.warning("The charging point list could not be saved: %s", e)


async def _download(path: str) -> None:
    """The file, written to `path` as it arrives (it is far too big to hold in memory beside its parsed form)."""
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)
    size = 0
    async with aiohttp.ClientSession(timeout=timeout, headers={"Accept": "application/xml, text/xml", "User-Agent": "EVbro"}) as client:
        async with client.get(SOURCE_URL) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            with open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise RuntimeError("the file is far bigger than the list")
                    f.write(chunk)


class Unavailable(Exception):
    """No list of charging points: never downloaded and the source cannot be reached (or the feature is off)."""


async def stations(max_age: datetime.timedelta = FRESH) -> List[Station]:
    """Every charging site, downloading the list again when it is older than `max_age`. A failed download keeps the last list."""
    if not settings.CHARGE_STATIONS_ENABLED:
        raise Unavailable("off")
    async with _lock:
        if not _state["loaded"]:
            _load_cache()
        fetched = _state["fetched_at"]
        if _state["stations"] is not None and fetched is not None and utcnow() - fetched < max_age:
            return _state["stations"]
        part = _path(DOWNLOAD_FILE)
        try:
            await _download(part)
            parsed, published = await asyncio.get_running_loop().run_in_executor(None, parse, part)      # seconds of CPU: off the loop
            if not parsed:
                raise RuntimeError("empty list")
            _state.update(stations=parsed, fetched_at=utcnow(), source_time=published)
            _save_cache()
            _LOGGER.info("Charging point list downloaded: %d sites (%s)", len(parsed), published)
        except Exception as e:                               # noqa: BLE001 — the source failing must not break the map
            _LOGGER.warning("The charging point list could not be downloaded: %s", e, extra={"telemetry": False})
            if _state["stations"] is None:
                raise Unavailable(str(e)) from e
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
        return _state["stations"]


def info() -> Dict:
    """When the list in use was downloaded and the publication time the file states."""
    fetched = _state["fetched_at"]
    return {"fetched_at": fetched.isoformat() + "Z" if fetched else None, "source_time": _state["source_time"]}


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _usable(station: Station, plugs: Tuple[str, ...], min_kw: float) -> Iterator[List]:
    return (p for p in station.plugs if p[0] in plugs and p[1] >= min_kw)


def nearby(all_stations: List[Station], lat: float, lon: float, radius_km: float, kind: str = DEFAULT_KIND, min_kw: float = 0.0,
           limit: int = 80) -> List[Dict]:
    """The sites within `radius_km` with at least one connector of `kind` of `min_kw` or more, nearest first. Each carries its
    distance, the connectors that match (`plugs`: [plug, kW, how many], the most powerful first) and the power of the best one."""
    wanted = KINDS.get(kind, KINDS[DEFAULT_KIND])
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    found = []
    for s in all_stations:
        if abs(s.lat - lat) > dlat or abs(s.lon - lon) > dlon:
            continue
        plugs = list(_usable(s, wanted, min_kw))
        if not plugs:
            continue
        d = distance_km(lat, lon, s.lat, s.lon)
        if d <= radius_km:
            found.append((d, s, plugs))
    found.sort(key=lambda x: x[0])
    return [{"id": s.id, "name": s.name, "operator": s.operator, "address": s.address, "town": s.town, "hours": s.hours,
             "lat": s.lat, "lon": s.lon, "distance_km": round(d, 2), "plugs": plugs, "max_kw": max(p[1] for p in plugs),
             "fast": any(p[0] == "ccs2" for p in plugs), "pay": s.pay} for d, s, plugs in found[:limit]]


def _reset_for_tests() -> None:
    _state.update(stations=None, fetched_at=None, source_time=None, loaded=False)
