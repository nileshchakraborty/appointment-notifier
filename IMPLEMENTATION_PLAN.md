# Implementation Plan: Appointment Notifier & System Integration

## Overview
This plan outlines the architecture, deployment, and operational workflows for `appointment-notifier` and the Raspberry Pi infrastructure (`rpi@192.168.1.229`).

## 1. Core System Components

### Appointment Notifier (`src/appointment_notifier`)
- **Telegram Watcher**: Listens in real time to `@Regular_H1B_H4_VisaSlotsChecking`.
- **Parsing & Filtering**: Identifies visa slot availability, suppresses "no slots" noise, and deduplicates events in SQLite (`.state/database.db`).
- **Multi-Channel Alerting**:
  - Telegram Bot (`TELEGRAM_ALERT_CHAT_ID`)
  - Self-hosted OpenWA WhatsApp gateway (`WHATSAPP_PROVIDER=openwa`)
  - Email via SMTP
  - Twilio SMS / WhatsApp
  - iMessage (macOS target)

### LLM & Trend Analysis (`/trend`, `/ask`)
- **Deterministic Clustering**: Groups historical slot reports into release windows.
- **Multi-Provider Fallback Chain**:
  1. `nvidia`: NVIDIA NIM hosted endpoints (`openai/gpt-oss-20b`).
  2. `ollama_cloud`: Direct authenticated access to Ollama Cloud.
  3. `ollama`: Local RPi Ollama instance (`gemma3:1b` model).

## 2. Raspberry Pi Infrastructure (`192.168.1.229`)

| Container / Service | Role | Local Endpoint |
|---|---|---|
| `appointment-notifier` | Visa slot watcher & alert engine | Docker Internal |
| `appointment-openwa` | OpenWA WhatsApp gateway & dashboard | `http://rpi.local:8081` |
| `rpi-caddy` | Reverse proxy & SSL gateway | `http://rpi.local` / `https://rpi.local` |
| `rpi-pihole` | Local DNS filter | `http://rpi.local:3000/admin/` |
| `rpi-portainer` | Docker stack management | `http://rpi.local:9000` |

## 3. Operational & Maintenance Procedures

## 4. Source-message classification and AI contract

The Telegram source is treated as a community signal, not an authoritative
appointment feed. Every observation is assigned one category before it can
affect alerts or trend calculations:

- `bulk_release`: multi-month/mass release announcements; counted as release
  signals, never as individual appointment quantities.
- `individual_availability`: a text/caption report that explicitly says slots
  are available. Image-only posts remain `unknown_image` until OCR/layout
  evidence is added.
- `unbookable`: ghost slots, missing time rows, disabled/missing Submit, OFC
  without consular, expired/no-slot reports.
- `na_heartbeat`: `NA 1 ALL` and related channel heartbeat messages.
- `booked_confirmation`, `discussion`, `admin`, `spam`, and `unknown_image`:
  retained for audit but excluded from availability forecasting.

Trend output must expose separate counts for bulk releases and individual
reports, while explicitly stating that the result is community activity and
not a count of bookable appointments. The AI receives this structured trusted
summary and must not recalculate dates, merge bulk and individual signals, or
turn invalid/image-only messages into availability claims.

NVIDIA requests default to a 90-second timeout to accommodate the richer
summary prompt. Historical classification/import work should be batchable and
resumable rather than sent as one giant prompt; interactive `/ask` remains
bounded by its response budget.

### Deployment
```bash
# On RPi (/home/rpi/appointment-notifier):
docker compose up -d --build
docker compose ps
```

### Health & Log Verification
```bash
docker compose logs -f appointment-notifier
docker compose logs -f appointment-openwa
```

### Notification Test
```bash
docker compose exec appointment-notifier appointment-notifier test-notify
```

## 5. OCR and portal-layout pipeline

Image posts are downloaded briefly into the container's `/tmp` tmpfs. Tesseract
OCR extracts portal text, while layout heuristics detect calendar/date controls,
time rows, availability counts, OFC/VAC and consular labels, and enabled/disabled
Submit controls. The classifier is fail-closed: an image without usable OCR is
stored as `unknown_image`, while ghost, partial-OFC, and unavailable layouts are
excluded from alerts.

OCR text, extracted features, portal state, and a SHA-256 media key are cached in
the SQLite `media_analysis` table. This prevents repeated OCR for the same image
and lets `/trend` and `/ask` receive a compact cached OCR evidence summary rather
than reprocessing Telegram history or sending raw images to the LLM. Raw media is
not persisted by the notifier.
