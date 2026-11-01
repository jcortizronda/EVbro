"""Tests for `core/codes.py` — the single code → readable sentence map."""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from app.core import codes


def test_mapa_completo(snapshot: SnapshotAssertion) -> None:
    """Snapshot of the whole map.

    They are the only diagnostic texts the user sees: pinning them makes any
    rewording go through an explicit diff review.
    """
    assert snapshot == codes.CODE_MEANING


@pytest.mark.parametrize(
    ("code", "esperado"),
    [
        ("000000", "ok ✅"),
        ("A00082", codes.CODE_MEANING["A00082"]),
        # non-string: normalised before the lookup
        (0, "código 0"),
        # unknown → generic with the raw code, never a made-up text
        ("A99999", "código A99999"),
        (None, "sin código"),
    ],
)
def test_meaning(code, esperado) -> None:
    assert codes.meaning(code) == esperado


@pytest.mark.parametrize("code", [None, "A99999"])
def test_meaning_honours_the_default(code) -> None:
    """The `default` wins both for `None` and for an unknown code."""
    assert codes.meaning(code, default="mi texto") == "mi texto"


def test_meaning_ignores_the_default_for_a_known_code() -> None:
    assert codes.meaning("000000", default="mi texto") == "ok ✅"


def test_routed_codes_have_text() -> None:
    """Every code with a decision attached in `routing` must also have a sentence.

    Otherwise the user would see «código A00546» instead of an explanation — the `codes` module
    exists precisely for that.
    """
    from app.core import routing

    sin_texto = set(routing._TABLE) - set(codes.CODE_MEANING)
    # The "configuration notice only" codes need no sentence of their own: the backend's
    # detail already arrives in the message. The rest do.
    assert sin_texto == {"A00374", "A00554", "A00604", "A00643", "A00757"}
