# October 22 Pi outage and Fedora migration

## Observed failure

Before the Pi reboot, SSH reset before login, SMB disconnected during negotiation,
the Streambert `:8090` HTTP route hung, and OpenWA `:8081` refused connections.
Pi-hole remained reachable. The Pi reboot restored remote access. The prior boot
logs inspected did not establish a hardware, disk, memory, or Docker root cause.

The Pi watchdog at `/usr/local/bin/rpi-watchdog.sh` was later found restarting
the notifier and Portainer containers every minute after they were intentionally
stopped. This caused a Telegram `409` poller conflict during migration. Its
critical container list now contains only `rpi-pihole`; the original script is
backed up beside it as `rpi-watchdog.sh.pre-fedora-migration-20261022`.

## Current placement

- Fedora `192.168.1.250`: `appointment-notifier` Docker service with the Pi's
  copied `.state` database and Telegram session; separate `talos-telegram.service`;
  Portainer server with copied database; OpenWA and Ollama Docker services.
- Pi `192.168.1.229`: `rpi-pihole` Docker service and host Nginx. Nginx proxies
  Streambert `:8090`, OpenWA `:8081`, Ollama `:11434`, and Portainer hostnames
  to Fedora.
  The Pi notifier, Streambert edge, and Portainer containers are stopped with
  Docker restart disabled. Pi `openwa.service` and `ollama.service` are stopped
  and disabled.
- Fedora `appointment-telegram-failover@visa.service` is inactive. It was a
  temporary visa polling service and must stay inactive while the Fedora
  notifier container polls the same bot token. Talos uses its own token and
  SQLite database.

The copied Pi notifier SQLite file matched the original SHA-256 immediately
after the Pi container stopped. The original Pi data and stopped containers are
retained for rollback. The Fedora notifier disables iMessage and Talos features;
Talos remains in its separate service. Its OpenWA URL points directly to
`http://192.168.1.250:8081`; its Ollama URL points to Fedora `:11434`.

The copied Ollama model files matched the Pi hashes, and a Fedora generation
request to `gemma3:1b` completed successfully.

OpenWA built from fork commit `975a32d4eed560f784a079e2466a0ece9cfad008`
reports `connected: true` and `session.ready: true` on Fedora and through the Pi
Nginx proxy. The first shutdown attempt did not finish before its
grace period, so the runtime was upgraded and the login was scanned again. The
encrypted session file now lives at
`/home/nilesh/openwa-service/chrome-profile/default.data.json` with mode 600.
After restarting the container, OpenWA restored the session and returned to
ready without another QR scan. The dashboard, `/health`, and `/api/session/getMe`
respond through the Pi route.

The notifier has `WHATSAPP_PROVIDER` unset, so it defaults to Twilio; its
Twilio fields are present. OpenWA currently runs as a separate service.

## Verify

```sh
ssh rpi@rpi.local 'nmcli -g ipv4.method,ipv4.addresses connection show "Wired connection 1"; sudo docker ps; systemctl is-active nginx openwa'
ssh nilesh@192.168.1.250 'docker ps --filter name=appointment-notifier --filter name=portainer-server --filter name=openwa; systemctl --user is-active talos-telegram appointment-telegram-failover@visa'
curl -fsS http://192.168.1.229:8081/health
curl -fsS http://192.168.1.229:8090/health
curl -fsS http://192.168.1.229:11434/api/tags
curl -fsS -H 'Host: portainer.rpi.home.arpa' http://192.168.1.229/api/status
```

Send `/current` to `@usvisaavaibalitynotifierinbot` and `/talos who should we
pitch first?` to `@talosprotocolaibot` for an end-to-end check. The Talos reply
should contain the saved answer rather than a generic task status. Do not run
two visa pollers at once.

The Talos token was shared in chat during the incident. Rotate it through
BotFather, replace the mode-600 Fedora token file, and restart
`talos-telegram.service`. Never print or commit tokens or `.env` files.
