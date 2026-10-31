"""Safe handling of the files people upload: car documents, maintenance invoices and the car photo.

The app may be on the internet, so every upload goes through the same rules:

* **Size while reading.** `read_limited` reads in chunks and stops as soon as the limit is passed (413); an empty file is 422.
* **Type from the content.** `sniff_type` decides by the first bytes (PDF, JPEG, PNG, WebP), never by the name or the content type the
  browser sends. The type that is stored and served is the sniffed one.
* **Random names.** The stored name is random (plus the sniffed extension); the client's name is only kept, cleaned, for display.
  Every path goes through `os.path.basename`.
* **Encrypted at rest.** Files are written as `MAGIC + nonce + tag + AES-GCM ciphertext` with the server key (`crypto.get_key()`), and
  the folder plus the stored name as associated data (a file cannot be swapped for another one). Writing is atomic (temp file + rename).
  A file without the magic header is legacy plaintext and is returned as it is (the startup migration encrypts those).
* **Served safely.** `file_response` sends the bytes inline with a safe `Content-Disposition`, `nosniff` and `private, no-store`. Images
  also get `Content-Security-Policy: sandbox`; PDFs do not, because the sandbox breaks Chrome's built-in PDF viewer (it shows a blank
  page), and a sniffed PDF is shown by the browser's viewer, not run as a page.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
import uuid
from typing import Iterable, Optional, Tuple
from urllib.parse import quote

from Crypto.Cipher import AES
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

from app.config import get_data_dir
from app.security import crypto

_LOGGER = logging.getLogger("app.services.uploads")

DOCUMENTS_FOLDER = "vehicle_documents"
INVOICES_FOLDER = "invoices"
PHOTOS_FOLDER = "vehicle_photos"
FOLDERS = (DOCUMENTS_FOLDER, INVOICES_FOLDER, PHOTOS_FOLDER)

MAGIC = b"EVBENC1\n"          # never the start of a PDF, JPEG, PNG or WebP
_NONCE_LEN = 12
_TAG_LEN = 16
TEMP_PREFIX = ".tmp-"
CHUNK_BYTES = 64 * 1024

PDF = "application/pdf"
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
DOCUMENT_TYPES = frozenset({PDF}) | IMAGE_TYPES


class FileDecryptionError(Exception):
    """The file could not be decrypted: another key, or a damaged file."""


# ───────────────────────── reading and sniffing ─────────────────────────
async def read_limited(upload: UploadFile, max_bytes: int, too_big: str) -> bytes:
    """The whole upload, read in chunks; 413 with `too_big` as soon as it passes `max_bytes`, 422 when it is empty."""
    parts = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=too_big)
        parts.append(chunk)
    if total == 0:
        raise HTTPException(status_code=422, detail="El fichero está vacío.")
    return b"".join(parts)


def sniff_type(data: bytes, allowed: Iterable[str] = DOCUMENT_TYPES) -> Optional[Tuple[str, str]]:
    """(content type, extension) from the magic bytes, or None when it is not one of the `allowed` types."""
    found = None
    if data.startswith(b"%PDF-"):
        found = (PDF, ".pdf")
    elif data.startswith(b"\xff\xd8\xff"):
        found = ("image/jpeg", ".jpg")
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        found = ("image/png", ".png")
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        found = ("image/webp", ".webp")
    return found if found and found[0] in allowed else None


# ───────────────────────── names ─────────────────────────
def clean_original_name(name: Optional[str], extension: str, fallback: str = "documento") -> str:
    """The uploaded name, only for display: no folders, no control characters, bounded length."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if not name:
        name = fallback + extension
    return name[:200]


def content_disposition(name: str) -> str:
    """inline, with an ASCII fallback and the UTF-8 name (RFC 6266 / 5987): quotes or line breaks cannot break the header."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", ascii_name).strip() or "documento"
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def folder_path(folder: str) -> str:
    path = os.path.join(get_data_dir(), os.path.basename(folder))
    os.makedirs(path, exist_ok=True)
    return path


def file_path(folder: str, stored_name: str) -> str:
    """basename: the stored name comes from the database, but a path never trusts it."""
    return os.path.join(folder_path(folder), os.path.basename(stored_name))


# ───────────────────────── encryption at rest ─────────────────────────
AAD_PREFIX = "evbro-file:"


def _associated_data(folder: str, stored_name: str, prefix: str = AAD_PREFIX) -> bytes:
    return f"{prefix}{os.path.basename(folder)}/{os.path.basename(stored_name)}".encode("utf-8")


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def encrypt_bytes(folder: str, stored_name: str, data: bytes) -> bytes:
    nonce = os.urandom(_NONCE_LEN)
    cipher = AES.new(crypto.get_key(), AES.MODE_GCM, nonce=nonce, mac_len=_TAG_LEN)
    cipher.update(_associated_data(folder, stored_name))
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return MAGIC + nonce + tag + ciphertext


def decrypt_bytes(folder: str, stored_name: str, blob: bytes, *, key: bytes | None = None, aad_prefix: str = AAD_PREFIX, magic: bytes = MAGIC) -> bytes:
    """The plaintext of a stored file. Without the magic header it is a legacy plaintext file and comes back as it is."""
    if not blob.startswith(magic):
        return blob
    body = blob[len(magic):]
    nonce, tag, ciphertext = body[:_NONCE_LEN], body[_NONCE_LEN:_NONCE_LEN + _TAG_LEN], body[_NONCE_LEN + _TAG_LEN:]
    try:
        cipher = AES.new(key or crypto.get_key(), AES.MODE_GCM, nonce=nonce, mac_len=_TAG_LEN)
        cipher.update(_associated_data(folder, stored_name, aad_prefix))
        return cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as exc:
        raise FileDecryptionError(f"{folder}/{stored_name}") from exc


def write_atomic(path: str, blob: bytes) -> None:
    """Writes to a temporary file in the same folder and renames it over `path` (a crash never leaves half a file)."""
    tmp = os.path.join(os.path.dirname(path), f"{TEMP_PREFIX}{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def new_stored_name(extension: str, prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}{extension}"


def store(folder: str, data: bytes, ext: str, prefix: str = "") -> str:
    """Encrypts and writes `data` under a new random name in `folder`; returns the stored name."""
    stored_name = new_stored_name(ext, prefix)
    write_atomic(file_path(folder, stored_name), encrypt_bytes(folder, stored_name, data))
    return stored_name


def load(folder: str, stored_name: str, missing: str = "El fichero no está en el servidor.") -> bytes:
    """The plaintext of a stored file. 404 when it is not on disk; 500 when it cannot be decrypted (never the raw bytes)."""
    path = file_path(folder, stored_name)
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=missing) from None
    try:
        return decrypt_bytes(folder, stored_name, blob)
    except FileDecryptionError:
        _LOGGER.error("No se pudo descifrar %s/%s: ¿ha cambiado la clave de cifrado (ENCRYPTION_KEY o secret.key)?", folder, os.path.basename(stored_name))
        raise HTTPException(status_code=500, detail="No se ha podido descifrar el fichero en el servidor. "
                                                    "Puede que haya cambiado la clave de cifrado; avisa al administrador.") from None


def remove(folder: str, stored_name: Optional[str]) -> None:
    """Deletes a stored file; a missing one is not an error."""
    if not stored_name:
        return
    try:
        os.remove(file_path(folder, stored_name))
    except OSError:
        pass


# ───────────────────────── serving ─────────────────────────
def file_response(data: bytes, content_type: str, original_name: str) -> Response:
    """The file inline with the safe headers (see the module docstring for why the sandbox is only for images).

    `X-Frame-Options: SAMEORIGIN` (instead of the app-wide DENY) lets the app's own invoice viewer show a PDF in a frame; no other site can."""
    headers = {
        "Content-Disposition": content_disposition(original_name),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "X-Frame-Options": "SAMEORIGIN",
    }
    if content_type in IMAGE_TYPES:
        headers["Content-Security-Policy"] = "sandbox"
    return Response(content=data, media_type=content_type, headers=headers)
