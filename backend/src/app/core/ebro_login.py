"""ebro_login.py — Login and token refresh for the Chery "legend" platform (Ebro brand, EU).

Recipe VERIFIED end to end (2026-07-27) by capturing the app's real login
(reFlutter+WireGuard) and replicating it from Python (HTTP 200, access_token+refresh_token).
AUTONOMOUS: the user logs in once with phone+password, then the token is renewed
using only the refresh_token (< 12h validity).

VERIFIED constants (POST https://legend.ebroauto.com/api/auth/oauth2/token):
  - OAuth client : Basic base64("legendApp:legendApp")
  - tenant-code / tenant-id : "3000010"   (NB: NOT "euebro" — euebro is only the TSP x-TenantId)
  - dept-id : "34" (country code), client-toc : "Y", version : "1.0.11", agent : "android"
  - NO gateway signature on oauth2/token (no signature/nonce/url/keys at all)
  - password : AES-CBC/PKCS7, key = IV = "w9R8Ag1KiL0pvMHc", base64 → sent in the BODY (password=..)
  - username : "<areaCode>_<mobile>"  e.g. "34_600123456"
  - grant_type / scope in the QUERY STRING
"""
from __future__ import annotations

import base64

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import requests

from .http import WRITE_TIMEOUT_S

LOGIN_HOST = "https://legend.ebroauto.com/api"
OAUTH_PATH = "/auth/oauth2/token"
CLIENT_BASIC = "Basic " + base64.b64encode(b"legendApp:legendApp").decode()

TENANT_CODE = "3000010"            # login tenant (numeric) — distinct from the TSP x-TenantId "euebro"
_PWD_KEY = b"w9R8Ag1KiL0pvMHc"     # AES-128 key == IV (Dart `encrypt` package, CBC mode)
APP_VERSION = "1.0.11"

# Slice of the body included in the error message when the response is not JSON:
# enough to see what the server answered, without dumping a whole page into the log.
_ERROR_SNIPPET_LEN = 150


def aes_encrypt_password(plaintext: str) -> str:
    """Replicate EncryptUtils.aesEncrypt: AES-CBC/PKCS7(key=IV=_PWD_KEY) → base64."""
    c = AES.new(_PWD_KEY, AES.MODE_CBC, _PWD_KEY)
    return base64.b64encode(c.encrypt(pad(plaintext.encode("utf-8"), 16))).decode()


def _headers(area_code: str = "34") -> dict:
    return {
        "Authorization": CLIENT_BASIC,
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "Dart/3.10 (dart:io)",
        "accept": "application/json, text/plain, */*",
        "accept-language": "es-ES",
        "tenant-code": TENANT_CODE,
        "tenant-id": TENANT_CODE,
        "dept-id": area_code,
        "client-toc": "Y",
        "version": APP_VERSION,
        "agent": "android",
    }


def _extract(j: dict) -> dict | None:
    d = j.get("data") if isinstance(j.get("data"), dict) else j
    at = d.get("access_token")
    if not at:
        return None
    return {"access_token": at, "refresh_token": d.get("refresh_token"),
            "token_type": d.get("token_type"), "expires_in": d.get("expires_in"), "raw": j}


def _oauth_post(params: dict, *, body: dict | None = None, area_code: str, host: str,
                error_key: str) -> tuple[bool, dict | str]:
    """The POST to `oauth2/token`, its parsing and the token extraction.

    Both grants (password and refresh_token) share the endpoint, headers, non-JSON body
    handling and error shape: only the query-string parameters change, whether there is a
    body, and under which key the failure detail comes (`key` on login, `error` on
    refresh). They were written out twice in full.
    """
    # `data` only when there is a body: the refresh grant carries NONE, and passing it as
    # `None` — even if `requests` treats it the same — would erase that guarantee from the call.
    extra = {"data": body} if body is not None else {}
    r = requests.post(host + OAUTH_PATH, params=params, headers=_headers(area_code),
                      timeout=WRITE_TIMEOUT_S, **extra)
    try:
        j = r.json()
    except Exception:
        return False, f"HTTP {r.status_code}: {r.text[:_ERROR_SNIPPET_LEN]}"
    tok = _extract(j)
    if tok:
        return True, tok
    return False, f"HTTP {r.status_code}: {j.get('msg') or j.get(error_key) or j}"


def password_login(mobile: str, password: str, area_code: str = "34",
                   host: str = LOGIN_HOST) -> tuple[bool, dict | str]:
    """Phone+password login. `mobile` = the number only; `area_code` e.g. "34" (Spain).
    Returns (True, {access_token, refresh_token,...}) or (False, message)."""
    return _oauth_post(
        {"username": f"{area_code}_{mobile}", "grant_type": "password", "scope": "server"},
        body={"password": aes_encrypt_password(password)},
        area_code=area_code, host=host, error_key="key")


def login_with_password(mobile: str, password: str, area_code: str = "34",
                        host: str = LOGIN_HOST) -> dict:
    """Convenience function for phone+password login.
    Returns the dict {access_token, refresh_token,...} or raises if it fails.
    """
    ok, res = password_login(mobile, password, area_code=area_code, host=host)
    if ok and isinstance(res, dict):
        return res
    raise RuntimeError(str(res))



def refresh_token(refresh_tok: str, area_code: str = "34",
                  host: str = LOGIN_HOST) -> tuple[bool, dict | str]:
    """Renew with the refresh_token grant (no password). Returns a new access+refresh."""
    return _oauth_post(
        {"grant_type": "refresh_token", "refresh_token": refresh_tok, "scope": "server"},
        area_code=area_code, host=host, error_key="error")
