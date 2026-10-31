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
- **La próxima revisión sale de la última registrada**, no del odómetro: es el primer escalón con km mayor que el último hecho.
- **Vence lo que antes llegue**: los kilómetros del escalón o su fecha (**fecha de entrega + los meses del escalón**). Sin fecha de entrega
  (`maintenance_plans.baseline_date`) solo se vigilan los kilómetros y la pantalla la pide.
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

## Avisos (`services/maintenance_alerts.py`)

El poller revisa cada 200 km o cada 6 h (los plazos en meses vencen sin que el coche se mueva) y manda cada aviso como mucho **una vez al día**:
en modo oficial (solo coches EBRO), la próxima revisión si está `warning`/`due`; en modo personalizado (o con otra marca), cada tarea propia `warning`/`due`. El texto dice km y/o días.
Se activan en Ajustes → Avisos → «Mantenimiento oficial».

## Migración

Las tareas que ya existían pasan a `custom` (columna `origin`, valor por defecto), con sus fechas e historial. Los registros antiguos de «revisión
oficial» siguen en el historial pero **no cuentan como escalón hecho** (no tenían `revision_km`): se vuelve a registrar el último escalón hecho.
