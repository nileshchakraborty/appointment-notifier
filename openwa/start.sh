#!/bin/sh
set -eu

OPENWA_FORK_DIR=/home/rpi/appointment-notifier/openwa-fork
OPENWA_COMMIT=177a310ae3e13c3205711f3b7dfe119f5263719f
export NODE_ENV=production
export WA_API_KEY="${OPENWA_API_KEY:?OPENWA_API_KEY is required}"

if [ "$(git -C "$OPENWA_FORK_DIR" rev-parse HEAD)" != "$OPENWA_COMMIT" ]; then
    echo "OpenWA checkout does not match pinned commit $OPENWA_COMMIT" >&2
    exit 1
fi

exec node "$OPENWA_FORK_DIR/packages/wa-automate/dist/cli.cjs" \
    --config ./wa.config.json \
    --host 172.17.0.1 \
    --port 8081 \
    --session-id appointment-notifier \
    --use-chrome \
    --qr-timeout 0
