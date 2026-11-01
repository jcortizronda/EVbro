"""Car channel constants, shared with the Home Assistant integration.

They belong to Chery's protocol, not to EVBro: if they change there, change them here too.
"""

# The 4 files expected in the certificates folder.
CERT_FILES = ("ca.pem", "client.pem", "client.key", "eu_prd_cheryinternational.cer")

# On-disk permissions: they are credentials, owner only.
CERT_DIR_MODE = 0o700
CERT_FILE_MODE = 0o600

# Shared app seed (not a user secret) used to derive the broker password.
CAR_SEED = "fa89db3abe8045919d70c6ed3cc65bc5"

# `serviceType` of the car's messages. Messages are told apart by TYPE, not by the fields present.
SERVICE_TYPE_TELEMETRY = "5A02"   # vehicle state (doors, climate, lock, cable…)
SERVICE_TYPE_POSITION = "1301"    # GPS position report (this car never sends it)

# MQTT keepalive and reconnection (the backoff is NOT paho's default: a fixed 1 s is exactly the
# pattern that gateways penalise).
MQTT_KEEPALIVE_S = 60
MQTT_RECONNECT_MIN_S = 1
MQTT_RECONNECT_MAX_S = 120

# How long without receiving anything the car is still considered "awake".
DEFAULT_AWAKE_WINDOW = 300

# `result` codes of command CONFIRMATION pushes (110x/1105/1135…).
CMD_RESULT_ASYNC_RUNNING = "5"       # asynchronous operation still running (always with hasAsy=1)
CMD_RESULT_APPLIED = ("1", "2")      # executed / applied

# ≥ MOVE_BURST_COUNT messages in the last MOVE_BURST_WINDOW_S seconds = moving.
MOVE_BURST_COUNT = 5
MOVE_BURST_WINDOW_S = 30

# Cap on the status texts shown to the user.
MAX_STATUS_LEN = 250

# ───────────────────────── Scheduled charging (Home Assistant integration) ─────────────────────────
# This car does not accept `chargeStartStopControl` (it answers A00084): the only way to act on charging is
# the car's schedule (`chargeAppointControl`). See `vehicle/charging.py`.
MINUTES_PER_DAY = 24 * 60
DEFAULT_CHARGE_START_MIN = 8 * 60      # 08:00
DEFAULT_CHARGE_DURATION_MIN = 6 * 60   # 6 h
CHARGE_MIN_DURATION_MIN = 60           # the car rejects less than 1 h (code 89)
CHARGE_STOP_DURATION_MIN = 60          # length of the "pause" window
CHARGE_STOP_START_BACK_MIN = 90        # startTime = now − 1:30 → window [now−90, now−30], already over
