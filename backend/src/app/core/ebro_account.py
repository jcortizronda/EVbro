"""ebro_account.py — Changing the password of the Ebro account (the one of the official app), as the official app does it.

Read from the official app 1.0.11 (`ChangePassWordViewModel.changePassword` and
`PassWordInputEl`, 24-11-2026). Tried against the real cloud the same day (three changes in a row, each followed by a login
with the new password):

  POST {BFF}/marketing/v1/user/user/updatePasswd?client_id=bigapp
  body {"oldPassword": aes(current), "newPassword": aes(new)}   (aes = the login's `aes_encrypt_password`)
  Bearer access_token and the signed gateway headers (empty value map, like every POST of the app).

The app takes `code == 0` as success, and then signs the user out and asks for the new password: the change
closes the sessions opened with the old one. Before sending, it checks the new password with
`FindPasswordViewModel._validatePassword` (see `password_format_ok`).
"""
from __future__ import annotations

import string

from . import ebro_auth as A, wake
from .ebro_login import aes_encrypt_password
from .http import bff_post

UPDATE_PASSWORD_PATH = "/marketing/v1/user/user/updatePasswd"
CLIENT_ID = "bigapp"
MIN_LENGTH, MAX_LENGTH = 8, 16
# The app's list, as written in it (`~` twice)
SPECIAL_CHARACTERS = "!@#$%~&*()_-+=}[]:;\"'<>,.?/~"


def password_format_ok(password: str) -> bool:
    """The official app's rule for a new password: 8 to 16 characters with at least one letter (A-Z, a-z), one digit and
    one of `SPECIAL_CHARACTERS`. Other characters are not checked by the app."""
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        return False
    return (any(c in string.ascii_letters for c in password) and any(c in string.digits for c in password)
            and any(c in SPECIAL_CHARACTERS for c in password))


def change_password(ctx, current: str, new: str) -> dict:
    """Send the change with the account's current session (`ctx.token_path`). Returns the cloud's JSON as a dict (never raises
    on a non-JSON answer; see `http.bff_post`). BLOCKING (network)."""
    access = wake._access_token(ctx)
    headers = A.headers_post(UPDATE_PASSWORD_PATH, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    body = {"oldPassword": aes_encrypt_password(current), "newPassword": aes_encrypt_password(new)}
    return bff_post(ctx, UPDATE_PASSWORD_PATH, body, headers=headers, params={"client_id": CLIENT_ID})


def accepted(answer: dict) -> bool:
    """Whether the cloud took the change: `code == 0`, as the app checks it."""
    return str(answer.get("code")) == "0"


def refusal(answer: dict) -> str:
    """The cloud's reason for a refusal, short and without anything that could be a token (it is shown to the manager)."""
    text = str(answer.get("msg") or answer.get("message") or answer.get("code") or answer.get("_raw") or "").strip()
    return text.split("access_token")[0].strip()[:160]


# ───────────────────────── The car-control PIN of the account ─────────────────────────
# Read from the official app 1.0.11 (`RusApiService.changePassword`, `CarSettingPasswordViewModel.controlAddPsw`, 24-11-2026). The first
# try from EVbro (scene 0) got A00089; with scene 2 not yet tried. The body does not carry the current PIN: the `taskId` proves it, minted by `checkPassword` with that PIN and
# `scene = 2` (the app asks for the PIN to open the car settings; with the commands' `scene = 0` the cloud answered A00089, 24-11-2026). `newPassword` is encoded like `checkPassword.password`.
CHANGE_PIN_PATH = "/tsp/v1/app/cpm/changePassword"
PIN_LENGTH = 6                       # the app's field: «Introduce la contraseña de 6 dígitos»


def pin_format_ok(pin: str) -> bool:
    return len(pin) == PIN_LENGTH and pin.isascii() and pin.isdigit()


def change_pin(ctx, tuid, current_pin: str, new_pin: str, language: str = "ES") -> dict:
    """Mint a taskId with `current_pin` (wrong-PIN lockout included: raises CommandError like any command) and send the new PIN.
    Returns the cloud's JSON as a dict. BLOCKING (network)."""
    from . import taskid

    task = taskid.mint_with_pin(ctx, tuid, current_pin, taskid.SCENE_CAR_SETTINGS)    # the settings' taskId, not a command's
    access = wake._access_token(ctx)
    headers = A.headers_post(CHANGE_PIN_PATH, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    body = {"tUserId": str(tuid), "channelId": ctx.channel_id, "language": language, "vin": ctx.vin, "taskId": task,
            "newPassword": taskid.encode_pin(new_pin), "needDecode": 0, "type": 0}
    return bff_post(ctx, CHANGE_PIN_PATH, body, headers=headers)


def pin_change_looks_accepted(answer: dict) -> bool:
    """The answer gives no reason to think it failed. What counts as success is not known for this route (the app reads it through
    a generic result type), so the caller then proves the new PIN with `checkPassword` before trusting it."""
    code = str(answer.get("code", "")).strip()
    return answer.get("success") is True or code in ("0", "200", "A00000")


# ───────────────────────── The car's nickname in the Ebro account ─────────────────────────
# Read from the official app 1.0.11 («Certificación de propietario» → edit; `CarOwnerCertifyApiService.updateCarInfo` and
# `fetchCertifyList`, 24-11-2026), not yet sent from here. It is the `nickname` that `queryList` reports and EVbro shows as
# the car's name. The PUT carries its fields as query parameters, signed with them (`ebro_auth.sign_values`).
OWNER_CARS_PATH = "/web/car-service/owner/list"
OWNER_AUTH_PATH = "/marketing/owner-auth/app"
NICKNAME_MAX_LENGTH = 30             # EVbro's own limit: the app's field limit is not known (the cloud says if it is too long)


def _bearer_headers(ctx, path: str, values: dict | None = None) -> dict:
    return A.headers_post(path, ctx=ctx, values=values, extra={
        "Authorization": f"Bearer {wake._access_token(ctx)}",
        "Accept": "application/json, text/plain, */*"})


def owner_cars(ctx) -> list:
    """The cars certified to the account, as the app lists them (each with `vin`, `nickname`, `defaultFlag`…). BLOCKING."""
    from .http import bff_request

    answer = bff_request(ctx, "GET", OWNER_CARS_PATH, headers=_bearer_headers(ctx, OWNER_CARS_PATH))
    data = answer.get("data")
    if isinstance(data, dict):
        data = data.get("records") or data.get("list") or data.get("rows") or []
    return [item for item in (data or []) if isinstance(item, dict)]


def set_car_nickname(ctx, vin: str, nickname: str, default_flag) -> dict:
    """Send the new nickname with the car's `defaultFlag` as it was (the app sends the three together). BLOCKING."""
    from .http import bff_request

    values = {"vin": vin, "defaultFlag": default_flag, "nickname": nickname}
    return bff_request(ctx, "PUT", OWNER_AUTH_PATH, headers=_bearer_headers(ctx, OWNER_AUTH_PATH, values), params=values)
