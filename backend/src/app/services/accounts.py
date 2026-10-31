"""Alta de cuenta Ebro: lista los vehículos de la cuenta y deja un token por VIN.

El login y el poller descubrían los vehículos cada uno con su copia de este código, y ambos
dejaban un `token.json` suelto en el directorio de trabajo (fuera del volumen de datos). Aquí
se hace una sola vez y sin dejar rastro: el token provisional vive en una carpeta temporal
dentro de DATA_DIR que se borra al terminar.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile

from app.config import get_data_dir, settings
from app.core.context import CoreCtx
from app.core.vehicles import iter_vehicles, query_list
from app.core.wake import _bff_login

_LOGGER = logging.getLogger("app.services.accounts")


_VIN_RE = re.compile(r"[A-Za-z0-9]{5,32}")


def vehicle_data_dir(vin: str) -> str:
    """Carpeta persistente del vehículo: token.json y taskid.txt.

    El VIN llega a veces desde la URL: se exige que sea alfanumérico para que no pueda salirse de
    DATA_DIR (`..`, barras) al construir la ruta."""
    if not _VIN_RE.fullmatch(vin or ""):
        raise ValueError("VIN no válido")
    path = os.path.join(get_data_dir(), vin)
    os.makedirs(path, exist_ok=True)
    return path


def write_token_file(path: str, token_data: dict) -> None:
    """Escribe el token de forma atómica y con permisos 0600."""
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(token_data, fh)
    os.replace(tmp, path)


def fetch_account_vehicles(token_data: dict, skip_vins: frozenset = frozenset()) -> list[dict]:
    """Lista los vehículos de la cuenta y guarda el token en `data/<vin>/token.json`.

    `skip_vins`: coches que ya son de OTRO gestor; su token no se toca (otra cuenta de Ebro que los vea no puede pisar el de su dueño).

    BLOQUEANTE (hace red): llamar desde un hilo (`run_in_executor`). Lanza excepción si el
    login BFF o la consulta fallan; el llamador decide cómo contarlo."""
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix=".login-", dir=data_dir)
    try:
        token_file = os.path.join(work, "token.json")
        write_token_file(token_file, token_data)
        ctx = CoreCtx(
            tsp_host=settings.EBRO_TSP_HOST,
            bff=settings.EBRO_BFF_HOST,
            channel_id=settings.EBRO_CHANNEL_ID,
            token_path=token_file,
        )
        _user_token, t_user_id = _bff_login(ctx)
        if t_user_id:
            ctx.tuserid = str(t_user_id)
        body = {"channelId": ctx.channel_id, "tUserId": str(t_user_id)} if t_user_id else None
        items = iter_vehicles(query_list(ctx, body=body))

        # `_bff_login` puede haber renovado el token: se reparte el más reciente.
        try:
            with open(token_file, encoding="utf-8") as fh:
                latest = json.load(fh)
        except (OSError, ValueError):
            latest = token_data
        for item in items:
            vin = item.get("vin")
            if not vin or vin in skip_vins:
                continue
            try:
                write_token_file(os.path.join(vehicle_data_dir(vin), "token.json"), latest)
            except OSError as exc:
                _LOGGER.warning("No se pudo escribir el token de %s: %s", vin, exc)
        return items
    finally:
        shutil.rmtree(work, ignore_errors=True)
