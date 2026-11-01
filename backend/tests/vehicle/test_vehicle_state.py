"""Tests for `vehicle_state.py` — the car's live state and its lock.

Three threads touch this data: paho's (MQTT messages), the executor (probe and commands) and
Home Assistant's event loop (entities reading). They used to be four private attributes of
the coordinator and every access repeated the `with` by hand; forgetting it once gives a
half-written read, which is the kind of bug that does not show up in tests but at night in
the user's car.

What is pinned down here is that **everything that comes out is a copy** and that the wake-up
edge is resolved inside the same lock as the write.
"""

from __future__ import annotations

import threading

from app.vehicle.state import VehicleState

VENTANA = 300.0


def test_returned_fields_are_a_copy() -> None:
    """If the live dict were returned, the paho thread could be mutating it while Home
    Assistant serialises it for the diagnostics report."""
    state = VehicleState(VENTANA)
    state.record_message({"doorLock": "0"}, now=1_000.0)

    copia = state.fields()
    copia["doorLock"] = "MANIPULADO"

    assert state.field("doorLock") == "0"


def test_returned_position_is_a_copy() -> None:
    state = VehicleState(VENTANA)
    state.set_position({"lat": "40.4", "lon": "-3.7"})

    copia = state.position
    copia["lat"] = "0"

    assert state.position["lat"] == "40.4"


def test_fields_accumulate_across_messages() -> None:
    """The car sends partial frames: a new message must not erase what is already known."""
    state = VehicleState(VENTANA)
    state.record_message({"doorLock": "0", "hood": "0"}, now=1_000.0)

    fields, _ = state.record_message({"hood": "1"}, now=1_001.0)

    assert fields == {"doorLock": "0", "hood": "1"}


def test_merge_position_keeps_the_fields_the_probe_does_not_bring() -> None:
    """The realtime probe may bring only part of the geolocation; losing the rest would leave
    the device_tracker worse than it was."""
    state = VehicleState(VENTANA)
    state.set_position({"lat": "40.4", "lon": "-3.7", "altitude": "600"})

    fundida = state.merge_position({"lat": "41.0", "lon": "-3.0"})

    assert fundida == {"lat": "41.0", "lon": "-3.0", "altitude": "600"}


def test_set_position_replaces_instead_of_merging() -> None:
    """The 1301 push carries the full position: merging would leave fields from an earlier fix."""
    state = VehicleState(VENTANA)
    state.set_position({"lat": "40.4", "lon": "-3.7", "altitude": "600"})

    nueva = state.set_position({"lat": "41.0", "lon": "-3.0"})

    assert nueva == {"lat": "41.0", "lon": "-3.0"}


def test_awake_is_measured_by_time_since_the_last_message() -> None:
    """It is not a flag: a flag has to be remembered to be cleared, and when that was forgotten
    the «Despertar coche» button answered "already awake" and sent nothing for days."""
    state = VehicleState(VENTANA)

    assert state.is_awake(now=1_000.0) is False   # has never spoken

    state.record_message({"doorLock": "0"}, now=1_000.0)

    assert state.is_awake(now=1_100.0) is True
    assert state.is_awake(now=1_000.0 + VENTANA + 1) is False


def test_the_wake_up_edge_is_resolved_inside_the_lock() -> None:
    """`record_message` returns whether the car was awake BEFORE this message.

    Reading it separately would open a window in which another message would already have moved
    the timestamp and the edge — which is what triggers the position probe — would be lost."""
    state = VehicleState(VENTANA)

    _, primero = state.record_message({"a": "1"}, now=1_000.0)
    _, segundo = state.record_message({"a": "1"}, now=1_001.0)
    _, tras_dormirse = state.record_message({"a": "1"}, now=1_001.0 + VENTANA + 1)

    assert primero is False        # asleep → this is the edge
    assert segundo is True         # still awake
    assert tras_dormirse is False  # fell asleep in between → edge again


def test_concurrent_writes_do_not_lose_fields() -> None:
    """Twenty threads writing at once: without the lock, `dict.update` on the same dict can
    lose writes. With it, all 200 fields must be there."""
    state = VehicleState(VENTANA)
    hilos = [
        threading.Thread(
            target=lambda n=n: [
                state.record_message({f"campo_{n}_{i}": str(i)}, now=1_000.0 + i)
                for i in range(10)
            ]
        )
        for n in range(20)
    ]

    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len(state.fields()) == 200


def test_reading_while_writing_never_gives_a_partial_read() -> None:
    """Reading returns a consistent copy, never a dict in the middle of an `update`."""
    state = VehicleState(VENTANA)
    parar = threading.Event()
    incoherencias: list[int] = []

    def escribir() -> None:
        i = 0
        while not parar.is_set():
            state.record_message({f"k{i}": "1", f"k{i}_par": "1"}, now=1_000.0)
            i += 1

    def leer() -> None:
        for _ in range(500):
            snapshot = state.fields()
            # each write adds TWO keys; a consistent copy never has an odd count
            if len(snapshot) % 2:
                incoherencias.append(len(snapshot))

    escritor = threading.Thread(target=escribir)
    escritor.start()
    leer()
    parar.set()
    escritor.join()

    assert incoherencias == []
