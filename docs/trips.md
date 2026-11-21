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

- **Empieza** un viaje con la primera lectura «en marcha» (`speed > 2 km/h` o `state == driving`). `ready` no inicia viaje (puede ser un
  climatizado remoto). **No hace falta posición** (desde el 08-11-2026): a algunos coches la nube no les da nunca la ubicación (dos S800;
  [ebro-protocol.md](ebro-protocol.md), «Qué manda cada modelo») y sus viajes se miden igual por odómetro, batería y depósito, sin ruta, sin
  direcciones (`start_address`/`end_address` a `null`, la lista pone «Sin posición») y sin geocercas; el detalle lo explica y no ofrece trazado ni GPX.
- **Se cierra** cuando llega una lectura al menos `IDLE_MINUTES_TO_END_TRIP` (5 min) después del último movimiento, o con el coche **enchufado**
  (`chargeGunState = 1`). `chargeState = 1` solo no cierra nada: en marcha el motor térmico también carga la batería y el coche lo notifica como
  «cargando» (el 04-11-2026 partió un viaje en cinco). Lo mismo vale para el detector de cargas y los avisos: carga = cable + `chargeState = 1`
  (`grid_charging()` en `services/charge_detector.py`).
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
- **Km en eléctrico:** el viaje guarda también el **cuentakilómetros EV** del coche al empezar y al terminar (`start_ev_odometer` /
  `end_ev_odometer`, de la misma lectura que el odómetro total, también cuando el inicio se recupera de la lectura aparcada). Solo se usa en
  sumas (meses, periodos): por viaje el EV y el total pueden diferir en un km sin gasolina, porque avanzan desfasados. Los viajes que gastaron
  gasolina del depósito quedan fuera del consumo eléctrico, porque el motor recarga la batería por el camino ([stats.md](stats.md),
  «Kilómetros en eléctrico y viajes con gasolina»).
- **Gasolina (`fuel_used_l`):** el coche manda el depósito en **%** (`oilSurplus`), como la batería; los litros de un viaje son los puntos
  gastados × litros del depósito de la ficha (50 por defecto), igual que en los repostajes detectados. Hasta la migración `0024_drive_fuel_litres`
  se guardaban los puntos como litros (un viaje de 400 km del 100 % al 43 % salía como «57 L», 04-11-2026); la migración convirtió los viajes
  antiguos cuyo valor seguía siendo el del detector y respetó los corregidos a mano. El detalle del viaje enseña también el depósito
  (`100 % → 43 %`). Un **repostaje en ruta** suma sus puntos (`fuel_refuelled_pct`, «Repostado en ruta» en el detalle): los litros son
  `salida + repostado − llegada`; sin esto un viaje del 9 % al 21 % con 38 puntos echados por el camino salía con 0 L (05-11-2026). El
  repostaje se asigna al viaje por el **cuentakilómetros** cuando los dos lo tienen (el formulario de repostajes guarda el día pero no
  la hora) y por la hora si no.
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

## Posición vieja de la nube (2026-11-04)

Algunos días la nube no refresca la posición mientras el coche se mueve y la actualiza horas después, con el coche parado (el 04-11-2026
mantuvo la posición de la mañana, el Trabajo, hasta una hora después de aparcar en Casa). Un viaje cerrado con esa posición y una carga nacida
con ella salen en el sitio equivocado. Como un coche aparcado o enchufado no se mueve, ambos detectores **recolocan**: una lectura parada con
el **mismo cuentakilómetros** que el final del último viaje y a más de 250 m (`RELOCATE_METERS`) mueve ese final (posición, dirección y
geocerca) durante las 6 h siguientes (`RELOCATE_MAX_AGE`; solo en memoria, un reinicio lo olvida), y una carga abierta cuya lectura esté a
más de 250 m de donde nació se muda allí (posición, dirección, geocerca y por tanto su tarifa al cerrar). La deriva del GPS aparcado queda
muy por debajo de esa distancia.

**Viajes no vistos** (`drives.unseen`, 18-11-2026). Un coche dormido se lee una vez por hora; si además su canal en directo está parado, un
trayecto corto entre dos lecturas no deja más rastro que un cuentakilómetros más alto. El 16-11-2026 un coche marcaba, 77 min después de un viaje de
98 km y en el mismo sitio, 4 km más y 3 puntos menos de batería, con datos que el propio coche había generado 72 min después de aparcar (campo
`time`): una salida de ida y vuelta que nadie vio. Esos km no estaban en ningún viaje (9 casos en 157 viajes del servidor).

Ahora se registran como **un viaje propio**, nunca sumados al anterior (decisión del dueño): cuando el coche se lee **apagado** con **2 km o más**
(`UNSEEN_MIN_KM`; menos es el cuentakilómetros, que va en km enteros) sobre la última lectura en parado y no hay ningún viaje entre las dos,
`_record_unseen_drive` crea el viaje con lo que dicen esas dos lecturas: origen y destino (posición, dirección, geocerca), cuentakilómetros,
batería y depósito con su consumo. El final es la hora que el coche pone a sus datos (`time`) y el inicio se estima a 35 km/h, sin pasar de la
lectura anterior; no hay ruta ni velocidades. No se crea si el coche está encendido (si circula, el viaje normal arranca en la lectura siguiente
con su inicio recuperado) ni si los km no caben entre las dos lecturas a menos de 110 km/h (`UNSEEN_MAX_SPEED_KMH`: es la lectura que cerró el
viaje anterior, que iba atrasada; esos km siguen sin viaje). La lista lo marca «No visto» y lo explica en el detalle, sin «Ver trazado» ni GPX.
Con el canal en directo conectado el coche avisa al despertarse y el trayecto sale como un viaje normal.

**Posición congelada (medido el 09-11-2026).** No es que falte la posición: en 3.851 lecturas de 13 días de un S900 solo 17 vinieron sin ella.
Lo que pasa es que la nube **repite el mismo punto** durante horas mientras el coche circula: tres episodios en ese coche (31-10 20:00→21:58,
5 km; 03-11 22:30→04-11 06:20, 27 km; 04-11 06:20→15:06, 26 km), ninguno en los demás coches del servidor. Durante esos viajes la telemetría en
tiempo real era fresca (velocidad, cuentakilómetros, `onlineStatus` 1): es el almacén de posición de la nube (o la subida de GPS del coche) lo que
se para, y los viajes salen con un solo punto. El «Localizar coche (GPS)» manual dio un punto fresco **cada vez**, pero no siempre reactivó las
actualizaciones (el del 04-11 06:20 dio su punto y la posición volvió a congelarse hasta las 15:06). Desde entonces cinco días sin congelación y sin
ningún localizar: no hay una relación «localizar → X días de posición». El `resultTime` de `queryVehicleLocation` es la hora del **último comando**
(localizar o incluso desbloquear), no la del último fix: no sirve para detectar la congelación. `travelQuery` es la «salida programada», no un
historial de viajes: no hay otra fuente de trazados.

EVBro lo **detecta** (`services/poller_position.py:_watch_frozen_position`: el mismo punto redondeado a 4 decimales durante 3 min **desde que el
cuentakilómetros empieza a subir**, y al menos 2 km. Hasta el 14-11-2026 bastaba 1 km y el reloj corría desde que se vio el punto: el cuentakilómetros va
en km enteros, así que un solo salto al arrancar tras una noche aparcado daba «1097 min congelado, 1,0 km»; hubo cuatro falsas alarmas así en dos días) y lo escribe en el log («Frozen position on …», y «moves again after …» cuando se descongela). Como **experimento**
(`AUTO_LOCATE_ENABLED`, apagado por defecto), al detectarlo manda él mismo `localizar_coche_gps` con el PIN guardado (origen `auto_locate` en la
auditoría), como mucho uno cada 5 min y **dos por episodio**. Los km solo cuentan en lecturas con el coche encendido: el 16-11-2026 un coche aparcado tras un viaje de 98 km recibió tres «localizar» en tres horas, dormido y en su posición real, porque el cuentakilómetros marcaba 4 km más una hora después de aparcar (una salida corta que EVBro no vio, entre dos lecturas horarias); cada uno lo despertó. Si la cuenta no tiene PIN guardado no lo reintenta en ese episodio y lo
anota como información, no como aviso. El log dice cuántos segundos tarda la posición en moverse
tras cada localizar: con eso se decide si vale.

**Visto funcionar el 21-11-2026** (el S900, con `AUTO_LOCATE_ENABLED` activado en el servidor): en tres viajes seguidos la nube solo movió
el punto después de cada localizar; hubo cuatro episodios y en los cuatro la posición se movió entre 11 y 69 s después del localizar
automático. Ese mismo día salió el hueco que quedaba: el último punto llegó a mitad del cuarto viaje, el coche recorrió 2 km más, aparcó en
casa y el mapa lo seguía poniendo a 2 km (el episodio no llegó a los 3 min de congelación). Por eso, **al pararse**, un coche que ha recorrido
2 km o más con el motor encendido sobre su último punto pide **un** localizar para saber dónde se ha quedado. Lo pide mientras sigue
despierto, no si ya se mandó un localizar sobre ese mismo punto, y nunca si el cuentakilómetros subió con el coche apagado. El viaje se cierra
unos minutos después de pararse y toma ese punto nuevo como final.

## Ruta: reproducir, exportar y lugares frecuentes (2026-10-30)

- **Reproducir** (modal «Ver trazado», `features/drives.js`): un marcador recorre los puntos guardados (`GET /api/drives/{id}/track`) con un deslizador; el viaje entero dura unos 20 s y se ve la hora, la velocidad y la batería de cada punto.
- **GPX** (`GET /api/drives/{id}/gpx`, `services/gpx.py`): descarga la ruta como GPX 1.1 (hora en UTC con `Z`, altitud, y velocidad y batería en `extensions`), para Traccar, Strava o cualquier mapa. Un viaje sin puntos responde 404.
- **Lugares frecuentes** (`GET /api/vehicles/{vin}/places`, `services/places.py`): un final de viaje dentro de una geocerca (por su `end_geofence_id` o por sus coordenadas, si la geocerca se creó después) cuenta para esa geocerca, ocupe las celdas que ocupe, y el lugar lleva su nombre; fuera de las geocercas agrupa en celdas de ~110 m, cuenta visitas (mínimo 2) y da la dirección más reciente que **no** sea el nombre de una geocerca. Así un destino renombrado a mano como «Casa» en un viaje cuyo punto final está en otro sitio no crea un segundo «Casa» (visto el 02-11-2026: la nube dio una posición vieja al terminar un viaje). La tarjeta de Viajes ofrece «Crear geocerca» en los lugares que no la tienen.
- **Entradas y salidas de geocercas** (`geofence_events`, `GET /api/geofences/events/{vin}`): el poller apunta **cada** cambio de geocerca con hora, posición, batería y odómetro, se avise o no (los avisos dependen de los interruptores de cada geocerca, «Avisar al entrar» y «Avisar al salir» en su formulario, y de los globales de Ajustes → Avisos; la tarjeta de cada geocerca dice si avisa y, si es punto de carga, su tarifa); la tarjeta «Entradas y salidas» de Geocercas los lista. Solo se registran cambios vistos por la sonda: una visita más corta que el intervalo de sondeo puede no aparecer.

## Viajes largos (2026-10-31)

Unas vacaciones o una escapada son muchos trayectos, cargas, repostajes y gastos repartidos en días. Un **viaje largo** (`journeys`, `services/journeys.py`, `api/routes_journeys.py`, `features/journeys.js`) es solo un nombre y dos instantes; **nada se copia**: cada vez que se consulta se suma lo que hay entre esas fechas (trayectos cerrados por su inicio, cargas por su inicio, repostajes, gastos e intervenciones de taller), así que corregir una carga o añadir un peaje después cambia el viaje. Devuelve kilómetros, horas al volante, kWh y litros consumidos (y consumo por 100 km eléctrico y de gasolina sobre los trayectos que tienen ese dato), cargas (kWh, euros, cuántas rápidas, cuántas sin precio), repostajes, gastos por categoría, taller, **coste total, por 100 km y por día**, kilómetros por día y las listas de lo que suma. Rutas: `GET/POST /api/vehicles/{vin}/journeys`, `POST …/journeys/preview` (las cifras de unas fechas antes de guardar; el formulario las enseña mientras se eligen), `GET/PUT/DELETE …/journeys/{id}`. Las fechas se guardan en UTC: el formulario convierte su hora local antes de enviarla, y los días del viaje se cuentan en hora local. Límite: 400 días. En la interfaz viven en Más → Viajes, tarjeta «Viajes largos»; el detalle enlaza con cada trayecto. Pendiente: el mapa con la ruta completa del viaje y que aparezcan en la cronología.

## Pendiente

- **Sin validar contra el coche real** con esta lógica: hay que ver que `hVoltageState` llega siempre y que el climatizado remoto lo pone a `1` o no.
- La hora de apagado es la de la **primera lectura** con `hVoltageState = 0`, no la del instante exacto: el error es el retraso de esa lectura
  (el canal MQTT dispara una lectura al apagarse, así que debería ser de segundos).
- Un viaje abierto con una lectura de «en marcha» obsoleta (nube con datos viejos) puede alargarse hasta que llegue una lectura nueva.
