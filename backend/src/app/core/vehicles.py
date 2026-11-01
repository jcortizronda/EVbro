#!/usr/bin/env python3
"""The account's vehicles: `queryList` and how its response is read.

`/tsp/v1/app/vmc/queryList` is the call that answers «which cars does this account have». It
was used in three places and each wrote it out in full — BFF login, signed headers, POST and
response parsing:

* the account setup (login), to discover the VINs;
* the poller, to fill in the vehicle's name/model;
* `commands._checkpassword`, as the first step of taskId minting.

The parsing was the dangerous part: the backend returns the list under plain `data`, or under
`data.controlCarList`, or `data.authorizedControlCarList`, or `data.carList`… and each copy
knew a different subset of those shapes. A single implementation is a single way of
getting it wrong.
"""
from __future__ import annotations

from . import ebro_auth as A, wake
from .http import bff_post

QUERY_LIST_PATH = "/tsp/v1/app/vmc/queryList"

# Keys under which the backend has returned the vehicle list. Order does not matter: they
# are all accumulated, because an owned car and a delegated one live in different lists.
_LIST_KEYS = ("controlCarList", "authorizedControlCarList", "carList", "vehicles")


def query_list(ctx, body: dict | None = None) -> dict:
    """Call `queryList` with the current token. Returns the raw JSON response.

    **The body can NOT be empty.** With `{}` the cloud answers `code=1` and `data: null` — it is not a
    network or session error, it simply does not return the list. Measured on a real install:
    the vehicle model came out empty and a reload left no trace, because the response arrived
    «fine» and what was missing was the content.

    The account setup knew this and sent `tUserId` and `channelId`; the poller called without a body and
    had been left without an identity all along. That is why the body is built HERE and not in each
    caller: it was one of those differences nobody sees until someone looks at the log.

    The account setup still passes it explicitly because during setup the `tUserId` is not yet
    in the `ctx` — the login has only just returned it."""
    if body is None:
        body = {"channelId": ctx.channel_id}
        if str(getattr(ctx, "tuserid", "") or "").strip():
            body["tUserId"] = str(ctx.tuserid)
    access = wake._access_token(ctx)
    headers = A.headers_post(QUERY_LIST_PATH, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    return bff_post(ctx, QUERY_LIST_PATH, body, headers=headers)


def _vehicles_with_source(response: dict) -> list[tuple[dict, str]]:
    """The vehicles of a `queryList` response, each with the KEY it came from.

    Shapes seen live: `data` as a list; `data` as a dict with one or more of the
    `_LIST_KEYS` lists; and `data` as the vehicle itself (a single car on the account).

    The traversal lives here exactly once on purpose. `iter_vehicles` and `source_list` need the
    same thing at different levels of detail, and writing it twice is exactly the problem this module
    came to solve."""
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, list):
        return [(v, "data") for v in data if isinstance(v, dict)]
    if not isinstance(data, dict):
        return []
    vehicles: list[tuple[dict, str]] = []
    for key in _LIST_KEYS:
        entries = data.get(key)
        if isinstance(entries, list):
            vehicles += [(v, key) for v in entries if isinstance(v, dict)]
    if not vehicles and "vin" in data:
        return [(data, "data")]
    return vehicles


SET_DEFAULT_PATH = "/tsp/v1/app/vmc/setVecDefault"


def set_default(ctx) -> dict:
    """Mark this vehicle as the DEFAULT one of the logged-in account.

    It does not need the PIN. It is the second step of the chain the app runs before every
    command —`queryList` → `setVecDefault` → `checkPassword`— and only the third asks for the PIN,
    so this can be called without risking a lockout attempt.

    **Why it is also called at start-up.** With a secondary account the MQTT subscription is
    granted («Granted QoS 1») and the channel receives nothing over hours of car use, while the
    official app on that same account reflects openings instantly. The vehicle record
    carries a `defCar` field, and the hypothesis is that the cloud only publishes the alerts of the vehicle
    marked as default on that account: the channel would be ours —hence the permission— but
    with nothing attached to it. The app marks it on entry; we only did so before a command
    with a PIN, which on that account never got to complete.

    If the hypothesis fails nothing is lost: it is a write-only request on the
    account's preference, the same one already made before every command.
    """
    access = wake._access_token(ctx)
    headers = A.headers_post(SET_DEFAULT_PATH, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    return bff_post(ctx, SET_DEFAULT_PATH, {"vin": ctx.vin}, headers=headers)


#: READ-ONLY endpoints on the account's permissions over the vehicle. They come from comparing
#: the 25 in the app binary with the 14 we use: we have never called these two,
#: and their names are the only ones that talk about authority and remote permissions.
#:
#: They matter because this cloud REQUIRES prior steps — `checkPassword` returns no taskId unless
#: `queryList` and `setVecDefault` have been called first. If a delegated account needed to
#: query its permissions before being able to execute, it would be here. And even if it is not a prior step,
#: its response would say WHAT this account can do, which is the question we have spent all night
#: trying to answer by elimination.
AUTHORITY_PATHS = (
    "/tsp/v1/app/vmc/queryVehicleAuthority",
    "/tsp/v1/app/vac/queryAuthRemoteList",
    # `vac/query`: the real name came from re-extracting the strings from the binary. The first
    # read gave `vac/query6` and `vac/add8` — the digits were bytes of the next string, not
    # part of the path. It is the query of the AUTHORISATIONS space, the only place where
    # WHO owns the vehicle might show up, which is what would be needed if the
    # car publishes on the owner's channel and not on the listener's.
    "/tsp/v1/app/vac/query",
)


def _escalar_corto(valor) -> bool:
    """Does this value fit in a summary? Discards long texts and nested structures."""
    return isinstance(valor, (bool, int, float)) or (isinstance(valor, str) and len(valor) <= 24)


def _resumen_permisos(datos) -> dict:
    """The `permissionList` compressed so it fits in the report.

    It takes 34 KB raw —measured— so dumping it whole is not an option. What matters in it
    is WHICH functions this account can run on the car, and that lives in the short fields
    of each entry (a code and a flag); what bloats it are descriptions and icons.

    Three things are returned: one FULL entry to see the shape —without it you do not know which
    field to look at—, how many there are, and all of them projected to their short fields. It is the same criterion as
    with the vehicle's field names: first the shape, then the content that matters.
    """
    lista = datos.get("permissionList") if isinstance(datos, dict) else None
    if not isinstance(lista, list) or not lista:
        return {}
    # {id: state} of all 329, SORTED. It is the only thing that allows truly comparing two accounts:
    # the previous summary cut to the first 40, and the backend returns the list in a different
    # order on each call, so different entries were being compared and they seemed to differ.
    # Sorted and complete it fits in ~3 KB and can be diffed line by line.
    estados = {}
    for entrada in lista:
        if isinstance(entrada, dict):
            estados[str(entrada.get("id"))] = entrada.get("state")
    denegados = sorted((k for k, v in estados.items() if v == 0), key=lambda k: (len(k), k))
    return {
        "perm_count": len(lista),
        # Only the DENIED ones, complete and sorted. All 329 with their state do not fit in the
        # report, and comparing two accounts comes down to comparing what each one lacks.
        #
        # Measured: both lists weigh exactly the same (34863) and YET they differ —
        # changing a `1` into a `0` does not change the size of the text. We nearly accepted the
        # equality by looking at the size. There are already two confirmed differences between owner and delegate
        # («Seguridad» and «Ajustes de seguridad», allowed only to the owner), so this list
        # is per ACCOUNT and not per car: it deserves to be compared in full.
        # In CHUNKS of 50 ids joined by commas, not as a list. The monitor's redaction trims
        # lists by number of elements and strings to 400 characters, so the 119
        # denied ones were lost halfway exactly when they were needed whole to subtract the two
        # accounts. Each chunk fits comfortably under both limits.
        **{f"perm_denied_{i // 50 + 1}": ",".join(denegados[i:i + 50])
           for i in range(0, len(denegados), 50)},
        "perm_denied_n": len(denegados),
    }


def claim_authority(ctx) -> dict:
    """`queryVehicleAuthority` as a STEP of the taskId chain, not as diagnostics.

    This cloud requires laying the groundwork: `checkPassword` returns no taskId unless
    `queryList` and `setVecDefault` have been called first — proven since the prototype. The app also
    has this call, which we never made, and with a delegated account `checkPassword`
    answers `code=1 'A07908'` with everything else verified.

    The response confirms the account DOES have the permission for the failing command («Desbloquear»
    is at 1), so it is not queried to decide anything: it is called in case the backend needs it
    to have been queried, just as with `setVecDefault`."""
    access = wake._access_token(ctx)
    path = AUTHORITY_PATHS[0]
    headers = A.headers_post(path, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    cuerpo = {"vin": ctx.vin, "channelId": ctx.channel_id}
    if str(getattr(ctx, "tuserid", "") or "").strip():
        cuerpo["tUserId"] = str(ctx.tuserid)
    return bff_post(ctx, path, cuerpo, headers=headers)


GET_TUSERID_PATH = "/tsp/v1/app/auth/getTuserId"


def probe_tuserid(ctx) -> dict:
    """`getTuserId` — is the channel identifier MY account's or the owner's?

    Measured with a delegated account: the MQTT subscription is granted and NOTHING arrives, not even
    the confirmation of a command that same account has just run successfully. The fact that the
    confirmation of its own command does not arrive rules out permissions and content: if the cloud publishes
    and we do not receive it, the channel we listen on is not its channel.

    The topic is built today with the `tuserid` of whoever logs in. If for a delegated
    vehicle the cloud publishes on the OWNER's channel, this endpoint —which the app has and
    we did not use— should return an identifier different from ours.

    They are compared, NEVER published: they point to the account and are on the report's
    redaction list."""
    access = wake._access_token(ctx)
    headers = A.headers_post(GET_TUSERID_PATH, ctx=ctx, extra={
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*"})
    cuerpo = {"vin": ctx.vin, "channelId": ctx.channel_id}
    if str(getattr(ctx, "tuserid", "") or "").strip():
        cuerpo["tUserId"] = str(ctx.tuserid)
    j = bff_post(ctx, GET_TUSERID_PATH, cuerpo, headers=headers)
    datos = j.get("data") if isinstance(j, dict) else None
    # The value may come bare or inside a dict; it is normalised to text for comparison.
    devuelto = datos
    if isinstance(datos, dict):
        devuelto = datos.get("tUserId") or datos.get("tuserId") or datos.get("userId")
    propio = str(getattr(ctx, "tuserid", "") or "").strip()
    return {
        "code": j.get("code") if isinstance(j, dict) else None,
        "data_type": type(datos).__name__,
        "data_keys": sorted(datos) if isinstance(datos, dict) else None,
        "devuelve_algo": devuelto is not None and str(devuelto).strip() != "",
        # WHAT MATTERS, without exposing either of the two values.
        "coincide_con_el_nuestro": (str(devuelto).strip() == propio) if devuelto is not None else None,
    }


def query_authority(ctx) -> dict:
    """Call the permission endpoints and return {path: summary} for diagnostics.

    A summary, not a dump: the `code` and the top-level keys of `data`, plus `data` itself
    when it is small — that is where the permissions would be, which is exactly what we are after. It then
    goes through the monitor's redaction like everything else.

    Never raises: it is an instrument, and a broken instrument must not bring start-up down."""
    resumen: dict = {}
    for path in AUTHORITY_PATHS:
        try:
            access = wake._access_token(ctx)
            headers = A.headers_post(path, ctx=ctx, extra={
                "Authorization": f"Bearer {access}",
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json, text/plain, */*"})
            # `tUserId` included: the first version sent only vin+channelId and both paths
            # answered `code=1` with `data: null` — exactly the symptom
            # `queryList` had last night when the body was incomplete. It is the same lesson, applied
            # to the same BFF: here `code=1` means «I don't like the request», not «you have no
            # permission», so it cannot be read as an answer about the account.
            cuerpo = {"vin": ctx.vin, "channelId": ctx.channel_id}
            if str(getattr(ctx, "tuserid", "") or "").strip():
                cuerpo["tUserId"] = str(ctx.tuserid)
            j = bff_post(ctx, path, cuerpo, headers=headers)
        except Exception as err:
            resumen[path] = {"error": type(err).__name__}
            continue
        datos = j.get("data") if isinstance(j, dict) else None
        resumen[path] = {
            "code": (j or {}).get("code") if isinstance(j, dict) else None,
            "message": str((j or {}).get("message") or "")[:60] if isinstance(j, dict) else None,
            "data_type": type(datos).__name__,
            "data_keys": sorted(datos) if isinstance(datos, dict) else None,
            "size": len(str(datos)) if datos is not None else 0,
            # SMALL responses are kept whole. `queryAuthRemoteList` returns a
            # 181-byte list that was being lost: the summary only knew how to handle the other path's
            # `permissionList`. And that response matters, because with the delegated account that same
            # call is rejected (`code=1`) and with the owner's it answers — another measured difference
            # between the two accounts.
            "data": datos if datos is not None and len(str(datos)) < 1500 else None,
            **_resumen_permisos(datos),
        }
    return resumen


def iter_vehicles(response: dict) -> list[dict]:
    """The vehicles of a `queryList` response, whatever shape it comes in."""
    return [v for v, _src in _vehicles_with_source(response)]


def source_list(response: dict, vin: str) -> dict | None:
    """WHICH list this VIN came from, and with what kind of authorisation. `None` if absent.

    It is DIAGNOSTIC data, not operational: nothing in the component changes depending on what
    it returns. It exists because of a suspicion we have not been able to confirm yet — that a
    secondary (guest) account receives commands and queries normally but NOT the alerts
    the car sends on its own (doors, locking, boot), which is the fault that took weeks
    to diagnose in the field. The names of the two lists point right there:
    `controlCarList` versus `authorizedControlCarList` («cars you have been authorised to
    control»), and the entries of the second carry an `authorizeType`.

    There is no proof: nothing observed in the official app mentions those
    keys, and we kept no response from when the user was on the secondary
    account. Recording it in diagnostics is what will let us confirm or rule it out with the
    first report from someone affected, before building a warning on top of an assumption.
    """
    for vehicle, source in _vehicles_with_source(response):
        if str(vehicle.get("vin")) == vin:
            tipo = vehicle.get("authorizeType")
            return {"list": source, "authorize_type": tipo} if tipo is not None else {"list": source}
    return None


def entry_fields(response: dict, vin: str) -> list[str] | None:
    """The NAMES of the fields the backend brings for this vehicle. Never the values.

    A diagnostic instrument, not an operational one. It comes from a measurement that knocked down the theory
    we had: with a secondary account the MQTT subscription is granted («Granted QoS 1») and
    the channel receives nothing over hours of car use, but **the official app on that same
    account does update instantly**. So the cloud does publish; what does not match is the channel.

    The channel is built today with the identifier of WHOEVER LOGS IN. If the car publishes
    on the owner's, we would need to know which one it is — and the cheapest candidate is this record, which
    for now is read in full and thrown away except for four fields. `authorizeType` turned up exactly like that.

    Names only: the values carry the VIN and the `carToken`, and this ends up in a report that
    gets shared."""
    for vehicle, _src in _vehicles_with_source(response):
        if str(vehicle.get("vin")) == vin:
            return sorted(vehicle)
    return None


#: Vehicle-record fields that are RECORDED WITH THEIR VALUE in diagnostics. They are enums and
#: flags —they carry no identifiers or secrets— and each one is here for a specific reason:
#:
#: * `passwordType`  — the main suspect. We always encrypt the PIN the same way (md5 + SM4 with
#:                     padding to 32), and if the backend supports more than one scheme this field would say
#:                     which one applies. It would fit with the app accepting a PIN that
#:                     rejects us with `checkPassword code=1`.
#: * `authorizeType` — 0 owner / 1 delegated account (measured on 2026-10-01).
#: * `defCar`        — whether this car is the account's default; it is the other open
#:                     hypothesis about why telemetry does not arrive.
#: * `vehicleType`, `powerType`, `typeCode` — model context, in case some difference
#:                     turns out to be tied to the car's variant.
#: * `authorizeStartTime` / `authorizeEndTime` / `authorizeTime` — the delegation has a validity
#:                     WINDOW. Added after checking that the `checkPassword` request is
#:                     identical to the one in the pre-repository prototype: if it used to work
#:                     and now the server answers «the account cannot run this command»,
#:                     what changed is not in our code but in the permission.
_FLAG_KEYS = ("passwordType", "authorizeType", "defCar", "vehicleType", "powerType", "typeCode",
              "authorizeStartTime", "authorizeEndTime", "authorizeTime", "isHaveLoAndHi")


def entry_flags(response: dict, vin: str) -> dict | None:
    """The `_FLAG_KEYS` values for this vehicle. Diagnostics, not operation.

    Unlike `entry_fields` —which gives only the NAMES because the values carry the VIN and
    the `carToken`— here the values do go, but from a closed allowlist of fields that do not
    identify anyone. The list is an allowlist on purpose: a dump of the whole record would end up
    exporting the chassis number in the first report someone shares."""
    for vehicle, _src in _vehicles_with_source(response):
        if str(vehicle.get("vin")) == vin:
            return {k: vehicle[k] for k in _FLAG_KEYS if k in vehicle}
    return None


def password_type(response: dict, vin: str):
    """The `passwordType` the backend declares for this vehicle, or `None` if absent.

    It comes as a LIST (`[0]` measured live), which reads as «supported verification
    types»; the first one is taken. If it arrived as a scalar it is returned as is.

    Needed for `checkPassword`. Three independent clues point there: the official
    app's binary contains `passwordType` among its field names, the cloud returns it PER
    ACCOUNT in the vehicle record, and we were not sending it. With a delegated account the app
    accepts a PIN that rejects us with `code=1`, and the other differences
    —account identifier, PIN encryption, default car— have already been ruled out one by one.
    """
    for vehicle, _src in _vehicles_with_source(response):
        if str(vehicle.get("vin")) == vin:
            valor = vehicle.get("passwordType")
            if isinstance(valor, list):
                return valor[0] if valor else None
            return valor
    return None


def vins(response: dict) -> list[str]:
    """The account's VINs, in the order the backend returns them."""
    return [str(v["vin"]) for v in iter_vehicles(response) if v.get("vin")]


def find_vehicle(response: dict, vin: str) -> dict | None:
    """The vehicle whose VIN matches; if absent, the first one in the list.

    Falling back to the first is deliberate: with a single car on the account, the backend sometimes
    returns the VIN in a different format from the one we stored, and losing the vehicle's
    identity because of that would be worse than using the only one there is."""
    vehicles = iter_vehicles(response)
    exact = next((v for v in vehicles if str(v.get("vin")) == vin), None)
    if exact is not None:
        return exact
    return vehicles[0] if vehicles else None


#: Keys under which the backend has returned the commercial MODEL. `fullName` came first and was
#: the only one: on a real install the device model came out empty (`model: null` in the
#: device registry) because this cloud does not send that key, it sends `modelName`. Both are
#: tried in case some account returns the other, which is exactly the kind of variation that has already
#: shown up with the vehicle list.
_MODEL_KEYS = ("modelName", "fullName", "seriesName")


def identity(response: dict, vin: str) -> dict | None:
    """Vehicle name and model for the registered vehicle.

    `nickname` is the nickname the user set in the app; the commercial model arrives in one of
    `_MODEL_KEYS`. The nickname is preferred, and if there is none the model is used. With neither
    it returns `None`, so the caller keeps its default instead of setting an
    empty name."""
    item = find_vehicle(response, vin)
    if not item:
        return None
    nick = str(item.get("nickname") or "").strip()
    model = next((v for v in (str(item.get(k) or "").strip() for k in _MODEL_KEYS) if v), "")
    name = nick or (model.title() if model else "")
    if not name:
        return None
    return {"name": name, "model": model.title() if model else None}
