"""The outcome of an order's confirmation is data, not the sentence shown (texts audit, 27-11-2026)."""
from app.vehicle.telemetry import command_outcome


def test_command_outcome_reads_the_push_not_the_text() -> None:
    assert command_outcome({"reason": ["door open"], "result": "1"}) == "failed"
    assert command_outcome({"result": "5"}) == "running"
    assert command_outcome({"result": "1"}) == "applied"
    assert command_outcome({"result": "9"}) == "unknown"
