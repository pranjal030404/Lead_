# DB connection hardening - deployment runbook

Nothing here has been run against production. `/var/www/Lead_` and the live
MySQL users/permissions were not touched.

## Backup (before deploying)

```bash
sudo systemctl cat leadgen > ~/leadgen.unit.$(date +%F).bak        # live unit, for rollback
sudo cp -a /var/www/Lead_ /var/www/Lead_.pre-dbfix.$(date +%F)       # code + data/ + backups/
sudo -u leadgen sh -c 'set -a; . /etc/leadgen/leadgen.env; set +a;   # path of the live env file may differ
  mysqldump --single-transaction --routines "$MYSQL_DATABASE" -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" \
  > /var/www/Lead_/backups/pre-dbfix-$(date +%F).sql'
cd /var/www/Lead_ && git rev-parse HEAD > ~/leadgen.pre-dbfix.rev     # exact rollback target
```

There are no schema changes in this release, so a DB restore is only needed if
something unrelated goes wrong.

## Deploy

```bash
cd /var/www/Lead_ && git status --short            # must be clean (or know why not)
git fetch && git checkout <commit-with-this-change>
.venv/bin/python -m py_compile app/db.py app/main.py app/config.py app/quota.py \
    app/automation.py app/search_service.py
# optional: add to the live env file (defaults are already sane)
#   MYSQL_READ_TIMEOUT=120
#   MYSQL_WRITE_TIMEOUT=30
sudo systemctl restart leadgen                       # one restart to load the new code
```

Optional hardened unit (review the diff against `systemctl cat leadgen` first -
User/Group and the env-file path are assumptions):

```bash
diff <(systemctl cat leadgen) deploy/leadgen.production.service
sudo install -m 0644 deploy/leadgen.production.service /etc/systemd/system/leadgen.service
sudo systemctl daemon-reload && sudo systemctl restart leadgen
```

## Verify

```bash
systemctl is-active leadgen
journalctl -u leadgen -n 50 --no-pager | grep -iE "error|traceback" || echo "no errors"
for p in /health/live /health/ready /health /landing /favicon.ico; do
  printf '%s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' https://lead.arthvex.co.in$p)"; done
# expected: 200 200 200 200 200 (favicon may be 302)
curl -s https://lead.arthvex.co.in/health/ready        # {"status":"ok","checks":{"mysql":"ok","migrations":"ok"}}
journalctl -u leadgen --since "1 hour ago" | grep -c "InterfaceError"   # should stay 0
journalctl -u leadgen -f | grep "leadgen.db"           # "reconnecting and retrying once" = a self-heal, not a failure
```

Soak check (the real acceptance test - do it in a maintenance window you
control): `sudo systemctl restart mysql` (or mariadb) and, without restarting
leadgen, confirm `/health/ready` is 503 while MySQL is down and 200 again within
seconds of it coming back, and `/landing` serves again.

## Rollback

```bash
cd /var/www/Lead_ && git checkout "$(cat ~/leadgen.pre-dbfix.rev)"     # or: sudo rsync -a --delete /var/www/Lead_.pre-dbfix.<date>/ /var/www/Lead_/
# unit, if replaced:  sudo cp ~/leadgen.unit.<date>.bak /etc/systemd/system/leadgen.service  (strip the "# /etc/..." header line)
sudo systemctl daemon-reload && sudo systemctl restart leadgen
```

## Optional external watchdog

systemd cannot see an app that is alive but unhealthy. The connection manager
makes DB outages self-healing, so this is a last resort:

```ini
# /etc/systemd/system/leadgen-healthwatch.service
[Service]
Type=oneshot
ExecStart=/var/www/Lead_/deploy/leadgen-healthwatch.sh
# /etc/systemd/system/leadgen-healthwatch.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
```

It restarts the service only after 10 consecutive minutes of `/health/ready`
failing while `/health/live` succeeds.
