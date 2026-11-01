#!/usr/bin/env python3
"""The two ways of talking to the backend, and their timeouts.

There are exactly two channels, and mixing them up does not work:

* **BFF** (`legend.ebroauto.com`) — login, PIN and vehicle list. Authenticated with
  `Bearer <access_token>` and signed gateway headers (`ebro_auth.headers_post`).
* **TSP** (`tspconsole-*.ebroauto.com`) — telemetry and commands. Authenticated with `userToken`
  and a **signed body** (`tsp_sign.sign_body`), which is a different signature.

They were hand-written in `wake`, `commands`, `probe` and even in the account setup, each with
its own timeout (20 s here, 25 s there, with no rationale) and its own handling of the «the
response is not JSON» case — which really happens: the BFF sometimes returns a body whose
top level is a string, and a bare `.get()` blows up there.
"""
from __future__ import annotations

import json
import time

import requests

from . import ebro_auth as A, tsp_sign as S

# Timeouts (seconds). The write one is more generous because `checkPassword` chains three
# backend-side calls before answering.
READ_TIMEOUT_S = 20
WRITE_TIMEOUT_S = 25


def _as_dict(response: requests.Response) -> dict:
    """The response as a dict. Never raises.

    The BFF can return a top level that is NOT an object (a bare string when the
    token has expired). Returning `{}` lets the caller treat it as an invalid session, which
    is right, instead of propagating an AttributeError from inside an executor."""
    try:
        parsed = response.json()
    except Exception:
        return {"_raw": response.text[:200]}
    return parsed if isinstance(parsed, dict) else {}


def bff_post(ctx, path: str, body: dict, *, headers: dict | None = None,
             timeout: int = WRITE_TIMEOUT_S) -> dict:
    """POST to the BFF (login/PIN/vehicles). Returns the JSON as a dict.

    If no headers are passed, the signed gateway ones are built, which is what any
    BFF route expects."""
    if headers is None:
        headers = A.headers_post(path, ctx=ctx)
    response = requests.post(ctx.bff + path, data=json.dumps(body), headers=headers,
                             timeout=timeout)
    return _as_dict(response)


def signed_post(ctx, user_token: str, path: str, params: dict,
                *, timeout: int = WRITE_TIMEOUT_S) -> tuple[int, dict]:
    """POST to the TSP with a signed BODY. Returns (HTTP status, JSON).

    The HTTP status is returned separately because the backend always answers 200 and puts the
    real result in the body's `code`: the caller needs both to tell «the
    server did not answer» from «the server said no»."""
    ts = int(time.time() * 1000)
    body = S.sign_body(dict(params), ts, half=ctx.sign_key)
    headers = S.auth_headers(user_token, ts)
    headers.update({"Content-Type": "application/json; charset=UTF-8",
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": "okhttp/4.9.0", "version": A.APP_VERSION,
                    "agent": "android"})
    response = requests.post(ctx.tsp_host + path, data=json.dumps(body), headers=headers,
                             timeout=timeout)
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, {"raw": response.text[:300]}
