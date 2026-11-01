"""Tests for `core/routing.py` — the single routing table for backend codes."""

from __future__ import annotations

from dataclasses import asdict

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import routing


def test_matriz_completa(snapshot: SnapshotAssertion) -> None:
    """Snapshot of the WHOLE code × context matrix.

    `classify()` is deterministic and clock-free, so the whole matrix fits in a snapshot.
    It is the module's most valuable test: any change to `_TABLE`, the overrides or
    the defaults shows up as a readable diff instead of as a wrong remedy in production.
    """
    codigos = sorted(set(routing._TABLE) | set(routing._OVERRIDE_CHECKPASSWORD))
    # codes the table does NOT know: the default branch, which is asymmetric per context
    codigos += ["A99999", "", "123"]

    matriz = {}
    for code in codigos:
        for context in (routing.CONTEXT_CHECKPASSWORD, routing.CONTEXT_COMMAND):
            c = routing.classify(code, context)
            matriz[f"{code or '<vacio>'}|{context}"] = {
                **asdict(c),
                "accion": c.action,
                "exito": c.success,
                "fallo": c.failed,
            }

    assert matriz == snapshot


def test_default_is_asymmetric_per_context() -> None:
    """An unknown code is PIN+blocking in checkPassword and non-blocking on a command.

    The asymmetry is deliberate (see the module docstring) and easy to break without
    noticing when touching the defaults, so it is asserted explicitly on top of the snapshot.
    """
    cp = routing.classify("A99999", routing.CONTEXT_CHECKPASSWORD)
    assert cp.reason == routing.REASON_PIN
    assert cp.counts_for_lockout is True
    assert cp.action == routing.ACTION_REPAIR_PIN

    cmd = routing.classify("A99999", routing.CONTEXT_COMMAND)
    assert cmd.reason is routing.REASON_NONE
    assert cmd.counts_for_lockout is False
    assert cmd.outcome == "unknown"
    assert cmd.action == routing.ACTION_NOTICE


def test_a00567_changes_meaning_by_context() -> None:
    """The case that led to passing the context to `classify()`.

    In checkPassword `A00567` is an incomplete request (the PIN may be fine); in
    response to a command it is an expired taskId that must be re-minted.
    """
    cp = routing.classify("A00567", routing.CONTEXT_CHECKPASSWORD)
    assert cp.reason == routing.REASON_CONFIG
    assert cp.regenerate_taskid is False
    assert cp.counts_for_lockout is False

    cmd = routing.classify("A00567", routing.CONTEXT_COMMAND)
    assert cmd.regenerate_taskid is True
    assert cmd.outcome == "ko"


@pytest.mark.parametrize(
    ("code", "esperado"),
    [
        (None, None),
        ("", None),
        (123, "123"),
        ("A00082", "A00082"),
    ],
)
def test_code_normalised(code, esperado) -> None:
    """`code` may arrive as None or a non-string (unreadable backend response)."""
    assert routing.classify(code, routing.CONTEXT_COMMAND).code == esperado


def test_only_pin_codes_count_towards_the_lockout() -> None:
    """Counting permission/config errors would bring the real account lockout closer for a cause
    that has nothing to do with the PIN — it is bug P1-2 documented by the module.

    The list is EXHAUSTIVE on purpose: adding a code here means deciding it may spend
    attempts of the user's account, and that deserves to go through this test rather than slip in via the
    default rule. A07908/A07909 were added on 2026-10-01 after being confirmed in the field as a genuinely
    wrong PIN."""
    bloqueantes = {
        code
        for code in set(routing._TABLE) | set(routing._OVERRIDE_CHECKPASSWORD)
        if routing.classify(code, routing.CONTEXT_CHECKPASSWORD).counts_for_lockout
    }
    assert bloqueantes == {"A00285", "A00282", "A07908", "A07909"}


@pytest.mark.parametrize(
    ("reason", "action"),
    [
        (routing.REASON_REAUTH, routing.ACTION_REAUTH),
        (routing.REASON_PIN, routing.ACTION_REPAIR_PIN),
        (routing.REASON_CONFIG, routing.ACTION_NOTICE),
        (routing.REASON_NONE, routing.ACTION_NOTICE),
        ("un-reason-que-no-existe", routing.ACTION_NOTICE),
    ],
)
def test_action_for_reason(reason, action) -> None:
    """An unknown `reason` degrades to a notice: never to reauth or to an unactionable Repair."""
    assert routing.action_for_reason(reason) == action


def test_derived_views_match_the_table() -> None:
    """The public frozensets are derived from `_TABLE`; they used to be parallel lists that could
    silently drift."""
    assert {"000000", "A00079"} == routing.SUCCESS_CODES
    assert {"A00082"} == routing.RETRYABLE_CODES
    assert {"A00089", "A00546", "A00567"} == routing.TASKID_INVALID
    assert routing.SUCCESS_CODES.isdisjoint(routing.FAILURE_CODES)


def test_checkpassword_a079_family_is_the_pin() -> None:
    """Confirmed in the field on 2026-10-01: with the correct PIN the same command went through first
    time, on the same account and after a whole morning returning A07908/A07909.

    For a few hours they were classified the other way round, on reasoning that looked solid
    —the app accepted the PIN that failed for us— and a false premise: the app was
    accepting the NEW PIN of another account.

    This test exists because being wrong here is not paid for by the software, it is paid for by the
    user's account: without `counts_for_lockout` the lockout stops braking a genuinely wrong PIN and
    Chery ends up locking the account."""
    for code in ("A07908", "A07909"):
        resultado = routing.classify(code, routing.CONTEXT_CHECKPASSWORD)

        assert resultado.counts_for_lockout is True, code
        assert resultado.reason == routing.REASON_PIN, code


def test_vehicle_permissions_do_not_count_towards_the_lockout() -> None:
    """The other half, which still stands: a rejection due to permissions or a malformed request must
    not bring the account lockout closer for a cause unrelated to the PIN."""
    for code in ("A00374", "A00554", "A00757"):
        resultado = routing.classify(code, routing.CONTEXT_CHECKPASSWORD)

        assert resultado.counts_for_lockout is False, code
        assert resultado.reason == routing.REASON_CONFIG, code
