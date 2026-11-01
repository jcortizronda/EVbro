"""
ebro_auth.py — EXACT replica of the signature and login of the Chery 'legend' app (Ebro brand).
Rebuilt from the official app's behaviour:
  - EncryptUtils.headerSignature  (encrypt_utils.dart ~299)
  - HttpUtils.request             (http_utils.dart ~438, signature block ~1200)
  - UserService.mailVerifyLogin   (user_service.dart ~3478)
  - SM4 / sm4RandomString         (sm4.dart createHexKey/encrypt)

SIGNATURE (for POST requests): the value map passed to headerSignature is EMPTY
  => signature = SHA256_hex(secret + nonce + url + timestamp_ms)
     (NO "[values]", NO 'keys' header)
  secret = (prod, CURRENT_CAR_CONTROL_ENV=0), nonce = "chery_legend_h5".
Headers sent: signature, nonce, url, timestamp (+ tenant, Authorization, etc.).

CODE: the login's 'code' field = base64( SM4_ECB_PKCS7( <transformed-code>, key ) )
  key = b"mHU80av2zFtf4OY6" (16 bytes, from SM4.createHexKey, fixed).

EMAIL: the login's 'email' field = "APP-LOGIN@" + email (from the builder).

Strictly personal use (the user's own car/account).
"""
import base64
import hashlib
import time

# ── Constants (from the official app) ──────────────────────────────────
# The REGION parameters (BFF/channel/country/tenant) are NOT module globals
# rewritten on every call: they come from the vehicle's `CoreCtx`. Only the APP
# constants stay here — identical for every user, extractable from the APK, not
# per-account data.
APP_BASIC    = "Basic bGVnZW5kQXBwOmxlZ2VuZEFwcA=="   # legendApp:legendApp (OAuth client, VERIFIED)
APP_VERSION  = "1.0.11"

# Region defaults, used only when `headers_post` is called without a context
# (command-line diagnostics). In EVBro the context is always there.
#
# Imported from `context`, where the definition lives: they used to be four `os.environ`
# READ AT IMPORT, i.e. a second source of truth for the same region parameters
# that `CoreCtx` already carries — and that could not be changed at runtime either.
from .context import (  # noqa: E402 — after the app constants, on purpose
    DEFAULT_CHANNEL_ID as CHANNEL_ID,
    DEFAULT_COUNTRY_ID as COUNTRY_ID,
    DEFAULT_TENANT_CODE as TENANT_CODE,
)

SIGN_NONCE   = "chery_legend_h5"                       # headerSignature nonce (VERIFIED)
SIGN_SECRET  = "5c7af05e6fbf562842ef483ee96e06a0"     # Ebro prod: SHA256(secret+nonce+url+ts) VERIFIED
SIGN_SECRET_TEST = "eQ9fQ9zM9yI7bZ1uY9wR2dQ1pJ6xU0zT"
SM4_KEY      = b"mHU80av2zFtf4OY6"                     # SM4.createHexKey -> hex of this

# ── SM4 (ECB) — pure implementation ──────────────────────────────────────────
_SM4_SBOX = bytes([
0xd6,0x90,0xe9,0xfe,0xcc,0xe1,0x3d,0xb7,0x16,0xb6,0x14,0xc2,0x28,0xfb,0x2c,0x05,
0x2b,0x67,0x9a,0x76,0x2a,0xbe,0x04,0xc3,0xaa,0x44,0x13,0x26,0x49,0x86,0x06,0x99,
0x9c,0x42,0x50,0xf4,0x91,0xef,0x98,0x7a,0x33,0x54,0x0b,0x43,0xed,0xcf,0xac,0x62,
0xe4,0xb3,0x1c,0xa9,0xc9,0x08,0xe8,0x95,0x80,0xdf,0x94,0xfa,0x75,0x8f,0x3f,0xa6,
0x47,0x07,0xa7,0xfc,0xf3,0x73,0x17,0xba,0x83,0x59,0x3c,0x19,0xe6,0x85,0x4f,0xa8,
0x68,0x6b,0x81,0xb2,0x71,0x64,0xda,0x8b,0xf8,0xeb,0x0f,0x4b,0x70,0x56,0x9d,0x35,
0x1e,0x24,0x0e,0x5e,0x63,0x58,0xd1,0xa2,0x25,0x22,0x7c,0x3b,0x01,0x21,0x78,0x87,
0xd4,0x00,0x46,0x57,0x9f,0xd3,0x27,0x52,0x4c,0x36,0x02,0xe7,0xa0,0xc4,0xc8,0x9e,
0xea,0xbf,0x8a,0xd2,0x40,0xc7,0x38,0xb5,0xa3,0xf7,0xf2,0xce,0xf9,0x61,0x15,0xa1,
0xe0,0xae,0x5d,0xa4,0x9b,0x34,0x1a,0x55,0xad,0x93,0x32,0x30,0xf5,0x8c,0xb1,0xe3,
0x1d,0xf6,0xe2,0x2e,0x82,0x66,0xca,0x60,0xc0,0x29,0x23,0xab,0x0d,0x53,0x4e,0x6f,
0xd5,0xdb,0x37,0x45,0xde,0xfd,0x8e,0x2f,0x03,0xff,0x6a,0x72,0x6d,0x6c,0x5b,0x51,
0x8d,0x1b,0xaf,0x92,0xbb,0xdd,0xbc,0x7f,0x11,0xd9,0x5c,0x41,0x1f,0x10,0x5a,0xd8,
0x0a,0xc1,0x31,0x88,0xa5,0xcd,0x7b,0xbd,0x2d,0x74,0xd0,0x12,0xb8,0xe5,0xb4,0xb0,
0x89,0x69,0x97,0x4a,0x0c,0x96,0x77,0x7e,0x65,0xb9,0xf1,0x09,0xc5,0x6e,0xc6,0x84,
0x18,0xf0,0x7d,0xec,0x3a,0xdc,0x4d,0x20,0x79,0xee,0x5f,0x3e,0xd7,0xcb,0x39,0x48,
])
_SM4_FK = [0xa3b1bac6,0x56aa3350,0x677d9197,0xb27022dc]
_SM4_CK = [
0x00070e15,0x1c232a31,0x383f464d,0x545b6269,0x70777e85,0x8c939aa1,0xa8afb6bd,0xc4cbd2d9,
0xe0e7eef5,0xfc030a11,0x181f262d,0x343b4249,0x50575e65,0x6c737a81,0x888f969d,0xa4abb2b9,
0xc0c7ced5,0xdce3eaf1,0xf8ff060d,0x141b2229,0x30373e45,0x4c535a61,0x686f767d,0x848b9299,
0xa0a7aeb5,0xbcc3cad1,0xd8dfe6ed,0xf4fb0209,0x10171e25,0x2c333a41,0x484f565d,0x646b7279,
]
_M32 = 0xffffffff
def _rotl(x, n):
    return ((x << n) & _M32) | (x >> (32 - n))
def _tau(a):
    return (_SM4_SBOX[(a>>24)&0xff]<<24)|(_SM4_SBOX[(a>>16)&0xff]<<16)|(_SM4_SBOX[(a>>8)&0xff]<<8)|_SM4_SBOX[a&0xff]
def _L(b):
    return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)
def _Lp(b):
    return b ^ _rotl(b, 13) ^ _rotl(b, 23)
def _sm4_key_schedule(key16):
    K=[ (int.from_bytes(key16[i*4:i*4+4],'big'))^_SM4_FK[i] for i in range(4)]
    rk=[]
    for i in range(32):
        t=K[1]^K[2]^K[3]^_SM4_CK[i]
        b=_Lp(_tau(t))
        K=[K[1],K[2],K[3],K[0]^b]
        rk.append(K[3])
    return rk
def _sm4_encrypt_block(rk, blk16):
    X=[int.from_bytes(blk16[i*4:i*4+4],'big') for i in range(4)]
    for i in range(32):
        t=X[1]^X[2]^X[3]^rk[i]
        X=[X[1],X[2],X[3],X[0]^_L(_tau(t))]
    out=X[::-1]
    return b"".join(x.to_bytes(4,'big') for x in out)
def sm4_ecb_encrypt_pkcs7(data: bytes, key: bytes=SM4_KEY) -> bytes:
    rk=_sm4_key_schedule(key)
    pad=16-(len(data)%16)
    data=data+bytes([pad])*pad
    return b"".join(_sm4_encrypt_block(rk,data[i:i+16]) for i in range(0,len(data),16))

def sm4_code(code: str, transform: str="plain") -> str:
    """base64( SM4_ECB_PKCS7( transform(code) ) ). transform: plain|padRight32|padLeft32."""
    s = str(code)
    if transform == "padRight32":
        s = s.ljust(32)
    elif transform == "padLeft32":
        s = s.rjust(32)
    ct = sm4_ecb_encrypt_pkcs7(s.encode("utf-8"))
    return base64.b64encode(ct).decode()

# ── App signature (POST: empty value map -> no brackets/keys) ──────────────
def sign_post(url_path: str, ts_ms: int | None = None, secret: str=SIGN_SECRET, nonce: str=SIGN_NONCE):
    ts = ts_ms if ts_ms is not None else int(time.time()*1000)
    sig = hashlib.sha256(f"{secret}{nonce}{url_path}{ts}".encode()).hexdigest()
    return sig, ts

DEPT_ID = "34"   # CountryArea.value() = country prefix (Spain=34, Italy=39, France=33...). VERIFIED: 34.

def headers_post(url_path: str, secret: str=SIGN_SECRET, nonce: str=SIGN_NONCE,
                 dept_id: str=DEPT_ID, extra=None, ctx=None):
    """Signed headers for a POST. `ctx` (CoreCtx) provides the region parameters.

    Without a context the module defaults are used — only useful for command-line
    diagnostics. EVBro always passes the vehicle's context, so two
    entries with different regions do not step on each other."""
    channel_id = ctx.channel_id if ctx is not None else CHANNEL_ID
    country_id = ctx.country_id if ctx is not None else COUNTRY_ID
    tenant = ctx.tenant_code if ctx is not None else TENANT_CODE
    sig, ts = sign_post(url_path, secret=secret, nonce=nonce)
    # FULL header set as the app sends it (http_config.dart headersJson + headerSignature).
    # Content-Type/Authorization override the token builder's extraHeaderParams.
    h = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "it-IT",
        "Accept-Encoding": "gzip, deflate",
        "agent": "android",
        "version": APP_VERSION,
        "Authorization": APP_BASIC,
        "DEPT-ID": dept_id,
        "TENANT-ID": tenant,
        "TENANT-CODE": tenant,
        "CLIENT-TOC": "Y",
        # lower-case variants we used to send (harmless, some routes read them)
        "tenantCode": tenant, "tenantID": tenant,
        "channelId": channel_id, "countryId": country_id,
        "appversion": APP_VERSION,
        "User-Agent": "okhttp/4.9.0",
        # signature
        "nonce": nonce, "timestamp": str(ts), "url": url_path,
        "signature": sig,
    }
    if extra:
        h.update(extra)
    return h
