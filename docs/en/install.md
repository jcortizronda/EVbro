# Installing EVBro step by step (no Docker knowledge needed)

*Versión en español: [docs/instalacion.md](../instalacion.md).*

This guide is for anyone who wants **EVBro, your car's companion**, at home and has never set up a server. There is nothing to compile and no code to touch: EVBro ships as a ready-made **image** and starts from two files. If something does not go as described, copy the error message as is and ask in the repository ([github.com/jcortizronda/EVbro](https://github.com/jcortizronda/EVbro)).

Estimated time: 20 minutes the first time.

## 1. What you need

- **A computer that is always on** at home: a mini PC, a Raspberry Pi 4 or 5 (64-bit), a NAS with Docker or a virtual machine. 1 GB of RAM and 2 GB of free disk are plenty. EVBro uses about 300 MB of memory.
- **Linux** on it (Debian, Ubuntu, Raspberry Pi OS…). It also runs on Windows or Mac with *Docker Desktop*, but this guide assumes Linux because that is what a home server usually runs.
- **Your official Ebro app account** (phone, password and remote-control PIN). EVBro talks to the car through that account.
- Optional but recommended: a **domain** pointing to your home and HTTPS (section 7). Without HTTPS everything works inside your network; phone notifications and fingerprint sign-in require HTTPS.

EVBro is **two programs** that start together: the application and its database (PostgreSQL). Both are prepared in the `docker-compose.yml` file; you do not install or configure the database yourself. Data lives in two Docker "volumes" (the database and the files: invoices, photos, keys) that survive restarts and updates.

## 2. Install Docker

On the server, open a terminal (or connect over SSH) and run, one line at a time:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in so the second command takes effect. Check that it works:

```bash
docker compose version
```

It should answer something like `Docker Compose version v2.x`. If it says "command not found", Docker did not install properly: run the first command again and read what it says.

## 3. Download the two EVBro files

```bash
mkdir -p ~/evbro && cd ~/evbro
curl -fsSLO https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/jcortizronda/EVbro/main/deploy/.env.example -o .env
```

You now have `docker-compose.yml` (leave it alone) and `.env` (the configuration, which you will edit) in `~/evbro`.

## 4. Configure `.env` (the minimum)

Open the file with a simple editor:

```bash
nano .env
```

Change **only these lines**; leave the rest as it is:

| Line | What to put | Why |
| :--- | :--- | :--- |
| `POSTGRES_PASSWORD=` | Any long password (you will never type it) | The internal database password |
| `ENCRYPTION_KEY=` | The output of `openssl rand -base64 32` run in another terminal | Your Ebro password and PIN are encrypted with it. **Keep a copy outside the server** (password manager): if you lose it, you will have to connect the Ebro account again |
| `TIMEZONE=` | `Europe/Madrid` (or yours) | So scheduled charging and times come out right |
| `LEGAL_OPERATOR=` | Your name or "John's home" | Shown in your server's legal notice |
| `LEGAL_CONTACT=` | Your e-mail | Same |

Save with `Ctrl+O`, Enter, and exit with `Ctrl+X`.

One warning:
- `WEB_PORT=8080` is the port EVBro will open on. If 8080 is already used by something else on that computer, change the number (for example `8081`).

## 5. Start

```bash
docker compose pull
docker compose up -d
```

The first time it downloads about 800 MB from `ghcr.io/jcortizronda/evbro`. When it finishes, check that both containers are running:

```bash
docker compose ps
```

`evbro` and `evbro-db` must show `running` or `healthy`. If `evbro` keeps restarting, see why with `docker compose logs --tail 50 app`.

## 6. First start: the administrator and your user

EVBro has **two doors**:

- **The application** (port `WEB_PORT`, 8080 by default): the one you and whoever shares your cars use. This is the one published on the internet.
- **The admin console** (port 8082, local network only): accounts are created and the server is tuned from here. It is never published on the internet.

Steps:

1. Get the **setup code** from the log:
   ```bash
   docker compose logs app | grep "código de configuración"
   ```
2. Open the console in a browser **on the server itself** or, if the server has no screen, first remove the `#` from the `ADMIN_BIND_ADDRESS` line in `.env` and put the server's IP on your network (for example `ADMIN_BIND_ADDRESS=192.168.1.50`), run `docker compose up -d` again and open `http://SERVER-IP:8082`.
3. Create the administrator with the code, a name and a password of at least 10 characters. The administrator **has no cars**: it only runs the server.
4. In the **Managers** tab press **Create manager**: that will be **your real user**, the one who owns the cars. Note the username and password.
5. Open the application: `http://SERVER-IP:8080`, sign in with the manager.
6. Go to **More → Settings → Account → Connect** and enter the phone, password and PIN of the official Ebro app. Your cars appear within seconds and EVBro starts collecting data.

It is working. The rest of this guide is about getting the most out of it.

## 7. HTTPS and access from outside home (recommended)

Without HTTPS, EVBro is only comfortable at home, and the phone cannot receive alerts or use the fingerprint. The simplest way is to put **Caddy** in front: it obtains and renews certificates on its own. You need a domain (a free DuckDNS one works) pointing to your home's public IP and ports 80 and 443 forwarded in the router to the server.

1. Install Caddy on the same server: [caddyserver.com/docs/install](https://caddyserver.com/docs/install).
2. Edit `/etc/caddy/Caddyfile` and leave only this (with your domain):
   ```caddyfile
   mycar.duckdns.org {
       request_body {
           max_size 20MB
       }
       reverse_proxy 127.0.0.1:8080 {
           header_up Host {host}
           header_up X-Real-IP {remote_host}
       }
   }
   ```
3. In EVBro's `.env` remove the `#` from the three lines of the "Behind an HTTPS reverse proxy" block (`COOKIE_SECURE=true`, `BIND_ADDRESS=127.0.0.1` so only Caddy sees port 8080, and `FORWARDED_ALLOW_IPS=172.16.0.0/12`), then restart: `docker compose up -d`.
4. Reload Caddy (`sudo systemctl reload caddy`) and open `https://mycar.duckdns.org`.

**Never publish port 8082** (the admin console) in the router or in Caddy.

## 8. Install EVBro on the phone

Open EVBro's address in Chrome (Android) or Safari (iPhone) and use **"Add to Home Screen"** (Chrome offers it on its own; on iPhone it is in the Share button). It opens full screen, like an app, and works offline with the latest data.

For alerts: **More → Settings → Alerts → Enable on this device**, then **Send a test**. Choose which alerts you want (geofences, charging, maintenance, tyre pressure, due dates…).

## 9. Sharing the car with someone else

In **More → Settings → Sharing → Add account** you create a user for your partner or a relative and choose what they can do with each car: view only, view and edit, view and control, or everything. They cannot add cars or see your Ebro account. To give access to a **new manager with their own cars** (a friend with another Ebro), the administrator creates an **invitation** in the console and hands over the code; that person registers in the application with it.

## 10. Backups

Three things matter: the database, the files (invoices, photos, tokens) and `.env` (it holds the encryption key). A manual backup:

```bash
cd ~/evbro
docker exec evbro-db pg_dump -U evbro evbro | gzip > evbro-db-$(date +%F).sql.gz
docker run --rm -v evbro_evbro_data:/d:ro -v "$PWD":/b alpine tar czf /b/evbro-data-$(date +%F).tgz -C /d .
cp .env evbro-env-$(date +%F).txt
```

Those three files are all you need to keep (on another disk or in the cloud). To do it every night on its own, put those three lines in a script and schedule it with `cron`.

To **export your data** from the app itself (without touching the server): **More → Settings → Account → Your data → Download everything (ZIP)**. That ZIP can be imported into another EVBro server.

## 11. Updating

```bash
cd ~/evbro
docker compose pull && docker compose up -d
```

The database updates itself on start. Make the backup of section 10 before updating: an update cannot be undone on its own. To stay on a specific version, put `EVBRO_TAG=v1.2.0` (whatever version) in `.env` instead of `latest`.

## 12. If something fails

| Symptom | What to check |
| :--- | :--- |
| The page does not open | `docker compose ps` (are both containers there?) and `docker compose logs --tail 50 app` |
| "Could not sign in" when connecting the Ebro account | The phone goes **without the country code**, which goes in its own field (34 for Spain). The password is the one of the official app. If the official app asked for SMS verification recently, wait a few minutes |
| Data does not refresh | EVBro queries the cloud every hour with the car parked and every minute while driving; it does not wake the car. **Settings → System → Car channel** must say "Listening to the car". If the official app is open on the phone, the cloud keeps only one live connection: the last one in |
| No alerts on the phone | HTTPS is needed (section 7) and they must be enabled on that device (section 8) |
| I lost `ENCRYPTION_KEY` | Put a new one in `.env`, start, and connect the Ebro account again in Settings → Account. Nothing else is lost |
| I want to start over | `docker compose down -v` deletes **everything** (database and files). Without `-v` it only stops the containers |

More technical detail (proxy, variables, Home Assistant, Traccar, package without the repository): [deployment.md](../deployment.md) (Spanish). What the server stores and who sees it: the `/legal` page of your installation and [security.md](../security.md) (Spanish).
