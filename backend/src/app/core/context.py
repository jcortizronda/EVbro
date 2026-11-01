#!/usr/bin/env python3
"""`CoreCtx` — configuration and state of ONE vehicle, passed explicitly.

One `CoreCtx` per vehicle, created by the poller and passed as the first argument to
every function in `core/`. No mutable module globals, no `os.environ` on EVBro's
live path.

**Why the context also holds STATE and not just configuration.** The PIN lockout,
the taskId cache, the wake and probe cooldowns are all
*per vehicle*. If they lived in module globals, with two cars configured one car's
PIN errors would block the other's commands, and the taskId minted for one would be
used for the other. Binding them to the context solves this by construction.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import os
import threading

from .pin_lockout import PinLockout

HERE = os.path.dirname(os.path.abspath(__file__))

# REGION defaults. Ebro Auto by default (VERIFIED 2026-07-27).
#  - BFF (login/auth/vmc) lives on legend.ebroauto.com; telemetry/control on tspconsole.
#  - login tenant = "3000010" (numeric); the TSP telemetry x-TenantId = "euebro".
DEFAULT_BFF = "https://legend.ebroauto.com/api"
DEFAULT_TSP_HOST = "https://tspconsole-eu.ebroauto.com"
DEFAULT_CHANNEL_ID = "4"
DEFAULT_COUNTRY_ID = "1"
DEFAULT_TENANT_CODE = "3000010"          # login/BFF tenant (legend). TSP telemetry uses "euebro".
DEFAULT_TSP_TENANT = "euebro"            # x-TenantId for telemetry/control calls (tspconsole)



@dataclass
class _VehicleState:
    """Mutable state that belongs to ONE vehicle (not configuration).

    Anything in here that were a module global would turn, with two cars
    configured, into interference between different accounts."""

    # PIN lockout: threshold and window are per account (see pin_lockout.py)
    lockout: PinLockout = field(default_factory=PinLockout)
    # cached taskId: minting it costs a checkPassword round trip (the slow part of every
    # command), but it is bound to the PIN and the VIN → it cannot be shared between vehicles.
    taskid: str | None = None
    taskid_ts: float = 0.0
    # cooldown of the SMS wake (real rate limit on the Chery side) and of the realtime probe
    last_sms_ts: float = 0.0
    last_probe_ts: float = 0.0
    # "one at a time" per vehicle: two cars can be woken/probed in parallel
    wake_lock: threading.Lock = field(default_factory=threading.Lock)
    probe_lock: threading.Lock = field(default_factory=threading.Lock)
    # serialises the token refresh: Chery rotates the refresh_token on every use, two
    # parallel renewals on the SAME file would invalidate the session.
    token_lock: threading.Lock = field(default_factory=threading.Lock)
    # Result of the LAST attempted refresh (stable marker, see wake._refresh_token_detail)
    # + when it was attempted. Lets `session.check` tell «the server revoked the
    # token» from «the network failed»: without it, a network outage disguises itself as a dead
    # session and makes the user re-authenticate for no reason.
    refresh_reason: str = ""
    refresh_ts: float = 0.0
    # Fingerprint (sha256) of the refresh_token the server rejected EXPLICITLY, and
    # when. Used to avoid presenting an already rejected credential again: the token does
    # not become valid again on its own, so retrying it on every call is just noise
    # towards the Chery gateway. Reset only once the file holds a different token.
    refresh_burned: str = ""
    refresh_burned_ts: float = 0.0
    # OVERLAPPING taskId mints. Counted on ENTRY, outside the lockout
    # lock: inside it would always see 1, because that lock serialises. If on entry it is >1
    # there is a second thread waiting for the lock right now — the race condition seen
    # live that brings the account lockout closer. It is PER VEHICLE: as a process global, two
    # configured cars polluted each other's counter (and the tests had to reset it).
    mint_inflight: int = 0
    mint_inflight_lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class CoreCtx:
    """Everything `core/` needs to operate on a vehicle.

    Built once per vehicle (see `VehiclePoller._get_or_create_ctx`) and passed
    as the first argument to the `core/` functions.
    """

    # — account/vehicle identity (per account: never defaults) —
    vin: str = ""
    tuserid: str = ""
    pin: str = ""
    email: str = ""
    sign_key: str = ""

    # — per-vehicle paths in the data volume (data/<vin>/) —
    token_path: str = ""
    taskid_file: str = ""
    # source folder for the login subprocesses (default: this package)
    src_dir: str = HERE

    # — region parameters —
    tsp_host: str = DEFAULT_TSP_HOST
    bff: str = DEFAULT_BFF
    channel_id: str = DEFAULT_CHANNEL_ID
    country_id: str = DEFAULT_COUNTRY_ID
    tenant_code: str = DEFAULT_TENANT_CODE

    # — behaviour —
    # automatic taskId minting: without it, commands cannot go out (they need a
    # taskId validated by checkPassword). Can be disabled for diagnostics only.
    mint_taskid: bool = True
    taskid_ttl: int = 600      # reuse of the cached taskId, in seconds

    # — wake and probe pacing —
    # These lived as module constants read from `os.environ` AT IMPORT, in `wake.py` and
    # `probe.py`. That made them impossible to change at runtime and to test with
    # `monkeypatch.setenv` — there was a test documenting exactly that limitation instead of
    # checking a behaviour. Here they are vehicle configuration, like everything else.
    #
    # `smsAwaken` HAS a real rate limit on the Chery side: the cooldown is not a courtesy.
    wake_cooldown_s: int = 300      # minimum between two smsAwaken actually sent
    wake_poll_attempts: int = 12    # polling cycles after the wake
    wake_poll_every_s: int = 5      # seconds between one cycle and the next
    # How often a new attempt is allowed with a refresh_token the server already rejected:
    # rare enough not to hammer, frequent enough to recover on its own if that
    # rejection was a gateway hiccup.
    retry_refresh_after_s: int = 3600
    # The realtime probe is READ ONLY and the car stays online for a good while after
    # waking up, so frequent opportunistic reads come for free. Scheduled
    # probes use `force=True` and skip the cooldown anyway.
    probe_cooldown_s: int = 120
    # PRIVACY: the probe's raw log contains the VIN and coordinates → OPT-IN. Empty =
    # nothing is written. For manual diagnostics only.
    probe_log_path: str = ""
    # Diagnostic monitor (diag.py): optional diagnostic callback (EVBro does not use it for now).
    # `None` = dormant, zero cost. `core/` does not know the poller.
    diag_hook: object | None = None

    # — per-vehicle state —
    state: _VehicleState = field(default_factory=_VehicleState)

    def __post_init__(self) -> None:
        # Fallback paths for command-line/diagnostic use only: in
        # EVBro the poller always sets them (per VIN, in data/<vin>/).
        if not self.token_path:
            self.token_path = os.path.join(HERE, "token.json")
        if not self.taskid_file:
            self.taskid_file = os.path.join(HERE, "data", "taskid.txt")

    # ───────────────────────── convenience ─────────────────────────
    @property
    def lockout(self) -> PinLockout:
        return self.state.lockout

    def diag(self, tipo: str, **fields_) -> None:
        """Record a diagnostic event, if the monitor is on. It must NEVER
        alter the flow: the monitor observes, it does not take part."""
        hook = self.diag_hook
        if hook is None:
            return
        # a broken monitor must not break a command: it observes, it does not take part.
        with contextlib.suppress(Exception):
            hook(tipo, **fields_)

    def invalidate_taskid(self) -> None:
        """Drop the cached taskId (the car rejected it or the PIN changed)."""
        self.state.taskid = None
        self.state.taskid_ts = 0.0

    def reset_pin_lockout(self) -> None:
        """Reset the lockout and the taskId: used when the user reconfigures the PIN."""
        self.state.lockout.reset()
        self.invalidate_taskid()


def ctx_from_environ() -> CoreCtx:
    """Context built from `os.environ` — ONLY for command-line use.

    The `core/` modules can still be launched by hand for diagnostics (`python -m …`) and in
    that case the environment is the handiest way to pass the parameters. EVBro NEVER
    goes through here: the poller builds the context from the configuration and the
    database, which are the source of truth."""
    return CoreCtx(
        vin=os.environ.get("VIN", ""),
        tuserid=os.environ.get("TUSERID", ""),
        pin=os.environ.get("EBRO_PIN", ""),
        email=os.environ.get("EBRO_EMAIL", ""),
        sign_key=os.environ.get("EBRO_SIGN_KEY", ""),
        token_path=os.environ.get("EBRO_TOKEN_PATH", ""),
        taskid_file=os.environ.get("EBRO_TASKID_FILE", ""),
        src_dir=os.environ.get("EBRO_SRC_DIR", HERE),
        tsp_host=os.environ.get("TSP_HOST", DEFAULT_TSP_HOST),
        bff=os.environ.get("EBRO_BFF", DEFAULT_BFF),
        channel_id=os.environ.get("CHANNEL_ID", DEFAULT_CHANNEL_ID),
        country_id=os.environ.get("EBRO_COUNTRY_ID", DEFAULT_COUNTRY_ID),
        tenant_code=os.environ.get("EBRO_TENANT_CODE", DEFAULT_TENANT_CODE),
        mint_taskid=os.environ.get("EBRO_MINT_TASKID", "1") not in ("0", "", "false", "no"),
        wake_cooldown_s=int(os.environ.get("WAKE_COOLDOWN", "300")),
        wake_poll_attempts=int(os.environ.get("WAKE_POLL_N", "12")),
        wake_poll_every_s=int(os.environ.get("WAKE_POLL_EVERY", "5")),
        retry_refresh_after_s=int(os.environ.get("EBRO_RETRY_REFRESH", "3600")),
        probe_cooldown_s=int(os.environ.get("PROBE_COOLDOWN", "120")),
        probe_log_path=os.environ.get("EBRO_PROBE_LOG", ""),
    )
