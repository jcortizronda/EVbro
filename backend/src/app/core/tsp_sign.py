#!/usr/bin/env python3
"""
tsp_sign.py — REST signature of the Chery Vehicle SDK (PROD EU), rebuilt byte by byte
from the smali `smali_dex2/h/ldkb.smali`:

  - method b(Map,J,String)  : builds the string to sign
  - method c(String)        : SHA-256 -> base64 -> toUpperCase()  (UPPER CASE, and base64, NOT hex!)
  - method a(Map,J,String)  : for tagEncrypt="1" adds appId, signs, adds sign
  - method a(J,String)      : header Authorization=token, timestamp, x-TenantId=""

Algorithm (EU, tagEncrypt="1"):
  half      = EVEN-position characters of APP_SECRET (-> "EUEBROProd89ec59274d23491084af")
  base      = <non-empty params sorted alphabetically as "k=v&"> + "secretKey=" + half + "&timestamp=" + ts
  sign      = base64(SHA256(base)).upper()   (verified on 71 real envelopes; hex does NOT work)
  body JSON = { ...params..., "appId": <APP_ID>, "sign": <sign> }
  header    = { Authorization: <userToken>, timestamp: <ts>, x-TenantId: "" }
"""
import base64
import hashlib
import os

# REGION constants. Ebro EU by default.
APP_ID     = os.environ.get("TSP_APP_ID", "euebro-1")
# Ebro's real APP_SECRET (extracted from the binary, 2026-07): the EVEN-position characters
# give the HALF "EUEBROProd89ec59274d23491084af". VERIFIED: reproduces byte by byte the signature of
# real captured requests (e.g. /asr/manager/realtime → 3ORGRGOITQCGB+WHK6T8AD8Z8XDGFP6QERFHW/ND+WQ=).
APP_SECRET = "EqU3E4B6RtO7P8r9otd4879descd5f9g2f7r4rdd2d3d4d9d1d0d8d4dadfw"
TAG_ENCRYPT = "1"          # EU = SHA-256 (NO HMAC)

def half_secret(secret: str = APP_SECRET) -> str:
    """EVEN-position characters (indices 0,2,4,...) → 'EUEBROProd89ec59274d23491084af'."""
    return "".join(secret[i] for i in range(len(secret)) if i % 2 == 0)

HALF = half_secret()   # "EUEBROProd89ec59274d23491084af" (verified)


def _flatten_value(v):
    """Serialise a nested ARRAY value as the native SDK does (a(JSONObject) in
    ldkb.smali) BEFORE computing the sign. For each element:
      - object  → 'key=value&' with keys sorted alphabetically (empty values skipped),
                  and any sub-list flattened recursively;
      - scalar  → str(element) concatenated WITHOUT a separator (e.g. [1,2,3] → '123').
    The trailing '&' is removed. Verified byte by byte on 4/4 real
    `chargeAppointControl` envelopes (cycleData [1..7] → '1234567')."""
    if isinstance(v, list):
        sb = ""
        for el in v:
            if isinstance(el, dict):
                fl = _flatten_obj(el)
                for k in sorted(fl.keys()):
                    val = fl[k]
                    if val is None or val == "":
                        continue
                    sb += f"{k}={val}&"
            else:
                sb += str(el)
        if sb.endswith("&"):
            sb = sb[:-1]
        return sb
    return v


def _flatten_obj(obj: dict) -> dict:
    """Copy of the object with only the list values flattened (see _flatten_value).
    Scalar values are left intact → for FLAT bodies it is a no-op (the historical
    algorithm stays identical, verified on 63/63 flat envelopes)."""
    return {k: (_flatten_value(v) if isinstance(v, list) else v) for k, v in obj.items()}


def build_sign(params: dict, ts_ms: int, half: str = HALF) -> str:
    """Replicate b(Map,J,String) for tagEncrypt='1': UPPER-CASE SHA-256.

    Nested array values (e.g. `chargeAppointPlans`) are flattened first as the native
    SDK does (_flatten_obj); without arrays the behaviour is unchanged. NB: it flattens a
    COPY → the body returned by sign_body keeps the real array."""
    flat = _flatten_obj(params)
    parts = []
    for k in sorted(flat.keys()):                # Arrays.sort over the keys
        v = flat[k]
        if v is None or v == "":                 # null/"" skipped
            continue
        parts.append(f"{k}={v}&")
    base = "".join(parts) + f"secretKey={half}&timestamp={ts_ms}"
    # FINDING S23 (2026-06-20, eCapture/Conscrypt capture): the REAL encoding of the
    # sign is base64(sha256(base)).upper(), NOT hexdigest().upper(). Verified on 71 real
    # envelopes (airControl/coolingControl/heatingControl/lockControl/seatControl/window/findCar...).
    return base64.b64encode(hashlib.sha256(base.encode("utf-8")).digest()).decode().upper()

def sign_body(body_params: dict, ts_ms: int, half: str | None = None) -> dict:
    """Replicate a(Map,J,String) tag1: return the final JSON body {params, appId, sign}."""
    m = dict(body_params)
    m["appId"] = APP_ID                          # appId among the signed parameters
    sign = build_sign(m, ts_ms, half=half or HALF)
    m["sign"] = sign                             # sign added AFTER signing
    return m

def auth_headers(user_token: str, ts_ms: int, tenant_id: str = "") -> dict:
    """Replicate a(J,String) tag1: Authorization=token, timestamp, x-TenantId."""
    return {
        "Authorization": user_token,
        "timestamp": str(ts_ms),
        "x-TenantId": tenant_id or "",
    }
