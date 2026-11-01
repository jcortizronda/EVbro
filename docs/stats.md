# Estadísticas

**Más → Estadísticas** (`#tabStats`, `features/stats.js`, `css/features/stats.css`) dibuja lo que el historial dice del coche, sin Grafana ni librerías: nueve gráficas en canvas con el color de acento del tema, para el periodo elegido con el selector común (12 meses, este año, año pasado, todo, o unos meses concretos). Todo lo calcula el servidor en `services/stats.py` (`GET /api/vehicles/{vin}/stats?period=12m|year|last_year|all` o `period=custom&from=AAAA-MM&to=AAAA-MM`, nivel `view`; ver «Periodos» en [frontend.md](frontend.md)); el navegador solo pinta.

| Gráfica | De dónde sale |
| :--- | :--- |
| Kilómetros por mes | suma de `distance_km` de los viajes |
| Energía por mes | kWh añadidos en cargas y litros repostados |
| Gasto por mes y coste por km | cargas + repostajes + gastos + intervenciones; €/km solo en meses con ≥ 20 km |
| Consumo eléctrico por mes | kWh/100 km de los viajes de ≥ 2 km con energía medida |
| Salud de la batería | capacidad útil **medida** en cada carga de ≥ 20 puntos con la curva completa: energía que entró en la batería (integral de la potencia de `charge_points`) ÷ puntos ganados × 100, media por mes; encima, la media de las 5 últimas frente a la capacidad de la ficha (`battery_capacity`). `services/battery_capacity.py` |
| Consumo según la velocidad | tramos de 10 km/h de `speed_avg_kmh` |
| Dónde se carga | kWh, cargas y coste por dirección (ocho primeras) |
| Gasto en reposo | puntos de batería perdidos por día entre el final de un viaje y el siguiente, con más de 6 h parado y sin carga entre medias |

`totals` resume el periodo (km, kWh cargados, litros, gasto, €/km, viajes, cargas y `kwh_per_100km`, el consumo eléctrico de los viajes de ≥ 2 km con energía medida); la pestaña Energía lo pide con `period=30d` para su ficha «Consumo EV · 30 días».

Límites honestos: los kWh de viajes y cargas son estimaciones a partir del porcentaje de batería y la capacidad de la ficha (ver [costs.md](costs.md)), pero la «salud de la batería» no los usa: hasta el 01-11-2026 dividía esos kWh estimados entre los puntos ganados y devolvía siempre la capacidad de la ficha. Ahora integra la potencia que el coche manda en cada punto de la curva (cada ~2 min). Medido en cargas reales: 36,3 y 35,6 kWh en un S900 de 34,5 kWh de ficha y 19,5 kWh en un coche de 19,27. Salir un poco por encima de la ficha es normal (la energía se mide en la batería e incluye sus pérdidas internas), y el porcentaje llega en puntos enteros (±5 % en una carga de 20 puntos): sirve para ver la tendencia con los meses, no para una cifra exacta. Una carga con un hueco de más de 10 min en la curva no cuenta. El gasto en reposo mezcla consumo propio y lo que gaste el coche al despertarlo; es una tendencia, no una medida. Informes periódicos por aviso se descartaron (decisión del dueño, 2026-10-30). La gráfica de **consumo según la temperatura** se quitó el 2026-10-31: el coche no informa de la temperatura exterior (`outside_temp_avg` de los viajes queda siempre vacío), así que nunca se rellenaba; la columna sigue en `drives` por si algún día llega el dato o se toma de un servicio meteorológico.
