# Despliegue

## Consola de administración

El administrador del servidor entra por **otro puerto** (`ADMIN_WEB_PORT`, 8082) con otra interfaz, pensada para un ordenador. En una instalación nueva, la primera vez pide el **código de configuración** (`docker compose logs app`, o `SETUP_CODE`) para crear el administrador. El administrador no tiene coches: crea gestores (que son quienes tienen los coches), invitaciones y fija los límites, la retención del historial y el sondeo. Para usarla desde el ordenador pon `ADMIN_BIND_ADDRESS` a la IP de la LAN del servidor y abre ese puerto solo a tu red (firewall); **no lo publiques en Caddy/proxy**.

## Requisitos

Docker + Compose v2. Recursos mínimos orientativos: **~300 MB de RAM** (app ≈ 120 MB + PostgreSQL ≈ 60 MB, más la compilación de la imagen) y 1–2 GB de disco.

## Con Docker Compose

```bash
cd deploy
cp .env.example .env            # y rellenar el primer bloque (contraseña de la BD, ENCRYPTION_KEY, zona horaria, aviso legal)
docker compose up -d --build
docker compose logs app | grep "código de configuración"   # código para crear el administrador (consola, puerto 8082)
```
El nombre del proyecto es fijo (`evbro`): los volúmenes (`evbro_evbro_data` con tokens, facturas y claves; `evbro_evbro_postgres_data`) no dependen de la carpeta. La imagen se construye desde la raíz del repositorio.

### Todas las variables

`.env.example` solo trae las que hay que rellenar; el resto tiene su valor por defecto en `docker-compose.yml` y se puede fijar en `.env` sin tocar el YAML:

| Variable | Por defecto | Para qué |
| :--- | :--- | :--- |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `evbro` / — / `evbro` | Base de datos interna (solo la contraseña hay que ponerla) |
| `ENCRYPTION_KEY` | vacío | Cifra en la BD la contraseña y el PIN de Ebro, tokens y canales de aviso (`openssl rand -base64 32`). Vacía = se crea una en el volumen de datos (`data/secret.key`); perderla obliga a reconectar la cuenta Ebro |
| `SETUP_CODE` | vacío | Código para crear el administrador; vacío = uno aleatorio en el log |
| `SESSION_DAYS` | `30` | Días que dura la sesión en un dispositivo |
| `COOKIE_SECURE` / `FORWARDED_ALLOW_IPS` / `ALLOWED_ORIGINS` / `BIND_ADDRESS` / `WEB_PORT` | ver «HTTPS y publicación» | Proxy inverso y publicación |
| `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGIN` | vacío (= el nombre con el que se abre la web) | Acceso con huella si el proxy no reenvía el `Host` |
| `TIMEZONE` | — | Zona IANA de la carga programada y las horas |
| `VAPID_SUBJECT` | — | Contacto declarado a Google/Apple en Web Push (`mailto:…`) |
| `LEGAL_OPERATOR` / `LEGAL_CONTACT` | vacío | Página `/legal` |
| `ANDROID_ASSETLINKS_JSON` | vacío | App Android (TWA): el JSON de `assetlinks` en una línea |
| `ADMIN_BIND_ADDRESS` / `ADMIN_WEB_PORT` | `127.0.0.1` / `8082` | Consola de administración (solo red local) |
| `DEFAULT_VEHICLE_LIMIT` / `DEFAULT_MEMBER_LIMIT` / `DEFAULT_STORAGE_MB` / `INVITE_DEFAULT_DAYS` | `2` / `5` / `50` / `14` | Límites de un gestor nuevo y caducidad de las invitaciones |
| `ALLOW_PRIVATE_WEBHOOKS` | `false` | Webhooks hacia direcciones privadas (p. ej. Home Assistant en la LAN) |
| `CATALOG_DEFAULT_URL` | `https://catalog.evbro.app` | Catálogo del proyecto: al que se da de alta el botón «Conectar» (y la pregunta del primer administrador) y el que se usa cuando se pega solo la clave (`ebc_…`) |
| `CATALOG_OPEN_SIGNUP` | `false` | Solo el servidor del proyecto: deja que otras instalaciones se den de alta solas en su catálogo (`POST /api/catalog/online/signup`) |
| `TELEMETRY_URL` | `https://data.evbro.app` | Adónde van los informes anónimos **si el administrador los activa** en la consola; vacío = nunca se envían ([telemetry.md](telemetry.md)) |
| `TELEMETRY_RECEIVER` | `false` | Solo el servidor del proyecto: recibe los informes de otras instalaciones y los muestra en la pestaña Informes de su consola |
| `POLL_INTERVAL_PARKED_MIN` / `_PLUGGED_MIN` / `_CHARGING_MIN` / `_DRIVING_MIN` / `_DRIVING_IDLE_MIN` | `60` / `30` / `2` / `1` / `3` | Cada cuánto se consulta la nube según el estado (0 = no sondear). No despierta el coche, pero compite con la app oficial: no bajarlos sin motivo |
| `CAR_MQTT_ENABLED` / `CAR_MQTT_HOST` / `CAR_MQTT_PORT` | `false` / nube de Ebro / `8083` | Canal en directo con el coche (puertas, cierre, cable, motor, confirmaciones). Solo la cuenta propietaria; la nube admite un cliente por cuenta |
| `MQTT_ENABLED` / `MQTT_HOST` / `MQTT_PORT` / `MQTT_USER` / `MQTT_PASSWORD` / `MQTT_TOPIC_PREFIX` / `MQTT_PUBLISH_SCOPE` / `MQTT_HA_DISCOVERY` / `MQTT_HA_PREFIX` | `false` / `mosquitto` / `1883` / — / — / `evbro` / `owner` / `true` / `homeassistant` | Publicar los coches en tu broker MQTT (Home Assistant los descubre). Cada gestor puede conectar el suyo desde Ajustes → Cuenta |
| `EVBRO_TAG` / `EVBRO_IMAGE_OWNER` / `EVBRO_COMMIT` | `latest` / `jcortizronda` / vacío | Imagen a usar y commit que se muestra en Ajustes → Sistema |

**Versión y compilación.** Ajustes → Sistema muestra «EVBro <versión>» (`__version__` en `backend/src/app/__init__.py`, igual que `backend/pyproject.toml`) y, si se conoce, el commit del que sale la imagen (`GET /api/version`). El commit entra como argumento de construcción `EVBRO_COMMIT` (`ARG`/`ENV` en `backend/Dockerfile`, `build.args` en `deploy/docker-compose.yml`): `EVBRO_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`. Sin repositorio en el servidor se puede poner `EVBRO_COMMIT=<hash>` en `deploy/.env`.

## Instalar con la imagen pública (sin el código)

Cada push a `main` y cada etiqueta `vX.Y.Z` publican la imagen en el registro de GitHub (`.github/workflows/image.yml`): `ghcr.io/jcortizronda/evbro:latest`, `:vX.Y.Z` y `:sha-<commit>`. El paquete nace privado: la primera vez hay que hacerlo público en GitHub → Packages → evbro → *Package settings* → *Change visibility*. Con eso, quien quiera autoalojar EVBro solo necesita dos ficheros de `deploy/` (`docker-compose.yml` y `.env.example` copiado a `.env`), Docker y PostgreSQL del propio compose:

```bash
mkdir evbro && cd evbro
curl -fsSLO https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/.env.example -o .env   # y editarlo
docker compose pull && docker compose up -d      # sin --build: no hay código que construir
docker compose logs app | grep "código de configuración"
```

`EVBRO_TAG` en `.env` fija la versión (`latest` o `v1.2.0`); actualizar es `docker compose pull && docker compose up -d`. El mismo `docker-compose.yml` sirve para las dos formas de instalar: con el repositorio clonado, `docker compose up -d --build` construye la imagen en local en vez de descargarla. El código está dentro de la imagen igualmente (es GPL): la imagen evita construir, no esconde nada.

## Paquete para servidores sin acceso al repositorio

```bash
tools/package.sh          # → dist/evbro-<fecha>-<commit>.tgz (backend/src, frontend, deploy, docs; sin tests ni secretos)
# en el servidor
mkdir -p /opt/evbro && tar xzf evbro-*.tgz -C /opt/evbro
cd /opt/evbro/deploy && docker compose up -d --build
```

## HTTPS y publicación

EVBro **no termina TLS**: va detrás de un proxy inverso. Necesita HTTPS válido para dos cosas: la cookie de sesión `Secure` y las **notificaciones Web Push** (el navegador solo ofrece `PushManager` en HTTPS o `localhost`).

Variables en `deploy/.env` cuando hay proxy:

| Variable | Valor | Para qué |
| :--- | :--- | :--- |
| `COOKIE_SECURE` | `true` | Cookie solo por HTTPS |
| `FORWARDED_ALLOW_IPS` | IP/red del proxy tal y como la ve el contenedor | Que se vea la IP real del cliente (límite de intentos) y se detecte HTTPS. **Nunca `*` con el puerto abierto a internet** |
| `BIND_ADDRESS` | `127.0.0.1` si el proxy está en la misma máquina; la IP de la LAN si está en otra | Dónde escucha el puerto publicado |
| `WEB_PORT` | p. ej. `8081` | Puerto del host |
| `TIMEZONE` | `Europe/Madrid` | Horas de la carga programada |
| `VAPID_SUBJECT` | `mailto:tu@dominio` | Contacto declarado a Google/Apple en Web Push |
| `ADMIN_BIND_ADDRESS` / `ADMIN_WEB_PORT` | `127.0.0.1` / `8082` | Dónde se publica la **consola de administración** (otra interfaz, otro puerto). **Solo red local; nunca detrás del proxy público.** Para abrirla desde tu ordenador, la IP de la LAN del servidor |
| `ALLOW_PRIVATE_WEBHOOKS` | `false` | Permite que los webhooks de los gestores apunten a direcciones privadas (p. ej. Home Assistant). El servidor es quien llama |
| `DEFAULT_VEHICLE_LIMIT` / `DEFAULT_MEMBER_LIMIT` | `2` / `5` | Coches y cuentas asociadas con los que empieza un gestor invitado (el administrador los cambia por cuenta) |
| `DEFAULT_STORAGE_MB` | `50` | Espacio (MB) para documentos, facturas y fotos de los coches de un gestor sin límite propio (-1 sin límite). El administrador lo cambia por cuenta |

Con `docker-proxy` (lo habitual) el contenedor ve las conexiones como llegadas desde la red de Docker, así que `FORWARDED_ALLOW_IPS` debe cubrir esa red (p. ej. `172.16.0.0/12`). Quien llegue directamente al puerto publicado podría falsear `X-Forwarded-For` y esquivar el límite de intentos **por IP** (el límite por IP+usuario sigue vigente): restringe ese puerto al proxy con el firewall.

### Ejemplo con Caddy

```caddyfile
evbro.example.com {
	request_body {
		max_size 20MB          # la subida más grande que acepta la app son 15 MB; el proxy corta antes lo demás
	}
	reverse_proxy 192.168.1.50:8081 {
		header_up Host {host}
		header_up X-Real-IP {remote_host}
	}
}
```
(Caddy añade `X-Forwarded-For` y `X-Forwarded-Proto` por sí solo.) Para dejarlo **solo en la red local**, añade antes del `reverse_proxy` una restricción por IP de origen (`@fuera not remote_ip 192.168.0.0/16` → `abort @fuera`). Ver `docs/security.md` («Para exponerlo a internet»).

## Primer arranque

1. Abre la URL pública, crea el usuario de EVBro con el **código de configuración** del log.
2. **Ajustes → Cuenta**: conecta tu teléfono, contraseña y PIN de Ebro (se guardan cifrados).
3. **Ajustes → Avisos → Activar en este dispositivo** para las notificaciones.

## Copias de seguridad

**Automática:** `deploy/backup.sh` hace cada madrugada el volcado de la BD, un archivo del **volumen de datos** (documentos, facturas y fotos cifrados, tokens, clave VAPID) y una copia de `deploy/.env` (lleva la clave de cifrado) en `backups/`; borra lo de más de 14 días y limpia los restos de construcción de Docker de más de 3 días (cada despliegue deja cientos de MB). Se programa con `cron` (`0 4 * * * /opt/evbro/deploy/backup.sh >> /opt/evbro/backups/backup.log 2>&1`).

**Fuera del servidor (obligatorio en cuanto haya datos de otra persona):** `backups/` vive dentro del mismo LXC; si se pierde el contenedor se pierde todo. Dos formas, la primera basta:
1. **Copia del contenedor desde Proxmox:** Datacenter → Backup → Add: CT 112, cada día a las 05:00 (después del `backup.sh`), modo *snapshot*, almacenamiento que no sea `local` (NAS por NFS/CIFS o Proxmox Backup Server), conservar 7. Incluye `/var/lib/docker` (volúmenes) y `/opt/evbro/backups`.
2. **Solo la carpeta de copias:** `rsync -a /opt/evbro/backups/ usuario@nas:/copias/evbro/` en otra línea de `cron`, después de las 04:00.
En los dos casos la carpeta contiene la clave de cifrado (`env-*`): el destino debe ser tan privado como el servidor.

Hacen falta **tres cosas juntas**; sin la clave, la copia de la base de datos no sirve, y tampoco los **ficheros subidos** (documentos, facturas y fotos se guardan cifrados en el volumen de datos con la misma clave: sin `ENCRYPTION_KEY` o `secret.key` no se pueden abrir):

```bash
# 1) Base de datos
docker exec evbro-db pg_dump -U evbro evbro | gzip > evbro-db-$(date +%F).sql.gz
# 2) Volumen de datos (tokens, documentos/facturas/fotos cifrados, certificados, vapid_private.pem, secret.key si no usas ENCRYPTION_KEY)
docker run --rm -v evbro_evbro_data:/d -v "$PWD":/b alpine tar czf /b/evbro-data-$(date +%F).tgz -C /d .
# 3) ENCRYPTION_KEY (deploy/.env): en un gestor de contraseñas, fuera del servidor
```
Restaurar: `gunzip -c evbro-db-*.sql.gz | docker exec -i evbro-db psql -U evbro evbro` y desempaquetar el volumen. Si se pierde `vapid_private.pem`, hay que reactivar las notificaciones en cada dispositivo.

## Aviso legal y privacidad

`/legal` es pública y la enlazan el registro y Ajustes. Pon en el `.env` `LEGAL_OPERATOR` (quién responde de este servidor) y `LEGAL_CONTACT` (correo para pedir o borrar datos); sin ellos la página habla de «el administrador de este servidor». Si vas a invitar a gente que no conoces, léela entera antes y consulta [hosted-service.md](hosted-service.md), «Cobrar por el servicio».

## App para Android (TWA)

La PWA ya se instala desde Chrome («Instalar»). Para tener además un **APK** (Play Store o instalación directa) sin reescribir nada, se empaqueta la web como *Trusted Web Activity* con [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap):

```bash
npm i -g @bubblewrap/cli
bubblewrap init --manifest https://evbro.ejemplo.es/manifest.json     # pide nombre del paquete (p. ej. es.ejemplo.evbro) y crea la clave de firma
bubblewrap build                                                       # app-release-signed.apk + la huella SHA-256 de la clave
```

Al terminar, Bubblewrap imprime el **assetlinks.json**. Pégalo en una sola línea en `ANDROID_ASSETLINKS_JSON` del `.env` y reinicia: EVBro lo sirve en `/.well-known/assetlinks.json` (público, con caché de una hora). Sin él, Android abre la app con la barra del navegador. Requiere HTTPS con el mismo dominio que `start_url` del manifiesto. **No se ha generado ni probado un APK todavía**: es la receta, no un paquete publicado.

## Sin conexión

El Service Worker guarda la última respuesta buena de las lecturas que pintan la aplicación (`/api/vehicles…`, `/api/geofences…`, `/api/auth/status`…). Sin red, la aplicación abre con esos datos y una franja amarilla «Sin conexión. Se muestran los últimos datos guardados (de las HH:MM)»; las órdenes y los guardados no se encolan: fallan con su aviso. Al volver la red la franja desaparece con la primera lectura fresca.

## Actualizar

```bash
cd /opt/evbro/deploy      # tras descomprimir el paquete nuevo encima (el .env y los volúmenes se conservan)
docker compose up -d --build
docker compose logs --tail 30 app
```
El esquema de la BD se migra solo al arrancar (Alembic, `upgrade head`; una BD anterior al 29-10-2026 se marca en la revisión base en el primer arranque; ver [development.md](development.md)). Haz `pg_dump` antes de actualizar: una migración no se deshace sola.

El contenedor **no corre como root**: `entrypoint.sh` arranca como root solo para hacer del usuario `app` el dueño del volumen `data/` (una instalación anterior lo tenía de root) y baja privilegios antes de arrancar la app. No hay que hacer nada a mano al actualizar.
