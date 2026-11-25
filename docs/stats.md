# Estadísticas

**Más → Estadísticas** (`#tabStats`, `features/stats.js`, `css/features/stats.css`) dibuja lo que el historial dice del coche, sin Grafana ni librerías: nueve gráficas en canvas con el color de acento del tema, para el periodo elegido con el selector común (12 meses, este año, año pasado, todo, o unos meses concretos). Todo lo calcula el servidor en `services/stats.py` (`GET /api/vehicles/{vin}/stats?period=12m|year|last_year|all` o `period=custom&from=AAAA-MM&to=AAAA-MM`, nivel `view`; ver «Periodos» en [frontend.md](frontend.md)); el navegador solo pinta.

| Gráfica | De dónde sale |
| :--- | :--- |
| Kilómetros por mes | suma de `distance_km` de los viajes, apilada en eléctricos (`ev_km`), con gasolina (`fuel_km`) y sin dato (viajes sin el cuentakilómetros EV en los dos extremos) |
| Energía por mes | kWh añadidos en cargas y litros repostados |
| Gasto por mes y coste por km | cargas + repostajes + gastos + intervenciones; €/km solo en meses con ≥ 20 km |
| Consumo eléctrico por mes | kWh/100 km de los viajes **sin gasolina** de ≥ 2 km con energía medida (ver «Viajes con gasolina») |
| Salud de la batería | capacidad útil **medida** en cada carga de ≥ 20 puntos con la curva completa: energía que entró en la batería (integral de la potencia de `charge_points`) ÷ puntos ganados × 100, media por mes; encima, la media de las 5 últimas frente a la capacidad de la ficha (`battery_capacity`). `services/battery_capacity.py` |
| Consumo según la velocidad | tramos de 10 km/h de `speed_avg_kmh` |
| Dónde se carga | kWh, cargas y coste por dirección (ocho primeras) |
| Gasto en reposo | puntos de batería perdidos por día entre el final de un viaje y el siguiente, con más de 6 h parado y sin carga entre medias |

`totals` resume el periodo (km, kWh cargados, litros, gasto, €/km, viajes, cargas, `ev_km` y `ev_pct`, los km en eléctrico y su porcentaje, y `kwh_per_100km`, el consumo eléctrico de los viajes solo eléctricos de ≥ 2 km con energía medida); la pestaña Energía lo pide con `period=30d` para su ficha «Consumo EV · 30 días».

Límites honestos: los kWh de viajes y cargas son estimaciones a partir del porcentaje de batería y la capacidad de la ficha (ver [costs.md](costs.md)), pero la «salud de la batería» no los usa: hasta el 01-11-2026 dividía esos kWh estimados entre los puntos ganados y devolvía siempre la capacidad de la ficha. Ahora integra la potencia que el coche manda en cada punto de la curva (cada ~2 min). Medido en cargas reales: 36,3 y 35,6 kWh en un S900 de 34,5 kWh de ficha y 19,5 kWh en un coche de 19,27. Salir un poco por encima de la ficha es normal (la energía se mide en la batería e incluye sus pérdidas internas), y el porcentaje llega en puntos enteros (±5 % en una carga de 20 puntos): sirve para ver la tendencia con los meses, no para una cifra exacta. Una carga con un hueco de más de 10 min en la curva no cuenta. El gasto en reposo mezcla consumo propio y lo que gaste el coche al despertarlo; es una tendencia, no una medida. Informes periódicos por aviso se descartaron (decisión del dueño, 2026-10-30). La gráfica de **consumo según la temperatura** se quitó el 2026-10-31: el coche no informa de la temperatura exterior (`outside_temp_avg` de los viajes queda siempre vacío), así que nunca se rellenaba; la columna sigue en `drives` por si algún día llega el dato o se toma de un servicio meteorológico.

## Kilómetros en eléctrico y viajes con gasolina (2026-11-10)

El coche lleva un **cuentakilómetros EV** aparte del total: los km hechos con el motor de gasolina apagado (`electricRange` en sus lecturas, `ev_odometer_km` en EVbro; en el S900 del autor, 6.645 de 10.977 km el 10-11-2026, contrastado con el historial de Home Assistant). Cuenta en km enteros, como el total.

- **Se guarda por viaje, se enseña solo en sumas.** El detector de viajes guarda los dos cuentakilómetros al empezar y al terminar (`drives.start_ev_odometer` / `end_ev_odometer`): la última lectura parada antes de salir y la primera ya parado al llegar. Pero los dos contadores van en km enteros y **avanzan desfasados unos 500 m** (lecturas del 11 y 12-11-2026: en marcha sube el EV, medio km después el total, y así sucesivamente), así que un viaje todo en eléctrico puede dar un km más o uno menos en el EV que en el total (3 km con 2 o con 4 eléctricos). Por viaje es ruido: no se muestra ni sirve para juzgar un viaje (decisión del dueño, 12-11-2026). En una suma de muchos viajes esos errores se compensan. La migración `0029` rellenó los viajes anteriores con las lecturas guardadas (`telemetry_snapshots.raw_data`, un año por defecto) y la `0030` corrigió el final, que a veces había tomado una lectura aún en marcha.
- **Por mes y periodo**: km eléctricos, km con gasolina y porcentaje, solo de los viajes con el dato.
- **Desde nuevo**: Estadísticas muestra el cociente de los dos cuentakilómetros de la última lectura; el libro del coche, el del último viaje.

**Viajes con gasolina fuera del consumo eléctrico.** Cuando el motor de gasolina funciona, también recarga la batería por el camino, así que los puntos de batería gastados se quedan cortos y el kWh/100 km sale más bajo de lo real (observación del dueño, 10-11-2026). Por eso el consumo eléctrico (Estadísticas, resumen de Viajes, ficha «Consumo EV · 30 días», viajes largos y libro del coche) cuenta solo los viajes **solo eléctricos** (`electric_only`): los que no gastaron gasolina del depósito. El cuentakilómetros EV no sirve para esto (ver arriba), y el depósito va en puntos de % (unos 0,5 l): un viaje en que el motor funcionó muy poco puede pasar por eléctrico. En el detalle de un viaje con gasolina el consumo eléctrico sale como «Sin medir».

