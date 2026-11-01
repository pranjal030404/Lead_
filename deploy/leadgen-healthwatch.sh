#!/bin/sh
# OPTIONAL external watchdog (not installed by default). Restarts the service
# only if /health/ready keeps failing while /health/live is fine, i.e. the app is
# up but stuck - something systemd's Restart= can never see. With the connection
# manager in app/db.py a plain MySQL outage heals itself, so this should almost
# never fire; it exists as a last resort and is deliberately slow to act.
#
# Run from a systemd timer every minute (see DEPLOY_DB_HARDENING.md).
URL=${URL:-http://127.0.0.1:8000}
STATE=${STATE:-/run/leadgen-healthwatch.fails}
LIMIT=${LIMIT:-10}   # consecutive failed minutes before acting

if curl -fsS --max-time 10 "$URL/health/ready" >/dev/null 2>&1; then
    rm -f "$STATE"; exit 0
fi
# Only act when the process itself answers: if live also fails, systemd's own
# restart logic (or a real outage of the host) is the right owner.
curl -fsS --max-time 10 "$URL/health/live" >/dev/null 2>&1 || exit 0
n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
if [ "$n" -ge "$LIMIT" ]; then
    logger -t leadgen-healthwatch "ready failing for $n checks while live is ok; restarting leadgen"
    rm -f "$STATE"
    systemctl restart leadgen
fi
