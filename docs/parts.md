# Consumibles y referencias (piezas del coche)

La ficha del coche (Más → Coches → **Consumibles**) recoge **lo que lleva el coche** para no marearse al buscar un consumible: aceite, filtros, pastillas, neumáticos, batería de 12 V, escobillas, bombillas, líquidos…
Una pieza del coche puede apuntar a una entrada del catálogo compartido (`catalog_id`): la propuesta de su dueño o la entrada aprobada de la que se copió.

## Modelo

- `vehicle_parts` (`VehiclePart`): categoría, posición (delantero/trasero/repuesto cuando procede), nombre, marca, **referencia** (`reference`, «Ref. marca» en la interfaz: el número que pone la marca de la pieza, recambio u original), **Ref. fabricante** (`oem_reference`: el número de pieza de la marca del coche, el de Ebro en un Ebro; migración `0020_oem_reference`), especificación (viscosidad, medida, capacidad…), cantidad, `attrs` (solo neumáticos: tipo y **DOT**), notas, y de dónde sale: `installed_at`, `installed_odometer`, `source_log_id` (la intervención que la montó).
- Las categorías (`services/parts.py`, `PART_CATEGORIES`) llevan sus etiquetas: cada una dice cómo se llama su «especificación» y su «cantidad». Son **sugerencias vacías**: no hay referencias de fábrica precargadas porque no se conocen las reales del S700/S800/S900; las rellena el dueño.
- **Una pieza por categoría y posición** (una pastilla delantera, una trasera): al anotar otra se **sustituye**, como pasa en el coche. Las categorías `multiple` (bombillas, «otro consumible») admiten varias, distinguidas por nombre.
- `attrs` está en lista blanca: solo `season` (`summer`/`winter`/`all_season`) y `dot` (4 cifras: semana y año, p. ej. `2324`), y solo para neumáticos. Todo el texto se guarda tal cual y se pinta escapado.
- **La ficha guarda el consumible, no una unidad concreta.** El DOT es la fecha de fabricación de unos neumáticos en particular: se anota en la **intervención** que los monta (queda en su historial) y la ficha no lo guarda ni lo muestra (`SHEET_DROPS` en `services/parts.py`; un DOT antiguo de la ficha tampoco se devuelve). El tipo de neumático (verano, invierno, todo tiempo) sí se queda en la ficha.

## API (todas bajo `protected`)

`GET /api/part-categories` · `GET/POST /api/vehicles/{vin}/parts` (`?q=` busca en nombre, marca, referencia, ref. fabricante, especificación, notas y etiquetas; `?category=`) · `PUT/DELETE /api/parts/{id}`.

Al crear o editar una pieza se puede mandar `catalog_id` para enlazarla con la entrada del catálogo de la que se copió. Solo se acepta una entrada **aprobada** de la **misma categoría**; si no, se ignora (nunca se enlaza con la propuesta de otro). Editar la pieza no rompe el enlace; la interfaz deja de mandarlo si se cambian a mano la marca o las referencias copiadas. Las intervenciones de mantenimiento no enlazan nunca: `catalog_id` se descarta en sus piezas.

## Piezas en las intervenciones

Los tres registros de mantenimiento (revisión oficial, tarea propia «Hecho», intervención puntual) aceptan `parts` y `update_parts` (por defecto `true`).
- El **historial** guarda las piezas tal como se montaron ese día (`maintenance_logs.parts_used`) y no cambia aunque la ficha cambie después.
- Con `update_parts`, la **ficha** se actualiza con ellas (fecha y kilómetros de montaje incluidos).
- Una pieza mal formada (categoría, posición o DOT inválidos) rechaza toda la intervención con 422: no se guarda nada a medias.

## Catálogo compartido (2026-10-30)

Lo que lleva cada modelo, compartido entre dueños: `catalog_entries` (`services/catalog.py`). Un gestor **propone** una de sus piezas desde su ficha («Proponer al catálogo» en el detalle de la pieza; `POST /api/parts/{part_id}/propose`, nivel `edit`): se copian la pieza y el modelo del coche en ese momento y la pieza guarda `catalog_id`; hace falta modelo en la ficha y al menos marca, referencia o especificación. La propuesta queda **pendiente** y solo la ve su autor (`GET /api/catalog/mine`) y el administrador, que la **aprueba o rechaza con motivo** en la consola (pestaña «Catálogo»; `GET /api/admin/catalog?status=`, `POST /api/admin/catalog/{id}/approve|reject`) y puede **completarla o corregirla** antes o después de aprobarla (`PATCH /api/admin/catalog/{id}`: marca y modelo del coche, años, nombre, marca, referencia, especificación, cantidad, notas; nunca el tipo de pieza; una rechazada no se edita). **Una referencia puede valer para varios coches**: cada entrada tiene una lista de *fitments* (marca, modelo, años; `catalog_fitments`), la primera sale del coche del proponente y el administrador añade las demás en el diálogo de edición (una fila por coche: marca, modelo, años y ref. fabricante); si otro dueño con un modelo distinto propone la misma marca y referencia, aprobarlo suma la confirmación **y añade su modelo** a la entrada. La búsqueda por modelo mira todos los fitments. La consola enseña los coches a los que vale junto a la pieza y cada especificación con su etiqueta propia (viscosidad, medida, norma…) y su unidad (litros, unidades…). Una propuesta rechazada puede volver a enviarse corregida; una igual a una entrada aprobada (modelo, categoría, marca y referencia) no se duplica: suma una **confirmación** a la existente y la pieza del proponente pasa a apuntar a ella. Lo aprobado lo ve todo el mundo filtrado por modelo (sin distinguir mayúsculas) y categoría (`GET /api/catalog?model=&category=`): al añadir una pieza, el formulario sugiere «Lo que usan otros S900 PHEV» y un toque rellena marca, referencia, especificación y cantidad. El administrador puede **exportar** las aprobadas como JSON versionado (`GET /api/admin/catalog/export`, formato `evbro-catalog/2`); y, desde la misma pestaña, elegir si este servidor usa su **catálogo propio o uno en línea** de otro servidor EVBro (enlace `https://servidor#clave`, espejo diario de solo lectura, propuestas reenviadas) y emitir **claves** para que otros servidores usen el suyo ([hosted-service.md](hosted-service.md), «Origen del catálogo por servidor»). El administrador no ve de quién es el coche: solo el modelo, la pieza y el nombre de usuario que la propuso.

**Ref. fabricante en el catálogo (2026-11-01).** El número de pieza de la marca del coche va **en cada fitment** (`catalog_fitments.oem_reference`), porque el mismo filtro tiene un número en Ebro y otro en otra marca. `catalog_entries.oem_reference` copia el del primer fitment, como marca, modelo y años. La propuesta lo toma de la pieza, y basta con él para proponer (cuenta como «una referencia»). En la consola cada coche es una fila con cuatro campos (marca, modelo, años y ref. fabricante); solo el modelo es obligatorio. Una propuesta **sin referencia** es duplicada de una aprobada si tiene la misma ref. fabricante para el mismo modelo. Una confirmación desde otro modelo añade su fitment con su ref. fabricante, y desde el mismo modelo la rellena si faltaba. La exportación y el catálogo en línea siguen en `evbro-catalog/2`: el campo es nuevo y los servidores anteriores lo ignoran.

**Lo que no se publica.** Las **notas** de la pieza son del dueño y nunca pasan a la propuesta: el administrador escribe las del catálogo en la consola. Una propuesta que llega de otro servidor tampoco guarda sus notas, por si viene de una versión anterior que aún las enviaba. En la consola el administrador puede corregir la **posición** (solo las que admite el tipo de pieza: las pastillas, delantera o trasera; el aceite, ninguna).

**Proponer al añadir (2026-11-01).** El botón de proponer está también en el formulario de **añadir** pieza («Guardar y proponer»): guarda la pieza y la propone en un paso. En una pieza ya guardada, «Proponer al catálogo» guarda antes los cambios del formulario, para que la propuesta sea lo que se ve. No aparece si la pieza ya está en el catálogo, tiene una propuesta pendiente o se acaba de copiar del catálogo. Si el guardado sale bien pero la propuesta no (por ejemplo, falta el modelo en la ficha), la pieza queda guardada y el aviso lo dice.

**Piezas añadidas por el administrador.** En la consola, «Añadir pieza» (`POST /api/admin/catalog`; tipos en `GET /api/admin/catalog/categories`) crea una entrada **ya aprobada**: tipo de pieza, coches a los que vale (al menos uno con modelo), nombre, posición si el tipo la tiene, marca, ref. marca, especificación, cantidad y notas. Necesita marca, alguna referencia o especificación. Si ya hay una aprobada igual (la misma regla de duplicados que al aprobar), responde 409 con su número para que se edite esa. Queda en el registro como «Catálogo: añadida» y, en modo en línea, se envía también a la cola del otro servidor. El diálogo de la consola tiene una fila de campos por coche (marca, modelo, años, ref. fabricante) con «Añadir coche» y «×» para quitar una (siempre queda una), y la tabla enseña cada coche en su propia línea.

## Ver catálogo online (Consumibles)

El interruptor **Ver catálogo online** (se recuerda en el dispositivo, `evbro_parts_catalog`) carga `GET /api/catalog?model=<modelo de la ficha>&limit=100` y lo mezcla con las piezas del coche, por grupos:
- Las piezas propias llevan su estado: *sin proponer* (solo en este coche), *propuesta enviada*, *en el catálogo* o *propuesta rechazada*.
- Debajo, las entradas aprobadas que el coche **no tiene ya**: mismo enlace (`catalog_id`), misma marca y referencia o, sin referencia, misma ref. fabricante. Llevan la etiqueta *Catálogo* con las confirmaciones (×N).
- Las propuestas propias pendientes de ese modelo cuya pieza ya no está en la ficha también aparecen, sin acción.
- Tocar una entrada abre el formulario relleno («Añadir del catálogo»). Si el coche ya tiene una pieza de ese tipo y posición, avisa de que la sustituye.
- Sin modelo en la ficha, o sin entradas aprobadas, lo dice en una línea bajo el interruptor.

La búsqueda filtra también las entradas del catálogo.

## Pendiente

- Referencias precargadas por modelo: dependen del catálogo compartido y de una fuente fiable (manual).
- Aviso por antigüedad del neumático (con el DOT de la última intervención que los montó) o por kilómetros desde el montaje.
