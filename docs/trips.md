# Detección de viajes

Código: `backend/src/app/services/trip_detector.py`. El detector se ejecuta con **cada lectura** que guarda el poller (sondeo o lectura
disparada por el canal MQTT del coche); no tiene temporizador propio.

## Estados del coche (`core/telemetry.py`)

Se recalculan en cada lectura, sin memoria. Gana la primera regla que se cumple:

| Estado | Condición |
| :-- | :-- |
| `charging` (Cargando) | cable conectado y `chargeState = 1` |
| `plugged` (Enchufado) | cable conectado |
| `driving` (En marcha) | alta tensión encendida (`hVoltageState = 1`) y velocidad > 1 km/h |
| `ready` (Encendido) | alta tensión encendida y velocidad ≤ 1 km/h: semáforo, o encendido sin moverse |
| `asleep` (Dormido) | alta tensión apagada y la nube dice que el coche no está en línea |
| `parked` (Estacionado) | alta tensión apagada (o resto de casos) |

Con la alta tensión **apagada** la velocidad y el motor térmico de la lectura no cuentan. Si **no llega** `hVoltageState`, se usa la regla antigua:
velocidad > 1 km/h o `engineState` = `driving`. El sondeo trata `ready` como «en marcha, parado». Las alarmas de puertas/ventanillas exigen `parked` (`left_car`).

## Reglas actuales

- **Empieza** un viaje con la primera lectura «en marcha» (`speed > 2 km/h` o `state == driving`) con posición. `ready` no inicia viaje (puede ser un
  climatizado remoto).
- **Se cierra** cuando llega una lectura al menos `IDLE_MINUTES_TO_END_TRIP` (5 min) después del último movimiento, o con el coche enchufado/cargando.
  Mientras haya un viaje abierto el sondeo no pasa de 150 s (`OPEN_TRIP_POLL_SECONDS`, solo consulta la nube) para poder cerrarlo a tiempo.
- **Termina (`end_time`) cuando se apagó el coche:** la primera lectura con `hVoltageState = 0` tras el último movimiento. Si se vuelve a encender
  antes del cierre, se descarta y el viaje sigue. Sin ese dato, `end_time` es la hora del último movimiento. El resto de datos de fin (posición,
  odómetro, batería, combustible) son los de la lectura que cierra.
- **Distancia: solo el odómetro** (`end_odometer - start_odometer`, km **enteros**, nunca negativa). Los puntos GPS no intervienen: se usan para la
  ruta, la reproducción y las geocercas, donde un hueco de cobertura se ve, pero no para medir. Hasta la migración `0016_odometer_distance` se
  sumaban los decimales del GPS cuando su parte entera coincidía con el odómetro; como el coche cuenta esos decimales en el kilómetro siguiente, la
  suma de los viajes crecía por encima del odómetro real (decisión del dueño, 2026-10-31; la migración truncó los viajes antiguos). Un trayecto muy
  corto puede salir como 0 km, igual que en el salpicadero; las gráficas de consumo, la ficha «Consumo EV» de Energía y el consumo medio del
  resumen de Viajes ignoran los viajes de menos de 2 km y los que no tienen energía medida (`MIN_DRIVE_KM` en `services/stats.py`). Los viajes construidos
  solo por GPS ([traccar.md](traccar.md)) no tienen odómetro y conservan la distancia de sus puntos.
- **Viaje abierto tras un reinicio:** se retoma si su última actividad fue hace menos de 30 min (y se cierra con las reglas de arriba); si es más
  antiguo se deja como estaba («Viajes incompletos»).
- **Inicio recuperado** (`start_estimated`): si el viaje se detecta con el coche ya en marcha, hora, posición, odómetro, batería y combustible de inicio son
  los de la **última lectura en parado** (en memoria o, tras un reinicio, de la BD) siempre que sea de hace menos de 6 h y el odómetro haya avanzado desde
  entonces. La hora se estima por los km recorridos a 35 km/h, sin pasar de esa lectura. La lista de viajes marca la hora con «≈» y lo explica en el detalle.
- **Batería y combustible de salida:** aunque el inicio no se recupere, se toman de la **última lectura en parado si es de hace menos de 15 min**
  (`START_LEVELS_MAX_AGE`). Al encender el coche y empezar a moverse se suele perder un punto antes de la primera lectura en marcha (caso real del
  03-11-2026: 80 % en `ready`, 79 % un minuto después ya circulando); ese punto es del viaje, no del tiempo aparcado, y si no se contaba aquí el
  viaje salía con un punto de menos y el «gasto en reposo» de Estadísticas con uno de más. La hora de inicio no cambia.
- **Viajes fantasma descartados:** al cerrar, un viaje de menos de 60 s y sin kilómetros de odómetro se borra (con sus puntos).

## Qué reporta el coche (caso real del 28/10/2026)

Antes el estado `parked` se deducía solo de `vehicleSpeed` y `engineState` (el motor térmico, siempre 0 conduciendo en eléctrico): con el coche
parado en un semáforo ya salía `parked` (pasó dos veces a mitad de un mismo viaje). La nube manda una señal fiable de «coche apagado»,
**`hVoltageState`**: `1` con el coche encendido (también en los semáforos) y `0` al apagarlo. En ese viaje valió `0` en las dos paradas finales
(14:30:19 y 14:41:10 UTC), justo en las lecturas disparadas por MQTT. Ahora es la base de los estados y de la hora de fin.

## Ruta: reproducir, exportar y lugares frecuentes (2026-10-30)

- **Reproducir** (modal «Ver trazado», `features/drives.js`): un marcador recorre los puntos guardados (`GET /api/drives/{id}/track`) con un deslizador; el viaje entero dura unos 20 s y se ve la hora, la velocidad y la batería de cada punto.
- **GPX** (`GET /api/drives/{id}/gpx`, `services/gpx.py`): descarga la ruta como GPX 1.1 (hora en UTC con `Z`, altitud, y velocidad y batería en `extensions`), para Traccar, Strava o cualquier mapa. Un viaje sin puntos responde 404.
- **Lugares frecuentes** (`GET /api/vehicles/{vin}/places`, `services/places.py`): un final de viaje dentro de una geocerca (por su `end_geofence_id` o por sus coordenadas, si la geocerca se creó después) cuenta para esa geocerca, ocupe las celdas que ocupe, y el lugar lleva su nombre; fuera de las geocercas agrupa en celdas de ~110 m, cuenta visitas (mínimo 2) y da la dirección más reciente que **no** sea el nombre de una geocerca. Así un destino renombrado a mano como «Casa» en un viaje cuyo punto final está en otro sitio no crea un segundo «Casa» (visto el 02-11-2026: la nube dio una posición vieja al terminar un viaje). La tarjeta de Viajes ofrece «Crear geocerca» en los lugares que no la tienen.
- **Entradas y salidas de geocercas** (`geofence_events`, `GET /api/geofences/events/{vin}`): el poller apunta **cada** cambio de geocerca con hora, posición, batería y odómetro, se avise o no (los avisos dependen de los interruptores de cada geocerca, «Avisar al entrar» y «Avisar al salir» en su formulario, y de los globales de Ajustes → Avisos; la tarjeta de cada geocerca dice si avisa y, si es punto de carga, su tarifa); la tarjeta «Entradas y salidas» de Geocercas los lista. Solo se registran cambios vistos por la sonda: una visita más corta que el intervalo de sondeo puede no aparecer.

## Viajes largos (2026-10-31)

Unas vacaciones o una escapada son muchos trayectos, cargas, repostajes y gastos repartidos en días. Un **viaje largo** (`journeys`, `services/journeys.py`, `api/routes_journeys.py`, `features/journeys.js`) es solo un nombre y dos instantes; **nada se copia**: cada vez que se consulta se suma lo que hay entre esas fechas (trayectos cerrados por su inicio, cargas por su inicio, repostajes, gastos e intervenciones de taller), así que corregir una carga o añadir un peaje después cambia el viaje. Devuelve kilómetros, horas al volante, kWh y litros consumidos (y consumo por 100 km eléctrico y de gasolina sobre los trayectos que tienen ese dato), cargas (kWh, euros, cuántas rápidas, cuántas sin precio), repostajes, gastos por categoría, taller, **coste total, por 100 km y por día**, kilómetros por día y las listas de lo que suma. Rutas: `GET/POST /api/vehicles/{vin}/journeys`, `POST …/journeys/preview` (las cifras de unas fechas antes de guardar; el formulario las enseña mientras se eligen), `GET/PUT/DELETE …/journeys/{id}`. Límite: 400 días. En la interfaz viven en Más → Viajes, tarjeta «Viajes largos»; el detalle enlaza con cada trayecto. Pendiente: el mapa con la ruta completa del viaje y que aparezcan en la cronología.

## Pendiente

- **Sin validar contra el coche real** con esta lógica: hay que ver que `hVoltageState` llega siempre y que el climatizado remoto lo pone a `1` o no.
- La hora de apagado es la de la **primera lectura** con `hVoltageState = 0`, no la del instante exacto: el error es el retraso de esa lectura
  (el canal MQTT dispara una lectura al apagarse, así que debería ser de segundos).
- Un viaje abierto con una lectura de «en marcha» obsoleta (nube con datos viejos) puede alargarse hasta que llegue una lectura nueva.
