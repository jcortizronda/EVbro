"""ebro_reset.py — Resetting the Ebro account's password or PIN with a code by SMS, as the official app does it.

Read from the official app 1.0.11 (24-11-2026): `UserService.sendSmsCode` / `canSendCode`, `CHCaptcha` (chery_droplet),
`RusApiService.smsCheck` / `retrievePassword` and `LoginApiService.resetPassword`. The cloud refuses the SMS without the captcha
(24-11-2026: «Le informazioni del codice di verifica non possono essere vuote»); `code/create` answered with the puzzle.
The rest NOT yet tried against the real cloud.

1. Captcha («block puzzle»): `POST {BFF}/code/create?captchaType=blockPuzzle` gives the background, the piece, a `token` and a
   `secretKey`. The user slides the piece; `POST {BFF}/code/check?captchaType=blockPuzzle&pointJson=…&token=…` with the position
   `{"x": <px on a 310 px wide image>, "y": 5}` AES-ECB encrypted with the `secretKey`. The proof for the SMS is
   AES(`token---{position}`), the same way.
2. SMS: `POST {BFF}/marketing/v2/app/code/sendSmsCode`, a form with `mobile`, `module`, `areaCode` and `captchaVerification`, signed
   with the first three (`nonce = chery_legend_marketing`).
3. PIN: `tsp/v1/app/cpm/smsCheck` with the code, then `tsp/v1/app/cpm/retrievePassword` with the new PIN (no taskId).
   Password: `marketing/v1/user/user/passwd` with everything in the query string, signed with `areaCode` and `mobile`.
"""
from __future__ import annotations

import base64
import json

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from . import ebro_auth as A, wake
from .ebro_login import aes_encrypt_password
from .http import WRITE_TIMEOUT_S, _as_dict, bff_post

CAPTCHA_TYPE = "blockPuzzle"
CAPTCHA_WIDTH = 310                     # the app draws the background 310 px wide; `x` is measured on it
CAPTCHA_Y = 5
MARKETING_NONCE = "chery_legend_marketing"
SMS_PATH = "marketing/v2/app/code/sendSmsCode"
MODULE_PIN = "TSP-UPDATE-PASSWD"
MODULE_PASSWORD = "APP-UPDATE-PASSWD"
SMS_CHECK_PATH = "/tsp/v1/app/cpm/smsCheck"
RETRIEVE_PIN_PATH = "/tsp/v1/app/cpm/retrievePassword"
RESET_PASSWORD_PATH = "/marketing/v1/user/user/passwd"
RESET_PASSWORD_SIGNED_URL = "/v1/user/user/passwd"      # what the app signs (`FindPasswordViewModel.findPassword`)


def _bearer(ctx, path: str, **kw) -> dict:
    return A.headers_post(path, ctx=ctx, extra={"Authorization": f"Bearer {wake._access_token(ctx)}",
                                                "Accept": "application/json, text/plain, */*"}, **kw)


def captcha_encrypt(text: str, secret_key: str) -> str:
    """`CHBlockPuzzleCaptchaModel.encode`: AES-ECB, PKCS7, key = the `secretKey` text, Base64. Without a key, the text as it is."""
    if not secret_key:
        return text
    cipher = AES.new(secret_key.encode(), AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(text.encode(), 16))).decode()


def _point(x: float) -> str:
    """The position as the app's `jsonEncode` writes it: no spaces, `x` a double, `y` an int."""
    return json.dumps({"x": float(x), "y": CAPTCHA_Y}, separators=(",", ":"))


def _captcha_post(ctx, path: str, params: dict) -> dict:
    headers = _bearer(ctx, path)
    headers["Content-Type"] = "application/json; charset=UTF-8"
    answer = _as_dict(requests.post(ctx.bff + path, params=params, headers=headers, data="{}", timeout=WRITE_TIMEOUT_S))
    data = answer.get("data") if isinstance(answer.get("data"), dict) else answer
    return data or {}


def captcha_create(ctx) -> dict:
    """A new puzzle: {"background", "piece" (Base64 PNG), "token", "secret_key"}, or {} if the cloud gave none. BLOCKING."""
    data = _captcha_post(ctx, "/code/create", {"captchaType": CAPTCHA_TYPE})
    rep = data.get("repData") if isinstance(data.get("repData"), dict) else {}
    if data.get("repCode") != "0000" or not rep.get("token"):
        return {}
    return {"background": rep.get("originalImageBase64") or "", "piece": rep.get("jigsawImageBase64") or "",
            "token": rep["token"], "secret_key": rep.get("secretKey") or ""}


def captcha_check(ctx, token: str, secret_key: str, x: float) -> str | None:
    """Check where the piece was left. Returns the proof for the SMS (`captchaVerification`), or None if it does not fit. BLOCKING."""
    point = _point(x)
    data = _captcha_post(ctx, "/code/check", {"captchaType": CAPTCHA_TYPE, "pointJson": captcha_encrypt(point, secret_key),
                                              "token": token})
    if data.get("repCode") != "0000":
        return None
    return captcha_encrypt(f"{token}---{point}", secret_key)


def send_sms_code(ctx, phone: str, area_code: str, module: str, verification: str) -> dict:
    """Ask the cloud to send the code by SMS. Returns its JSON (`code == 0` = sent). BLOCKING."""
    values = {"mobile": phone, "module": module, "areaCode": area_code}
    sig, ts, keys = A.sign_values(SMS_PATH, values, nonce=MARKETING_NONCE)
    headers = _bearer(ctx, "/" + SMS_PATH)
    headers.pop("Content-Type", None)                    # a multipart form: `requests` writes its own
    headers.update({"signature": sig, "nonce": MARKETING_NONCE, "url": SMS_PATH, "timestamp": str(ts), "keys": keys})
    form = {**values, "captchaVerification": verification}
    response = requests.post(ctx.bff + "/" + SMS_PATH, headers=headers, files={k: (None, v) for k, v in form.items()},
                             timeout=WRITE_TIMEOUT_S)
    return _as_dict(response)


def reset_pin(ctx, tuid, phone: str, area_code: str, code: str, new_pin: str) -> tuple[dict, dict | None]:
    """Check the SMS code and set the new PIN. Returns (smsCheck's answer, retrievePassword's answer or None when the code was
    refused). BLOCKING."""
    from .taskid import encode_pin

    json_headers = {"Content-Type": "application/json; charset=UTF-8"}
    check = bff_post(ctx, SMS_CHECK_PATH, {"phone": phone, "captcha": code, "areaCode": area_code, "module": MODULE_PIN},
                     headers=_bearer(ctx, SMS_CHECK_PATH) | json_headers)
    if not succeeded(check):
        return check, None
    body = {"tUserId": str(tuid), "channelId": ctx.channel_id, "vin": ctx.vin, "password": encode_pin(new_pin), "needDecode": 0}
    return check, bff_post(ctx, RETRIEVE_PIN_PATH, body, headers=_bearer(ctx, RETRIEVE_PIN_PATH) | json_headers)


def reset_password(ctx, phone: str, area_code: str, code: str, new_password: str) -> dict:
    """Set a new password with the SMS code. Returns the cloud's JSON (`code == 0` = changed). BLOCKING."""
    sig, ts, keys = A.sign_values(RESET_PASSWORD_SIGNED_URL, {"areaCode": area_code, "mobile": phone}, nonce=MARKETING_NONCE)
    headers = _bearer(ctx, RESET_PASSWORD_PATH)
    headers.update({"Content-Type": "application/json; charset=UTF-8", "signature": sig, "nonce": MARKETING_NONCE,
                    "url": RESET_PASSWORD_SIGNED_URL, "timestamp": str(ts), "keys": keys})
    params = {"mobile": phone, "code": A.sm4_code(code), "needDecode": 0, "password": aes_encrypt_password(new_password),
              "areaCode": area_code}
    return bff_post(ctx, RESET_PASSWORD_PATH, {}, headers=headers, params=params)


def succeeded(answer: dict) -> bool:
    """`code` 0 (marketing) or one of the TSP's «ok» spellings."""
    code = str(answer.get("code", "")).strip()
    return answer.get("success") is True or code in ("0", "200", "A00000")


def refusal(answer: dict) -> str:
    text = str(answer.get("msg") or answer.get("message") or answer.get("code") or answer.get("_raw") or "").strip()
    return text.split("access_token")[0].strip()[:160]
