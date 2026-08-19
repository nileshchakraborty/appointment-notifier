#!/bin/sh
set -eu

BASE=/home/rpi/appointment-notifier/management/tls
CERT="$BASE/server.crt"
KEY="$BASE/server.key"
CA_CERT="$BASE/local-ca.crt"
CA_KEY="$BASE/local-ca.key"
LOCK=/run/lock/rpi-caddy-tls-renew.lock

exec 9>"$LOCK"
flock -n 9 || exit 0

mkdir -p "$BASE"
chmod 700 "$BASE"

if [ ! -s "$CA_CERT" ] || [ ! -s "$CA_KEY" ]; then
  umask 077
  openssl req -x509 -newkey rsa:4096 -sha256 -nodes -days 3650 \
    -keyout "$CA_KEY" -out "$CA_CERT" \
    -subj "/CN=RPi Local CA"
fi

if [ -s "$CERT" ] && [ -s "$KEY" ] && openssl x509 -checkend 2592000 -noout -in "$CERT"; then
  exit 0
fi

work=$(mktemp -d "$BASE/.renew.XXXXXX")
trap 'rm -rf "$work"' EXIT
umask 077

cat > "$work/server.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:rpi.local,DNS:rpi.home.arpa,DNS:*.rpi.home.arpa
EOF

openssl req -new -newkey rsa:4096 -nodes -sha256 \
  -keyout "$work/server.key" -out "$work/server.csr" \
  -subj "/CN=rpi.home.arpa"
openssl x509 -req -sha256 -days 397 \
  -in "$work/server.csr" -CA "$CA_CERT" -CAkey "$CA_KEY" \
  -CAcreateserial -out "$work/server.crt" -extfile "$work/server.ext"

install -m 600 "$work/server.key" "$KEY"
install -m 644 "$work/server.crt" "$CERT"
docker restart rpi-caddy >/dev/null
