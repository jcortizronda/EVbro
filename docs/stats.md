# Estadísticas

**Más → Estadísticas** (`#tabStats`, `features/stats.js`, `css/features/stats.css`) dibuja lo que el historial dice del coche, sin Grafana ni librerías: nueve gráficas en canvas con el color de acento del tema, para el periodo elegido con el selector común (12 meses, este año, año pasado, todo, o unos meses concretos). Todo lo calcula el servidor en `services/stats.py` (`GET /api/vehicles/{vin}/stats?period=12m|year|last_year|all` o `period=custom&from=AAAA-MM&to=AAAA-MM`, nivel `view`; ver «Periodos» en [frontend.md](frontend.md)); el navegador solo pinta.

| Gráfica | De dónde sale |
| :--- | :--- |
| Kilómetros por mes | suma de `distance_km` de los viajes |
| Energía por mes | kWh añadidos en cargas y litros repostados |
| Gasto por mes y coste por km | cargas + repostajes + gastos + intervenciones; €/km solo en meses con ≥ 20 km |
| Consumo eléctrico por mes | kWh/100 km de los viajes de ≥ 2 km con energía medida |
| Salud de la batería | capacidad útil observada en cada carga de ≥ 30 puntos: kWh añadidos ÷ puntos ganados × 100, media por mes |
| Consumo según la velocidad | tramos de 10 km/h de `speed_avg_kmh` |
| Dónde se carga | kWh, cargas y coste por dirección (ocho primeras) |
| Gasto en reposo | puntos de batería perdidos por día entre el final de un viaje y el siguiente, con más de 6 h parado y sin carga entre medias |

`totals` resume el periodo (km, kWh cargados, litros, gasto, €/km, viajes, cargas y `kwh_per_100km`, el consumo eléctrico de los viajes de ≥ 2 km con energía medida); la pestaña Energía lo pide con `period=30d` para su ficha «Consumo EV · 30 días».

Límites honestos: los kWh de viajes y cargas son estimaciones a partir del porcentaje de batería y la capacidad de la ficha (ver [costs.md](costs.md)), así que la «salud de la batería» mide la coherencia de esa estimación más que la degradación real; con pocas cargas grandes la línea baila. El gasto en reposo mezcla consumo propio y lo que gaste el coche al despertarlo; es una tendencia, no una medida. Informes periódicos por aviso se descartaron (decisión del dueño, 2026-10-30). La gráfica de **consumo según la temperatura** se quitó el 2026-10-31: el coche no informa de la temperatura exterior (`outside_temp_avg` de los viajes queda siempre vacío), así que nunca se rellenaba; la columna sigue en `drives` por si algún día llega el dato o se toma de un servicio meteorológico.
