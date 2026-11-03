# Posición por GPS desde un servidor Traccar

**Para qué.** Un coche con su propio localizador GPS (un rastreador que reporta a un servidor [Traccar](https://www.traccar.org/)) puede dar a EVBro su posición en directo aunque no tenga cuenta de Ebro, o cuando la nube no la dé. Con esas posiciones EVBro **construye los viajes** (coches manuales) o **completa el trazado** del viaje abierto (coches conectados). La idea del GPS del móvil (OwnTracks/Traccar Client) se descartó el mismo día: la nube actualiza la posición con el coche encendido y no aportaba nada.

## Cómo se conecta (igual que la integración `traccar_server` de Home Assistant)

- **Un token de la API** por servidor (en Traccar: usuario → Preferencias → Token), nunca usuario y contraseña. Se guarda cifrado (`traccar_links.api_token`) y la API no lo devuelve (`has_token`).
- **Traccar empuja, EVBro no sondea:** `services/traccar.py` abre el WebSocket `/api/socket` (que solo admite cookie de sesión, así que antes hace `GET /api/session?token=…` y al terminar `DELETE /api/session`, como `pytraccar`) y recibe cada posición nueva. El REST se usa solo para validar (`GET /api/server`), listar dispositivos (`GET /api/devices`) y sembrar la última posición conocida al conectar (`GET /api/positions`). Si la conexión cae, reintenta a los 10, 30, 60, 120 y 300 s y el error queda visible en la ficha.
- **Un WebSocket por servidor** (`TraccarHub`): varios coches en el mismo Traccar comparten la conexión; cada posición se enruta por `deviceId`.
- Alternativa no usada: Traccar también puede reenviar posiciones a una URL (`forward.url`), pero exige tocar la configuración del servidor; el token no.

## Configuración (Ajustes → Conexiones → «Posición por GPS (Traccar)»; la tarjeta tiene su propio selector de coche, con el de la cabecera preseleccionado; solo el dueño)

Servidor (nombre o URL completa), puerto (8082 por defecto), HTTPS, aceptar certificado autofirmado, token, **«Probar conexión»** (devuelve versión y la lista de dispositivos para elegir el del coche), **«Construir los viajes con estas posiciones»**, minutos parado para cerrar un viaje (5 por defecto) y Activo. Rutas: `GET/PUT/DELETE /api/vehicles/{vin}/traccar`, `POST …/traccar/test` (nivel `owner`), `GET /api/vehicles/{vin}/gps/latest` (`view`, para el mapa).

## Qué hace cada posición (`services/gps_positions.py`)

Se guarda en `gps_positions` (velocidad pasada de nudos a km/h; `ignition`, `motion` y batería del rastreador si vienen) y actualiza «última posición» del enlace. En el mapa de Inicio: en un coche **con** cuenta de Ebro se pinta con su propio marcador si tiene menos de 10 min (si no, manda la posición de la nube); en un coche **sin** cuenta, la última posición GPS **es** la del coche aunque sea antigua (el rastreador duerme aparcado), con «Posición GPS · hace X», y se vuelve a pedir cada 30 s mientras Inicio está a la vista. Al cambiar de coche se olvida la posición del anterior (antes, un coche sin cuenta podía quedarse mostrando la del coche visto antes, normalmente casa). Después:

- **Trazado** (`build_trips` desactivado, coches con cuenta de Ebro): solo se añade al viaje abierto; empezar y terminar lo decide el coche ([trips.md](trips.md)).
- **Viajes** (`build_trips` activado, coches sin cuenta): un viaje empieza con la primera posición **en movimiento** (atributo `motion` de Traccar, o más de 3 km/h) y termina cuando el coche lleva **los minutos configurados parado** a menos de 50 m de donde se paró; la hora de fin es la de la primera posición parado. Un silencio más largo que ese plazo cierra el viaje: muchos rastreadores se duermen un par de minutos después de aparcar y no mandan nada más hasta que el coche se mueve, así que el hub de Traccar revisa cada minuto (`close_silent_drives`) los viajes abiertos cuyo rastreador lleva callado más que los minutos de parada y los cierra **donde y cuando se paró el coche** (la primera posición de la parada final, sin contar el «movimiento» del GPS bailando a paso de peatón); si el coche iba en marcha en la última posición (señal perdida en carretera), en esa última posición. Caso real del 03-11-2026: el viaje se quedaba abierto hasta el siguiente movimiento. Un «viaje» de menos de 150 m (el GPS bailando aparcado) se descarta. Distancia por GPS, velocidad máxima y media, direcciones y geocercas de inicio y fin como en el detector del coche. Las posiciones de Traccar se borran con el plazo de la telemetría en bruto («Historial guardado»).

## No probado

Construido siguiendo el código de Home Assistant y `pytraccar` y la documentación de Traccar; **no se ha conectado todavía a un Traccar real**. Lo primero que hay que comprobar con uno: que el token vale para `GET /api/session?token=`, que el WebSocket llega con `positions`, y los umbrales de movimiento y parada con un rastreador de verdad.
