# October 22 Pi outage and Fedora migration

## November 1 Fedora reachability and Talos bot incident

Fedora is a headless Apple MacBookPro16,1 running Fedora 44 KDE. The host did
not suspend during the observed outage. Its previous boot remained active from
06:17 to 14:12 PST; the homelab watchdog ran every minute, and the journal
contains no suspend or hibernate transition. The 14:12 shutdown followed a
short power-button press during manual recovery. Sleep and suspend targets
were already masked, and `/etc/systemd/logind.conf.d/00-halftop-server.conf`
already ignored lid, suspend, hibernate, and idle actions. Do not diagnose a
future loss of SSH as sleep without checking the boot journal.

`dnf5-automatic` applied updates and scheduled a reboot at 06:16 PST. The
prior boot kept SSH, NetworkManager, and firewalld running. The Genesys
`05e3:0626` USB 3 hub enumerated, but its Realtek `0bda:8153` Ethernet child
never appeared. Only Wi-Fi
remained, configured as `192.168.1.250/32`. That profile had no direct LAN
route; a Wi-Fi-bound ping to the Pi produced a router redirect and packet
loss. This explains why SSH and application ports became unreachable even
though Fedora was awake. The exact hub power or enumeration failure remains
unknown; the journal recorded no USB error for that port.

The persistent NetworkManager `DemonSlayerCorp` profile now uses
`192.168.1.250/24`, with its existing gateway and route priority retained.
`nmcli device reapply wlp5s0` installed a direct `192.168.1.0/24` Wi-Fi route,
and a Wi-Fi-bound ping to the Pi passed 3/3 packets. DNF's original automatic
update and `reboot = when-needed` settings were restored; the host can now
fall back to Wi-Fi if USB Ethernet is missing after a future reboot.

`80-fedora-ethernet-power.rules` keeps this USB hub and RTL8153 adapter out of
runtime autosuspend. `fedora-ethernet-recover.timer` checks at boot and every
five minutes. If the adapter is absent while its hub is present, the service
resets only the hub's Ethernet port, up to two times. It does not reset a
healthy Ethernet connection. A controlled test disabled the port, invoked the
service, and confirmed the adapter recovered on the first reset with wired
`192.168.1.215/24` and SSH restored. The source files are in `management/`;
the deployed script, units, and udev rule are under `/usr/local/sbin`,
`/etc/systemd/system`, and `/etc/udev/rules.d` on Fedora.

Talos Telegram polling had a separate persistence fault: the enabled
`talos-telegram.service` is a user service, but `loginctl` showed `Linger=no`.
Its user manager and bot therefore stopped after each SSH session ended.
`sudo loginctl enable-linger nilesh` changed this to `Linger=yes`. Verify it
and the bot after reboot with `loginctl show-user nilesh -p Linger` and
`systemctl --user status talos-telegram.service`.

A controlled Fedora reboot on November 1 succeeded. The Realtek adapter
enumerated, wired `192.168.1.215/24` and Wi-Fi `192.168.1.250/24` answered
SSH, the recovery timer ran, and the Talos Telegram service started through
user lingering. PowerTOP later changed the hub and adapter's USB power control
back to `auto`. The recovery unit now runs after PowerTOP, and
`10-powertop-ethernet-recovery.conf` reapplies the recovery script after each
PowerTOP start. Restarting PowerTOP verified both USB devices stayed at `on`.
The final ordering change also passed a second full reboot test. Fedora booted
with both wired and Wi-Fi addresses, SSH, Talos Telegram, PowerTOP, and the
recovery timer active. The recovery service completed successfully, and both
USB devices retained `power/control=on`. Apple SMC reported both chassis fans
spinning at about 1800/1700 RPM when cool; they rose to about 3730/3450 RPM
as the CPU package reached 65 C. `t2fanrd.service` and `thermald` were active.

The Pi check found `rpi-pihole`, SSH, and Nginx active. Pi-hole answered public
and local DNS queries; its dashboard, Streambert, and Ollama proxy routes
responded. The active Pi-hole `dns.hosts` list was missing
`openwa.rpi.home.arpa`, so that record was restored as
`192.168.1.229 openwa.rpi.home.arpa` and its resolution verified. OpenWA's
proxy route answered with its expected API-key challenge, while an authenticated
health check reported `connected=false` and `ready=false`. The WhatsApp session
remains a separate issue.

The Pi then passed its own reboot test. Ethernet `192.168.1.229/24` and Wi-Fi
`192.168.1.228/24` returned; SSH and Nginx were active with no failed systemd
units, and Pi-hole reached Docker `healthy`. Public DNS and the OpenWA local
record resolved, the Pi-hole admin route redirected to login, and the
dashboard, Streambert, and Ollama proxy routes returned HTTP 200. OpenWA's
proxy route returned the expected HTTP 401 without an API key. Fedora remained
reachable after the Pi reboot. OpenWA's authenticated health response still
reported `connected=false` during startup.

## November 1 OpenWA pairing QR mismatch

The Mac localhost pairing helper at `http://127.0.0.1:8765/` is a temporary
process and does not start automatically. During pairing, OpenWA's `/qr` API
returned a cached QR value while the current WhatsApp Web canvas and OpenWA's
`qr_code_generated` event moved to a new value. Repeated hash comparisons
confirmed the mismatch. The helper must read the live canvas `data-ref`, as
OpenWA's own `QR_CHECK_SCRIPT` does; using `/qr` here can show an expired code.
The helper was restored to the live source, WhatsApp paired, `/health` reported
`connected=true` and `session.ready=true`, and Docker health became `healthy`.
The original reason the previous WhatsApp linked device disappeared remains
unproven by these checks.

An additional observed session unlinked again about five minutes after it
became ready. OpenWA remained running, but WhatsApp Web navigated to
`?post_logout=1&logout_reason=0`, and its state moved through `PAIRING` to
`UNPAIRED`. The page also logged an offline storage restore error shortly
before the navigation; this timing alone does not prove causation. A patched
OpenWA image now serves a focused `/dashboard/pairing` page with connection
status, a QR read directly from the browser, and an authenticated, confirmed
unlink action. The API state is updated to `DISCONNECTED` when the logout event
fires. The recurring spontaneous unlink remains under investigation.

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
