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

`docker-weekly-maintenance.py` recreates the explicitly listed running Compose
services one at a time. Fedora runs Sunday at 03:00 and the Pi at 04:00
America/Los_Angeles. It pulls official image tags, rebuilds the notifier from
the checked-out source, checks container recovery, then prunes only unused
images and build cache older than seven days. It preserves all bind mounts and
volumes. The Streambert release image and OpenWA pinned fork are updated by
their own release procedures; the weekly run recreates those images without
changing their application revision. A service already unhealthy is skipped
and logged, so it cannot be mistaken for a successful recovery drill.

Install the script at `/opt/docker-weekly-maintenance.py` on both hosts and the
matching unit and timer files under `/etc/systemd/system/`. Test with
`python3 /opt/docker-weekly-maintenance.py --host fedora --dry-run` (or `pi`),
then enable `docker-weekly-maintenance-{fedora,pi}.timer` on the relevant host.
Inspect the result with `journalctl -u docker-weekly-maintenance@fedora.service`
or the Pi equivalent. The listed services are the current live inventory; add
new services deliberately after confirming their mounts and dependencies.
