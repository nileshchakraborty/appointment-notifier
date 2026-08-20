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

Media staging is capacity-aware and configured through `MEDIA_TEMP_DIRS`. The
default order is Drive 1, Drive 2, then `/tmp`; each candidate must be writable
and have at least 8 MB free. If no candidate is available, the message remains
`unknown_image` and the notifier does not risk filling local storage.

The host-side media directory is supplied through the ignored `MEDIA_HOST_DIR`
environment variable and is never committed to the repository.

## 6. Historical Telegram inventory, OCR backfill, and cleansed trend dataset

### Goal

Build a resumable, auditable historical dataset before answering trend questions.
The LLM must receive a compact, precomputed snapshot—not raw Telegram messages,
raw OCR, or an instruction to infer the history itself. Bulk forecasts must use
the complete historical set of bulk-release events, not only the latest post.

### Phase A — Source inventory and canonical message storage

1. Add `TELEGRAM_SOURCE_CHATS` (usernames/IDs) while retaining
   `TELEGRAM_CHANNEL` as a backwards-compatible default.
2. Use a Telethon inventory/backfill command to enumerate configured chats and
   record chat metadata, access errors, scan cursors, and the Telegram history
   range. Do not silently treat an inaccessible chat as empty.
3. Add a canonical `telegram_messages` table keyed by `(chat_id, message_id)`.
   Store caption/text, sent/edit timestamps, media metadata, permalink, reply and
   forward metadata, and fetch provenance. The existing `observed_messages`
   table becomes the derived classification projection; message IDs must not be
   globally unique across chats.
4. Make the importer idempotent and resumable by chat/message cursor. A restart
   must continue from the last committed page and never redownload completed
   media.

### Phase B — OCR and portal-layout backfill

1. For every historical image/document, download to the capacity-aware Drive 1
   → Drive 2 → `/tmp` staging chain.
2. Hash media before OCR. Reuse cached results only when the media hash,
   OCR-engine version, preprocessing version, and layout-classifier version all
   match; otherwise create a new analysis revision.
3. Run bounded OCR with preprocessing variants and store raw/normalized OCR,
   extracted fields, layout features, portal state, confidence, error code, and
   timestamps. Never send images to the LLM in this batch.
4. Classify image-only posts using caption + OCR + portal layout. Fail closed for
   unreadable, ambiguous, ghost, partial-OFC, and disabled-submit screens.
5. Record per-message provenance (`text`, `ocr`, `layout`, or `manual`) so rule
   changes can reprocess only affected rows.

### Phase C — Cleansing and reviewable derived classifications

Use non-destructive cleansing. Preserve raw messages and every analysis; derive
a current canonical classification with `bulk_release`,
`individual_availability`, `unbookable`, `na_heartbeat`,
`booked_confirmation`, `discussion`, `admin`, `spam`, `unknown_image`, and
`needs_review`. Low-confidence or conflicting text/OCR/layout evidence goes to
`needs_review` and is never counted as availability.

Add classifier version, confidence, evidence JSON, exclusion reason, and optional
manual override fields. Produce a review export with links and OCR snippets so
ambiguous cases can be corrected without editing raw data.

### Phase D — Materialized history and bulk-event statistics

1. Cluster canonical rows into release events using a documented time-gap rule,
   separately for all availability, bulk releases, and individual reports.
2. Materialize a `trend_snapshots` record containing dataset version, source
   chats, time range, counts, excluded counts, last bulk event, bulk event count,
   median bulk gap, next bulk center/window, OCR coverage, unresolved-review
   count, caveats, and the exact source IDs used.
3. Rebuild snapshots transactionally and expose snapshot age/status. `/trend`
   and `/ask` read the latest complete snapshot; they do not scan Telegram,
   invoke OCR, or recompute history synchronously.
4. If a snapshot is stale or incomplete, return that status with the last
   complete snapshot rather than presenting an unverified prediction.

### Phase E — Live parity, operations, and validation

- Route new Telegram messages through the same canonical ingest → OCR cache →
  classification → event/snapshot pipeline as backfill.
- Add a lock so backfill and live ingestion cannot race on the same chat/media.
- Add progress metrics, dry-run/date/chat filters, rate limiting, bounded
  concurrency, and restart-safe Pi checkpoints.
- Add BDD tests for multi-chat ID collisions, resume-after-failure, duplicate
  media hashes, OCR failures, ambiguous classifications, legacy migration,
  complete bulk-history forecasting, stale snapshots, and `/ask` using only the
  materialized snapshot.

### Rollout order

1. Ship schema migrations and read-only inventory/dry-run tooling.
2. Run a bounded backfill and inspect the review export and OCR coverage.
3. Enable canonical classification and snapshot generation with the current
   live path as a compatibility fallback.
4. Compare old/new counts and bulk windows, then switch `/trend` and `/ask` to
   the snapshot reader.
5. Run the full suite, verify Pi disk capacity and logs, and retain rollback to
   the previous snapshot.
