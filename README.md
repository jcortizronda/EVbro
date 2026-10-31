# EVBro

**El compañero de tu coche.** Plataforma **autoalojada** y de código abierto para monitorizar, registrar y controlar coches **Ebro** (S700, S800, S900 PHEV…) sin suscripciones. Habla con la nube de Ebro con el mismo protocolo que la app oficial y junta en un solo sitio el control del coche, su historial completo y su mantenimiento.


## Qué hace

- **Inicio en vivo:** batería, autonomía, gasolina, odómetro, cierre, puertas, ventanillas, techo, neumáticos y mapa (y la de un **localizador GPS** propio vía Traccar, si el coche lo lleva).
- **Control remoto seguro:** cierre, maletero, ventanillas, techo, clima, asientos, volante, desempañado, alarma y «localizar». Lo que **abre** el coche pide el PIN en cada envío. **Climatización programada** por hora y días.
- **Energía:** objetivo de batería, parar/iniciar y programar la carga usando solo la programación del coche (Ebro no deja arrancar ni parar directamente), curva y coste de cada carga.
- **Históricos:** viajes con ruta (reproducir, GPX), lugares frecuentes, geocercas con tarifa (fija, gratuita, 2.0TD o PVPC) y su registro de entradas y salidas, repostajes con **consumo real** entre llenados, gastos y balance de costes.
- **Mantenimiento:** plan oficial de EBRO o personalizado, ITV, intervenciones con factura, consumibles y referencias; ficha del coche con seguro y documentación.
- **Avisos:** Web Push (Google FCM en Android), Telegram, Pushover y webhook.
- **Varias cuentas:** gestor con sus coches, cuentas asociadas por nivel (ver, anotar, controlar), invitaciones y una consola de administración en otro puerto.
- **PWA:** móvil (dock, claro/oscuro, instalable, funciona sin conexión con los últimos datos) y ordenador (barra lateral y dos columnas).
- **Tus datos son tuyos:** exporta toda la cuenta en un ZIP (e impórtala en otro servidor), descarga el **libro del coche** en PDF con las secciones que elijas, y una página `/legal` dice sin letra pequeña qué se guarda y quién lo ve.

## Estructura

```
backend/    FastAPI + SQLAlchemy + Alembic (Python 3.11), tests y Dockerfile
frontend/   PWA sin compilación (HTML + CSS + JS por áreas), servida por el backend
deploy/     docker-compose.yml, .env.example y backup.sh
docs/       instalación, manual, arquitectura, seguridad, desarrollo, despliegue
tools/      pruebas de navegador (Playwright) y utilidades
```

## Puesta en marcha (Docker Compose)

```bash
cd deploy
cp .env.example .env
echo "ENCRYPTION_KEY=$(openssl rand -base64 32)" >> .env   # haz copia de este valor fuera del servidor
docker compose up -d
docker compose logs app | grep "código de configuración"
```
Abre `http://localhost:8080` (o el `WEB_PORT` que fijes), crea el administrador en la consola (`http://IP:8082`) con ese código, crea tu usuario gestor y conecta tu cuenta Ebro en **Ajustes → Cuenta**. Para publicarlo con HTTPS, las notificaciones, las copias de seguridad y la app de Android: [docs/deployment.md](docs/deployment.md).

## Instalar sin el código (imagen pública)

```bash
mkdir evbro && cd evbro
curl -fsSLO https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/.env.example -o .env
docker compose pull && docker compose up -d
```
Detalles y actualización en [docs/deployment.md](docs/deployment.md).

## Documentación

- **[Instalar paso a paso](docs/instalacion.md)** ([English](docs/en/install.md)), para quien no ha montado nunca un servidor.
- **[Manual de usuario](docs/manual.md)** ([English](docs/en/manual.md)): todas las pantallas y lo que hace cada una.

| | |
| :--- | :--- |
| [docs/architecture.md](docs/architecture.md) | Componentes, almacenamiento y stack |
| [docs/ebro-protocol.md](docs/ebro-protocol.md) | Protocolo de la nube Ebro/Chery y control de la carga |
| [docs/security.md](docs/security.md) | Acceso, secretos, comandos y exposición a internet |
| [docs/trips.md](docs/trips.md) · [docs/traccar.md](docs/traccar.md) | Detección de viajes, ruta y posición por GPS (Traccar) |
| [docs/costs.md](docs/costs.md) · [docs/maintenance.md](docs/maintenance.md) · [docs/parts.md](docs/parts.md) | Repostajes, gastos y costes; mantenimiento; consumibles |
| [docs/frontend.md](docs/frontend.md) | Organización y convenciones de la interfaz |
| [docs/development.md](docs/development.md) | Entorno local, tests y convenciones |
| [docs/deployment.md](docs/deployment.md) | Despliegue, HTTPS, copias de seguridad, APK |
| [docs/hosted-service.md](docs/hosted-service.md) | Varias cuentas, invitaciones y consola de administración |
| [docs/decisions.md](docs/decisions.md) · [docs/roadmap.md](docs/roadmap.md) | Decisiones, límites conocidos y futuro |

Toda la API exige sesión salvo el acceso (`/api/auth/*`) y `/health`. La documentación interactiva está desactivada.

## Qué no está probado todavía

Se ha construido contra un único coche real y hay partes que **no se han validado** con él o con los servicios reales: el control de la carga por programación, el canal MQTT del coche, la alarma, la climatización programada, el envío real de Web Push, la detección de repostajes y la conexión con un servidor Traccar. La documentación lo dice en cada sitio; si lo pruebas, abre una incidencia con lo que viste.

## Licencia

GNU GPL v3 o posterior (ver [LICENSE](LICENSE)): puedes usarlo, estudiarlo y modificarlo; si lo redistribuyes, modificado o no, tiene que ser bajo la misma licencia y con el código. Uso personal en tu propio vehículo y cuenta. No afiliado con Ebro ni con Chery Automobile.
