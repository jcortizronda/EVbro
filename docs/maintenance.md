# Mantenimiento

Un **único plan por coche** con un interruptor: **Oficial EBRO** (la pauta de la marca) o **Personalizado** (tareas e intervalos propios). El
historial de intervenciones y las facturas son comunes. Cambiar de modo no borra nada.

**El plan oficial es de EBRO**: solo existe para coches cuya marca (ficha del coche) es Ebro, sin distinguir mayúsculas (`is_ebro` en
`services/maintenance_official.py`). Con otra marca la pantalla oculta el interruptor y la vista oficial y usa el plan personalizado;
`GET …/maintenance/plan` devuelve `official_available: false` y `mode: "custom"` (aunque el plan guardado diga otra cosa), `PUT` con
`mode: "official"` responde **409** y los avisos no calculan escalones oficiales. La **ITV** funciona igual para cualquier marca.

## Plan oficial (`services/maintenance_official.py`)

- Seis escalones: 15.000 km / 12 meses, 30.000 / 24 … 90.000 / 72. Cada uno lleva operaciones a **reemplazar (R)** e **inspeccionar (I)** y un
  tiempo de mano de obra (con extras en los 60.000 km: anticongelante y aceite de transmisión).
- **La próxima revisión sale de la última registrada**, no del odómetro: es el primer escalón con km mayor que el último hecho. El escalón
  dice **qué** se hace (la tabla R/I).
- **Cuándo vence**, lo que antes llegue:
  - con una revisión registrada, **15.000 km y 12 meses después de cuando se hizo de verdad** (sus km y su fecha; la diferencia entre los
    dos escalones). La de 15.000 hecha a 10.688 km deja la siguiente a 25.688 km, no a 30.000 (16-11-2026: hasta entonces se contaba
    desde los escalones fijos y la entrega, y dos coches del servidor la veían a 30.000). Hecha tarde, también se corre;
  - sin ninguna registrada, a los km del escalón o en su fecha (**fecha de entrega + los meses del escalón**). Sin fecha de entrega
    (`maintenance_plans.baseline_date`) solo se vigilan los kilómetros y la pantalla la pide.
- La API devuelve `km` (el escalón), `due_km` (dónde vence), `interval_km`/`interval_months` y `since_last` (si se cuenta desde la última).
- Estados: `ok`, `warning` (faltan ≤ 1.500 km o ≤ 30 días) y `due` (vencida).
- Registrar un escalón crea un `MaintenanceLog` con `revision_km` colgado de un ítem «contenedor» (`origin = "official"`, no se lista como tarea).
  Un escalón no se puede registrar dos veces (409).

## Plan personalizado

Tareas propias (`MaintenanceItem`, `origin = "custom"`) con intervalo en km, en meses o en ambos; vencen lo que antes llegue. Ya **no se crean
tareas solas**: hay sugeridas (`SUGGESTIONS`) que se añaden con un toque. Si se cambia el intervalo, el vencimiento se recalcula desde el último servicio.

## API (todas exigen sesión)

| Método | Ruta | Para qué |
| :--- | :--- | :--- |
| GET | `/api/vehicles/{vin}/maintenance/plan` | Modo, `official_available`, punto de partida, próxima revisión oficial, tareas propias y sugeridas |
| PUT | `/api/vehicles/{vin}/maintenance/plan` | `mode` (`official`/`custom`; `official` da 409 si la marca no es Ebro), `baseline_date`, `baseline_odometer` |
| POST | `/api/vehicles/{vin}/maintenance/official/complete` | Registrar un escalón (`step_km`, fecha, km, coste, taller) |
| GET/POST | `/api/vehicles/{vin}/maintenance` | Tareas propias |
| PUT/DELETE | `/api/maintenance/{id}` · POST `/{id}/complete` | Editar, borrar y marcar «hecho» |
| GET | `/api/vehicles/{vin}/maintenance/logs` | Historial común (con `origin` y `revision_km`) |
| DELETE | `/api/maintenance/logs/{id}` | Borrar un registro del historial (escalón oficial, tarea hecha, intervención o ITV) y su factura. El plan oficial y la ITV se recalculan solos; una tarea propia vuelve a su registro anterior si lo tiene (si era el único, se queda como estaba) |

## Avisos (`services/maintenance_alerts.py`)

El poller revisa cada 200 km o cada 6 h (los plazos en meses vencen sin que el coche se mueva) y manda cada aviso **una vez** cuando se acerca y **otra** cuando vence (hasta el 18-11-2026 lo repetía cada día; ver `docs/costs.md`, «Una vez por episodio»):
en modo oficial (solo coches EBRO), la próxima revisión si está `warning`/`due`; en modo personalizado (o con otra marca), cada tarea propia `warning`/`due`. El texto dice km y/o días.
Se activan en Ajustes → Avisos → «Mantenimiento oficial».

## Recomendaciones de EVbro (`services/maintenance_advice.py`, desde el 25-11-2026)

Revisiones **fuera del plan de la marca** que dependen de cómo se usa un híbrido enchufable. Son consejos de EVbro: los plazos son
**práctica general de taller, no datos de EBRO**, y así se dice en la pantalla. **[DUDA]** no se han contrastado con un taller de la marca.

| Recomendación (`key`) | Cuándo toca | Cuenta desde |
| :--- | :--- | :--- |
| Pastillas y discos (`brakes`) | 15.000 km o 12 meses, lo que llegue antes | último «revisado», pastillas o discos nuevos en Consumibles, o la entrega |
| Gasolina envejecida (`fuel_age`) | 4 meses sin repostar; segundo aviso a los 6 | último repostaje o último «revisado» |
| Rotar neumáticos (`tyre_rotation`) | 10.000 km; 8.000 si más de la mitad de los km del último año son en viajes a ≥ 90 km/h de media (con al menos 1.000 km de viajes) | último «revisado», neumáticos nuevos, o la entrega |
| Edad de los neumáticos (`tyre_age`) | a los 5 años; después cada año | el DOT del neumático más viejo (o su fecha de montaje); sin ellos, la entrega |
| Batería de 12 V (`battery_12v`) | a los 3 años; después cada año | la fecha de montaje en Consumibles; sin ella, la entrega |
| Escobillas (`wipers`) | 12 meses | último «revisado», escobillas nuevas, o la entrega |

Con más del 70 % de los km en eléctrico, la de frenos añade una nota sobre el óxido de los discos. Sin fecha de entrega (ni de matriculación)
solo se vigilan los km: las que cuentan solo meses no aparecen. Sin ningún repostaje registrado no hay «gasolina envejecida». Un coche con plan
**personalizado** que ya tiene una tarea para una de ellas (su nombre contiene «rotaci», «escobilla», «12 V» o «pastilla») no recibe esa recomendación.

**Respuestas** (tabla `maintenance_advice`, una fila por coche y recomendación, migración `0041`; van en la exportación de la cuenta):
- **«Revisado, está bien»** (`POST /vehicles/{vin}/maintenance/advice/{key}/check`, opcionalmente con `date` y `odometer`): vuelve a contar **desde ese día y esos km**, no en múltiplos fijos (avisado a 10.000 y contestado a 10.400 → el siguiente a 20.400), como el plan oficial.
- **«Registrar intervención»**: la intervención puntual de siempre (`POST …/maintenance/events`) con `advice_key`; cuenta desde su fecha y sus km.
- **«Recuérdamelo más adelante»** (`POST …/advice/{key}/snooze`): 30 días, o 1.000 km en las que cuentan km, lo que llegue antes.
- **Desactivar / activar** (`PUT …/advice/{key}` con `enabled`).

`GET /vehicles/{vin}/maintenance/advice` las devuelve ordenadas: las que tocan, las aplazadas, las que van al día y las desactivadas. Leer pide
nivel «ver»; contestar, «editar». **Aviso**: el poller las mira con el mantenimiento (cada 200 km o 6 h) y manda **un aviso por recomendación
cuando vence**; si vencen varias a la vez, **uno solo con la lista**. No se repite hasta que se contesta y vuelve a vencer. **La primera
vez que mira un coche no avisa** (marca `advice:_seen` en `alert_states`): lo que ya tocaba ese día (un coche con años el día del
despliegue, o recién añadido) sale en la lista y solo avisa lo que venza después. Usa el interruptor de Ajustes → Avisos → «Mantenimiento». Interfaz: `features/maintenance-advice.js`.

## Migración

Las tareas que ya existían pasan a `custom` (columna `origin`, valor por defecto), con sus fechas e historial. Los registros antiguos de «revisión
oficial» siguen en el historial pero **no cuentan como escalón hecho** (no tenían `revision_km`): se vuelve a registrar el último escalón hecho.
