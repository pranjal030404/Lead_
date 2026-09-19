# Arthvex LeadGen

Self-hosted lead generation for a web studio. Finds local businesses that need
websites, enriches them, scores them, tracks every interaction, and never
searches the same place twice.

Built from the v3.1 blueprint. FastAPI + SQLite + vanilla JS — no build step, no
database server, no paid API required to start. It runs on a $4/month VPS.

---

## Quick start

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Set `ADMIN_PASSWORD` and `SECRET_KEY` in `.env`, then:

```bash
.venv/bin/python run.py
```

Open http://127.0.0.1:8000 and sign in. Generate a secret key with:

```bash
python3 -c "import secrets;print(secrets.token_hex(32))"
```

Run the tests any time with:

```bash
.venv/bin/python -m pytest tests -q
```

### Windows

Replace `.venv/bin/` with `.venv\Scripts\` and `cp` with `copy` in the commands above.

---

## It works with no API key

`PROVIDER` picks where businesses come from:

| Provider | Cost | Notes |
|---|---|---|
| `osm` **(default)** | Free, no key, no billing | OpenStreetMap via Overpass. Carries name, address, and often phone and website. **No ratings or reviews.** Density varies by region — spot-check your city. |
| `google` | Per-SKU billing | Google Places API (New). Needs `GOOGLE_API_KEY`. Richest data. |
| `mock` | Free, offline | Deterministic fake businesses for demos and tests. |

Start on `osm` to prove the pipeline works on your target city, then switch to
`google` for the niches worth paying for.

---

## What it does

**Discovery** — search a niche + city + area. Every search is logged, and the
coverage map tracks which combos are FRESH / STALE / EXHAUSTED / NEVER, so you
always know where to look next.

**Search near me** — hit *Search near me* and the browser's coordinates drive the
search directly, with a radius you pick (1–25km). You don't type a city: the
server reverse-geocodes the point and fills in the city and area itself, so the
lead records and the coverage map read as places rather than decimals. Free via
Nominatim by default; set `GEOCODER=google` to use Google's geocoder instead.

**Map** — every lead with coordinates, plotted. Colour is priority, dot size is
score, and each pin opens the lead or jumps to it in Google Maps. *Find me*
centres the map on where you are. Leaflet is vendored into `static/vendor/`, not
pulled from a CDN — the tool runs on your box without depending on someone else's.

**Deduplication** — four checks in order: `place_id`, phone, exact name+city,
then fuzzy name match (80%+). A duplicate never creates a second lead; it fills
in blanks on the one you already have. Two different phone numbers block a fuzzy
merge, so chain branches don't collapse into one lead.

**Enrichment** — for each new lead: website reachability, HTTPS, mobile-
friendliness, load speed, platform detection (WordPress/Wix/Squarespace/...),
copyright-year staleness, contact routes, social links and `mailto:` addresses
scraped from the site. All free — no paid API.

**Scoring** — deterministic weighted rules, never a model. Every lead stores the
list of rules that fired, so you can always see why it scored what it did.

**Pipeline** — NEW → CONTACTED → REPLIED → MEETING SET → PROPOSAL SENT →
NEGOTIATION → WON/LOST, with interaction logging, follow-up dates, conversion
rates between every stage, and per-niche/per-city performance.

**The Lead Automator** — scheduled discovery, enrichment and scoring run
themselves. Follow-up messages are *generated* into an approval queue. Nothing
reaches a human until you approve it.

---

## The guardrails, built in from day one

These are the parts most tools skip, and the reason this one won't get you
banned or blacklisted.

- **Suppression list.** Checked immediately before *every* send — not when the
  message was generated, which could have been days earlier. Hard bounces are
  added automatically.
- **Approval gate.** The automator writes to `approval_queue`; only sending reads
  from it, and only rows you marked APPROVED.
- **Dry run.** `DRY_RUN=true` logs what would have been sent instead of sending
  it. Leave it on for a week and read the output before going live.
- **WhatsApp is click-to-chat only.** It generates a pre-filled `wa.me` link for
  you to tap. It never automates sends against a personal number — that is the
  single biggest account-ban risk in a tool like this.
- **Quota hard stop.** A running counter blocks the call *before* it goes out and
  raises `QuotaExceeded`, rather than warning after the fact.
- **Unsubscribe.** Every email gets an opt-out footer and `List-Unsubscribe`
  headers.
- **Hard delete.** `DELETE /api/leads/{id}` removes the lead and its entire
  history, for GDPR / DPDP erasure requests.
- **Auth + rate limiting.** Session cookies, hashed comparison, 8 login attempts
  per 5 minutes, 600 API calls/minute per IP.
- **Backups.** Nightly, via SQLite's online backup API so a backup taken
  mid-write is still consistent. 30-day retention, and `BACKUP_REMOTE` pushes
  each night's file off-server with rclone — a local backup survives losing the
  file, not losing the box. A failed sync never costs you the local copy that
  already succeeded; it shows up in the next morning's digest instead.
- **Alerts that reach you.** A new HOT lead, an API budget crossing 80%, and a
  daily health check. These go out through `notify.py`, not the outreach path:
  no unsubscribe footer, no suppression check, and they send during `DRY_RUN`.
  Mail to yourself must not be silenceable by the machinery that protects
  prospects — and a dry-run week you get no reports from isn't a test.
- **Circuit breakers.** Any dependency that fails 5 times in a row is paused for
  15 minutes instead of being hammered.

> **Set a Requests-per-day Quota limit in the Google Cloud Console too.** The
> in-app cap is a second layer. A Cloud Billing *budget alert* only emails you —
> it does not stop spending. Only a quota limit does.

---

## Scale

Defaults suit one operator on a $4 VPS. Every limit below is an environment
variable, so raising them is a config change and a restart, not a rewrite.

Four things were genuinely slow. Measured, changed, measured again:

| | Before | After |
|---|---|---|
| DB reads | 112/sec | 89,500/sec |
| DB writes | 15/sec | 6,050/sec |
| Enrichment, 12 leads with dead sites | 60.8s | 12.4s |
| `GET /api/leads` at 20 concurrent | 36/sec, 540ms | 141/sec, 138ms |

What actually caused each one:

- **A fresh SQLite connection per query**, re-running four PRAGMAs each time.
  Connections are now thread-local and reused. `synchronous=NORMAL` (the standard
  pairing for WAL) stopped the fsync on every commit — an OS crash can lose the
  last few transactions, it cannot corrupt the file.
- **A new `httpx.Client` per outbound call**, so every website check paid a fresh
  TCP and TLS handshake. One pooled client now, with keep-alive.
- **Strictly sequential enrichment.** It's almost all waiting on other people's
  web servers, and a dead site burns the full timeout — so 60 leads meant 60
  timeouts end to end. A bounded pool (`ENRICH_WORKERS`) overlaps the wait.
- **FastAPI's `jsonable_encoder`**, which walks every value of every response.
  Rows out of SQLite are already primitives, so the walk found nothing to convert
  and cost 13× plain `json.dumps` — 12.5ms per page, on the CPU running the event
  loop. The list endpoints return a `Response` directly to skip it, and send the
  12 columns the table draws instead of all 56 (`?full=true` for the whole row).

Under sustained mixed load — 700 requests, 24 concurrent — it holds 154 req/sec
at a p99 of 218ms with zero errors.

**Running more than one worker.** `uvicorn --workers N` used to mean N schedulers
all running the morning search. A lease in the database now means exactly one
process holds the scheduler; the rest stand by and take over within ~2 minutes if
it dies. Note that the rate limiter is per-process, so N workers effectively
multiply the limit by N — it's there to stop runaway loops, not to be an exact
quota.

**Behind a proxy, set `TRUST_PROXY=true`** or every visitor shares one rate-limit
bucket keyed on the proxy's own IP. Leave it off when the app is exposed
directly: `X-Forwarded-For` is client-controlled, and trusting it unproxied is a
free way to dodge the limiter.

---

## Deploying

**Docker** — one image, app and scheduler together:

```bash
docker compose up -d
```

Add TLS once `DOMAIN` and `TLS_EMAIL` are in `.env` (point the A record at the
box first, or the ACME challenge fails):

```bash
docker compose --profile tls up -d
```

The app binds loopback and Caddy terminates TLS. Data and backups are named
volumes, not bind mounts — SQLite's file locking is unreliable over a bind mount
into a VM, and broken locking on a database is data loss, not slowness.

**Without Docker** — `deploy/leadgen.service` is a hardened systemd unit; adjust
`User` and the paths, then `systemctl enable --now leadgen`.

### Deploy behind Apache on a VPS — full walkthrough

The complete path from a fresh Ubuntu VPS (22.04/24.04) to a live site at
`https://your.domain`. Same flow as the Docker route — the app binds loopback
and Apache terminates TLS; nothing is ever exposed directly.

**Step 0 — DNS first.** In your DNS panel, point an `A` record `your.domain`
at the box's public IP *before* running certbot, or the ACME challenge fails.

**Step 1 — log in and prep the box.**

```bash
ssh user@<VPS_IP>
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip apache2 certbot python3-certbot-apache
sudo a2enmod proxy proxy_http headers ssl
```

**Step 2 — firewall.** Allow SSH + web, drop the rest:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Apache Full'          # opens 80 and 443
sudo ufw --force enable
sudo ufw status                       # confirm both are allowed
```

**Step 3 — app user.** The service must never run as root:

```bash
sudo useradd --system --home /opt/leadgen --shell /usr/sbin/nologin leadgen
sudo mkdir -p /opt/leadgen
sudo chown leadgen:leadgen /opt/leadgen
```

**Step 4 — pull the code.**

```bash
sudo -u leadgen git clone https://github.com/pranjal030404/Lead_ /opt/leadgen
```

(Future updates are just `sudo -u leadgen git -C /opt/leadgen pull`.)

**Step 5 — venv and dependencies.**

```bash
sudo -u leadgen python3 -m venv /opt/leadgen/.venv
sudo -u leadgen /opt/leadgen/.venv/bin/pip install --upgrade pip
sudo -u leadgen /opt/leadgen/.venv/bin/pip install -r /opt/leadgen/requirements.txt
```

**Step 6 — `.env`.** Generate a key first with `python3 -c "import secrets;print(secrets.token_hex(32))"`, then:

```bash
sudo -u leadgen cp /opt/leadgen/.env.example /opt/leadgen/.env
sudo -u leadgen nano /opt/leadgen/.env
```

Set at minimum:

```env
ADMIN_USER=admin
ADMIN_PASSWORD=<your strong password>
SECRET_KEY=<paste the generated hex>
COOKIE_SECURE=true      # we're behind HTTPS now
TRUST_PROXY=true        # rate limits key on X-Forwarded-For from Apache
HOST=127.0.0.1          # loopback only - Apache is the front door
PORT=8000
PROVIDER=osm            # or google (needs GOOGLE_API_KEY)
DRY_RUN=true            # keep true until you've watched the queue for a week
```

**Step 7 — run the app as a service.** The repo ships a hardened unit at
`deploy/leadgen.service`. Confirm it says `User=leadgen`,
`WorkingDirectory=/opt/leadgen`, `EnvironmentFile=/opt/leadgen/.env`, then:

```bash
sudo cp /opt/leadgen/deploy/leadgen.service /etc/systemd/system/leadgen.service
sudo systemctl daemon-reload
sudo systemctl enable --now leadgen
sudo systemctl status leadgen                          # active (running)
sudo curl -s http://127.0.0.1:8000/health              # {"status":"ok"}
```

**Step 8 — point Apache at it.** Install the shipped vhost and swap in your
domain:

```bash
sudo cp /opt/leadgen/deploy/leadgen-apache.conf /etc/apache2/sites-available/leadgen.conf
sudo sed -i 's/leadgen\.example\.com/your.domain/g' /etc/apache2/sites-available/leadgen.conf
sudo a2dissite 000-default
sudo a2ensite leadgen
sudo apachectl configtest                              # Syntax OK
sudo systemctl reload apache2
```

`http://your.domain` now reverse-proxies to uvicorn on `127.0.0.1:8000`.

**Step 9 — TLS, the moment it goes live.** certbot rewrites the vhost for
HTTPS and wires auto-renewal:

```bash
sudo certbot --apache -d your.domain
sudo certbot renew --dry-run                            # renewal actually works
curl -s https://your.domain/health                      # {"status":"ok"}
```

Sign in at `https://your.domain/login`.

**Step 10 — config for a live run.** Pick up the shared checklist below
exactly as written: quotas, `ALERT_EMAIL`, `DRY_RUN` for a week, then flip
automation switches one at a time.

**Updating the app later.**

```bash
sudo -u leadgen git -C /opt/leadgen pull
sudo -u leadgen /opt/leadgen/.venv/bin/pip install -r /opt/leadgen/requirements.txt
sudo systemctl restart leadgen
```

Traffic path once live: browser → Apache `:443` (TLS) → uvicorn
`127.0.0.1:8000` → SQLite. `journalctl -u leadgen -f` for app logs,
`/var/log/apache2/leadgen-*.{log}` for web logs.

> The `deploy/leadgen-apache.conf` ships with the same security headers as
> `deploy/Caddyfile`. Do not add a `DocumentRoot` for the app vhost: every
> request is proxied, and `.env`/`data/` must stay out of Apache's document
> tree. mod_proxy already forwards `X-Forwarded-For` (`ProxyAddHeaders` is on
> by default) and the app reads its *first* entry — don't also enable
> `RemoteIPHeader`/`mod_remoteip` for this vhost or the address gets
> double-stamped.

Either way, in order:

1. `PROVIDER`, `ADMIN_PASSWORD`, `SECRET_KEY` in `.env`.
2. `COOKIE_SECURE=true` and `TRUST_PROXY=true` once you're behind HTTPS.
3. Set the Requests-per-day Quota limit in the Google Cloud Console.
4. Set `ALERT_EMAIL`, and `BACKUP_REMOTE` after `rclone config`. Run the digest
   job once from the Automation page and confirm the mail actually arrives —
   this is the channel that tells you when everything else breaks.
5. Leave `DRY_RUN=true` for a week. Read the approval queue daily.
6. Turn the switches on one at a time: discovery, then enrichment, then
   follow-up generation, and sending last.

Stay on SQLite until you have concurrent writers or 100k+ leads; move to
Postgres at that point, not before.

---

## Where this deviates from the blueprint, and why

Four places where the spec as written doesn't survive contact with real data.
All four are deliberate, and the code says so at the point of the change.

**1. Scoring weights reach the HOT band.** As specified (`no website +4`,
`social+no site +2`, `reviews≥20 +1`, HOT at 8–10), the ideal prospect tops out
around 6–7 and is never HOT — so the operator's main working queue stays empty.
Worse, a business with no website can't earn the social bonus, because social
discovery reads a business's own website. Added an explicit combination bonus:
*real business + no working website = ideal prospect (+2)*.

**2. "Established" has a fallback when there are no reviews.** OpenStreetMap
carries no review counts at all, so a review-based signal makes HOT unreachable
on the free provider. When review data is absent, the code falls back to whether
the listing is actually contactable (valid phone + address). Verified against
live Faridabad data: contactable no-website restaurants score 8 (HOT),
uncontactable ones score 4 (COLD) — which is the right call for outreach anyway.

**3. Fuzzy matching needed token containment.** The spec names
`"Sharma's Dental"` vs `"Sharma Dental Clinic"` as a duplicate at 80%+. Plain
string similarity scores that pair 0.74 and misses it, because the second name
is simply longer. `similarity()` blends character similarity with token
containment, and `normalise_name()` strips possessives first (a stray one-letter
`s` token badly skews containment).

**4. The hot-lead alert fires at 8, not 9.** Part 7.6 asks for an alert when a
lead scores 9–10. Nothing can score 9. The only rule that lifts a lead past 8 is
the *social but no website* bonus, and social links are scraped from the
business's own website — which the ideal prospect, by definition, doesn't have.
Verified against live data: every HOT lead in the database sits at exactly 8. A
threshold of 9 would have shipped an alert that silently never fires, which is
the precise failure the alert exists to prevent. `HOT_ALERT_MIN_SCORE` defaults
to 8, and a test asserts an ideal prospect still clears it — so if you change the
scoring weights, the suite tells you to re-check the threshold.

One more thing to re-check yourself: **the Google Places field-to-price-tier
mapping in `app/providers/google_places.py` moves between Google's pricing
updates.** The staged calling pattern is what saves money regardless — never
fetch details for a place you already have, never fetch ratings for a lead that
already looks cold — but verify the exact field groupings against Google's live
pricing page before scaling usage up.

---

## Layout

```
app/
  main.py              FastAPI app, auth middleware, static serving
  config.py            environment / .env loading
  db.py                schema + SQLite helpers
  search_service.py    discovery -> dedup -> staged fetch -> enrichment
  dedup.py             duplicate detection and merging
  enrich.py            the enrichment pipeline
  website.py           free website analysis
  scoring.py           deterministic lead scoring
  validation.py        phone/email normalisation, data quality score
  outreach.py          templates, suppression, approval queue, sending
  automation.py        the Lead Automator: scheduler, jobs, kill switch
  notify.py            operator alerts: hot leads, quota, daily digest
  geo.py               reverse/forward geocoding for "search near me"
  fastjson.py          fast responses for the big list endpoints
  quota.py             API budget hard stop
  http.py              pooled client, timeouts, backoff, circuit breakers
  security.py          sessions, password hashing, rate limiting
  backup.py            nightly backup + retention + off-site sync
  providers/           osm, google_places, mock
  routers/             the HTTP API
  static/              the whole UI (no build step)
  static/vendor/       Leaflet, vendored so the map needs no CDN
deploy/
  Caddyfile            reverse proxy + automatic HTTPS
  leadgen-apache.conf  Apache vhost: TLS + reverse proxy to 127.0.0.1:8000
  leadgen.service      hardened systemd unit
Dockerfile             non-root image, healthcheck, tini
docker-compose.yml     app alone, or `--profile tls` with Caddy
tests/test_core.py     43 tests, no network, no API key
```

---

## Not built

Honest list of what the blueprint describes that isn't here:

- **Automated social discovery beyond a business's own website.** Instagram and
  Facebook links are extracted from the site's HTML (free, reliable). Going
  further needs a search API; scraping a search engine directly is fragile and
  against most of their terms, so `stage_social` in `app/enrich.py` is a
  documented extension point rather than a broken scraper.
- **WhatsApp Business Cloud API.** Click-to-chat only, by design.
- **Multi-user accounts.** Single operator, single login.
- **Telegram/WhatsApp alert channels.** Part 7.6 offers these as alternatives to
  email; only email is wired up.
"# Lead_" 
