"""Tests for `core/context.py` — a vehicle's `CoreCtx`.

⚠️ `CoreCtx`'s default paths point INSIDE the installed package
(`core/token.json`, `core/data/taskid.txt`). Any test that writes must pass
`tmp_path` explicitly; here only the path computation is checked, without touching disk.
"""

from __future__ import annotations

import os
from unittest.mock import Mock

from app.core.context import (
    DEFAULT_BFF,
    DEFAULT_CHANNEL_ID,
    DEFAULT_TSP_HOST,
    HERE,
    CoreCtx,
)


def test_default_paths_inside_the_package() -> None:
    """Only for command-line use: in HA the coordinator always sets them."""
    ctx = CoreCtx(vin="V")

    assert ctx.token_path == os.path.join(HERE, "token.json")
    assert ctx.taskid_file == os.path.join(HERE, "data", "taskid.txt")


def test_explicit_paths_win(tmp_path) -> None:
    ctx = CoreCtx(
        vin="V",
        token_path=str(tmp_path / "token.json"),
        taskid_file=str(tmp_path / "taskid.txt"),
    )

    assert ctx.token_path == str(tmp_path / "token.json")
    assert ctx.taskid_file == str(tmp_path / "taskid.txt")


def test_region_defaults() -> None:
    ctx = CoreCtx()
    assert ctx.bff == DEFAULT_BFF
    assert ctx.tsp_host == DEFAULT_TSP_HOST
    assert ctx.channel_id == DEFAULT_CHANNEL_ID
    assert ctx.mint_taskid is True


def test_each_context_has_its_own_state() -> None:
    """The module's raison d'être: with two cars configured, one car's PIN errors must not
    block the other's commands, nor its taskId end up on the wrong vehicle."""
    a = CoreCtx(vin="VIN_A")
    b = CoreCtx(vin="VIN_B")

    assert a.state is not b.state
    assert a.lockout is not b.lockout
    assert a.state.wake_lock is not b.state.wake_lock

    a.state.taskid = "task-a"
    assert b.state.taskid is None


def test_lockout_is_the_state_one() -> None:
    ctx = CoreCtx()
    assert ctx.lockout is ctx.state.lockout


def test_invalidate_taskid() -> None:
    ctx = CoreCtx()
    ctx.state.taskid = "task-123"
    ctx.state.taskid_ts = 1_700_000_000.0

    ctx.invalidate_taskid()

    assert ctx.state.taskid is None
    assert ctx.state.taskid_ts == 0.0


def test_reset_pin_lockout_clears_counter_and_taskid() -> None:
    """The taskId is bound to the PIN: changing it without dropping it would leave a useless one cached."""
    ctx = CoreCtx()
    with ctx.lockout.attempt() as attempt:
        attempt.record_failure()
    ctx.state.taskid = "task-viejo"

    ctx.reset_pin_lockout()

    assert ctx.lockout.failed_attempts == 0
    assert ctx.state.taskid is None


def test_diag_without_hook_does_nothing() -> None:
    """With the monitor dormant the cost must be a mere `is None`."""
    CoreCtx().diag("command", key="bloquear")  # must not raise


def test_diag_calls_the_hook() -> None:
    hook = Mock()
    ctx = CoreCtx(diag_hook=hook)

    ctx.diag("command", key="bloquear", ok=True)

    hook.assert_called_once_with("command", key="bloquear", ok=True)


def test_diag_swallows_a_broken_hook() -> None:
    """The monitor observes, it does not take part: if it blows up it cannot bring a command down."""
    ctx = CoreCtx(diag_hook=Mock(side_effect=RuntimeError("monitor roto")))

    ctx.diag("command", key="bloquear")  # must not raise


def test_ctx_from_environ_reads_the_environment(monkeypatch) -> None:
    """Command-line-only path: HA never goes through here (the PIN must never
    end up in the Home Assistant process environment)."""
    from app.core.context import ctx_from_environ

    monkeypatch.setenv("VIN", "VIN_CLI")
    monkeypatch.setenv("EBRO_PIN", "9999")
    monkeypatch.setenv("EBRO_MINT_TASKID", "0")

    ctx = ctx_from_environ()

    assert ctx.vin == "VIN_CLI"
    assert ctx.pin == "9999"
    assert ctx.mint_taskid is False
