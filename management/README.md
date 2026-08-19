# RPi management gateway

- `https://rpi.local/`, `https://rpi.home.arpa/`, or `https://dashboard.rpi.home.arpa/` — local quick-links dashboard
- `https://portainer.rpi.home.arpa/` — Portainer Docker management
- `https://openwa.rpi.home.arpa/` — OpenWA
- `https://pihole.rpi.home.arpa/` — Pi-hole
- `https://streambert-tv.rpi.home.arpa/` — Streambert TV

Caddy is the only service exposed on port 80. Portainer itself is bound to
loopback and is reachable through Caddy only.
