# Pruebas de navegador (Playwright)

Complementan a `backend/tests/` (que solo comprueban la *forma* del frontend): ejecutan la interfaz real en Chromium,
con la API simulada donde hace falta, a 390 px, en tema claro y oscuro.

```bash
pip install playwright && playwright install chromium     # una vez
tools/e2e/demo_server.sh start                             # servidor aislado con datos de demo (SQLite)
python tools/e2e/charge.py [carpeta_capturas]              # Energía: objetivo deslizante, parar/iniciar, programación
python tools/e2e/nav.py                                    # navegación según dispositivo: táctil → dock inferior; ratón → barra lateral; sin zoom
python tools/e2e/map.py                                    # mapa de la portada: ampliar, centrar, «Ir a», «atrás»
python tools/e2e/maintenance.py                            # mantenimiento: plan oficial/personalizado, fecha de entrega, escalones y tareas propias
python tools/e2e/profile_costs.py                             # ficha del coche, Gastos, Costes, repostajes y cabecera
python tools/e2e/parts.py                                     # consumibles y referencias: ficha, buscador y piezas de las intervenciones
python tools/e2e/documents.py                                 # documentos del coche: factura de compra, póliza (varios con título), otros; abrir y borrar
python tools/e2e/accounts.py                                  # cuentas asociadas: crear, compartir un coche con un nivel y ver qué se oculta
python tools/e2e/admin.py                                    # consola de administración (puerto aparte): gestores, invitaciones, límites, ajustes del servidor
python tools/e2e/overlays.py                               # modales y hojas de acciones sin backdrop-filter en táctil
python tools/e2e/home.py                                   # Inicio: fichas, ventanillas, modelo y estado de la programación
python tools/e2e/passkeys.py                               # acceso con huella (autenticador virtual): alta, entrar, quitar. Usa http://localhost:PUERTO, no la IP
python tools/e2e/history.py                                # historial de viajes y cargas: filas desplegables, meses y «Ver más» (datos simulados)
python tools/e2e/csp.py                                    # recorre todas las pestañas y falla si el navegador registra una violación de la Content-Security-Policy
python tools/e2e/climate_schedule.py                       # climatización programada: crear, pausar y borrar una programación desde Confort
python tools/e2e/catalog.py                                # catálogo compartido: proponer una pieza, aprobarla en la consola y verla sugerida
python tools/e2e/home_widgets.py                           # Inicio por widgets: mover (flechas y arrastrar), quitar, añadir, guardar en el servidor y restablecer
python tools/e2e/page_widgets.py                           # Energía y Confort por widgets, y los atajos del icono (?go=…)
python tools/e2e/comfort.py                                # Confort: estado ON/OFF leído del coche, iconos de estado
python tools/e2e/timeline.py                               # cronología del coche (todos los tipos mezclados, chips) y buscador global
python tools/e2e/stats.py                                  # estadísticas: diez gráficas pintadas con la demo, periodo, sin desbordes
python tools/mobile_check.py                               # ninguna pantalla se sale a 360/390/430 px
python tools/desktop_check.py --shots /tmp/desk               # ordenador: barra lateral, sin dock, sin desbordes a 1024/1366/1920 px
tools/e2e/demo_server.sh stop
```
La demo crea un gestor (`juan`, dueño del coche de demo) y, por la consola, un administrador (`admin` / `contraseña-del-admin-1`, puerto 8098).
Variables: `EVBRO_ADMIN_URL`, `EVBRO_URL`, `EVBRO_USER`, `EVBRO_PASSWORD` (por defecto, el servidor de demo). Cada script termina con
`FAILURES: none` o la lista de comprobaciones que fallan (código de salida ≠ 0).

Las pruebas que simulan la API con `page.route` crean el contexto con `service_workers="block"`: desde el modo sin conexión el Service Worker atiende las lecturas de `/api/vehicles…` y Playwright no intercepta lo que pide un Service Worker.
