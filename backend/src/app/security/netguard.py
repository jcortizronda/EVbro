"""Where the server may be sent by something a user typed (a webhook, a push endpoint, a Traccar or MQTT server).

The server makes those connections itself, from inside its own network: without a check, an account could use it to reach what
sits next to it (the router, a NAS, the host's other services). A self-hosted EVbro *wants* its LAN (Traccar and the MQTT broker
usually live there); a server shared with other people does not.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse

from app.config import settings


def inside_address(ip_text: str) -> bool:
    """Whether an address belongs to the server's own surroundings: loopback, private and shared ranges, link-local/metadata…"""
    ip = ipaddress.ip_address(ip_text.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)           # ::ffff:192.168.1.1 is 192.168.1.1
    if mapped is not None:
        ip = mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
            or not ip.is_global)                        # `is_global` also leaves out 100.64.0.0/10 (shared address space)


def host_points_inside(hostname: str, port: int) -> Optional[bool]:
    """True when `hostname` resolves to an address inside the server's network, False when all are public, None when it does not resolve."""
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return None
    return any(inside_address(info[4][0]) for info in infos)


def private_link_problem(hostname: Optional[str], port: int) -> Optional[str]:
    """Why a manager's own server (Traccar, MQTT broker) must not be used, or None. Only with `ALLOW_PRIVATE_LINKS=false`: then the
    name must resolve, and only to public addresses. It is checked when the link is saved or tried **and again on every connection**
    (a name can change where it points)."""
    if settings.ALLOW_PRIVATE_LINKS:
        return None
    inside = host_points_inside((hostname or "").strip().strip("[]"), port) if hostname else None
    if inside is None:
        return "No se pudo resolver el nombre del servidor."
    if inside:
        return "Este servidor de EVbro solo admite direcciones públicas, no las de una red local."
    return None


def url_host_port(url: str) -> tuple[Optional[str], int]:
    """Host and port a URL really goes to (whatever was typed around them)."""
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return None, 0
    return parsed.hostname, port or (443 if parsed.scheme == "https" else 80)
