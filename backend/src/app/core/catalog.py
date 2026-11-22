#!/usr/bin/env python3
"""The car's command catalogue: what can be asked of it and with which body.

Pure data — no network, no state, no decisions. Each entry says which endpoint the command
goes to and which fields of its own it carries; the common ones (`clientType`, `seq`,
`taskId`, `vin`, signature) are added by `commands.send()`.

They used to live inside `commands.py`, taking 150 of its 572 lines and pushing the sending
logic to the end of the file. Splitting them out shows the car's repertoire at a glance,
which is exactly what you look up when adding a new command.

⚠️ The bodies are rebuilt 1:1 from real captured envelopes. Changing one is not a matter of
style: it changes what is sent to the vehicle.
"""


# ───────────────────────── Command catalogue ─────────────────────────
# Each entry: key -> {endpoint, body (specific fixed fields), name, icon, group}
# The common fields (clientType/seq/taskId/vin/appId/sign) are added by send().
COMMANDS = [
    # — Climate —
    # climate ON/OFF: temperature and duration are PARAMETRIC (the interface passes them via
    # `params`); the body values are only the defaults when invoked without an override.
    ("clima_on",  {"endpoint": "airControl",
                   "body": {"airControlType": "1", "airType": "1", "temperature": "21.0", "times": "15"},
                   "name": "Clima encendido", "icon": "mdi:air-conditioner", "group": "Climatización"}),
    ("clima_off", {"endpoint": "airControl",
                   "body": {"airControlType": "0", "airType": "1", "temperature": "21.0", "times": "15"},
                   "name": "Clima apagado", "icon": "mdi:air-conditioner", "group": "Climatización"}),
    # Air purifier and windscreen demisting are FIELDS of the climate order, not orders of their own (captured from the
    # official app on 2026-11-21, S900; the car confirmed each one). Purifier: `airPurControlType` 1/0, reported back as
    # `airPurification`. Demisting (air blown at the glass): `frontDefrosting` 1/0, reported back as `fWinHeatingState`.
    # Both turn the climate on; the OFF ones keep `airControlType` "1", so only the purifier or the demisting stops and the
    # climate stays on. The app sends the temperature the car has set: the interface passes it via `params`.
    ("purificador_on",  {"endpoint": "airControl",
                   "body": {"airControlType": "1", "airPurControlType": "1", "airType": "1", "temperature": "21.0", "times": "15"},
                   "name": "Purificador de aire", "icon": "mdi:air-purifier", "group": "Climatización"}),
    ("purificador_off", {"endpoint": "airControl",
                   "body": {"airControlType": "1", "airPurControlType": "0", "airType": "1", "temperature": "21.0", "times": "15"},
                   "name": "Purificador de aire OFF", "icon": "mdi:air-purifier", "group": "Climatización"}),
    ("desempanar_on",  {"endpoint": "airControl",
                   "body": {"airControlType": "1", "airType": "1", "frontDefrosting": "1", "temperature": "21.0", "times": "15"},
                   "name": "Desempañar parabrisas", "icon": "mdi:car-defrost-front", "group": "Climatización"}),
    ("desempanar_off", {"endpoint": "airControl",
                   "body": {"airControlType": "1", "airType": "1", "frontDefrosting": "0", "temperature": "21.0", "times": "15"},
                   "name": "Desempañar parabrisas OFF", "icon": "mdi:car-defrost-front", "group": "Climatización"}),
    # HEATED windscreen (the resistive glass; «luneta térmica delantera» in the official app), reported back as
    # `frontWindshieldHeat`. Until 2026-11-21 it was called «Desempañar parabrisas» here, which is the order above.
    ("defrost_parabrisas", {"endpoint": "frontWindshieldControl",
                   "body": {"frontWindshieldHeat": "1", "times": "15"},
                   "name": "Parabrisas térmico", "icon": "mdi:car-defrost-front", "group": "Climatización"}),
    ("defrost_parabrisas_off", {"endpoint": "frontWindshieldControl",
                   "body": {"frontWindshieldHeat": "0"},
                   "name": "Parabrisas térmico OFF", "icon": "mdi:car-defrost-front", "group": "Climatización"}),
    ("defrost_luneta", {"endpoint": "backDefrostingControl",
                   "body": {"backDefrosting": "1", "times": "15"},
                   "name": "Desempañar luneta", "icon": "mdi:car-defrost-rear", "group": "Climatización"}),
    ("defrost_luneta_off", {"endpoint": "backDefrostingControl",
                   "body": {"backDefrosting": "0"},
                   "name": "Desempañar luneta OFF", "icon": "mdi:car-defrost-rear", "group": "Climatización"}),
    ("volante_caliente", {"endpoint": "steeringWheelControl",
                   "body": {"controlType": "1"},
                   "name": "Volante calefactado", "icon": "mdi:steering", "group": "Climatización"}),
    ("volante_caliente_off", {"endpoint": "steeringWheelControl",
                   "body": {"controlType": "0"},
                   "name": "Volante calefactado OFF", "icon": "mdi:steering", "group": "Climatización"}),
    ("asiento_conductor_caliente", {"endpoint": "seatControl",
                   "body": {"mSeatHeating": "3", "times": "15"},
                   "name": "Asiento conductor calefactado", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_conductor_caliente_off", {"endpoint": "seatControl",
                   "body": {"mSeatHeating": "0"},
                   "name": "Asiento conductor calefactado OFF", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_conductor_ventilacion", {"endpoint": "seatControl",
                   "body": {"mSeatAiry": "3", "times": "15"},
                   "name": "Asiento conductor ventilado", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_conductor_ventilacion_off", {"endpoint": "seatControl",
                   "body": {"mSeatAiry": "0"},
                   "name": "Asiento conductor ventilado OFF", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    # Passenger and rear seats — same single `seatControl` endpoint, parameters
    # confirmed by the CVSeatControlReqBean bean (p=passenger, bl=rear left, br=rear right).
    # Rear centre: the bean has NO dedicated parameter → no command.
    ("asiento_pasajero_caliente", {"endpoint": "seatControl",
                   "body": {"pSeatHeating": "3", "times": "15"},
                   "name": "Asiento pasajero calefactado", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_pasajero_caliente_off", {"endpoint": "seatControl",
                   "body": {"pSeatHeating": "0"},
                   "name": "Asiento pasajero calefactado OFF", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_pasajero_ventilacion", {"endpoint": "seatControl",
                   "body": {"pSeatAiry": "3", "times": "15"},
                   "name": "Asiento pasajero ventilado", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_pasajero_ventilacion_off", {"endpoint": "seatControl",
                   "body": {"pSeatAiry": "0"},
                   "name": "Asiento pasajero ventilado OFF", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_tras_izq_caliente", {"endpoint": "seatControl",
                   "body": {"blSeatHeating": "3", "times": "15"},
                   "name": "Asiento tras. izq. calefactado", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_tras_izq_caliente_off", {"endpoint": "seatControl",
                   "body": {"blSeatHeating": "0"},
                   "name": "Asiento tras. izq. calefactado OFF", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_tras_izq_ventilacion", {"endpoint": "seatControl",
                   "body": {"blSeatAiry": "3", "times": "15"},
                   "name": "Asiento tras. izq. ventilado", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_tras_izq_ventilacion_off", {"endpoint": "seatControl",
                   "body": {"blSeatAiry": "0"},
                   "name": "Asiento tras. izq. ventilado OFF", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_tras_der_caliente", {"endpoint": "seatControl",
                   "body": {"brSeatHeating": "3", "times": "15"},
                   "name": "Asiento tras. der. calefactado", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_tras_der_caliente_off", {"endpoint": "seatControl",
                   "body": {"brSeatHeating": "0"},
                   "name": "Asiento tras. der. calefactado OFF", "icon": "mdi:car-seat-heater", "group": "Climatización"}),
    ("asiento_tras_der_ventilacion", {"endpoint": "seatControl",
                   "body": {"brSeatAiry": "3", "times": "15"},
                   "name": "Asiento tras. der. ventilado", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),
    ("asiento_tras_der_ventilacion_off", {"endpoint": "seatControl",
                   "body": {"brSeatAiry": "0"},
                   "name": "Asiento tras. der. ventilado OFF", "icon": "mdi:car-seat-cooler", "group": "Climatización"}),

    # Quick heating («Calefacción rápida» in the official app): ONE order that turns on the climate at 30 °C, the four seats
    # at level 3, the steering wheel and the heated windscreen, for 15 minutes; the OFF one turns all of it off, climate
    # included. Bodies 1:1 from the official app (2026-11-21, S900: accepted and seen on the car). It had been REMOVED on
    # 2026-07-31 because the car answered A00084 «comando no permitido»: the body sent then was not this one.
    # NB: note `duration` here, where airControl says `times`.
    ("calefaccion_rapida", {"endpoint": "heatingControl",
                   "body": {"airControlType": "1", "airType": "1", "temperature": "30", "duration": "15",
                            "mSeatHeating": "3", "pSeatHeating": "3", "blSeatHeating": "3", "brSeatHeating": "3",
                            "steerWheelHeatSwitch": "1", "frontWindshieldHeat": "1"},
                   "name": "Calefacción rápida", "icon": "mdi:fire", "group": "Climatización"}),
    ("calefaccion_rapida_off", {"endpoint": "heatingControl",
                   "body": {"airControlType": "0", "airType": "1", "temperature": "30", "duration": "15",
                            "mSeatHeating": "0", "pSeatHeating": "0", "blSeatHeating": "0", "brSeatHeating": "0",
                            "steerWheelHeatSwitch": "0", "frontWindshieldHeat": "0"},
                   "name": "Calefacción rápida OFF", "icon": "mdi:fire", "group": "Climatización"}),
    # The cooling macro (coolingControl, «Enfriar todo») stays out: the car answered A00084 on 2026-07-31 and its body has
    # not been captured from the official app.

    # — Doors / locks —
    ("desbloquear",   {"endpoint": "lockControl", "body": {"lockType": "1"},
                   "name": "Desbloquear puertas", "icon": "mdi:lock-open-variant", "group": "Accesos"}),
    ("bloquear",    {"endpoint": "lockControl", "body": {"lockType": "0"},
                   "name": "Bloquear puertas", "icon": "mdi:lock", "group": "Accesos"}),
    ("maletero_abrir",  {"endpoint": "powerLiftgateControl", "body": {"controlType": "1"},
                   "name": "Abrir maletero", "icon": "mdi:car-back", "group": "Accesos"}),
    ("maletero_cerrar", {"endpoint": "powerLiftgateControl", "body": {"controlType": "0"},
                   "name": "Cerrar maletero", "icon": "mdi:car-back", "group": "Accesos"}),

    # — Windows / roof —
    ("ventanillas_abrir",   {"endpoint": "windowControl", "body": {"controlType": "1"},
                   "name": "Abrir ventanillas", "icon": "mdi:car-door", "group": "Ventanillas y techo"}),
    ("ventanillas_cerrar", {"endpoint": "windowControl", "body": {"controlType": "0"},
                   "name": "Cerrar ventanillas", "icon": "mdi:car-door", "group": "Ventanillas y techo"}),
    ("ventilar_ventanillas", {"endpoint": "windowControl", "body": {"controlType": "2"},
                   "name": "Ventilar ventanillas", "icon": "mdi:weather-windy", "group": "Ventanillas y techo"}),
    ("techo_abrir",   {"endpoint": "skylightControl", "body": {"controlType": "1", "skylightType": "1"},
                   "name": "Abrir techo", "icon": "mdi:car-select", "group": "Ventanillas y techo"}),
    ("techo_cerrar", {"endpoint": "skylightControl", "body": {"controlType": "0", "skylightType": "1"},
                   "name": "Cerrar techo", "icon": "mdi:car-select", "group": "Ventanillas y techo"}),
    # controlType "2" = tilt («Ventilar» in the official app, 2026-11-21): the car then reports `sunroofState` 2 (0 closed, 3 open)
    ("techo_ventilar", {"endpoint": "skylightControl", "body": {"controlType": "2", "skylightType": "1"},
                   "name": "Ventilar techo", "icon": "mdi:weather-windy", "group": "Ventanillas y techo"}),

    # — EV charging —
    # IMMEDIATE charge start/stop (endpoint chargeStartStopControl, bean CVChargeStartStopBean
    # → only `controlType`; 1=start, 0=stop, same convention as every *Control).
    ("carga_iniciar", {"endpoint": "chargeStartStopControl", "body": {"controlType": "1"},
                   "name": "Iniciar carga", "icon": "mdi:battery-charging", "group": "Carga"}),
    ("carga_detener", {"endpoint": "chargeStartStopControl", "body": {"controlType": "0"},
                   "name": "Detener carga", "icon": "mdi:battery-off", "group": "Carga"}),
    # SCHEDULED charging (chargeAppointControl) — body with a nested ARRAY `chargeAppointPlans`
    # (tsp_sign handles the nested signature, verified on 4/4 real envelopes). mainSwitch =
    # master switch; the plan (time/duration/days) is passed by the interface via `params`.
    # cycleData [1..7] = days; startTime/timeConsuming in MINUTES; switchStatus = plan active.
    ("carga_prog_on", {"endpoint": "chargeAppointControl",
                   "body": {"mainSwitch": 1, "chargeAppointPlans": [
                       {"cycleData": [1, 2, 3, 4, 5, 6, 7], "startTime": 480,
                        "switchStatus": 1, "timeConsuming": 360}]},
                   "name": "Carga programada ON", "icon": "mdi:calendar-clock", "group": "Carga"}),
    ("carga_prog_off", {"endpoint": "chargeAppointControl",
                   "body": {"mainSwitch": 0, "chargeAppointPlans": [
                       {"cycleData": [1, 2, 3, 4, 5, 6, 7], "startTime": 480,
                        "switchStatus": 0, "timeConsuming": 360}]},
                   "name": "Carga programada OFF", "icon": "mdi:calendar-remove", "group": "Carga"}),

    # SCHEDULED DEPARTURE («Reserva tu viaje» in the official app; endpoint appointmentTravel, captured 2026-11-21). The app's
    # own text: «El aislamiento de la batería comienza 1 hora y 30 minutos antes de su salida». Body with a nested ARRAY like
    # the scheduled charge (signature verified on 2/2 real envelopes): `mainSwitcher` = master switch; one entry per departure
    # with `switcher`, `travelTime` in MINUTES of the day and `travelWeekday` [1..7]. Read back with travelQuery
    # (`appointmentTravelSetVOS`, where the days are called `travelDate`). The plan is passed by the interface via `params`.
    ("salida_prog_on", {"endpoint": "appointmentTravel",
                   "body": {"mainSwitcher": 1, "appointmentTravelAOS": [
                       {"switcher": 1, "travelTime": 420, "travelWeekday": [1, 2, 3, 4, 5, 6, 7]}]},
                   "name": "Salida programada ON", "icon": "mdi:calendar-clock", "group": "Salida programada"}),
    ("salida_prog_off", {"endpoint": "appointmentTravel",
                   "body": {"mainSwitcher": 0, "appointmentTravelAOS": [
                       {"switcher": 0, "travelTime": 420, "travelWeekday": [1, 2, 3, 4, 5, 6, 7]}]},
                   "name": "Salida programada OFF", "icon": "mdi:calendar-remove", "group": "Salida programada"}),

    # — Other —
    ("encontrar_coche_luces", {"endpoint": "findCar", "body": {},
                   "name": "Encontrar coche (luces)", "icon": "mdi:car-search", "group": "Otros"}),
    # NB: remoteStart (remote engine start) REMOVED: tested live (2026-06-21) → the car
    # answers A00084 "No vehicle control command permission" (permission denied for this
    # vehicle). No point exposing a button that always fails. The CVRemoteStartReqBean bean (no
    # fields) stays documented in case the permission changes in the future.
    # GPS position request: does NOT actuate anything; the car answers with an MQTT push serviceType
    # 1301 (lat/lon) that this car in practice never sends (no 1301 in a week of logging, see the integration's ARCHITECTURE.md): the new fix is then read with queryVehicleLocation.
    ("localizar_coche_gps", {"endpoint": "vehicleLocation", "body": {},
                   "name": "Localizar coche (GPS)", "icon": "mdi:crosshairs-gps", "group": "Otros"}),

    # — Security — Anti-theft alarm (theftAlarm). Alerts+siren on unauthorised movement,
    # forced doors, broken glass (the app's official description). NB: lives under /act (NOT
    # /asc/vehicleControl) → uses the `path` key instead of `endpoint`. Body = theftAlarmSwitch
    # 0/1; send() adds clientType/seq/vin and the minted taskId (the backend requires it: A00643
    # without it). State readable via query_theft_switch() (/act/theftAlarm/querySwitch).
    ("alarma_on",  {"path": "/act/theftAlarm/setSwitch", "body": {"theftAlarmSwitch": "1"},
                   "name": "Alarma activada", "icon": "mdi:shield-car", "group": "Seguridad"}),
    ("alarma_off", {"path": "/act/theftAlarm/setSwitch", "body": {"theftAlarmSwitch": "0"},
                   "name": "Alarma desactivada", "icon": "mdi:shield-off-outline", "group": "Seguridad"}),
]
CMD_MAP = dict(COMMANDS)
