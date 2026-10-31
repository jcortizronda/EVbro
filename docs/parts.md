# Consumibles y referencias (piezas del coche)

La ficha del coche (Más → Coches → **Consumibles**) recoge **lo que lleva el coche** para no marearse al buscar un consumible: aceite, filtros, pastillas, neumáticos, batería de 12 V, escobillas, bombillas, líquidos…
Es el primer paso del catálogo compartido de [hosted-service.md](hosted-service.md): una pieza del coche puede apuntar a una entrada del catálogo (`catalog_id`, hoy sin uso).

## Modelo

- `vehicle_parts` (`VehiclePart`): categoría, posición (delantero/trasero/repuesto cuando procede), nombre, marca, **referencia**, especificación (viscosidad, medida, capacidad…), cantidad, `attrs` (solo neumáticos: tipo y **DOT**), notas, y de dónde sale: `installed_at`, `installed_odometer`, `source_log_id` (la intervención que la montó).
- Las categorías (`services/parts.py`, `PART_CATEGORIES`) llevan sus etiquetas: cada una dice cómo se llama su «especificación» y su «cantidad». Son **sugerencias vacías**: no hay referencias de fábrica precargadas porque no se conocen las reales del S700/S800/S900; las rellena el dueño.
- **Una pieza por categoría y posición** (una pastilla delantera, una trasera): al anotar otra se **sustituye**, como pasa en el coche. Las categorías `multiple` (bombillas, «otro consumible») admiten varias, distinguidas por nombre.
- `attrs` está en lista blanca: solo `season` (`summer`/`winter`/`all_season`) y `dot` (4 cifras: semana y año, p. ej. `2324`), y solo para neumáticos. Todo el texto se guarda tal cual y se pinta escapado.

## API (todas bajo `protected`)

`GET /api/part-categories` · `GET/POST /api/vehicles/{vin}/parts` (`?q=` busca en nombre, marca, referencia, especificación, notas y etiquetas; `?category=`) · `PUT/DELETE /api/parts/{id}`.

## Piezas en las intervenciones

Los tres registros de mantenimiento (revisión oficial, tarea propia «Hecho», intervención puntual) aceptan `parts` y `update_parts` (por defecto `true`).
- El **historial** guarda las piezas tal como se montaron ese día (`maintenance_logs.parts_used`) y no cambia aunque la ficha cambie después.
- Con `update_parts`, la **ficha** se actualiza con ellas (fecha y kilómetros de montaje incluidos).
- Una pieza mal formada (categoría, posición o DOT inválidos) rechaza toda la intervención con 422: no se guarda nada a medias.

## Catálogo compartido (2026-10-30)

Lo que lleva cada modelo, compartido entre dueños: `catalog_entries` (`services/catalog.py`). Un gestor **propone** una de sus piezas desde su ficha («Proponer al catálogo» en el detalle de la pieza; `POST /api/parts/{part_id}/propose`, nivel `edit`): se copian la pieza y el modelo del coche en ese momento y la pieza guarda `catalog_id`; hace falta modelo en la ficha y al menos marca, referencia o especificación. La propuesta queda **pendiente** y solo la ve su autor (`GET /api/catalog/mine`) y el administrador, que la **aprueba o rechaza con motivo** en la consola (pestaña «Catálogo»; `GET /api/admin/catalog?status=`, `POST /api/admin/catalog/{id}/approve|reject`) y puede **completarla o corregirla** antes o después de aprobarla (`PATCH /api/admin/catalog/{id}`: marca y modelo del coche, años, nombre, marca, referencia, especificación, cantidad, notas; nunca el tipo de pieza; una rechazada no se edita). **Una referencia puede valer para varios coches**: cada entrada tiene una lista de *fitments* (marca, modelo, años; `catalog_fitments`), la primera sale del coche del proponente y el administrador añade las demás en el diálogo de edición (una por línea: `marca | modelo | años`); si otro dueño con un modelo distinto propone la misma marca y referencia, aprobarlo suma la confirmación **y añade su modelo** a la entrada. La búsqueda por modelo mira todos los fitments. La consola enseña los coches a los que vale junto a la pieza y cada especificación con su etiqueta propia (viscosidad, medida, norma…) y su unidad (litros, unidades…). Una propuesta rechazada puede volver a enviarse corregida; una igual a una entrada aprobada (modelo, categoría, marca y referencia) no se duplica: suma una **confirmación** a la existente y la pieza del proponente pasa a apuntar a ella. Lo aprobado lo ve todo el mundo filtrado por modelo (sin distinguir mayúsculas) y categoría (`GET /api/catalog?model=&category=`): al añadir una pieza, el formulario sugiere «Lo que usan otros S900 PHEV» y un toque rellena marca, referencia, especificación y cantidad. El administrador puede **exportar** las aprobadas como JSON versionado (`GET /api/admin/catalog/export`, formato `evbro-catalog/2`); y, desde la misma pestaña, elegir si este servidor usa su **catálogo propio o uno en línea** de otro servidor EVBro (enlace `https://servidor#clave`, espejo diario de solo lectura, propuestas reenviadas) y emitir **claves** para que otros servidores usen el suyo ([hosted-service.md](hosted-service.md), «Origen del catálogo por servidor»). El administrador no ve de quién es el coche: solo el modelo, la pieza y el nombre de usuario que la propuso.

## Pendiente

- Referencias precargadas por modelo: dependen del catálogo compartido y de una fuente fiable (manual).
- Vincular una pieza con una entrada del catálogo (`catalog_id`) y proponer entradas nuevas.
- Aviso por antigüedad del neumático (DOT) o por kilómetros desde el montaje.
