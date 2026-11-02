# Informes anónimos (telemetría)

Sirven para que el proyecto sepa qué versiones y funciones se usan y, sobre todo, para **encontrar y corregir fallos** que pasan en instalaciones ajenas. Código: `backend/src/app/services/telemetry.py`, rutas en `api/routes_telemetry.py` y `api/routes_admin.py`, script de la web `frontend/static/js/core/error-report.js`.

## Quién decide

- **Nada se recoge ni se envía hasta que el administrador lo activa**: en el formulario con el que se crea el administrador (respuesta opcional), en la pestaña Servidor o en el aviso que sale arriba mientras no ha decidido. Se guarda en `app_settings.telemetry_enabled` (`"1"`, `"0"`; sin fila = sin decidir).
- Antes de decidir, **«Ver qué se envía»** enseña el informe exacto que saldría en ese momento (`GET /api/admin/telemetry/preview`).
- `TELEMETRY_URL` vacía desactiva el envío aunque esté marcado (la consola lo indica). Por defecto es `https://data.evbro.app`.
- Al desactivarlo se borran los fallos que esperaban envío. «Nuevo identificador» cambia el identificador aleatorio de la instalación: los informes siguientes no se pueden relacionar con los anteriores.
- Las acciones quedan en el registro de la consola (`telemetry.enabled`, `.disabled`, `.send`, `.new_id`).

## Qué lleva un informe (`evbro-telemetry/1`)

Uno al día (el primero 10 minutos después de arrancar, para que un bucle de reinicios no envíe uno por arranque; si falla, se reintenta a las 3 horas):

| Parte | Contenido |
| :--- | :--- |
| `install_id` | UUID aleatorio de la instalación |
| `server` | versión, commit, Python, arquitectura, sistema, base de datos (`postgresql`/`sqlite`), horas encendido, idiomas de las cuentas |
| `size` | gestores, cuentas asociadas, coches conectados, coches manuales y cuentas de Ebro, **por tramos** (`0`, `1`, `2-5`, `6-20`, `21+`) |
| `models` | modelo del coche tal como lo nombra la nube de Ebro y tipo (`phev`, `ev`…), con el número de coches por tramos |
| `features` | sí/no de cada función: MQTT, canal en directo del coche, Traccar, push, Telegram, Pushover, webhook, huella, catálogo en línea, clima programado, carga programada, geocercas, plan de mantenimiento, repostajes, gastos, documentos, consumibles, escapadas |
| `polls` | consultas a la nube desde el último informe: con datos, vacías y con error |
| `commands` | órdenes desde el último informe (como mucho 7 días), por orden y modelo: cuántas bien, cuántas mal y con qué código (`A00084`…) |
| `errors` | hasta 150 grupos de fallos (abajo) |

**Nunca**: nombres, usuarios, correos, VIN, matrículas, posiciones, direcciones, geocercas, viajes, horas de uso, kilómetros, importes, claves ni nada escrito por un gestor. Un test (`tests/test_telemetry.py`) comprueba que el informe no contiene el VIN, la matrícula, el nombre del coche, el usuario ni la dirección de una orden.

## Fallos

- **Servidor:** un manejador de `logging` recoge cada `WARNING` y `ERROR` mientras los informes están activados. Con excepción: su tipo y las líneas **del código de EVBro** (`app/services/poller.py:310 poll_vehicle`); las de librerías no viajan. Sin excepción: el nombre del registrador y el sitio de la llamada.
- **Web:** `core/error-report.js` envía los errores de JavaScript de una sesión iniciada a `POST /api/telemetry/client-error` (ruta protegida): tipo, mensaje y líneas `fichero.js:línea función` de los scripts de EVBro. Nunca la dirección de la página ni lo que hay en pantalla. Como mucho 8 por visita, y deja de enviar en cuanto el servidor contesta que no recoge.
- **Limpieza del mensaje** (`scrub`): se sustituyen direcciones web, correos, VIN, IPs, claves largas, coordenadas y números, y el texto entre comillas se conserva solo si parece un identificador (`'battery_level'`). El receptor vuelve a limpiarlo.
- **Agrupación:** la huella es el tipo más las cuatro últimas líneas de código sin el número de línea, así que el mismo fallo en el mismo sitio es un solo grupo aunque cambie el mensaje. En la instalación se guardan como mucho 300 grupos (`telemetry_local_errors`) hasta que un informe aceptado los lleva.

## El receptor (`TELEMETRY_RECEIVER=true`, solo el servidor del proyecto)

- `POST /api/telemetry/v1/report` es pública a propósito (las instalaciones no tienen clave): sin `TELEMETRY_RECEIVER` contesta 404. Límites: 272 KB por informe, un informe cada 30 minutos por identificador, formato e identificador validados, textos recortados y limpiados de nuevo.
- **No se guarda la IP.** El proxy inverso tampoco debe registrar las peticiones de ese dominio (en Caddy, `log_skip` en el bloque de `data.evbro.app`).
- Tablas: `telemetry_installs` (último informe de cada instalación, sin su lista de fallos), `telemetry_error_groups` (un fallo, venga de donde venga) y `telemetry_error_hits` (cuántas veces lo envió cada instalación). Las instalaciones que no envían nada en 180 días se borran con sus apariciones.
- La consola del servidor del proyecto tiene la pestaña **Informes**: instalaciones activas, versiones, modelos, funciones en uso, base de datos y arquitectura, consultas a la nube, órdenes con sus códigos de fallo y la lista de fallos con sus líneas de código. Un fallo se marca como **resuelto** y vuelve a abrirse solo si lo envía una versión que no lo había enviado antes.

## Pendiente

- No se ha probado todavía contra el servidor real (`data.evbro.app`); los tests lo prueban dentro del mismo proceso.
