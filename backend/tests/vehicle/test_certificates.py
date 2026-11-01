"""Tests for `certificates.py` — where the MQTT mutual-TLS certificates come from.

This used to be exercised only with `_provision_certs` mocked in the conftest, that is: it was
not exercised. And it is not minor code — if it fails, the entry does not load
(`ConfigEntryNotReady`) and the user has no car in Home Assistant. Now that the operation is a
function of its arguments it is tested for real, on a `tmp_path`.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
from unittest.mock import patch

from app.vehicle.certificates import REQUIRED_CERTS, provision, tls_paths

HOST = "tspemqx-app-eu.ebroauto.com"


def _crear_certs(carpeta: Path, names=REQUIRED_CERTS) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    for name in names:
        (carpeta / name).write_bytes(b"-----BEGIN CERTIFICATE-----\n")


def test_nothing_is_touched_when_already_present(tmp_path: Path) -> None:
    certs_dir = tmp_path / "certs"
    _crear_certs(certs_dir)
    antes = {f: (certs_dir / f).stat().st_mtime_ns for f in REQUIRED_CERTS}

    ok, detalle = provision(str(certs_dir), HOST)

    assert ok is True
    assert detalle == "certificados presentes"
    assert {f: (certs_dir / f).stat().st_mtime_ns for f in REQUIRED_CERTS} == antes


def test_imports_from_the_user_given_folder(tmp_path: Path) -> None:
    origen = tmp_path / "mis_certs"
    _crear_certs(origen)
    certs_dir = tmp_path / "certs"

    ok, detalle = provision(str(certs_dir), HOST, certs_src=str(origen))

    assert ok is True
    assert "importados" in detalle
    assert all((certs_dir / f).is_file() for f in REQUIRED_CERTS)


def test_imported_certificates_are_owner_only(tmp_path: Path) -> None:
    """They are credentials: if the file is world-readable, anything inside HA
    can talk to the car."""
    origen = tmp_path / "mis_certs"
    _crear_certs(origen)
    certs_dir = tmp_path / "certs"

    provision(str(certs_dir), HOST, certs_src=str(origen))

    for name in REQUIRED_CERTS:
        modo = stat.S_IMODE((certs_dir / name).stat().st_mode)
        assert modo == 0o600, f"{name} tiene permisos {modo:o}"
    assert stat.S_IMODE(certs_dir.stat().st_mode) == 0o700


def test_an_incomplete_folder_does_not_count_as_success(tmp_path: Path) -> None:
    """With only two of the three, mutual-TLS cannot be negotiated: it is a failure, not a
    partial success.

    The bundle is disabled on purpose to isolate this branch: with the bundle available the
    incomplete folder DOES end in success, because the bundle supplies the missing one — and
    that is correct, not a failure (see the next test)."""
    origen = tmp_path / "mis_certs"
    _crear_certs(origen, names=("ca.pem", "client.pem"))
    certs_dir = tmp_path / "certs"

    with patch("app.vehicle.cert_bundle.decrypt_region", return_value=None):
        ok, detalle = provision(str(certs_dir), HOST, certs_src=str(origen))

    assert ok is False
    assert "faltan los certificados" in detalle


def test_the_bundle_completes_an_incomplete_folder(tmp_path: Path) -> None:
    """Sources add up: whatever the user provides only partially, the bundle completes."""
    origen = tmp_path / "mis_certs"
    _crear_certs(origen, names=("ca.pem", "client.pem"))
    certs_dir = tmp_path / "certs"

    ok, _detalle = provision(str(certs_dir), HOST, certs_src=str(origen))

    assert ok is True
    assert all((certs_dir / f).is_file() for f in REQUIRED_CERTS)


def test_falls_back_to_the_per_region_bundle(tmp_path: Path) -> None:
    certs_dir = tmp_path / "certs"
    bundle = {n: f"contenido de {n}".encode() for n in REQUIRED_CERTS}

    with patch("app.vehicle.cert_bundle.decrypt_region", return_value=bundle):
        ok, detalle = provision(str(certs_dir), HOST)

    assert ok is True
    assert "auto-aprovisionados" in detalle
    assert (certs_dir / "ca.pem").read_bytes() == b"contenido de ca.pem"


def test_unknown_region_explains_what_to_do(tmp_path: Path) -> None:
    """The message ends up in `ConfigEntryNotReady`, i.e. in front of the user: it has to
    tell them where to put the files."""
    certs_dir = tmp_path / "certs"

    with patch("app.vehicle.cert_bundle.decrypt_region", return_value=None):
        ok, detalle = provision(str(certs_dir), "broker-de-otra-region.example.com")

    assert ok is False
    assert str(certs_dir) in detalle
    assert all(name in detalle for name in REQUIRED_CERTS)


def test_a_broken_bundle_does_not_raise(tmp_path: Path) -> None:
    """An exception here would leave the entry in an unexplained error."""
    certs_dir = tmp_path / "certs"

    with patch(
        "app.vehicle.cert_bundle.decrypt_region", side_effect=ValueError("corrupto")
    ):
        ok, detalle = provision(str(certs_dir), HOST)

    assert ok is False
    assert "faltan los certificados" in detalle


def test_tls_paths_points_to_the_three_files_paho_needs(tmp_path: Path) -> None:
    rutas = tls_paths(str(tmp_path))

    assert rutas == {
        "ca_certs": os.path.join(str(tmp_path), "ca.pem"),
        "certfile": os.path.join(str(tmp_path), "client.pem"),
        "keyfile": os.path.join(str(tmp_path), "client.key"),
    }
