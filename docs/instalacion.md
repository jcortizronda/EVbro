# Instalar EVBro paso a paso (sin saber de Docker)

*English version: [docs/en/install.md](en/install.md).*

Esta guía es para quien quiere tener **EVBro, el compañero de tu coche**, en su casa y no ha montado nunca un servidor. No hace falta tocar el código ni compilar nada: EVBro se distribuye como una **imagen** lista para usar y se arranca con dos ficheros. Si algo no sale como se describe, copia el mensaje de error tal cual y pregunta en el repositorio ([github.com/jcortizronda/EVbro](https://github.com/jcortizronda/EVbro)).

Tiempo estimado: 20 minutos la primera vez.

## 1. Qué hace falta

- **Un ordenador que esté siempre encendido** en casa: un mini PC, una Raspberry Pi 4 o 5 (64 bits), un NAS con Docker o una máquina virtual. Con 1 GB de RAM y 2 GB de disco libres sobra. EVBro consume unos 300 MB de memoria.
- **Linux** en ese ordenador (Debian, Ubuntu, Raspberry Pi OS…). También funciona en Windows o Mac con *Docker Desktop*, pero esta guía supone Linux porque es lo normal en un servidor de casa.
- **Tu cuenta de la app oficial de Ebro** (teléfono, contraseña y PIN de control remoto). EVBro habla con el coche a través de esa cuenta.
- Opcional pero recomendable: un **dominio** apuntando a tu casa y HTTPS (apartado 7). Sin HTTPS todo funciona dentro de tu red; las notificaciones en el móvil y el acceso con huella exigen HTTPS.

EVBro son **dos programas** que arrancan juntos: la aplicación y su base de datos (PostgreSQL). Los dos vienen preparados en el fichero `docker-compose.yml`; no tienes que instalar la base de datos aparte ni configurarla. Los datos se guardan en dos «volúmenes» de Docker (la base de datos y los ficheros: facturas, fotos, claves) que sobreviven a reinicios y actualizaciones.

## 2. Instalar Docker

En el servidor, abre una terminal (o entra por SSH) y ejecuta, línea a línea:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Cierra la sesión y vuelve a entrar para que el segundo comando tenga efecto. Comprueba que funciona:

```bash
docker compose version
```

Debe responder algo como `Docker Compose version v2.x`. Si dice «command not found», Docker no se instaló bien: vuelve a ejecutar el primer comando y lee lo que diga.

## 3. Descargar los dos ficheros de EVBro

```bash
mkdir -p ~/evbro && cd ~/evbro
curl -fsSLO https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/.env.example -o .env
```

Ahora tienes en `~/evbro` el fichero `docker-compose.yml` (no hay que tocarlo) y `.env` (la configuración, que sí vas a editar).

## 4. Configurar `.env` (lo mínimo)

Abre el fichero con un editor sencillo:

```bash
nano .env
```

Cambia **solo estas líneas**; el resto déjalo como está:

| Línea | Qué poner | Por qué |
| :--- | :--- | :--- |
| `POSTGRES_PASSWORD=` | Una contraseña larga cualquiera (no la vas a teclear nunca) | Es la contraseña interna de la base de datos |
| `ENCRYPTION_KEY=` | El resultado de ejecutar `openssl rand -base64 32` en otra terminal | Con esta clave se cifran tu contraseña y tu PIN de Ebro. **Guárdala también fuera del servidor** (gestor de contraseñas): si la pierdes, habrá que volver a conectar la cuenta de Ebro |
| `TIMEZONE=` | `Europe/Madrid` (o la tuya) | Para que la carga programada y las horas salgan bien |
| `LEGAL_OPERATOR=` | Tu nombre o «Casa de Juan» | Sale en el aviso legal de tu servidor |
| `LEGAL_CONTACT=` | Tu correo | Idem |

Guarda con `Ctrl+O`, Intro, y sal con `Ctrl+X`.

Un aviso:
- `WEB_PORT=8080` es el puerto por el que se abrirá EVBro. Si el 8080 ya lo usa otra cosa en ese ordenador, cambia el número (por ejemplo `8081`).

## 5. Arrancar

```bash
docker compose pull
docker compose up -d
```

La primera vez descarga unos 800 MB. Cuando termine, comprueba que los dos contenedores están en marcha:

```bash
docker compose ps
```

Tienen que aparecer `evbro` y `evbro-db` con estado `running` o `healthy`. Si `evbro` se reinicia una y otra vez, mira el motivo con `docker compose logs --tail 50 app`.

## 6. Primer arranque: el administrador y tu usuario

EVBro tiene **dos puertas**:

- **La aplicación** (puerto `WEB_PORT`, 8080 por defecto): la que usas tú y quien comparta tus coches. Es la que se publica en internet.
- **La consola de administración** (puerto 8082, solo dentro de tu red local): desde ahí se crean las cuentas y se ajusta el servidor. Nunca se publica en internet.

Pasos:

1. Saca el **código de configuración** del registro:
   ```bash
   docker compose logs app | grep "código de configuración"
   ```
2. Abre la consola en el navegador **desde el propio servidor** o, si es un servidor sin pantalla, quita antes en `.env` el `#` de la línea `ADMIN_BIND_ADDRESS` y pon la IP del servidor en tu red (por ejemplo `ADMIN_BIND_ADDRESS=192.168.1.50`), y haz `docker compose up -d` otra vez. Después abre `http://IP-DEL-SERVIDOR:8082`.
3. Crea el administrador con el código, un nombre y una contraseña de al menos 10 caracteres. El administrador **no tiene coches**: solo gestiona el servidor.
4. En la pestaña **Gestores**, pulsa **Crear gestor**: ese será **tu usuario de verdad**, el que tiene los coches. Apunta el usuario y la contraseña.
5. Abre la aplicación: `http://IP-DEL-SERVIDOR:8080`, entra con el gestor.
6. Ve a **Más → Ajustes → Cuenta → Conectar** e introduce el teléfono, la contraseña y el PIN de la app oficial de Ebro. En unos segundos aparecen tus coches y EVBro empieza a recoger datos.

Ya está funcionando. El resto de esta guía es para sacarle todo el partido.

## 7. HTTPS y acceso desde fuera de casa (recomendado)

Sin HTTPS, EVBro solo es cómodo dentro de casa, y el móvil no puede recibir avisos ni usar la huella. La forma más sencilla es poner **Caddy** delante, que consigue y renueva los certificados solo. Necesitas un dominio (vale uno gratuito de DuckDNS) apuntando a la IP pública de tu casa y abrir en el router los puertos 80 y 443 hacia el servidor.

1. Instala Caddy en el mismo servidor: [caddyserver.com/docs/install](https://caddyserver.com/docs/install).
2. Edita `/etc/caddy/Caddyfile` y deja solo esto (con tu dominio):
   ```caddyfile
   micoche.duckdns.org {
       request_body {
           max_size 20MB
       }
       reverse_proxy 127.0.0.1:8080 {
           header_up Host {host}
           header_up X-Real-IP {remote_host}
       }
   }
   ```
3. En el `.env` de EVBro quita el `#` de las tres líneas del bloque «Behind an HTTPS reverse proxy» (`COOKIE_SECURE=true`, `BIND_ADDRESS=127.0.0.1` para que el puerto 8080 solo lo vea Caddy, y `FORWARDED_ALLOW_IPS=172.16.0.0/12`) y reinicia: `docker compose up -d`.
4. Reinicia Caddy (`sudo systemctl reload caddy`) y abre `https://micoche.duckdns.org`.

**No publiques nunca el puerto 8082** (la consola de administración) en el router ni en Caddy.

## 8. Instalar EVBro en el móvil

Abre la dirección de EVBro en Chrome (Android) o Safari (iPhone) y usa **«Añadir a la pantalla de inicio»** (Chrome lo ofrece solo; en iPhone está en el botón Compartir). Se abre a pantalla completa, como una app, y funciona sin conexión con los últimos datos.

Para los avisos: **Más → Ajustes → Avisos → Activar en este dispositivo** y después **Enviar prueba**. Elige qué avisos quieres (geocercas, carga, mantenimiento, presión de neumáticos, vencimientos…).

## 9. Compartir el coche con otra persona

En **Más → Ajustes → Compartir → Añadir cuenta** creas un usuario para tu pareja o un familiar y eliges qué puede hacer con cada coche: solo ver, ver y anotar, ver y controlar, o todo. No puede añadir coches ni ver tu cuenta de Ebro. Para dar acceso a un **gestor nuevo con sus propios coches** (un amigo con otro Ebro), el administrador crea una **invitación** en la consola y le pasa el código; esa persona se registra en la aplicación con él.

## 10. Copias de seguridad

Lo imprescindible son tres cosas: la base de datos, los ficheros (facturas, fotos, tokens) y el `.env` (lleva la clave de cifrado). Una copia a mano:

```bash
cd ~/evbro
docker exec evbro-db pg_dump -U evbro evbro | gzip > evbro-db-$(date +%F).sql.gz
docker run --rm -v evbro_evbro_data:/d:ro -v "$PWD":/b alpine tar czf /b/evbro-data-$(date +%F).tgz -C /d .
cp .env evbro-env-$(date +%F).txt
```

Esos tres ficheros son todo lo que hay que guardar (en otro disco o en la nube). Para hacerlo solo cada noche, mete esas tres líneas en un script y prográmalo con `cron`.

Para **exportar tus datos** desde la propia app (sin tocar el servidor): **Más → Ajustes → Cuenta → Tus datos → Descargar todo (ZIP)**. Ese ZIP se puede importar en otro servidor de EVBro.

## 11. Actualizar

```bash
cd ~/evbro
docker compose pull && docker compose up -d
```

La base de datos se actualiza sola al arrancar. Antes de actualizar, haz la copia del apartado 10: una actualización no se deshace sola. Si quieres quedarte en una versión concreta, pon en `.env` la línea `EVBRO_TAG=v1.2.0` (la versión que sea) en vez de `latest`.

## 12. Si algo falla

| Síntoma | Qué mirar |
| :--- | :--- |
| La página no abre | `docker compose ps` (¿están los dos contenedores?) y `docker compose logs --tail 50 app` |
| «No se pudo iniciar sesión» al conectar la cuenta de Ebro | El teléfono va **sin prefijo** y el prefijo aparte (34 para España). La contraseña es la de la app oficial. Si la app oficial pidió verificación por SMS hace poco, espera unos minutos |
| Los datos no se actualizan | EVBro consulta la nube cada hora con el coche aparcado y cada minuto en marcha; no despierta el coche. **Ajustes → Sistema → Canal del coche** debe decir «Escuchando al coche». Si la app oficial está abierta en el móvil, la nube solo mantiene una conexión en directo: la última que entra |
| No llegan los avisos al móvil | Hace falta HTTPS (apartado 7) y haberlos activado en ese dispositivo (apartado 8) |
| Perdí la clave `ENCRYPTION_KEY` | Pon una nueva en `.env`, arranca y vuelve a conectar la cuenta de Ebro en Ajustes → Cuenta. El resto de datos no se pierde |
| Quiero empezar de cero | `docker compose down -v` borra **todo** (base de datos y ficheros). Sin `-v` solo para los contenedores |

Más detalle técnico (proxy, variables, Home Assistant, Traccar, paquete sin repositorio): [deployment.md](deployment.md). Qué guarda el servidor y quién lo ve: la página `/legal` de tu instalación y [security.md](security.md).
