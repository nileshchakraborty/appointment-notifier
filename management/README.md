# RPi management gateway

- `https://rpi.local/`, `https://rpi.home.arpa/`, or `https://dashboard.rpi.home.arpa/` — local quick-links dashboard
- `https://portainer.rpi.home.arpa/` — Portainer Docker management
- `https://openwa.rpi.home.arpa/` — OpenWA
- `https://pihole.rpi.home.arpa/` — Pi-hole
- `https://streambert-tv.rpi.home.arpa/` — Streambert TV
- `http://rpi.local/firestick/streambert.apk` — Streambert Fire TV installer

Caddy is the only service exposed on port 80. Portainer itself is bound to
loopback and is reachable through Caddy only. The `/firestick/*` path is proxied
to the Streambert TV service so its generated APK can be downloaded from the
main dashboard without adding another container or host-specific file mount.
Because the Caddyfile itself is an individual bind mount, replacing the host
file requires `docker compose up -d --force-recreate caddy`; an in-container
reload alone can continue reading the previously mounted inode.
