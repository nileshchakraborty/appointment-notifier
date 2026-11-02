#!/bin/sh
set -eu

: "${WA_API_KEY:?WA_API_KEY is required}"

profile_dir=/app/chrome-profile/_IGNORE_default
for artifact in SingletonLock SingletonSocket SingletonCookie DevToolsActivePort; do
    if [ -e "${profile_dir}/${artifact}" ] || [ -L "${profile_dir}/${artifact}" ]; then
        rm -f -- "${profile_dir}/${artifact}"
    fi
done

Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
sleep 1

node <<'NODE'
const fs = require('node:fs');
const config = JSON.parse(fs.readFileSync('/app/wa.config.json', 'utf8'));
config.apiKey = process.env.WA_API_KEY;
fs.writeFileSync('/tmp/wa.config.json', JSON.stringify(config), { mode: 0o600 });
NODE

exec node /app/dist/cli.cjs --config /tmp/wa.config.json --headless
