"""Constantes del canal del coche, compartidas con la integración de Home Assistant.

Son del protocolo de Chery, no de EVBro: si cambian allí, cámbialas aquí también.
"""

# Los 4 archivos esperados en la carpeta de certificados.
CERT_FILES = ("ca.pem", "client.pem", "client.key", "eu_prd_cheryinternational.cer")

# Permisos en disco: son credenciales, solo su propietario.
CERT_DIR_MODE = 0o700
CERT_FILE_MODE = 0o600

# Semilla de app compartida (no es un secreto del usuario) para derivar la contraseña del broker.
CAR_SEED = "fa89db3abe8045919d70c6ed3cc65bc5"

# `serviceType` de los mensajes del coche. Se discrimina por el TIPO, no por los campos presentes.
SERVICE_TYPE_TELEMETRY = "5A02"   # estado del vehículo (puertas, clima, cierre, cable…)
SERVICE_TYPE_POSITION = "1301"    # reporte de posición GPS (este coche no lo envía nunca)

# Latido MQTT y reconexión (el backoff NO es el de paho por defecto: 1 s fijo es justo el patrón
# que los gateways sancionan).
MQTT_KEEPALIVE_S = 60
MQTT_RECONNECT_MIN_S = 1
MQTT_RECONNECT_MAX_S = 120

# Cuánto tiempo sin recibir nada se sigue considerando al coche «despierto».
DEFAULT_AWAKE_WINDOW = 300

# Códigos `result` de los push de CONFIRMACIÓN de comando (110x/1105/1135…).
CMD_RESULT_ASYNC_RUNNING = "5"       # operación asíncrona aún en curso (siempre con hasAsy=1)
CMD_RESULT_APPLIED = ("1", "2")      # ejecutado / aplicado

# ≥ MOVE_BURST_COUNT mensajes en los últimos MOVE_BURST_WINDOW_S segundos = en movimiento.
MOVE_BURST_COUNT = 5
MOVE_BURST_WINDOW_S = 30

# Tope de los textos de estado que se muestran al usuario.
MAX_STATUS_LEN = 250

# ───────────────────────── Carga programada (integración de Home Assistant) ─────────────────────────
# Este coche no acepta `chargeStartStopControl` (responde A00084): la única forma de actuar sobre la carga es
# la programación del coche (`chargeAppointControl`). Ver `vehicle/charging.py`.
MINUTES_PER_DAY = 24 * 60
DEFAULT_CHARGE_START_MIN = 8 * 60      # 08:00
DEFAULT_CHARGE_DURATION_MIN = 6 * 60   # 6 h
CHARGE_MIN_DURATION_MIN = 60           # el coche rechaza menos de 1 h (code 89)
CHARGE_STOP_DURATION_MIN = 60          # duración de la ventana de «pausa»
CHARGE_STOP_START_BACK_MIN = 90        # startTime = ahora − 1:30 → ventana [ahora−90, ahora−30], ya terminada
