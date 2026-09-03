#!/usr/bin/env bash
# Create the Signal K admin account without touching the browser.
#
# Signal K v2 refuses every REST call with 401 until an admin user exists — which is
# correct, and which is also the first wall you hit when scripting the thing. This does
# it once, writes the password to .env.local (gitignored), and turns on read-only access
# for unauthenticated clients so other tools — a dashboard, an AI assistant — can read the
# boat without carrying a token around.
#
#   ./setup-security.sh            after `docker compose up -d`
#
# Read-only means read-only: nobody unauthenticated can change a setting or steer anything.

set -euo pipefail
cd "$(dirname "$0")"

CONTAINER="${CONTAINER:-signalk}"
CONFIG="./signalk-config"
USER="${SIGNALK_USER:-admin}"

if [ -f "$CONFIG/security.json" ]; then
  echo "security.json already exists — nothing to do. Delete it to start over."
  exit 0
fi

# openssl, not `tr </dev/urandom | head` — that pair raises SIGPIPE under `set -o pipefail`.
PASSWORD="$(openssl rand -base64 18 | tr -d '/+=')"

HASH="$(docker exec "$CONTAINER" node -e \
  "console.log(require('/home/node/signalk/node_modules/bcryptjs').hashSync(process.argv[1], 10))" \
  "$PASSWORD")"

SECRET="$(openssl rand -hex 32)"

cat > "$CONFIG/security.json" <<JSON
{
  "users": [
    { "username": "$USER", "type": "admin", "password": "$HASH" }
  ],
  "secretKey": "$SECRET",
  "immutableConfig": false,
  "allow_readonly": true,
  "allowNewUserRegistration": false,
  "allowDeviceAccessRequests": true,
  "expiration": "7d"
}
JSON

printf 'SIGNALK_USER=%s\nSIGNALK_PASSWORD=%s\n' "$USER" "$PASSWORD" > .env.local
chmod 600 .env.local

docker restart "$CONTAINER" >/dev/null
echo "admin created — credentials in signalk/.env.local (gitignored)"
echo "waiting for the server to come back…"

for _ in $(seq 1 30); do
  if curl -sf --max-time 3 http://localhost:3000/signalk/v1/api/vessels/self >/dev/null; then
    echo "read-only API is open: http://localhost:3000/signalk/v1/api/vessels/self"
    exit 0
  fi
  sleep 2
done

echo "server did not answer in 60 s — check: docker logs $CONTAINER" >&2
exit 1
