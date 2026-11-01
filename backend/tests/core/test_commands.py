"""Tests for `core/commands.py` — catalogue, taskId and command sending.

Two details of the module shape how it is mocked:

* `send()` uses **urllib**, while `_checkpassword` imports **requests inside the
  function**. That is why `patch("app.core.commands.requests")` does NOT work:
  the module has no such attribute. The global `requests.post` must be patched.
* `send()` puts `int(time.time()*1000)` into `seq` and into the signature → every test that looks at the
  envelope needs a frozen clock.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import catalog, commands, routing, taskid
from app.core.context import CoreCtx

FROZEN = "2026-01-15 12:00:00+00:00"


@pytest.fixture
def ctx(tmp_path) -> CoreCtx:
    return CoreCtx(
        vin="LSJA0000000000001",
        tuserid="tuser-0000000000",
        pin="1234",
        token_path=str(tmp_path / "token.json"),
        taskid_file=str(tmp_path / "taskid.txt"),
    )


def _urlopen(payload: dict, status: int = 200) -> MagicMock:
    """Build the context manager returned by `urllib.request.urlopen`."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.status = status
    cm = MagicMock()
    cm.__enter__.return_value = resp
    return cm


# ───────────────────────── catalogue (pure) ─────────────────────────


def test_catalogue_has_no_duplicate_keys() -> None:
    """`CMD_MAP = dict(COMMANDS)`: a repeated key would silently swallow a command."""
    claves = [k for k, _ in catalog.COMMANDS]
    assert len(claves) == len(set(claves))
    assert len(catalog.CMD_MAP) == len(catalog.COMMANDS)


def test_every_command_has_target_and_name() -> None:
    for key, spec in catalog.COMMANDS:
        assert spec.get("path") or spec.get("endpoint"), key
        assert spec.get("name"), key
        assert isinstance(spec.get("body"), dict), key


def test_command_error_flags_retryable() -> None:
    assert commands.CommandError("x", code="A00082").retryable is True
    assert commands.CommandError("x", code="A00084").retryable is False
    assert commands.CommandError("x").retryable is False


# ───────────────────────── messages (pure) ─────────────────────────


def test_mensajes_de_checkpassword(snapshot: SnapshotAssertion) -> None:
    """The text is kept apart from the decision on purpose: it can be rewritten or translated without
    touching the routing. The snapshot pins all three messages at once."""
    detail = "code=A00285 'wrong password'"
    assert {
        reason: taskid._checkpassword_message(
            routing.Classification(reason=reason), detail
        )
        for reason in (routing.REASON_REAUTH, routing.REASON_CONFIG, routing.REASON_PIN)
    } == snapshot


# ───────────────────────── get_taskid ─────────────────────────


def test_get_taskid_from_file(ctx: CoreCtx) -> None:
    """Source order: file → cache → checkPassword."""
    with open(ctx.taskid_file, "w", encoding="utf-8") as fh:
        fh.write("  task-de-archivo  \n")

    assert taskid.get_taskid(ctx, "tu") == ("task-de-archivo", "file")


def test_get_taskid_from_cache(ctx: CoreCtx, freezer) -> None:
    """Re-minting it costs the whole checkPassword round trip, which is the slow part of the command."""
    freezer.move_to(FROZEN)
    ctx.state.taskid = "task-en-cache"
    ctx.state.taskid_ts = __import__("time").time()

    assert taskid.get_taskid(ctx, "tu") == ("task-en-cache", "cache")


def test_get_taskid_expired_cache_remints(ctx: CoreCtx, freezer) -> None:
    freezer.move_to(FROZEN)
    ctx.state.taskid = "task-viejo"
    ctx.state.taskid_ts = __import__("time").time()
    freezer.tick(ctx.taskid_ttl + 1)

    with patch.object(taskid, "_mint_taskid", return_value="task-nuevo"):
        tid, src = taskid.get_taskid(ctx, "tu")

    assert (tid, src) == ("task-nuevo", "checkPassword")
    assert ctx.state.taskid == "task-nuevo"


def test_get_taskid_force_mint_skips_file_and_cache(ctx: CoreCtx) -> None:
    """On the retry after a rejection, reusing the same source would give the same error."""
    with open(ctx.taskid_file, "w", encoding="utf-8") as fh:
        fh.write("task-de-archivo")

    with patch.object(taskid, "_mint_taskid", return_value="task-fresco") as mint:
        tid, src = taskid.get_taskid(ctx, "tu", force_mint=True)

    assert (tid, src) == ("task-fresco", "checkPassword")
    mint.assert_called_once()


def test_get_taskid_with_minting_disabled(ctx: CoreCtx) -> None:
    ctx.mint_taskid = False
    assert taskid.get_taskid(ctx, "tu") == (None, "none")


def test_get_taskid_propagates_command_error_with_its_reason(ctx: CoreCtx) -> None:
    """`send()` needs the `reason` intact to route the right remedy."""
    err = commands.CommandError("PIN rechazado", reason="pin")

    with (
        patch.object(taskid, "_mint_taskid", side_effect=err),
        pytest.raises(commands.CommandError) as capt,
    ):
        taskid.get_taskid(ctx, "tu")

    assert capt.value.reason == "pin"


def test_get_taskid_unexpected_error_is_wrapped_as_pin(ctx: CoreCtx) -> None:
    with (
        patch.object(taskid, "_mint_taskid", side_effect=ValueError("raro")),
        pytest.raises(commands.CommandError) as capt,
    ):
        taskid.get_taskid(ctx, "tu")

    assert capt.value.reason == "pin"


# ───────────────────────── _mint_taskid_impl ─────────────────────────


def test_empty_pin_fails_before_the_lockout(ctx: CoreCtx) -> None:
    """An unconfigured PIN is not a wrong attempt: it must not use up the threshold or touch the
    backend."""
    ctx.pin = "   "

    with pytest.raises(commands.CommandError) as capt:
        taskid._mint_taskid_impl(ctx, "tu")

    assert capt.value.reason == "pin"
    assert ctx.lockout.failed_attempts == 0


def test_lockout_turns_the_lock_into_command_error(ctx: CoreCtx) -> None:
    ctx.lockout.max_failures = 1
    with ctx.lockout.attempt() as attempt:
        attempt.record_failure()

    with pytest.raises(commands.CommandError) as capt:
        taskid._mint_taskid_impl(ctx, "tu")

    assert capt.value.reason == "pin"
    assert "bloqueado temporalmente" in str(capt.value)


# ───────────────────────── send() ─────────────────────────


def test_send_unknown_command(ctx: CoreCtx) -> None:
    with pytest.raises(commands.CommandError, match="Comando desconocido"):
        commands.send(ctx, "no-existe-este-comando")


def test_send_without_token_asks_for_reauth(ctx: CoreCtx) -> None:
    with (
        patch.object(commands.wake, "_bff_login", return_value=(None, None)),
        pytest.raises(commands.CommandError) as capt,
    ):
        commands.send(ctx, "bloquear")

    assert capt.value.reason == "reauth"


@pytest.mark.freeze_time(FROZEN)
def test_send_ok_y_envelope_firmado(ctx: CoreCtx, snapshot: SnapshotAssertion) -> None:
    """With the clock frozen, `seq` and the signature are reproducible → the whole envelope fits in
    a snapshot. It is the best safety net against an accidental format change."""
    ctx.state.taskid = "task-en-cache"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", return_value=_urlopen({"code": "000000"})) as urlopen,
    ):
        out = commands.send(ctx, "bloquear")

    assert "000000" in out

    req = urlopen.call_args[0][0]
    enviado = json.loads(req.data.decode())

    assert enviado["seq"] == f"{ctx.vin}-1768478400000"
    assert enviado["taskId"] == "task-en-cache"
    assert enviado["vin"] == ctx.vin
    assert enviado["clientType"] == "1"
    assert enviado == snapshot


@pytest.mark.freeze_time(FROZEN)
def test_send_applies_params_before_system_fields(ctx: CoreCtx) -> None:
    """`params` override the catalogue body, but clientType/seq/taskId/vin are always set
    by `send()`: a `params` cannot forge them."""
    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", return_value=_urlopen({"code": "000000"})) as urlopen,
    ):
        commands.send(ctx, "clima_on", params={"temperature": "22.0", "vin": "VIN-FALSO"})

    enviado = json.loads(urlopen.call_args[0][0].data.decode())
    assert enviado["temperature"] == "22.0"
    assert enviado["vin"] == ctx.vin


@pytest.mark.freeze_time(FROZEN)
def test_send_failure_code_raises_command_error(ctx: CoreCtx) -> None:
    """A known failure = command NOT executed → optimistic entities roll back their state instead
    of showing a false success."""
    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", return_value=_urlopen({"code": "A00084"})),
        pytest.raises(commands.CommandError) as capt,
    ):
        commands.send(ctx, "bloquear")

    assert capt.value.code == "A00084"
    assert capt.value.retryable is False


@pytest.mark.freeze_time(FROZEN)
def test_send_unknown_code_does_not_block(ctx: CoreCtx) -> None:
    """Deliberate caution: we do not invent a failure the backend has not declared."""
    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", return_value=_urlopen({"code": "A99999"})),
    ):
        out = commands.send(ctx, "bloquear")

    assert "A99999" in out


@pytest.mark.freeze_time(FROZEN)
def test_send_remints_the_taskid_and_retries_once(ctx: CoreCtx) -> None:
    """Without this retry the user would see an error for a merely expired taskId."""
    ctx.state.taskid = "task-caducado"
    ctx.state.taskid_ts = __import__("time").time()
    respuestas = [_urlopen({"code": "A00089"}), _urlopen({"code": "000000"})]

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch.object(taskid, "_mint_taskid", return_value="task-nuevo") as mint,
        patch("urllib.request.urlopen", side_effect=respuestas) as urlopen,
    ):
        out = commands.send(ctx, "bloquear")

    assert "000000" in out
    assert urlopen.call_count == 2
    mint.assert_called_once()
    # the second send already carries the re-minted taskId
    assert json.loads(urlopen.call_args[0][0].data.decode())["taskId"] == "task-nuevo"


@pytest.mark.freeze_time(FROZEN)
def test_send_does_not_retry_forever(ctx: CoreCtx) -> None:
    """Two rejections in a row → gives up with CommandError, not a loop."""
    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch.object(taskid, "_mint_taskid", return_value="task-nuevo"),
        patch("urllib.request.urlopen", return_value=_urlopen({"code": "A00089"})) as urlopen,
        pytest.raises(commands.CommandError),
    ):
        commands.send(ctx, "bloquear")

    assert urlopen.call_count == 2


@pytest.mark.freeze_time(FROZEN)
def test_send_reads_the_body_of_an_http_error(ctx: CoreCtx) -> None:
    """The backend sends the real `code` in 4xx responses too."""
    import urllib.error

    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()
    err = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)
    err.read = lambda: json.dumps({"code": "A00084"}).encode()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", side_effect=err),
        pytest.raises(commands.CommandError) as capt,
    ):
        commands.send(ctx, "bloquear")

    assert capt.value.code == "A00084"


@pytest.mark.freeze_time(FROZEN)
def test_send_network_error(ctx: CoreCtx) -> None:
    ctx.state.taskid = "T"
    ctx.state.taskid_ts = __import__("time").time()

    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch("urllib.request.urlopen", side_effect=OSError("sin ruta al host")),
        pytest.raises(commands.CommandError, match="Error de red"),
    ):
        commands.send(ctx, "bloquear")


# ───────────────────────── query_theft_switch ─────────────────────────


@pytest.mark.parametrize(
    ("respuesta", "esperado"),
    [
        ({"body": {"theftAlarmSwitch": 1}}, 1),
        ({"body": {"theftAlarmSwitch": "0"}}, 0),
        ({"body": {}}, None),
        ({"body": {"theftAlarmSwitch": "no-numero"}}, None),
        ({}, None),
        ("no es un dict", None),
    ],
)
def test_query_theft_switch(ctx: CoreCtx, respuesta, esperado) -> None:
    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch.object(commands.wake, "_signed_post", return_value=(200, respuesta)),
    ):
        assert commands.query_theft_switch(ctx) == esperado


def test_query_theft_switch_without_token(ctx: CoreCtx) -> None:
    with patch.object(commands.wake, "_bff_login", return_value=(None, None)):
        assert commands.query_theft_switch(ctx) is None


def test_query_theft_switch_does_not_raise(ctx: CoreCtx) -> None:
    """Called by the alarm switch's `async_added_to_hass`: it cannot bring the setup down."""
    with (
        patch.object(commands.wake, "_bff_login", return_value=("UT", "tu")),
        patch.object(commands.wake, "_signed_post", side_effect=OSError("red")),
    ):
        assert commands.query_theft_switch(ctx) is None
