"""Tests for `core/vehicles.py` — «which cars does this account have?».

This module exists because the parsing was dangerous: the backend returns the vehicle list
under plain `data`, or under one of four keys inside `data`, or as the bare car itself
if the account has only one. That untangling was written three times —config flow, coordinator and
`taskid`— and each copy knew a different subset of the shapes. It was unified to have a
single way of getting it wrong... and it was the only module in the component without a single test.

What is tested is the PARSING, which is where the risk is: if the cloud changes shape, the
integration setup stops finding the car. For `query_list` it is enough to check it hits the
right path, because the HTTP is already tested in `core/test_http`.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.core import vehicles

COCHE = {"vin": "LSJA0000000000001", "nickname": "Mi Ebro", "modelName": "S900"}
OTRO = {"vin": "LSJA0000000000002", "nickname": "El otro", "modelName": "S800"}


# ───────────────────── iter_vehicles: the six shapes seen live ─────────────────────


@pytest.mark.parametrize(
    ("forma", "respuesta"),
    [
        ("data como lista", {"data": [COCHE]}),
        ("data.controlCarList", {"data": {"controlCarList": [COCHE]}}),
        ("data.authorizedControlCarList", {"data": {"authorizedControlCarList": [COCHE]}}),
        ("data.carList", {"data": {"carList": [COCHE]}}),
        ("data.vehicles", {"data": {"vehicles": [COCHE]}}),
        ("data como el coche suelto", {"data": COCHE}),
    ],
)
def test_iter_vehicles_understands_every_shape(forma: str, respuesta: dict) -> None:
    assert vehicles.iter_vehicles(respuesta) == [COCHE], forma


def test_an_owned_and_a_delegated_car_both_arrive() -> None:
    """The case that led to unifying the module: they live in DIFFERENT lists, so they must all
    be accumulated instead of keeping the first one that shows up."""
    respuesta = {"data": {"controlCarList": [COCHE], "authorizedControlCarList": [OTRO]}}

    assert vehicles.iter_vehicles(respuesta) == [COCHE, OTRO]


@pytest.mark.parametrize(
    "basura",
    [
        {},                                   # no `data`
        {"data": None},
        {"data": "ups"},                      # `data` is neither a list nor a dict
        {"data": {}},                         # empty dict, no `vin`
        {"data": {"controlCarList": "ups"}},  # the key exists but is not a list
        {"data": [None, 42, "x"]},            # entries that are not dicts
        None,                                 # not even a dict
        "respuesta de texto",
    ],
)
def test_iter_vehicles_survives_garbage(basura) -> None:
    """It is a third-party backend's response: an unexpected shape must give an empty list, not
    an exception that brings the integration setup down."""
    assert vehicles.iter_vehicles(basura) == []


def test_bare_data_only_counts_as_a_car_with_a_vin() -> None:
    """The «`data` is the car itself» fallback relies on the `vin`: without it, any
    metadata dict would slip in as if it were a vehicle."""
    assert vehicles.iter_vehicles({"data": {"total": 0, "page": 1}}) == []
    assert vehicles.iter_vehicles({"data": {"vin": "X", "total": 0}}) == [{"vin": "X", "total": 0}]


# ───────────────────────────────── vins ─────────────────────────────────


def test_vins_keeps_the_backend_order() -> None:
    assert vehicles.vins({"data": [COCHE, OTRO]}) == [COCHE["vin"], OTRO["vin"]]


def test_vins_drops_entries_without_vin() -> None:
    assert vehicles.vins({"data": [{"nickname": "sin vin"}, COCHE]}) == [COCHE["vin"]]


# ────────────────────────────── find_vehicle ──────────────────────────────


def test_find_vehicle_finds_the_exact_vin() -> None:
    assert vehicles.find_vehicle({"data": [COCHE, OTRO]}, OTRO["vin"]) is OTRO


def test_find_vehicle_falls_back_to_the_first_if_vin_is_absent() -> None:
    """Deliberate, documented rule: with a single car, the backend sometimes returns the VIN in
    another format, and losing the identity because of that would be worse than using the only one there is.

    CAREFUL: as written, the rule also applies with SEVERAL cars, and then it
    silently returns the wrong one. It is pinned here so that the day it changes it is a
    decision and not an accident."""
    assert vehicles.find_vehicle({"data": [COCHE]}, "BASTIDOR-QUE-NO-ESTA") is COCHE
    assert vehicles.find_vehicle({"data": [COCHE, OTRO]}, "BASTIDOR-QUE-NO-ESTA") is COCHE


def test_find_vehicle_without_cars_returns_none() -> None:
    assert vehicles.find_vehicle({"data": []}, COCHE["vin"]) is None


# ──────────────────────────────── identity ────────────────────────────────


def test_identity_prefers_the_user_nickname() -> None:
    info = vehicles.identity({"data": [COCHE]}, COCHE["vin"])

    assert info is not None
    assert info["name"] == "Mi Ebro"


def test_identity_uses_the_model_without_nickname() -> None:
    sin_apodo = {"vin": "X", "modelName": "s900"}

    info = vehicles.identity({"data": [sin_apodo]}, "X")

    assert info == {"name": "S900", "model": "S900"}


def test_identity_takes_the_model_from_what_the_backend_sends() -> None:
    """The device model came out EMPTY on a real install (`model: null` in the
    device registry) because it was looked up in `fullName`, a key this cloud does not send:
    what it returns is `modelName`."""
    info = vehicles.identity({"data": [COCHE]}, COCHE["vin"])

    assert info is not None
    assert info["model"] == "S900"


def test_identity_without_name_or_model_returns_none() -> None:
    """`None` and not a dict with empty strings: the coordinator uses it to keep its default
    name instead of leaving the device nameless."""
    assert vehicles.identity({"data": [{"vin": "X"}]}, "X") is None


def test_identity_without_cars_returns_none() -> None:
    assert vehicles.identity({"data": []}, "X") is None


# ────────────────────────────── source_list ──────────────────────────────


@pytest.mark.parametrize(
    "clave",
    ["controlCarList", "authorizedControlCarList", "carList", "vehicles"],
)
def test_source_list_says_which_list_the_car_came_from(clave: str) -> None:
    """Pure diagnostic data: the suspicion is that a secondary account arrives via
    `authorizedControlCarList` and does not receive the alerts the car sends on its own. Without recording it, that
    information is read and thrown away on every query."""
    respuesta = {"data": {clave: [COCHE]}}

    assert vehicles.source_list(respuesta, COCHE["vin"]) == {"list": clave}


def test_source_list_tells_owned_from_delegated_car() -> None:
    """The case that gives all this its meaning: with both lists at once, each car must
    be attributed to its own."""
    respuesta = {"data": {"controlCarList": [COCHE], "authorizedControlCarList": [OTRO]}}

    assert vehicles.source_list(respuesta, COCHE["vin"]) == {"list": "controlCarList"}
    assert vehicles.source_list(respuesta, OTRO["vin"]) == {"list": "authorizedControlCarList"}


def test_source_list_picks_up_the_authorisation_type_if_present() -> None:
    """`authorizeType` only appears in delegated entries. It is exactly the field that could
    say WHAT kind of permission the account has, so it travels with the rest."""
    delegado = {**COCHE, "authorizeType": 0}

    info = vehicles.source_list({"data": {"authorizedControlCarList": [delegado]}}, COCHE["vin"])

    assert info == {"list": "authorizedControlCarList", "authorize_type": 0}


def test_source_list_returns_none_if_the_car_is_absent() -> None:
    """No fallback to the first one, unlike `find_vehicle`: here a wrong attribution
    would be worse than not having the data, because it is exactly what we want to measure."""
    assert vehicles.source_list({"data": [COCHE]}, "BASTIDOR-QUE-NO-ESTA") is None
    assert vehicles.source_list({}, COCHE["vin"]) is None


# ────────────────────────────── entry_fields ──────────────────────────────


def test_entry_fields_returns_sorted_names() -> None:
    """Only the NAMES: the values carry the VIN and the `carToken`, and this ends up in a diagnostic
    report shared with third parties."""
    campos = vehicles.entry_fields({"data": [COCHE]}, COCHE["vin"])

    assert campos == ["modelName", "nickname", "vin"]


def test_entry_fields_leaks_no_value() -> None:
    """The instrument's safety net: if it ever returned the whole record instead of
    its keys, we would be exporting the VIN without noticing."""
    con_secretos = {**COCHE, "carToken": "token-secreto", "authorizeType": 1}

    campos = vehicles.entry_fields({"data": [con_secretos]}, COCHE["vin"])

    assert campos is not None
    assert "carToken" in campos                      # the name yes
    assert not any("token-secreto" in c for c in campos)   # the value no
    assert not any(COCHE["vin"] in c for c in campos)


def test_entry_fields_without_the_car_returns_none() -> None:
    assert vehicles.entry_fields({"data": [COCHE]}, "BASTIDOR-QUE-NO-ESTA") is None
    assert vehicles.entry_fields({}, COCHE["vin"]) is None


# ────────────────────────────── entry_flags ──────────────────────────────


def test_entry_flags_picks_up_allowlisted_flags() -> None:
    ficha = {**COCHE, "passwordType": 1, "authorizeType": 0, "defCar": 1}

    assert vehicles.entry_flags({"data": [ficha]}, COCHE["vin"]) == {
        "passwordType": 1, "authorizeType": 0, "defCar": 1}


def test_entry_flags_exports_nothing_outside_the_allowlist() -> None:
    """The list is an allowlist on purpose: a dump of the whole record would export the VIN and the
    `carToken` in the first report someone shares."""
    ficha = {**COCHE, "passwordType": 1, "carToken": "token-secreto", "iccid": "8934…"}

    banderas = vehicles.entry_flags({"data": [ficha]}, COCHE["vin"])

    assert banderas == {"passwordType": 1}
    volcado = json.dumps(banderas)
    assert "token-secreto" not in volcado
    assert COCHE["vin"] not in volcado
    assert "8934" not in volcado


def test_entry_flags_omits_missing_ones() -> None:
    """No made-up defaults: a missing flag is a piece of data in itself."""
    assert vehicles.entry_flags({"data": [COCHE]}, COCHE["vin"]) == {}
    assert vehicles.entry_flags({"data": [COCHE]}, "OTRO") is None


# ───────────────────────────── password_type ─────────────────────────────


def test_password_type_takes_the_first_of_the_list() -> None:
    """The backend returns it as a list (`[0]` measured live): supported verification
    types. The first one is sent."""
    assert vehicles.password_type({"data": [{**COCHE, "passwordType": [0]}]}, COCHE["vin"]) == 0
    assert vehicles.password_type({"data": [{**COCHE, "passwordType": [2, 0]}]}, COCHE["vin"]) == 2


def test_password_type_accepts_a_scalar() -> None:
    """In case some account returns it bare: this backend has already changed the shape of a
    response on us once (the vehicle list)."""
    assert vehicles.password_type({"data": [{**COCHE, "passwordType": 1}]}, COCHE["vin"]) == 1


def test_password_type_without_data_returns_none() -> None:
    """`None` must be distinguishable from `0`, which is a valid type: with `None` the field is NOT
    sent, and sending a made-up one would be worse than not sending it."""
    assert vehicles.password_type({"data": [COCHE]}, COCHE["vin"]) is None
    assert vehicles.password_type({"data": [{**COCHE, "passwordType": []}]}, COCHE["vin"]) is None
    assert vehicles.password_type({"data": [COCHE]}, "OTRO") is None


# ─────────────────────────────── query_list ───────────────────────────────


class _Ctx:
    """The bare minimum `query_list` looks at in `CoreCtx`."""

    channel_id = "4"
    tuserid = "401643347363401728"


def test_query_list_sends_the_account_and_the_channel() -> None:
    """With an EMPTY body the cloud answers `code=1` and `data: null` — measured on a real
    install, where the vehicle model had always been empty because of this. The config flow
    sent it and the coordinator did not; building it here is what stops them from diverging again."""
    with (
        patch.object(vehicles.wake, "_access_token", return_value="AT"),
        patch.object(vehicles.A, "headers_post", return_value={}),
        patch.object(vehicles, "bff_post", return_value={"code": "000000"}) as post,
    ):
        assert vehicles.query_list(_Ctx()) == {"code": "000000"}

    _ctx, path, body = post.call_args[0]
    assert path == vehicles.QUERY_LIST_PATH
    assert body == {"channelId": "4", "tUserId": "401643347363401728"}


def test_query_list_without_tuserid_sends_only_the_channel() -> None:
    """During setup the `tUserId` is not yet in the `ctx` —the login has only just returned it—, so
    the config flow passes the body explicitly. Sending `tUserId: ""` would be worse than not sending it."""
    ctx = _Ctx()
    ctx.tuserid = ""

    with (
        patch.object(vehicles.wake, "_access_token", return_value="AT"),
        patch.object(vehicles.A, "headers_post", return_value={}),
        patch.object(vehicles, "bff_post", return_value={}) as post,
    ):
        vehicles.query_list(ctx)

    assert post.call_args[0][2] == {"channelId": "4"}


def test_query_list_honours_the_explicit_body() -> None:
    """The config flow sends its own; it must not be overwritten."""
    propio = {"tUserId": "otro", "channelId": "9"}

    with (
        patch.object(vehicles.wake, "_access_token", return_value="AT"),
        patch.object(vehicles.A, "headers_post", return_value={}),
        patch.object(vehicles, "bff_post", return_value={}) as post,
    ):
        vehicles.query_list(_Ctx(), propio)

    assert post.call_args[0][2] == propio
