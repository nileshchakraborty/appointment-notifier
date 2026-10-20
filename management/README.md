# RPi management gateway

- `http://rpi.local/`, `http://rpi.home.arpa/`, or `http://dashboard.rpi.home.arpa/` — local quick-links dashboard
- `http://portainer.rpi.home.arpa/` — Portainer Docker management
- `http://openwa.rpi.home.arpa/` — OpenWA
- `http://pihole.rpi.home.arpa/` — Pi-hole
- `http://streambert-tv.rpi.home.arpa/` — Streambert TV
- `http://rpi.local/firestick/streambert.apk` — Streambert Fire TV installer

The active RPi host Nginx service owns port 80. Portainer itself is bound to
loopback and is reachable through the Nginx hostname route only. The Caddy
service remains in this Compose project but must not be started while Nginx is
active because both services require host port 80. The HTTPS Caddy routes are
therefore not part of the current live path.

## Portainer environments

The Portainer Server runs on the RPi and manages two Docker environments:

- `RPi` uses the RPi's local `/var/run/docker.sock`.
- `Fedora` connects to a Portainer Agent at `192.168.1.250:9001`.

The server and agent must use the same `PORTAINER_AGENT_SECRET` in their local
`.env` files. Never commit that value. The Fedora agent is deployed separately
from Streambert with `docker-compose.fedora-agent.yml`, so Streambert's
`--remove-orphans` deployment does not remove it.

The initial administrator username is `admin`. Its generated password is kept
only on the RPi at `/home/rpi/.config/portainer/admin-password` with mode 600;
read it locally over SSH and move it into a password manager. Portainer stores
the active password as a one-way hash in its database.
