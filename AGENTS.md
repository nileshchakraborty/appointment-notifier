# Appointment Notifier / OpenWA RPi Deployment

Shared platform conventions are maintained in
[`../rpi-local-platform/AGENTS.md`](../rpi-local-platform/AGENTS.md). This file
contains only appointment-notifier/OpenWA-specific history and operations.

## Purpose

This project runs the appointment-notifier service and a privacy-first,
multi-session OpenWA WhatsApp gateway on a Raspberry Pi. The Pi is intended to
be reachable from the local network only. It also hosts Pi-hole for local DNS,
Portainer for Docker administration, Caddy as the local gateway, and Streambert
TV as a separate existing service.

Never commit or document credentials. API keys, Pi-hole passwords, Portainer
tokens, WhatsApp session material, TLS private keys, and `.env` files are
secrets.

## Workspace layout

- `docker-compose.yml`: appointment-notifier and OpenWA deployment.
- `openwa/`: OpenWA runtime configuration, persistent sessions, Docker overlay,
  and entrypoint.
- `dns-management/`: Pi-hole Docker stack and its persistent configuration.
- `management/`: Caddy gateway, Portainer stack, local TLS material, renewal
  script, and the static quick-links dashboard.
- `src/`, `tests/`, `Dockerfile`, `pyproject.toml`: appointment-notifier code.
- `/Users/nileshchakraborty/workspace/wa-automate-nodejs`: local OpenWA fork.

## RPi topology

Target host: `rpi@192.168.1.229` (hostname `rpi`; Wi-Fi may also advertise
`192.168.1.230`). SSH uses the local key and known-hosts file from the prior
deployment workflow; never put private key material in this repository.

Core containers:

| Container | Function | LAN access |
|---|---|---|
| `appointment-openwa` | OpenWA multi-session gateway/dashboard | `:8081` |
| `appointment-notifier` | Appointment notification worker | internal Docker network |
| `rpi-pihole` | DNS and DNS-management UI | DNS `:53`, UI `:3000` |
| `rpi-caddy` | HTTP/HTTPS local reverse proxy | `:80`, `:443` |
| `rpi-portainer` | Docker management | `:9000`, proxied by Caddy |
| `streambert-tv` | Existing TV service | `:8090` |

## URLs

The quick-links dashboard is available at:

- `http://rpi.local/`
- `http://rpi.home.arpa/`
- `http://dashboard.rpi.home.arpa/`

HTTPS versions exist using the locally issued OpenSSL certificate. Devices
that do not trust the local CA should use HTTP on the trusted LAN.

Service hostnames and direct fallbacks:

- Portainer: `portainer.rpi.home.arpa` or `rpi.local:9000`
- OpenWA: `openwa.rpi.home.arpa` or `rpi.local:8081/dashboard/`
- Pi-hole: `pihole.rpi.home.arpa/admin/` or `rpi.local:3000/admin/`
- Streambert TV: `streambert-tv.rpi.home.arpa` or `rpi.local:8090`

The `.home.arpa` names are served by Pi-hole. `rpi.local` is mDNS and should
remain usable even when a client has not configured Pi-hole DNS. If a client
does not resolve `.home.arpa`, configure its DNS server as `192.168.1.229` or
use the `rpi.local:<port>` fallback.

## Security and privacy rules

- LAN-only exposure is intentional. Do not port-forward these services to the
  Internet.
- OpenWA telemetry, MCP, plugins, crash reporting, and external link parsing
  are disabled in the runtime configuration.
- Every registered OpenWA user gets an isolated profile and API key. Do not
  reuse the legacy deployment key for new users.
- Portainer has Docker-socket/root-equivalent authority. Keep it LAN-only and
  protect its admin account.
- Pi-hole currently forwards DNS to the local router (`192.168.1.1`). Review
  upstream settings before changing them if privacy requirements change.
- Plain HTTP fallback is provided for Firestick/iPhone compatibility. It is
  not encrypted; use it only on a trusted LAN.
- OpenSSL TLS uses an RPi-local CA. The CA certificate may be installed on
  trusted clients; never distribute `local-ca.key` or `server.key`.

## Deployment workflow

1. Pull/update the fork and build the OpenWA API, dashboard, and wa-automate
   distributions.
2. Copy only non-secret deployment files to the Pi.
3. Build the release overlay from `/home/rpi/appointment-notifier` using
   `openwa/Dockerfile.release-overlay`.
4. Recreate only the affected service with Docker Compose; persistent session,
   Pi-hole, Portainer, and TLS volumes must remain intact.
5. Verify container health, DNS records, HTTP fallbacks, HTTPS routes, and
   authenticated OpenWA endpoints.

Useful checks:

```sh
docker compose ps
docker logs --tail 100 appointment-openwa
curl -sS http://openwa.rpi.home.arpa/auth/status
curl -sS http://192.168.1.229:8081/auth/status
dig @192.168.1.229 openwa.rpi.home.arpa
```

Do not delete `openwa/sessions`, Pi-hole volumes, Portainer data, or TLS keys
while troubleshooting. Restart/recreate the relevant container first.

## Authentication and pairing

The OpenWA dashboard intentionally starts unauthenticated. Use **Connect** to
register/login or use an existing API key. A 401 from `/health` without a key
is expected. WhatsApp pairing errors are distinct from DNS/proxy failures;
check session state and Chromium logs before changing networking.

The dashboard client must use the current gateway origin when the gateway is
active. Stale direct `:8081` profiles can make the socket appear connected
while API cards remain pending; clear old browser site data or use the current
gateway hostname.

## Change history

- Started from the upstream OpenWA master source and detached the local fork so
  the user-owned fork is the only Git origin.
- Added privacy/security hardening, API authentication, registration/login,
  per-user session isolation, key rotation, CSRF/CORS protections, and rate
  limiting.
- Added the dashboard Connect flow for registration, login, existing keys, and
  one-time key display.
- Built and deployed the merged OpenWA release overlay to the RPi in Docker;
  verified API, dashboard, health, and endpoint behavior.
- Added Pi-hole as the local DNS management service after evaluating AdGuard
  Home. AdGuard was removed; Pi-hole data remains persistent.
- Added Caddy and Portainer, then changed `rpi.local` into a quick-links
  dashboard while retaining Portainer at `portainer.rpi.home.arpa` and
  `rpi.local:9000`.
- Added managed aliases for OpenWA, Pi-hole, Streambert TV, and Portainer, with
  direct `rpi.local:<port>` fallbacks for devices without Pi-hole DNS.
- Added a responsive, dependency-free quick-links table with clickable primary
  and fallback URLs.
- Added OpenSSL local CA/leaf certificates and a daily root cron renewal check
  that renews the leaf when fewer than 30 days remain and restarts Caddy.
- Recreated Pi-hole after environment-password changes so its configured admin
  password and persisted `.env` stay in sync.

## Engineering principles

Keep changes DRY and YAGNI: prefer the existing Caddy/Pi-hole/Compose layers,
avoid introducing a new service for a single route, preserve persistent data,
and add only the minimum configuration needed for a verifiable local-network
workflow.
