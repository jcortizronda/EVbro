# Costes, gastos, repostajes, ficha del coche e historial

## Tres cosas distintas (y la ficha)

| Qué es | Dónde se anota | Dónde se ve |
| :-- | :-- | :-- |
| **Intervenciones** de mantenimiento (revisiones, ITV, pastillas, neumáticos…) | Mantenimiento | Mantenimiento |
| **Gastos** que no son del taller: seguro, impuestos, garaje, peajes, multas, lavado, accesorios | Más → **Gastos** | Gastos |
| **Repostajes** (detectados o a mano) y **cargas** | Repostajes (con consumo real) · Energía (cargas) | ídem |
| **Balance** de todo (compra + cargas + repostajes + mantenimiento + gastos) | — (solo lectura) | Más → **Costes** |

La **ficha del coche** (Más → Coches) recoge lo que el coche *es y lleva*; lo que cambia cada año (la prima del seguro, el impuesto) son gastos, no ficha.

## Gastos (`routes_expenses.py`, tabla `expenses`)

Fecha, categoría (`insurance`, `tax`, `parking`, `tolls`, `fines`, `wash`, `accessories`, `other`), concepto, importe, notas y **`next_due_date`** opcional: cuándo toca pagarlo otra vez.
Cada gasto se puede editar y borrar; la pantalla filtra por año y categoría y muestra los próximos vencimientos.

## Vencimientos y avisos (`services/renewal_alerts.py`)

Dos fuentes: la **renovación del seguro** de la ficha (`insurance_renewal_date`) y el último gasto de cada concepto con `next_due_date` (cuando se registra el pago del año siguiente, el aviso pasa al nuevo).
Un gasto de seguro con fecha no se avisa si la ficha ya tiene la renovación. Se avisa desde **30 días antes** (una vez a la semana), **a diario la última semana** y cada 3 días una vez vencido. El poller comprueba cada 6 h y el interruptor está en Ajustes → Avisos («Vencimientos», `notify_renewals`).

## Ficha del coche (Más → Coches)

Todo lo que EVBro sabe del coche y no viene de la nube de Chery: foto (se guarda en `DATA_DIR`, se recorta en el cliente), **marca** (lista: EBRO primero y las principales marcas vendidas en España y Europa por orden alfabético, más «Otra…» con texto libre; un valor guardado que no esté en la lista se conserva como opción extra), **modelo** (lista S700/S800/S900 PHEV si la marca es EBRO; texto libre para cualquier otra), año, precio de compra y kilómetros al comprarlo (la compra cuenta desde la **fecha de entrega**; puede ser posterior o anterior a la matriculación: un coche de segunda mano se entrega años después), matrícula, **fecha de matriculación** (la ITV cuenta desde ella), **fecha de entrega** (los plazos en meses del plan oficial cuentan desde ella), seguro (compañía, póliza, cobertura, teléfono de asistencia y fecha de renovación), versión, color, etiqueta DGT, medidas y presiones de neumáticos, capacidad útil de la batería (19,27 kWh por defecto) y litros del depósito (50 por defecto). El margen (%) del aviso de presión se ajusta en Ajustes → Avisos (se guarda en el coche). Nº de bastidor (VIN): en un coche conectado se lee de la cuenta Ebro y no se edita; en un **coche sin cuenta Ebro** su `vin` es un identificador interno generado (`MAN…`) que no cambia nunca (claves ajenas, carpetas), y el campo edita en su lugar `chassis_number` (11 a 17 letras o números, se guarda en mayúsculas; solo informativo; un coche conectado responde 409 si se le envía). La marca decide el plan de mantenimiento: el oficial solo existe para EBRO (ver [maintenance.md](maintenance.md)). Columnas en `vehicles` (migración manual en `db/session.py`); `PUT /api/vehicles/{vin}` valida y acepta `null` para borrar opcionales.

## Documentos del coche (Más → Coches)

La ficha guarda también los **papeles del coche** (`routes_documents.py`, tabla `vehicle_documents`, interfaz en `features/vehicle-documents.js`):

- En **Compra**: la **factura de compra**.
- En **Seguro** (compañía, póliza, cobertura, teléfono de asistencia con botón de llamada y renovación): la **póliza del seguro**, con tantos documentos como haga falta, cada uno con su título (se sugieren «Condiciones generales» y «Condiciones particulares»), y **otros documentos** (permiso de circulación, ficha técnica u otro).

Se suben en el momento (no dependen de «Guardar cambios»); un toque los abre en otra pestaña y la papelera los borra tras confirmar. Tipos (`kind`): `purchase_invoice`, `insurance_policy`, `registration`, `technical_sheet`, `other`.

| Ruta | Nivel |
| :-- | :-- |
| `GET /api/vehicles/{vin}/documents` (lista, sin contenido) | ver |
| `POST /api/vehicles/{vin}/documents` (multipart: `file`, `kind`, `title` opcional) | dueño |
| `GET /api/documents/{document_id}` (el fichero, *inline*) | ver |
| `DELETE /api/documents/{document_id}` | dueño |

Seguridad (común con las facturas de mantenimiento y la foto: `services/uploads.py`, detalle en [security.md](security.md#ficheros-subidos-servicesuploadspy)): el tipo se decide por los **primeros bytes** (PDF, JPEG, PNG o WebP), nunca por el nombre ni por el tipo que manda el navegador; hasta **15 MB** (se corta al leer) y **50 documentos por coche**; el fichero se guarda **cifrado** en `DATA_DIR/vehicle_documents/` con un **nombre aleatorio** (el nombre original solo se muestra y va codificado en `Content-Disposition`); se sirve con `X-Content-Type-Options: nosniff` y sin caché. Un `document_id` de un coche ajeno responde 404 (`security/access.py`). Al borrar el coche o el espacio de un gestor se borran también los ficheros (`services/tenant.py`). Entran en la copia de seguridad si se copia `DATA_DIR`, pero **solo se leen con la clave de cifrado** (`ENCRYPTION_KEY` o `secret.key`).

Debajo de «Otros documentos» se ve el **espacio usado** del gestor («Espacio usado: 12,3 MB de 50 MB», o «sin límite»; `GET /api/account/storage`), que se actualiza al subir o borrar. Una subida que no cabe responde 409 con el espacio usado (ver [hosted-service.md](hosted-service.md), «Espacio de ficheros»).

## Costes (Más → Costes)

`GET /api/vehicles/{vin}/costs?period=all|year|last_year|12m|month` (o `from`/`to` en `AAAA-MM`, ver «Periodos» en [frontend.md](frontend.md)) suma **compra + cargas + repostajes + mantenimiento + gastos** y devuelve totales, gasto por mes, desglose de gastos por categoría, euros por 100 km y los últimos movimientos. Es **solo de lectura**: no hay altas aquí. Cargas y repostajes **sin precio no suman**: se listan como «sin precio» y se avisa cuánto falta por valorar.

- Precio de una carga: el de la geocerca si la hay; si no, **0 €/kWh** por defecto (modo `default`, no cuenta como «sin precio»); el precio real se introduce a mano (`PUT /api/charges/{id}/cost`, por €/kWh o total).
- **Motor** (`Vehicle.fuel_type`: `phev` por defecto, `ev`, `gasoline`, `diesel`; en la ficha, sección Energía, y en el alta de un coche sin cuenta de Ebro, donde un Ebro es PHEV y cualquier otra marca gasolina salvo que se diga otra cosa). La interfaz lo respeta (`body[data-fuel]`, `applyFuelType()` en `core/vehicles.js`): con gasolina o diésel no hay pestaña Energía ni columna de batería en la portada; con un eléctrico no hay Repostajes ni columna de gasolina.
- **AC o DC.** Cada carga lleva `charge_type` (`ac` | `dc`): el detector la marca **DC rápida** cuando el coche informa del cable de carga rápida (`fast_charging`, `fastChargingGunStatus`) o la potencia supera los 20 kW (más de lo que admite el cargador de a bordo), también si eso aparece a mitad de carga; en cualquier otro caso es **AC**, y una carga sin dato se muestra como AC. Se puede corregir a mano en la fila de la carga («Marcar como DC rápida / AC», `PUT /api/charges/{id}/type`). El resumen de Energía (`/charges/summary`) da `dc_count` y `dc_kwh_added`; la cronología y el libro del coche lo indican.
- **Energía estimada y capacidad de la ficha.** Al guardar una capacidad de batería distinta en la ficha, `services/energy_recalc.py` vuelve a estimar con ella la energía de **todos** los viajes (puntos gastados × capacidad) y los kWh añadidos de todas las cargas con batería inicial y final (también las editadas a mano: no hay marca que las distinga). El coste de una carga con precio por kWh (manual o de geocerca) sigue a los kWh nuevos con el mismo €/kWh efectivo; una carga con lectura de contador (`kwh_used`) conserva su coste. La migración `0017_energy_recalc` hizo lo mismo una vez con la capacidad que hubiera en cada ficha (2026-10-31): los viajes guardados antes de poner los 34,5 kWh del S900 se habían calculado con los 19,27 por defecto y salían a la mitad.
- Los kWh de una carga son **estimados**: `% ganado × capacidad`. Antes se guardaba también un «consumido» = añadido/0,95, que era inventado: se borró (migración de una sola vez, marca `migration.kwh_used_cleared` en `app_settings`) y la eficiencia solo se calcula si se introducen los kWh del contador.
- Mantenimiento suma el coste de cada registro del historial (revisiones, ITV, acciones puntuales, tareas propias).

## Repostajes (sección propia: Más → Repostajes)

`services/refuel_detector.py` los detecta como las cargas, con el depósito: subida de **≥ 8 puntos** con el coche parado respecto al mínimo desde el último repostaje, se ignoran los 0 % espurios y hay 30 min de separación entre dos. Los **litros son estimados** (`% × litros del depósito`) y se marcan como tales; se corrigen con el ticket (`PUT /api/refuels/{id}`). También se pueden añadir a mano (`POST /api/vehicles/{vin}/refuels`), por €/L o por total, con el cuentakilómetros y la casilla **«Depósito lleno»** (`full_tank`; por defecto sí en los manuales; en los detectados, si no se dice nada, cuenta como lleno a partir del 95 % del indicador).

**Estadísticas** (`GET /api/vehicles/{vin}/refuels/summary?period=…` o `from`/`to`, `services/refuel_stats.py`; el historial del depósito se corta en el final del periodo para que el consumo entre llenados sea el de esos meses): el **consumo real** se mide **entre dos llenados**: los litros echados después del primero, hasta el segundo incluido, entre los km del cuentakilómetros (tramos de menos de 20 km o fuera de 1–30 l/100 km se descartan como ruido). Es litros por cada 100 km recorridos, **eléctricos incluidos**: lo que se paga de gasolina por km. Si no hay dos llenados, se enseña la estimación por lo que bajó el indicador (`gauge_l_per_100km`) avisando de que es estimada. Además: precio medio del litro (total pagado ÷ litros con precio), gasolina por 100 km (precio medio × consumo), autonomía con el depósito lleno (litros del depósito × 100 ÷ consumo), km medios entre repostajes, litros y euros por mes, y la lista de repostajes con los km desde el anterior y el consumo del tramo que cierra cada llenado. La sección está en `features/refuels.js` + `css/features/refuels.css`; en Gastos ya no hay repostajes, y Costes los sigue sumando.

## Historial guardado (Ajustes → Sistema)

`services/retention.py` borra lo que supere el plazo de cada tipo, cada madrugada y al guardar los plazos (`GET/PUT /api/settings/retention`, `POST …/run`). Por defecto 0 (para siempre) en viajes y cargas, y 365 días para la **telemetría en bruto**, que es lo que más crece y solo sirve para recalcular. Se separan en tres porque tienen valor distinto: los viajes y las cargas son lo que se consulta; la telemetría no.

## Avisos relacionados

- **Presión de neumáticos** (`notify_tyre_pressure`): aviso si una presión se aleja más del margen de la indicada; debe mantenerse 10 min y no se repite en 12 h.
- **Puertas/ventanillas con el coche aparcado**: solo cuentan si el coche está «dejado» (aparcado y con la alta tensión apagada) durante 5 min seguidos; así no saltan mientras uno se baja.
- **ITV**: se calcula desde la matriculación (4 años, luego cada 2, anual a partir de los 10); una fecha de vencimiento introducida manda sobre el cálculo.

## Pendiente / límites

Todo esto es nuevo y **no se ha contrastado con el coche real**: los litros y kWh son estimaciones, la detección de repostajes no se ha visto con un repostaje real y la ITV es orientativa.
