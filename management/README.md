# RPi management gateway

- `http://rpi.local/`, `http://rpi.home.arpa/`, or `http://dashboard.rpi.home.arpa/` — local quick-links dashboard
- `http://portainer.rpi.home.arpa/` — Portainer Docker management
- `http://openwa.rpi.home.arpa/` — OpenWA
- `http://pihole.rpi.home.arpa/` — Pi-hole
- `http://streambert-tv.rpi.home.arpa/` — Streambert TV
- `http://rpi.local/firestick/streambert.apk` — Streambert Fire TV installer

The active RPi host Nginx service owns port 80. Portainer runs on Fedora at
`192.168.1.250:9000` and is reachable through the Pi Nginx hostname route.
The Caddy service remains in this Compose project but must not be started while Nginx is
active because both services require host port 80. The HTTPS Caddy routes are
therefore not part of the current live path.

## Portainer environments

The Portainer Server runs on Fedora with its database copied from the Pi. Its
Compose file is `docker-compose.fedora-portainer.yml`; live data is under
`/home/nilesh/portainer-server/portainer-data` on Fedora. The original Pi data and stopped
container are retained for rollback. Existing environments in the copied DB
include:

- `RPi` used the RPi's local `/var/run/docker.sock` and needs endpoint review
  after the server move.
- `Fedora` connects to a Portainer Agent at `192.168.1.250:9001`.

The server and agent must use the same `PORTAINER_AGENT_SECRET` in their local
`.env` files. Never commit that value. The Fedora agent is deployed separately
from Streambert with `docker-compose.fedora-agent.yml`, so Streambert's
`--remove-orphans` deployment does not remove it.

Portainer stores the active administrator password as a one-way hash in its
database. The original Pi password file is not part of the Fedora migration.

The Pi's wired NetworkManager profile is already persistent and manual:
`192.168.1.229/24` with gateway `192.168.1.1`. Its Wi-Fi profile uses a separate
static `192.168.1.228/24` address. Avoid assigning `.229` to another LAN device.

## Weekly Docker recovery

`docker-weekly-maintenance.py` runs Sunday at 03:00 on Fedora and 04:00 on the
Pi, America/Los_Angeles. It checks that protected services have the expected
number of running, healthy containers and verifies OpenWA's authenticated
session readiness before touching anything. Only the
Streambert API and worker, Ollama, and Portainer Agent are recreated weekly.
The Pi timer checks Pi-hole without restarting DNS. Previous images, bind
mounts, named volumes, and application data are retained; only build cache
older than seven days is pruned. External image tags are pulled for services
that are recreated. Local Streambert release images change only through its
release procedure.

OpenWA, the notifier, all Telegram delivery workers, Kafka, Portainer Server,
Pi-hole, Qdrant, and Redis services are **not** automatically recreated.
OpenWA's Chromium profile is bind mounted at
`/home/nilesh/openwa-service/chrome-profile`; notifier state and credentials
are bind mounted under `/home/nilesh/appointment-notifier/.state` and
`.secrets`; Kafka data is a named volume. A restart normally preserves these,
but a healthy container does not prove that WhatsApp remained linked or that
Telegram delivery resumed. Use a controlled, service-specific update for
these services and verify application state before declaring success.

### Recovery if a session or delivery path fails after a manual update

1. Stop further recreations and keep the existing profile, state directories,
   named volumes, and previous image. Do not run `docker compose down -v`,
   `docker volume prune`, or delete the Chromium profile.
   Before a planned manual update, record the image IDs and mount paths; take a
   protected backup of the OpenWA profile while its browser is stopped and an
   online SQLite backup of the notifier state. Keep credentials out of logs and
   ensure the backup is readable before changing an image.
2. For OpenWA, inspect its authenticated `/health` response for `connected`
   and `session.ready`. Check that the container still mounts the same host
   profile directory. If the mount or image changed, restore the prior Compose
   configuration and image. If WhatsApp revoked the link, use a fresh QR and
   confirm `/health` reports both values as true; a file restore cannot undo a
   server-side revocation.
3. For Telegram, verify Kafka health and the delivery worker count, then check
   the SQLite outbox in `.state` for pending or failed alerts. The Bot API token
   comes from `.env` or `.secrets`; workers do not have a WhatsApp-style login
   session. Restore a missing token from the protected secret source, restart
   only the affected worker, and confirm a queued test reaches an authorized
   recipient before clearing the incident.
4. Record the failed image ID and logs. Roll back the image/configuration for
   an update regression while preserving the same mounts. Recheck OpenWA
   connectivity, Kafka, worker count, and actual alert delivery.

Install the script at `/opt/docker-weekly-maintenance.py` on both hosts and the
matching unit and timer files under `/etc/systemd/system/`. Test with
`python3 /opt/docker-weekly-maintenance.py --host fedora --dry-run` (or `pi`),
then enable `docker-weekly-maintenance-{fedora,pi}.timer` on the relevant host.
Inspect the result with `journalctl -u docker-weekly-maintenance@fedora.service`
or the Pi equivalent. The listed services are the current live inventory; add
new services deliberately after confirming their mounts and dependencies.
