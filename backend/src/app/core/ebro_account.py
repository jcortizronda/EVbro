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
