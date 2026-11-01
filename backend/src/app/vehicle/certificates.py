"""Where the mutual-TLS certificates for the car's MQTT come from and where they end up.

The car's EMQX broker requires mutual-TLS: without the three certificates there is no connection,
which is why provisioning runs BEFORE connecting — if it fails, `services/car_events.py` stays in
the "no certificates" state and does not try to connect.

Three sources, in order:

1. **already present** in the per-vehicle folder → nothing is touched;
2. **folder given by the user** (`certs_src`), for whoever extracted them from the app by hand;
3. **bundle** per region (see `cert_bundle.py`): the EMQX client certificates
   are universal per-region constants, not per-account data.

It used to live inside the coordinator, which has no business knowing about paths, permissions
or certificate formats. Here, besides, the operation is a function of its arguments: it can be
tested on a `tmp_path` without building any service.

Everything in this module is **blocking** (it touches the file system): the caller runs it
in an executor, never on the event loop.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import shutil

from .constants import CERT_DIR_MODE, CERT_FILE_MODE, CERT_FILES

_LOGGER = logging.getLogger(__name__)

# Minimum certificates for MQTT mutual-TLS. The server `.cer` is in `CERT_FILES`
# because it is copied if the user provides it, but `tls_set` does not need it: that is why the
# list of what is ESSENTIAL is shorter than the list of what gets imported.
REQUIRED_CERTS = ("ca.pem", "client.pem", "client.key")


@dataclass(frozen=True)
class CertResult:
    """Provisioning result: whether usable certificates exist and why."""

    ok: bool
    detail: str

    def __iter__(self):
        """Unpackable as `(ok, detail)`, which is how the setup consumes it."""
        return iter((self.ok, self.detail))


def _has_required(certs_dir: str) -> bool:
    return all(os.path.isfile(os.path.join(certs_dir, f)) for f in REQUIRED_CERTS)


def _import_from_dir(certs_dir: str, source: str) -> list[str]:
    """Copy into `certs_dir` the certificates found in `source`. Return the copied ones."""
    copied: list[str] = []
    for name in CERT_FILES:
        src = os.path.join(source, name)
        if os.path.isfile(src):
            dest = os.path.join(certs_dir, name)
            shutil.copy2(src, dest)
            os.chmod(dest, CERT_FILE_MODE)   # they are credentials: owner only
            copied.append(name)
    return copied


def _write_bundle(certs_dir: str, certs: dict[str, bytes]) -> str | None:
    """Write out the deobfuscated bundle. Return the error detail, or `None` on success."""
    try:
        for name, data in certs.items():
            path = os.path.join(certs_dir, name)
            with open(path, "wb") as fh:
                fh.write(data)
            os.chmod(path, CERT_FILE_MODE)
    except OSError as err:
        return f"escritura de certificados del bundle fallida en {certs_dir}: {err}"
    return None


def provision(certs_dir: str, car_host: str, certs_src: str = "") -> CertResult:
    """Ensure the mutual-TLS certificates are in `certs_dir`. Blocking: run in an executor.

    Provisioning from scratch cannot be automated: the certificates come from the official app's
    device registration, which cannot be reproduced here. Hence the three sources.
    """
    os.makedirs(certs_dir, mode=CERT_DIR_MODE, exist_ok=True)

    if _has_required(certs_dir):
        return CertResult(True, "certificados presentes")

    # 1) manual override: folder given by the user in `certs_src`
    if certs_src and os.path.isdir(certs_src):
        copied = _import_from_dir(certs_dir, certs_src)
        if _has_required(certs_dir):
            return CertResult(True, f"certificados importados de {certs_src}: {', '.join(copied)}")

    # 2) per-region bundle
    try:
        from .cert_bundle import decrypt_region

        certs = decrypt_region(car_host)
    except Exception as err:
        _LOGGER.debug("[certs] bundle no disponible para %s: %s", car_host, err)
        certs = None
    if certs:
        error = _write_bundle(certs_dir, certs)
        if error:
            return CertResult(False, error)
        if _has_required(certs_dir):
            return CertResult(True, f"certificados auto-aprovisionados ({car_host})")

    return CertResult(False, (
        f"faltan los certificados mutual-TLS para {car_host}: región no en el bundle. "
        f"Copia {', '.join(REQUIRED_CERTS)} en {certs_dir} "
        f"(o indica una carpeta en certs_src)."
    ))


def tls_paths(certs_dir: str) -> dict[str, str]:
    """The three files `paho.tls_set` needs, already resolved to absolute paths."""
    return {
        "ca_certs": os.path.join(certs_dir, "ca.pem"),
        "certfile": os.path.join(certs_dir, "client.pem"),
        "keyfile": os.path.join(certs_dir, "client.key"),
    }
