# Appointment Notifier

Watches `@Regular_H1B_H4_VisaSlotsChecking` on Telegram and notifies you by Telegram bot, email, SMS, WhatsApp, and/or iMessage when a message looks like an available H1B/H4 visa appointment slot.

## Phase 1 Scope

- Connects to Telegram using your own Telegram API credentials.
- Watches the configured channel in real time.
- Checks a small recent-message backlog on startup.
- Suppresses common "no slots" messages.
- Deduplicates alerts in SQLite so restarts do not spam you.
- Supports Telegram bot, SMTP email, Twilio SMS, self-hosted OpenWA or Twilio WhatsApp, and local macOS iMessage notifications.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[sms,dev]"
cp .env.example .env
```

Fill in `.env`, then run:

```bash
set -a
source .env
set +a
appointment-notifier run
```

The first Telegram run will ask for your phone login code and create the session file at `TELEGRAM_SESSION_PATH`.

## Commands

```bash
appointment-notifier run
appointment-notifier test-notify
appointment-notifier parse-text "H1B Dropbox slots available in Chennai"
appointment-notifier trend
appointment-notifier trend --no-llm
appointment-notifier ask "When are appointments usually posted?"
```

## Notification Notes

Telegram bot alerts use `TELEGRAM_ALERT_ENABLED=true`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_ALERT_CHAT_ID`.

Email uses SMTP. Gmail generally requires an app password, not your normal account password.

SMS uses Twilio when `SMS_ENABLED=true` and Twilio credentials are present.

WhatsApp uses self-hosted OpenWA or Twilio. Set `WHATSAPP_PROVIDER=openwa` (the Raspberry Pi default) or `WHATSAPP_PROVIDER=twilio`.

iMessage uses macOS AppleScript through the local Messages app. macOS may prompt for Automation permissions.

## WhatsApp Alert Setup with OpenWA

The Raspberry Pi runs the pinned OpenWA fork in Docker with native ARM64 Chromium. OpenWA v4.76.0 no longer completes authentication against the current WhatsApp Web page, so this deployment intentionally uses the newer v5 alpha. OpenWA is unofficial WhatsApp Web automation; using it may violate WhatsApp's terms and can put the linked account at risk. Prefer a dedicated account.

Generate an API key and add these values to `.env`:

```bash
WHATSAPP_ENABLED=true
WHATSAPP_PROVIDER=openwa
WHATSAPP_TO=whatsapp:+15551234567
OPENWA_URL=http://openwa:8081
OPENWA_API_KEY=replace-with-a-long-random-value
```

The Compose build context expects the pinned fork at `openwa-fork`. Build and start both services:

```bash
git -C openwa-fork fetch origin master
git -C openwa-fork switch --detach fa63b0fca5367306bdf3fefe7efd9a6e299fc424
docker compose up -d --build
```

Open `http://192.168.1.229:8081/dashboard/` (or use `rpi.local` if mDNS works on your network), enter the OpenWA API key, and scan the QR code from WhatsApp's Linked Devices screen. Port 8081 is published only on the Pi's LAN address. The session is persisted under `openwa/sessions`.

After OpenWA reports that it is connected, send a test from the Pi:

```bash
docker compose exec appointment-notifier appointment-notifier test-notify
```

To use Twilio instead, set `WHATSAPP_PROVIDER=twilio` and configure `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `WHATSAPP_FROM`, and `WHATSAPP_TO` as before.

## Raspberry Pi Service

This app can run continuously on a Raspberry Pi with Docker Compose:

```bash
docker compose up -d --build
docker compose logs -f appointment-notifier
docker compose restart appointment-notifier
docker compose down
```

The container reads `.env` from the project directory and mounts `.state` so the Telegram session and SQLite state survive container rebuilds.

If using the older host-level systemd service:

```bash
sudo systemctl status appointment-notifier.service
sudo journalctl -u appointment-notifier.service -f
sudo systemctl restart appointment-notifier.service
sudo systemctl stop appointment-notifier.service
```

On Linux, keep `IMESSAGE_ENABLED=false`; iMessage delivery requires macOS Messages.

## Telegram Bot Alert Setup

1. Open Telegram and message `@BotFather`.
2. Send `/newbot`, choose a display name, then choose a bot username ending in `bot`.
3. Copy the token BotFather gives you into `.env` as `TELEGRAM_BOT_TOKEN`.
4. Open a chat with your new bot and send it any message, such as `start`.
5. Get your chat id:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates"
```

Look for `message.chat.id` in the JSON response. Put that value in `.env`:

```bash
TELEGRAM_ALERT_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:replace-with-real-token
TELEGRAM_ALERT_CHAT_ID=123456789
# Optional bootstrap users. Owners can manage users from Telegram commands.
TELEGRAM_BOT_ALLOWED_USERNAMES=friend_username
TELEGRAM_BOT_OWNER_CHAT_ID=123456789
```

Then test:

```bash
set -a
source .env
set +a
appointment-notifier test-notify
```

The same bot stores allowed command users in SQLite. On startup it seeds that table from `TELEGRAM_ALERT_CHAT_ID`, `TELEGRAM_BOT_ALLOWED_USERNAMES`, `TELEGRAM_BOT_OWNER_CHAT_ID`, and `TELEGRAM_BOT_OWNER_USERNAMES`, so keep your own numeric chat id in `TELEGRAM_BOT_OWNER_CHAT_ID` as a recovery path.

```text
/current
/last
/trend
/ask When are slots most often reported?
/forget
/status
/whoami
/users
/allow <chat_id|@username> [owner]
/revoke <chat_id|@username>
/help
```

`/current` returns the current boolean availability state. `/last` returns the last known availability timestamp/source. Authorized users can send normal text or use `/ask` to chat with the local model. `/forget` deletes that Telegram chat's bounded conversation history. `/users`, `/allow`, and `/revoke` are owner-only. `/revoke` will not remove owner rows.

## Historical Trend Forecast

Every observed Telegram message is retained in SQLite with its parser result.
`appointment-notifier trend` clusters matching posts into likely release events,
reports posting frequency and reports-per-event, and calculates a conservative
next window from historical gaps. These are community report counts, not the
number of bookable consular appointments.

The optional LLM integration explains deterministic statistics and provides
bounded Telegram chat. It does not generate or replace the statistical
forecast. Providers are attempted in `LLM_PROVIDER_ORDER`; failures, HTTP 429
responses, and unavailable providers move to the next configured provider.
Every response names the provider that answered. If all providers fail,
`/trend` still returns the deterministic report and chat fails closed with a
short retry message.

### Historical backfill

Run the resumable importer before relying on long-range forecasts:

```bash
appointment-notifier backfill --chat @Regular_H1B_H4_VisaSlotsChecking --limit 5000
```

Use `--all-chats` to inventory accessible Telegram groups/channels, or repeat
`--chat` for an explicit allowlist. The importer stores canonical messages,
OCR/layout results, parser classifications, checkpoints, and a materialized
trend snapshot in SQLite. It does not call the LLM. Re-running resumes from
the per-chat checkpoint and reuses cached OCR by media hash.

The supported providers are:

- `nvidia`: NVIDIA-hosted NIM using its OpenAI-compatible chat endpoint.
- `ollama_cloud`: direct authenticated access to Ollama Cloud.
- `ollama`: a local or remote Ollama server, including the Pi or a laptop.

Cloud API keys are read only from files mounted under `/run/secrets`; never put
them in `.env`. Create the host directory before starting Compose:

```bash
install -d -m 700 .secrets
```

The default secret filenames are `.secrets/nvidia_api_key` and
`.secrets/ollama_api_key`. Enable a provider only after its secret is present.
To enter or rotate either key without echoing it or placing it in shell history,
run `management/configure-llm-secrets.sh` on the Pi. Leaving a prompt blank
preserves the existing value.
For the NVIDIA-first chain:

```bash
LLM_PROVIDER_ORDER=nvidia,ollama_cloud,ollama
NVIDIA_NIM_ENABLED=true
NVIDIA_NIM_MODEL=openai/gpt-oss-20b
NVIDIA_NIM_TIMEOUT_SECONDS=90
OLLAMA_CLOUD_ENABLED=true
OLLAMA_ENABLED=true
```

The NVIDIA timeout is intentionally shorter than the other provider timeouts
so a stalled hosted deployment quickly yields to the configured fallbacks.

The lightweight `gemma3:1b` local Ollama model remains available as the final
fallback. `OLLAMA_URL` can point to any remote Ollama server, so moving local
inference off the Pi does not require a code change.

On the Raspberry Pi, run Ollama as a native systemd service so model memory is
not duplicated inside Docker. Install
`management/ollama.service.override.conf` as
`/etc/systemd/system/ollama.service.d/appointment-notifier.conf`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
ollama pull gemma3:1b
```

The override binds Ollama only to the Pi's LAN address, limits it to one loaded
model and one parallel request, and disables cloud model fallback. The Docker
container should use `OLLAMA_URL=http://192.168.1.229:11434`. Keep port 11434
limited to the trusted LAN.

## Future US Visa Scheduling Integration

Direct integration with `usvisascheduling.com` should be treated as high-risk automation. The safe next phase is:

- Keep Telegram monitoring as the primary low-risk signal.
- Use any official notifications or user-initiated checks first.
- If browser assistance is added, make it user-controlled, rate-limited, and auditable.
- Do not add CAPTCHA bypass, fingerprint spoofing, proxy rotation, or bot-detection evasion.
- Use long cool-downs after login failures or warning pages, because aggressive lookup behavior can lead to temporary blocks.
