# Arthvex LeadGen — Detailed Description

A complete, code-level walkthrough of how this application works and every feature
it contains. Written by reading the source, not the README — where the two differ
or where the code has a rough edge, this document says so.

---

## Table of contents

1. [What this application is](#1-what-this-application-is)
2. [Technology stack](#2-technology-stack)
3. [Architecture and file map](#3-architecture-and-file-map)
4. [The request lifecycle](#4-the-request-lifecycle)
5. [Startup sequence](#5-startup-sequence)
6. [The core pipeline, end to end](#6-the-core-pipeline-end-to-end)
7. [Feature catalogue](#7-feature-catalogue)
8. [Data model — every table](#8-data-model--every-table)
9. [Scoring engines, rule by rule](#9-scoring-engines-rule-by-rule)
10. [HTTP API reference](#10-http-api-reference)
11. [Configuration reference](#11-configuration-reference)
12. [Performance engineering](#12-performance-engineering)
13. [Security model](#13-security-model)
14. [Testing](#14-testing)
15. [Deployment](#15-deployment)
16. [Known rough edges and limitations](#16-known-rough-edges-and-limitations)
17. [Glossary](#17-glossary)

---

## 1. What this application is

**Arthvex LeadGen** is a self-hosted lead-generation system for a web studio. Its
business purpose is narrow and specific:

> Find small local businesses that need a website, work out whether they really
> need one, and get a human to contact them — without ever contacting anyone
> automatically.

The "without ever contacting anyone automatically" is not a footnote; it is the
central design constraint. The system automates the entire *upstream* of sales —
finding, enriching, scoring, scheduling — and stops dead at the moment a message
would reach a real person. A message is *generated* automatically, parked in an
approval queue, and only goes out after a human marks it APPROVED.

The second load-bearing constraint is **cost discipline**. The system is designed
so that a solo operator can run it on a ~$4/month VPS. That drives the free-first
provider strategy, the staged API calling, the shared search cache, and the quota
hard stop.

The third is **reproducibility**. Every score is deterministic and explains
itself, every search is logged, every duplicate merge is recorded, and coverage
is tracked per niche/city/area so the operator always knows where they have
already looked.

### The data flow in one picture

```
                      ┌──────────────────────────────────────────┐
   operator types     │  POST /api/search                        │
   niche + city  ───► │  1. plan entitlement check (gating)      │
                      │  2. search-cache lookup by fingerprint   │
                      │  3. insert a `searches` row (RUNNING)    │
                      │  4. spawn a worker thread                │
                      └────────────────┬─────────────────────────┘
                                       │
   ┌───────────────────────────────────▼─────────────────────────────────────┐
   │ run_search()  — blocking, in the worker thread                          │
   │                                                                          │
   │  STAGE 1  provider.search()      cheap fields, every result             │
   │             ├── osm    (Overpass + Nominatim, free)                     │
   │             ├── google (Places API New, billed, stage-1 field mask)    │
   │             └── mock   (deterministic offline generator)                │
   │                                                                          │
   │  for each candidate:                                                     │
   │    ┌── DEDUP ── 4 checks: place_id → phone → name+city → fuzzy ≥0.80     │
   │    │      hit  → merge blanks into the existing lead, ROLLBACK out, next │
   │    │      miss → CONTINUE                                                 │
   │    │                                                                     │
   │    ├── STAGE 2  fetch_details()   phone/website/status  (new places only)│
   │    │      skip if business_status is CLOSED_PERMANENTLY                  │
   │    │                                                                     │
   │    ├── STAGE 3  fetch_ratings()   rating/reviews                        │
   │    │      only if provisional score ≥ ENTERPRISE_FETCH_MIN_SCORE (5)      │
   │    │                                                                     │
   │    └── INSERT lead (scored provisionally)                                │
   │                                                                          │
   │  ENRICH  enrich_many(new_ids)  — bounded thread pool                    │
   │          stage 1 website check ─ 2 social ─ 3 email ─ 4 validate ─ 5 score│
   │                                                                          │
   │  ALERT  notify.alert_new_hot_leads(new_ids)                              │
   │  WRITE  searches row (COMPLETED) + search_cache snapshot + coverage      │
   │  CHARGE subscription.count_search(user_id)                               │
   └──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ (`.venv` is 3.14) | `from __future__ import annotations` throughout |
| Web framework | FastAPI 0.115.6 | ASGI, middleware-based auth, no controllers |
| ASGI server | uvicorn[standard] 0.34.0 | loopback-bound, TLS terminated by a proxy |
| Database | **MySQL 8 / MariaDB** via PyMySQL 1.2.3 | the only storage backend now; raw SQL, no ORM |
| Scheduling | APScheduler 3.11.0 `BackgroundScheduler` | cron + interval triggers, UTC |
| HTTP client | httpx 0.28.1 | one pooled, keep-alive `Client` for the process |
| Config | python-dotenv 1.0.1 | `.env` at repo root |
| Frontend | Vanilla JS, ~2,000-line single `app.js` | **no build step, no framework, no bundler** |
| Maps | Leaflet, **vendored** into `static/vendor/` | deliberately not a CDN |
| Icons | inline SVG | no icon font |
| Fonts | Google Fonts (Inter, Sora) | the only CDN dependency in the app |
| Tests | pytest, 48 tests | no network, no API key, runs against `leadgen_test` |

**Dependencies: seven packages.** That is the whole list. There is no ORM, no
task queue, no Redis, no payment SDK, no email framework.

---

## 3. Architecture and file map

```
app/
  main.py              134  FastAPI app, auth+rate-limit middleware, static serving
  config.py            178  env/.env loading, Settings class, first-run secret gen
  db.py                613  schema DDL, thread-local MySQL connections, query helpers
  search_service.py    484  discovery orchestration, dedup→fetch→insert, coverage, cache
  dedup.py             169  duplicate detection, fuzzy matching, merging, table sweep
  enrich.py            264  5-stage enrichment pipeline, bounded thread pool
  website.py           224  free website analysis (reachability, SSL, speed, platform)
  scoring.py           143  deterministic lead scoring + copy observation helpers
  validation.py        103  phone/email normalisation, data-quality score
  outreach.py          358  template merge, suppression, approval queue, sending
  automation.py        445  scheduler, 7 jobs, kill switches, leader-election lease
  notify.py            221  operator alerts: HOT leads, quota, daily digest
  geo.py               144  reverse/forward geocoding ("search near me")
  fastjson.py           51  fast responses for the big list endpoints
  quota.py             128  API budget hard stop with transactional reserve
  http.py              152  pooled client, timeouts, backoff, circuit breakers
  security.py          170  sessions, password hashing, roles, rate limiting
  subscription.py      119  plan entitlements, search accounting, gating
  backup.py            129  nightly mysqldump + retention + rclone off-site sync
  seed.py              172  8 default outreach templates
  providers/
    base.py             66  Provider Protocol + blank_candidate()
    osm.py             171  OpenStreetMap Overpass + Nominatim
    google_places.py   187  Google Places API (New), 3-tier staged field masks
    mock.py             73  deterministic offline generator
    __init__.py        35  provider registry + availability check
  routers/                   the HTTP API
    auth.py            58  login / logout / me
    search.py         269  search, geo, coverage, targets, providers, quota
    leads.py          328  list, filter, export, edit, delete, enrich, bulk, interactions
    stats.py          260  dashboard stats, pipeline, conversions, weekly, follow-ups
    outreach.py       195  templates, approval queue, suppression, identity
    automation.py     121  switches, kill, jobs, failures, digest, backups
    users.py          121  user CRUD, role guardrails
    plans.py          278  plans, subscriptions, ordering, payment toggle
  static/
    index.html         86  app shell + sidebar nav
    app.js           2,012  hash router + all 16 page renderers
    styles.css        589  app styling (CSS custom properties, dark theme)
    landing.html      326  public marketing page
    landing.css       394  marketing page styling
    login.html        105  sign-in form
    vendor/                Leaflet (js + css), vendored
tests/
  test_core.py        624  48 tests
  conftest.py          90  forces MYSQL_DATABASE=leadgen_test, refuses otherwise
deploy/
  Caddyfile            36  reverse proxy + automatic HTTPS
  leadgen-apache.conf  58  Apache vhost: TLS + reverse proxy
  leadgen.service      48  hardened systemd unit
Dockerfile            42 lines  non-root image, healthcheck, tini
docker-compose.yml    85 lines  app + MySQL (+ --profile tls with Caddy)
scripts/migrate_sqlite_to_mysql.py   111  one-time SQLite → MySQL copy
run.py                8 lines  uvicorn launcher
```

Roughly **11,000 lines** of hand-written code (backend + frontend + deploy +
tests), with the frontend (`app.js`, ~2,000 lines) being the single largest file.

---

## 4. The request lifecycle

Every HTTP request passes through one middleware in `app/main.py:63`:

```
request
  │
  ├─▶ is it /api/* ? ── yes ──▶ rate_limit(f"api:{client_ip}", 600, 60s)
  │                                   └─ over limit ──▶ 429 JSONResponse
  │
  ├─▶ path in PUBLIC_PATHS or starts with /static/ ? ── yes ──▶ pass through
  │        PUBLIC_PATHS = /landing, /login, /health, /api/auth/login, /favicon.ico
  │        (static assets get Cache-Control: no-cache so a deploy is picked up,
  │         and the ETag turns that into a cheap 304 instead of a re-download)
  │
  ├─▶ verify_token(cookie)  →  HMAC-verify the signed session token
  │        └─ invalid/absent → is it /api/* ?  401 JSON  :  302 → /login
  │
  ├─▶ load_user(username)   →  re-read the row from MySQL on EVERY request
  │        └─ this is deliberate: a deactivated or deleted account is locked out
  │           on its very next request, not when the token happens to expire
  │
  └─▶ request.state.user = user  →  call the route
             └─ route-level role checks via Depends(require_role(...))
```

`/health` is deliberately unauthenticated — it is meant to be pointed at by an
uptime monitor — and it runs `SELECT 1`, returning 503 with the exception text if
the database is unreachable.

### The frontend router

`app.js` implements a ~30-line hash router:

- Routes are registered as `routes['/dashboard'] = async () => {...}`.
- `navigate()` parses `location.hash` into path + query params.
- Admin-only routes (`/users`, `/plans`, `/settings`) redirect to `/dashboard`
  for anyone below `admin` role — **client-side only**; the server enforces the
  same rule via `require_role` dependencies, so the redirect is cosmetic.
- On every navigation it paints a skeleton, then swaps in the rendered page.
  A render rejection paints a red banner with the error message.
- `refreshBadges()` polls `/api/stats` on load and every 60s to keep the sidebar
  counters (leads / follow-ups due / approvals pending) fresh.
- `window.__me` is populated once at boot from `/api/auth/me` and is the source
  of truth for all role checks in the UI.

---

## 5. Startup sequence

`app/main.py:21` defines a `lifespan` async context manager that runs on boot
and shutdown:

**On start:**
1. `settings.ensure_runtime_secrets()` — generates a `SECRET_KEY` and an
   `ADMIN_PASSWORD` if the operator left them blank, so first run works with zero
   configuration.
2. `init_db()` (`app/db.py:579`) —
   - executes all 21 `CREATE TABLE IF NOT EXISTS` statements
   - applies schema drift: adds `searches.user_id` and `searches.cached_from`
     if an older database is missing them (checked via `information_schema`)
   - seeds the 8 pipeline stages with `INSERT IGNORE`
   - seeds default automation switches
   - `seed_templates()` — 8 outreach templates with `INSERT IGNORE`
   - seeds the `superadmin` user from `ADMIN_USER`/`ADMIN_PASSWORD` if no
     superadmin row exists
3. `start_scheduler()` — see [the Lead Automator](#73-the-lead-automator).
4. Prints a startup banner: URL, active provider, dry-run state, and loud
   warnings if the password or secret key was auto-generated.

**On shutdown:** `stop_scheduler()` shuts the scheduler down and, if this process
held the lease, deletes the lease row so a standby worker picks up immediately
rather than waiting out the full 120-second lease.

---

## 6. The core pipeline, end to end

### 6.1 Entry point and gating

`POST /api/search` (`app/routers/search.py:34`):

1. Rejects a blank niche with 400.
2. **Subscription gate** — `check_search_entitlement(user)`. `superadmin` always
   passes. Everyone else needs an active, unexpired subscription with searches
   remaining. Failures return 403 with a specific reason
   (`no_active_subscription` or `search_limit_reached`).
3. If `lat`/`lng` are present, clamps `radius_m` to `MAX_NEARBY_RADIUS_M` and —
   if no city was typed — reverse-geocodes the point to fill in city/area/country.
   Geocoding failure is a 400.
4. If no city and no coordinates: 400.
5. **Cache probe** — computes the fingerprint and looks for a live snapshot.
6. **Quota pre-check** — 429 if the daily Google cap is exhausted *and* this is
   not a cache hit. The comment explains why: a cache hit makes zero API calls,
   so it must still work at the wall — that is exactly when the cache is most
   valuable.
7. `start_search(...)` inserts the `searches` row and spawns a daemon thread,
   returning `{"search_id": N, "status": "RUNNING"}` immediately.

The browser then polls `GET /api/search/{id}/status` every 1.2s
(`pollSearch`) and renders the live `progress` text until the status leaves
`RUNNING`.

### 6.2 The coverage map

Before running, `_update_coverage()` maintains one row per
`(city, area, niche, country)` in `search_coverage`, and
`coverage_status()` derives a status:

| Status | Condition | Meaning |
|---|---|---|
| `NEVER` | no `last_searched` | never searched — highest priority |
| `FRESH` | last searched < 7 days ago | probably nothing new yet |
| `STALE` | ≥ 7 days ago | new businesses may have appeared |
| `EXHAUSTED` | `barren_runs >= 3` | three runs in a row found zero new leads |

`barren_runs` increments each time a search of that combo produces 0 new leads
and resets to 0 the moment it produces any. `EXHAUSTED` is not just a label —
`job_discovery` **deactivates the target** and logs a warning telling the
operator to pick a new area, so the automator stops burning quota on a dried-up
combo.

`GET /api/search/preview` returns this status plus a human sentence, and the
Search page calls it on blur of any of the four fields.

### 6.3 The shared search cache

`app/search_service.py:99-235`. This is a genuinely distinctive feature.

**The fingerprint** is a SHA-256 over everything that *defines the result set*:

```python
(niche, city, area, country, provider, round(lat,6), round(lng,6), radius_m)
```

all lowercased/stripped. `max_results` is **deliberately excluded** — it is a
page size, not content. Two users on different plans with different caps share
the cache, and the snapshot simply serves the top-N each asked for. The test
`test_fingerprint_is_case_and_space_insensitive` locks in the normalisation.

**Lookup** (`find_cached_search`) refuses the snapshot if the cache is disabled,
if the producing search did not reach `COMPLETED`, or if
`created_at` is older than `SEARCH_CACHE_TTL_DAYS` (default 7).

**Serve** (`_serve_from_cache`) copies the source search's counts into the new
`searches` row, sets `cached_from = source_id`, `api_calls = 0`, writes an
automation log line, and **still charges the user's subscription** — it is one of
their searches. Enrichment and alerts are *not* re-run.

**Refresh** (`record_cache`) upserts on the fingerprint and resets `created_at`
every time a search completes, so the TTL measures from the last scrape.

**Freshness is preserved** by only consulting the cache for
`triggered_by == "manual"`. Scheduled and target runs always scrape fresh, so
the daily sweep continuously refreshes snapshots and the cache can never go
permanently stale. Four dedicated tests cover this:
`test_identical_manual_search_is_served_from_cache_with_zero_api_calls`,
`test_changed_query_is_a_cache_miss`, `test_automated_searches_bypass_the_cache`,
`test_cache_expires_after_ttl_then_scrapes_fresh`.

### 6.4 Deduplication

`app/dedup.py`. Four checks, ordered cheapest-and-most-certain first:

| # | Check | Confidence |
|---|---|---|
| 1 | `place_id` exact match (unique indexed column) | certain |
| 2 | `phone` exact match, non-empty | very high |
| 3 | normalised `business_name` + same `city` | high |
| 4 | fuzzy `similarity() >= 0.80` within the same city | good |

**Normalisation** (`normalise_name`) lowercases, strips possessives *first*
(`sharma's` → `sharma`, not `sharma s` — a stray one-letter token badly skews
containment), replaces punctuation, drops single-character tokens, and removes
21 noise words (`the`, `co`, `pvt`, `ltd`, `shop`, `centre`, `&`, …).

**Similarity** blends two measures:

```python
sequence    = SequenceMatcher(None, na, nb).ratio()          # character-level
containment = |tokens_a ∩ tokens_b| / min(|tokens_a|, |tokens_b|)
return (sequence + containment) / 2
```

This exists because of a concrete failure. The spec names
`"Sharma's Dental"` vs `"Sharma Dental Clinic"` as a duplicate at 80%+; pure
character similarity scores that pair **0.74** and misses it, because the second
name is simply longer. Containment catches it (every token of the shorter name
appears in the longer one), and the average keeps unrelated names well below
threshold.

**Chain-branch protection** — if a candidate and a candidate-existing lead both
have phones and the phones differ, the pair is skipped before the fuzzy check.
This is what stops "Domino's Sector 15" and "Domino's Sector 21" collapsing into
one lead.

**Merging** (`merge_into_existing`) only ever **fills blanks** across a fixed
20-field `MERGEABLE` list. A re-search never overwrites data you already have or
manually edited. The search loop runs the dedup check inside a transaction and
**rolls back** on a miss so the read lock doesn't linger.

**Table sweep** (`dedupe_existing_leads`, exposed at `POST /api/leads/dedupe`)
walks all leads in id order, and on a match merges, **re-points
`interactions.lead_id`** at the survivor, and deletes the duplicate. This is the
recovery path for duplicates that predate a rule change.

### 6.5 Staged provider fetching

`app/providers/base.py` defines the `Provider` Protocol with three methods, and
`search_service.run_search` calls them in a deliberate order:

**Stage 1 — `provider.search()`** runs for *every* result. Cheap fields only:
identity, address, coordinates. Enough to answer "do we already have this?"

**Stage 2 — `provider.fetch_details()`** runs only for places that survived dedup.
This is where phone/website/status live. For OSM and mock this is a no-op
returning `{}`; Overpass already returned everything in one call.

Permanently-closed businesses are dropped here:
```python
if (candidate.get("business_status") or "").upper() in ("CLOSED_PERMANENTLY", "PERMANENTLY_CLOSED"):
    continue
```

**Stage 3 — `provider.fetch_ratings()`** runs only if the *provisional* score
(after stage 2) is `>= ENTERPRISE_FETCH_MIN_SCORE` (default 5). Ratings are the
most expensive SKU on Google, and a lead that already looks cold is not worth
them. This single condition is where most of the cost saving lives.

`QuotaExceeded` from any stage aborts the whole search with status
`STOPPED_QUOTA`; any other stage exception is caught, logged to `failed_jobs`,
and the search continues — one bad place never sinks a batch of 60.

### 6.6 Enrichment

`app/enrich.py`. Five stages, run per lead, **each writing its result back
immediately** so a lead that fails at stage 3 keeps what stages 1–2 found.

```
enrichment_status: pending → partial → complete
```

| Stage | Function | What it does |
|---|---|---|
| 1 | `stage_website` | Full website analysis (§7.5), plus free social-link and `mailto:` extraction. Skipped if `website_checked_at` is within `WEBSITE_CACHE_DAYS` (30). |
| 2 | `stage_social` | **Deliberately empty.** A documented extension point — see §16. |
| 3 | `stage_email` | Free first: scrape `/contact`, `/contact-us`, `/about`, `/about-us` for an address. Paid fallback (Hunter.io) **only** for HOT leads with a website domain, only if the monthly cap allows. Also harvests `owner_name` when Hunter returns first/last name. |
| 4 | `stage_validate` | Re-normalise the phone, recompute the WhatsApp number *from the normalised value* (so a corrected number doesn't leave a stale `wa.me` link), re-validate the email. |
| 5 | `stage_score` | Deterministic scoring, run last so it sees everything. |

**Isolation:** each stage is wrapped in its own try/except. A failure appends to
`enrichment_error` and sets status `partial`, but the lead survives.

**Concurrency:** `enrich_many` uses a `ThreadPoolExecutor` bounded by
`ENRICH_WORKERS` (default 8). The docstring explains exactly why — enrichment is
almost entirely *waiting on other people's web servers*, and a dead site burns
the full `HTTP_TIMEOUT` before giving up, so 60 leads sequentially meant 60
timeouts end to end. Bounding matters too: it caps both outbound connections and
MySQL write contention. One bad lead cannot sink the batch.

**Alerts:** `enrich_pending` and `run_search` both call
`notify.alert_new_hot_leads()` on the HOT subset. Safe to call from every path
because each lead is individually claimed (see §7.8).

### 6.7 The insert

`_insert_lead` normalises the phone, derives the WhatsApp number, runs
`score_lead()` provisionally, and inserts across the 34-column `LEAD_COLUMNS`
list with `status='NEW'`, `found_date`, `created_at`, `updated_at`,
`source`, and the `search_id` that produced it.

### 6.8 Completion

On `COMPLETED`:
- the `searches` row gets its final counts and duration markers
- `subscription.count_search(user_id)` charges the plan
- `record_cache(...)` snapshots the result set
- `_update_coverage(...)` advances the coverage row
- an automation log line is written

On `STOPPED_QUOTA` or `FAILED`, the error text is stored on the row, a
`failed_jobs` entry is written, and — importantly — **the coverage row and
subscription are not charged**.

---

## 7. Feature catalogue

### 7.1 Discovery

- **Search by niche + city + optional area + optional country.**
- **Three providers**, swappable per request from the UI dropdown; the active
  default comes from `PROVIDER` in `.env`.
- **39 niche → OSM-tag mappings** in `app/providers/osm.py` covering restaurant,
  cafe, bakery, bar/pub, hotel, gym, yoga, spa, salon, dentist, clinic,
  pharmacy, vet, lawyer, accountant, real estate, insurance, travel, plumber,
  electrician, carpenter, builder, car repair/dealership, school/coaching,
  driving school, boutique, jeweller, furniture, hardware, florist,
  photo studio, optician, bank, supermarket/grocery, bookshop, pet, laundry
  and more. An unmapped niche falls back to a case-insensitive regex name match.
- **Radius selection** — OSM uses 4,000m when an area or coordinate is given,
  12,000m for a bare city name, or the requested radius.
- **Live progress** — the `searches.progress` column is updated at each phase
  and each candidate, and the browser polls it.
- **Cancellation** — `POST /api/search/{id}/cancel` flips a flag the loop checks
  per candidate, so it stops after the current lead rather than mid-write.
- **Search history** — every run is retained with results found, new leads,
  duplicates skipped, hot/warm/cold counts, API calls, status, trigger
  (`manual`/`automation`/`target`), and timings.

### 7.2 The coverage map

Covered in §6.2. Exposed at:
- `GET /api/coverage` — all rows + distinct city and niche lists
- `GET /api/coverage/{city}` — one city's rows
- `GET /api/search/preview` — status for one combo, with a human sentence

The Coverage map page renders every row as a status-coloured card grouped by
city, so the operator's next move is always obvious.

### 7.3 "Search near me"

Click *Search near me* on the Search page:

1. The browser asks for `navigator.geolocation` (`currentPosition()` wraps it in
   a promise with actionable error messages).
2. The city and area fields are **cleared** and the coordinates plus the chosen
   radius (1 / 3 / 5 / 10 / 25 km) are sent instead.
3. The server reverse-geocodes the point via `app/geo.py` and fills city, area,
   country and pincode itself, so lead records and the coverage map read as
   *places*, not decimal pairs.
4. The search is centred on the point. Google gets a `locationBias` circle, not
   a `locationRestriction` — a hard restriction silently returns nothing when
   the radius is tighter than the nearest match, which reads as "no businesses
   here" rather than "look wider".
5. The UI writes the resolved city/area back into the fields and shows
   "Searching 3.0km around Sector 15, Faridabad".

**Two geocoder backends:** Nominatim (free, default, honours the 1 req/sec
policy with a real User-Agent) or Google (only when `GEOCODER=google` *and* a key
exists — it is never automatic, because it bills per call).
`geo.forward()` also exists to turn a typed place into coordinates.

### 7.4 Lead management

**List** (`GET /api/leads`) with filters: free-text `q` (name/phone/email/
address), `status`, `priority`, `city`, `niche`, `country`, `source`,
`has_website`, `tag`, `min_quality`, `enrichment_status`. Sortable by
`created_at`, `lead_score`, `business_name`, `updated_at`, `data_quality_score`,
`follow_up_date`, ASC/DESC. `limit` is clamped to `MAX_PAGE_SIZE` (500) — and
clamped in the handler, not the signature, because a `Query(le=…)` literal is
frozen at import time and could not be raised by config.

**Optimisation:** by default the list endpoint returns **12 columns, not 56**.
`LIST_COLUMNS` is exactly what the table draws; the drawer fetches the full row
from `GET /api/leads/{id}` when you open a lead. `?full=true` gets everything.

**Bulk operations:** select rows → change status, or add a tag (tags are
comma-separated and de-duplicated, not overwritten).

**CSV export** (`GET /api/leads/export`) — honours every list filter, streams as
`text/csv` with a `Content-Disposition` attachment header. The code calls it
"the manual 'get everything out' safety net".

**Facets** (`GET /api/leads/facets`) — distinct cities, niches, countries,
statuses and sources, so the filter dropdowns only ever offer values that
actually exist.

**Find duplicates** button → `POST /api/leads/dedupe` (§6.4).

**The lead drawer** — four tabs:

| Tab | Contents |
|---|---|
| **Details** | Call / WhatsApp / Email / Maps buttons, status dropdown, follow-up date, owner, phone (with an "unvalidated" marker), email, address, website, Instagram, Facebook, LinkedIn, tags, notes, source, niche. Editable and saved. Re-enrich and hard-delete buttons. |
| **Outreach** | Template picker, live rendered subject + body, copy-to-clipboard, queue-for-approval, WhatsApp deep link, and a form to log a manual interaction. |
| **Timeline** | Every interaction, newest first, with type, date, subject, outcome and follow-up date. |
| **Analysis** | Lead score, priority, the **full list of rules that fired** (`score_reasons`), the website score, the website issues list, data-quality score, and enrichment status/error. |

**Hard delete** (`DELETE /api/leads/{id}`) removes the lead, its interactions and
its queued messages. This is the GDPR / DPDP erasure path and is not recoverable
outside a backup.

### 7.5 Website analysis (the core of the pitch)

`app/website.py check_website()` — a plain HTTP request plus HTML parsing, no
paid API. **Never raises: a dead site is a finding, not an error.**

| Finding | How |
|---|---|
| `website_exists` | HTTP request succeeded and status < 400 |
| `website_ssl` | final URL scheme is `https` (follows redirects) |
| `website_mobile_friendly` | `<meta name="viewport">` present in the HTML |
| `website_speed_ms` | wall-clock around the request |
| `website_platform` | 10 signature regexes: WordPress, Wix, Squarespace, Shopify, Webflow, Google Business Site, GoDaddy Builder, Weebly, Next.js, React |
| `website_last_updated` | newest year in a `©`/`copyright` match |
| `website_score` | 0–10, see §9.2 |
| `website_issues` | human-readable strings, phrased to complete the sentence "your site …" |
| socials | Instagram/Facebook/LinkedIn regexes, with share/sharer/login URLs filtered out |
| emails | `mailto:` + in-text regex, filtering 12 disposable domains and asset extensions, capped at 5 |

Free email discovery also runs separately: `find_contact_email()` walks
`/contact`, `/contact-us`, `/about`, `/about-us` and stops at the first page
that yields an address.

Every message is phrased for the sales context — "Doesn't load at all — the
domain isn't responding", "Shows a 2019 copyright — looks abandoned", "Is not
mobile-friendly — no viewport tag, so it won't scale on a phone".

### 7.6 The map

- Every lead with coordinates, plotted with Leaflet.
- **Colour = priority** (HOT `#f85149`, WARM `#d29922`, COLD `#6e7681`).
- **Dot size = score** — `radius: 5 + lead_score * 0.6`.
- Filter dropdown for priority.
- Popup shows name, priority, score, status, area/city, phone, a bold red
  "No website" flag when applicable, plus **Open lead** (opens the drawer) and a
  **Google Maps** deep link.
- **Find me** centres the map on the browser's position and drops a "You are
  here" marker.
- Auto-fits bounds to all plotted leads.
- **Leaflet loads lazily** on first visit to this page (it's ~150KB and only
  this page needs it) and is **vendored** at `/static/vendor/leaflet.js` — the
  tool is meant to run on your own box without depending on someone else's CDN.
  A friendly error is shown if the file is missing.
- The endpoint `/api/geo/map` returns only the 13 columns a marker needs,
  not 56.

### 7.7 The pipeline (CRM)

Eight stages, seeded from `PIPELINE_STAGES` in `app/db.py:31`:

| Stage | Colour | Auto follow-up after |
|---|---|---|
| NEW | grey | 0 days |
| CONTACTED | blue | 3 days |
| REPLIED | violet | 2 days |
| MEETING_SET | cyan | 2 days |
| PROPOSAL_SENT | amber | 3 days |
| NEGOTIATION | orange | 3 days |
| WON | green | 0 days |
| LOST | red | 0 days |

The Pipeline page renders every stage as a column with its count and its top 25
leads by score, ready to click into.

**Conversion funnel** (`GET /api/stats/conversions`) — a lead's *current* status
implies it passed every earlier stage, so it counts toward each preceding stage.
LOST leads count as reached-CONTACTED if they have any touchpoint. Returns each
step's `from_count`, `to_count` and `rate`, plus `search_to_lead` and
`search_to_won`.

**Weekly report** (`GET /api/stats/weekly`) — leads found, HOT found, searches
run, outreach by type, total touchpoints, meetings set, deals won, per-niche
and per-city performance, and `cost_per_hot_lead()`.

**`cost_per_hot_lead()`** is described in the code as "the one number that tells
you if a change was really cheaper": this month's Google calls ÷ this month's
HOT leads, with a note to multiply by your per-1,000 SKU rate for a money figure
because the tier depends on which fields each call requested.

**Follow-ups** — four views:
- `/api/follow-ups/today` — due today or overdue, excluding WON/LOST
- `/api/follow-ups/overdue`
- `/api/follow-ups/upcoming?days=7`
- `/api/follow-ups/suggested` — **leads the Day 3/7/14 cadence says are due even
  without an explicit follow-up date set**, each with a `step` and a suggested
  `action` ("Day 3 — Follow up on WhatsApp", "Day 7 — Call them", "Day 14 —
  Final follow-up email", "Day 21 — Mark Lost - No Response")

The Follow-ups page has one-click actions to mark done or snooze by N days.

### 7.8 The Lead Automator

`app/automation.py` — APScheduler `BackgroundScheduler` on UTC, started from
`lifespan` in `main.py`.

**The seven jobs and their default cron times:**

| Job | Default time | Switch | What it does |
|---|---|---|---|
| `backup` | 02:00 | — | `mysqldump` + retention prune + off-site rclone sync + `notify.prune()` |
| `discovery` | 06:00 | `job_discovery` | Run due targets (see below) |
| `enrichment` | 06:30 | `job_enrichment` | `enrich_pending(limit=100)`, then alert on new HOT |
| `scoring` | 07:00 | `job_enrichment` | Re-score leads updated in the last 24h (no API calls) |
| `followups` | 07:30 | `job_followup_generation` | Generate cadence follow-ups into the approval queue; auto-mark LOST at day 21 |
| `digest` | 08:00 | — | Build and email the daily health check |
| `sending` | every 30 min | `job_sending` | Send rows already marked APPROVED |

All times are configurable via `.env`.

**Two switches gate every job:** a master `automation_master` and a per-job
switch. Both live in the `app_settings` table, so flipping one takes effect on
the next tick **without a restart**. `POST /api/automation/kill` is a
one-click master off, "always available even if individual jobs are
misbehaving".

**Defaults on first boot:** master `off`, discovery `on`, enrichment `on`,
follow-up generation `on`, sending `off`. Sending is off by default even with
the master on.

**Target-driven discovery** — `job_discovery` selects up to `TARGETS_PER_RUN`
(default 3) active targets where `auto_search != 'manual'` and
`next_search_date <= today`, ordered by least-recently-searched. For each:
- `EXHAUSTED` coverage → **deactivate the target** and log a warning
- otherwise run a fresh search (bypassing the cache), update `last_searched` and
  `next_search_date` from `auto_search` (daily +1, weekly +7, monthly +30)
- if the quota is hit, log and `break` for the day

**Manual "Run now"** — `run_job_now(name)` passes `force=True`, which bypasses
the switch check (an explicit operator action shouldn't silently no-op because
the schedule is paused) and, for the four long jobs (discovery, enrichment,
scoring, followups), runs on a background thread so the browser isn't held open
for minutes. `sending`, `digest` and `backup` run inline.

**Leader election / the scheduler lease** — this is the fix for
`uvicorn --workers N` meaning N schedulers all running the 6am search:

- A single-row `scheduler_lease` table with `CHECK (id = 1)`.
- `claim_lease()` takes the write lock (`SELECT … FOR UPDATE`) **before**
  reading, inside a transaction, so two workers starting at the same instant
  cannot both conclude the lease is free.
- Lease is 120s, renewed every 45s by an interval job that runs in **every**
  worker. Only the winner registers the real cron jobs; the rest stand by.
- If the holder dies, its lease expires and a standby takes over within ~2
  minutes, logging "took the scheduler lease".
- If a worker stalls long enough to lose the lease, it **stands down** and
  removes its jobs rather than double-running.
- A clean shutdown deletes the row so a standby picks up immediately.
- Owner is `hostname:pid`, surfaced in the log.

### 7.9 Outreach

**Templates** (`app/outreach.py` + `app/seed.py`). Eight seeded:

| Name | Type | Stage | Market |
|---|---|---|---|
| `cold_email_first` | email | first | international |
| `follow_up_day_3` | email | day3 | international |
| `follow_up_day_7` | email | day7 | international |
| `follow_up_day_14` | email | day14 | international |
| `whatsapp_first` | whatsapp | first | india |
| `whatsapp_follow_up` | whatsapp | day3 | india |
| `cold_call_script` | call_script | first | india |
| `proposal_email` | email | proposal | international |

**Merge fields:** `{business_name}`, `{business_possessive}`, `{owner_name}`,
`{city}`, `{area}`, `{niche}`, `{phone}`, `{email}`, `{website_url}`,
`{observation}`, `{website_issue}`, `{portfolio_url}`, `{sender_name}`,
`{sender_company}`.

Two of these are computed, not stored:

- **`{observation}`** (`observation_for`) turns the lead's analysis into one
  human sentence: "you're active on social but don't have a website yet", "your
  site shows a 2019 copyright — looks abandoned", "you don't have a website
  listed on your business profile". It lowercases only the first letter so
  "Has no HTTPS" reads correctly but "Doesn't load at all" keeps its capitals.
- **`{business_possessive}`** (`possessive`) appends a bare `'` to names ending
  in s ("Django Meals'") and `'s` otherwise — because a mangled possessive in
  the subject line is the first thing a recipient sees.

`render_template` also returns `unfilled` — the merge tags the template uses
that the context doesn't supply, so a typo'd field is visible rather than
silently left as literal `{braces}`.

**The follow-up cadence** (`CADENCE`): day 3, day 7, day 14. **Day 21 =
give up** — the lead is auto-marked `LOST` with an appended note. Generation
walks `CONTACTED` leads with a `last_contacted_date` and picks the *highest*
threshold reached, so a lead contacted 20 days ago gets the day-14 message once,
not three.

Channel is chosen by data availability: email if the lead has one, otherwise
WhatsApp (using the `whatsapp_follow_up` template). Generation **only writes to
`approval_queue`** — it never sends.

**Duplicate-queue protection** — `queue_message` refuses if a row already exists
for that `(lead_id, step)` in `PENDING`/`APPROVED`/`SENT`, so re-running the job
cannot pile up three day-3 emails for one lead.

**WhatsApp is click-to-chat only.** `whatsapp_link()` builds
`https://wa.me/{number}?text={quoted message}` for the operator to tap. The
module docstring is blunt about why: automating sends against a personal number
is "the single biggest account-ban risk in a tool like this". Note that
`whatsapp_number()` returns `None` for Indian landlines — a `wa.me` link to a
landline opens a dead chat, which is worse than showing no button.

**Sending** (`send_approved`) reads **only** rows with `status='APPROVED'` and
`channel='email'`, budgeted by `MAX_EMAILS_PER_HOUR` minus what was already sent
in the last hour. For each row:
1. **Re-checks the suppression list at send time** — not at generation time,
   which could have been days earlier
2. suppressed → mark `SKIPPED` with the reason, count it
3. `send_email()` → which checks suppression *again*, appends the footer, then
   either logs `[DRY RUN] would email …` or calls Resend
4. success → mark `SENT`, `log_interaction()` (which also advances
   `NEW → CONTACTED`, sets first/last contact dates, increments
   `total_touchpoints`, records the method)
5. failure → mark `FAILED` with the error, write a `failed_jobs` row

**Hard-bounce auto-suppression:** a 400/422 whose body contains "invalid" adds
the address to the suppression list, so it is never retried.

**Every email carries compliance:** an opt-out footer ("Reply STOP or email X and
I'll remove you immediately") and `List-Unsubscribe` +
`List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers.

**The approval gate** is structural, not conventional: the automator only ever
*writes* to `approval_queue`; sending only ever *reads* from it; and only rows
you explicitly marked `APPROVED` are eligible. `PATCH /api/approvals/{id}`
lets you edit subject and body before approving — but only while the row is
`PENDING` or `APPROVED`, never once it is sent.

### 7.10 Multi-user, roles and subscriptions

**Three roles:** `user` < `admin` < `superadmin`.

- Only the **superadmin** can create a non-`user` role, or change someone's
  role. Admins can create and manage plain users.
- Only the superadmin can delete a superadmin, and only if another *active*
  superadmin remains — "Every system needs at least one active superadmin".
- Nobody can delete their own account.
- Duplicate usernames → 409.
- The Users page is paginated and searchable.
- An admin can assign a plan directly to any user from the user drawer.

**Plans** (`plans` table): name, description, price, currency (default INR),
`period_days`, `search_limit`, `max_results_per_search`, `is_active`.

**Subscriptions** (`user_subscriptions`): user, plan, status
(`pending`/`active`/`expired`/`rejected`/`cancelled`), period start/end,
`searches_used`, `channel` (`admin`/`self`), `payment_status`, `payment_ref`.

**The model is deliberately one active plan per user** — any purchase or
assignment expires the previous one, so "which plan is active" never has to rank
competing rows.

**Gating** (`check_search_entitlement`):
- `superadmin` → always allowed, never charged
- no active subscription → 403
- `searches_used >= search_limit` → 403 with an upgrade prompt
- otherwise allowed, and `max_results_for()` caps the request to
  `min(requested, plan_cap, MAX_RESULTS_PER_SEARCH)`

**Search accounting** charges on `COMPLETED` only, and it charges for cache hits
too — the user still consumed a search. Failed and quota-stopped searches are
not charged.

**Self-serve ordering** — when the admin flips `payment_enabled` on, users see
a Buy button and `POST /api/me/subscriptions` creates a `pending`/`unpaid`
subscription. It is idempotent per `(user, plan)`. An admin then approves it from
the Plans page, which sets `active`/`paid`, resets `searches_used` to 0, and
expires any previous active subscription. They can also reject or cancel.

**My Plan** page shows the user's own order history, current plan, remaining
searches (with a coloured progress bar), the result cap, and the renewal date.

---

## 8. Data model — every table

19 tables, all InnoDB / `utf8mb4_unicode_ci`. Raw SQL, no ORM.

### `leads` — 56 columns, the centre of everything
- **Identity:** `business_name`, `owner_name`, `place_id` (**UNIQUE**), `niche`,
  `source`
- **Contact:** `phone`, `phone_valid`, `email`, `email_valid`, `whatsapp`,
  `instagram_url`, `facebook_url`, `linkedin_url`, `contact_method`
- **Location:** `address`, `city`, `area`, `pincode`, `state`, `country`,
  `lat`, `lng`, `google_maps_url`
- **Google data:** `google_rating`, `google_reviews`, `business_status`
- **Website analysis:** `has_website`, `website_url`, `website_score`,
  `website_issues` (JSON), `website_platform`, `website_ssl`,
  `website_mobile_friendly`, `website_speed_ms`, `website_last_updated`,
  `website_checked_at`
- **Scoring:** `lead_score`, `lead_priority` (HOT/WARM/COLD/EXCLUDED),
  `data_quality_score`, `score_reasons` (JSON)
- **Pipeline:** `status`, `first_contacted_date`, `last_contacted_date`,
  `follow_up_date`, `total_touchpoints`
- **Enrichment:** `enrichment_status` (pending/partial/complete),
  `enrichment_error`
- **Manual:** `tags`, `notes`, `estimated_budget`, `business_size`
- **Bookkeeping:** `search_id`, `found_date`, `created_at`, `updated_at`

Indexes on `place_id` (unique), `status`, `lead_priority`, `city`, `niche`,
`follow_up_date`, `(business_name, city)`, `phone`, `enrichment_status`,
`search_id`.

### Everything else

| Table | Purpose | Notable |
|---|---|---|
| `interactions` | The activity log per lead — type, direction, date, subject, content, outcome, next step, follow-up date, `automated` flag | FK to leads `ON DELETE CASCADE` |
| `searches` | One row per search run — every count, `status`, `progress`, `triggered_by`, `user_id`, `cached_from` | `user_id` and `cached_from` applied as drift fixes at boot |
| `search_cache` | The fingerprint → search_id snapshot, with `result_count` and TTL timestamps | `UNIQUE(fingerprint)` |
| `search_coverage` | Per city/area/niche/country: `last_searched`, `times_searched`, `total_leads_found`, `barren_runs`, `status` | `UNIQUE(city, area, niche, country)` |
| `targets` | Scheduled search definitions — `auto_search` daily/weekly/monthly/manual, `next_search_date`, `is_active` | `UNIQUE(niche, city, area, country)` |
| `templates` | Outreach templates — type, stage, subject, body, language, target_market | `UNIQUE(name)` |
| `pipeline_stages` | The 8 pipeline stages with position, colour, `auto_follow_up_days` | `UNIQUE(name)` |
| `suppression_list` | Never-contact entries — `value`, `kind` (email/phone), `reason` | `UNIQUE(value, kind)` |
| `approval_queue` | The human gate — lead, channel, template, step, to_address, subject, body, status, `reviewed_at`, `sent_at`, `error_message` | FK cascade; index on `status` |
| `api_usage` | Per-day per-service per-tier call counters | `UNIQUE(day, service, tier)` |
| `failed_jobs` | Unresolved errors — job, ref_id, error, payload, `resolved` | Surfaced on Dashboard and Control |
| `automation_log` | The activity feed — job, message, level (info/warn/error) | Index on `created_at` |
| `app_settings` | Key/value store for switches and identity | |
| `scheduler_lease` | Exactly one row, `CHECK (id = 1)` | Leader election |
| `alerts_sent` | One row per alert already sent | `UNIQUE(alert_key)` — this is what makes alerting idempotent |
| `users` | username, `password_hash` (PBKDF2-SHA256, 200k iterations, salted), full_name, email, role, is_active | `UNIQUE(username)` |
| `plans` | Pricing and entitlements | `UNIQUE(name)` |
| `user_subscriptions` | Who is on what, and how many searches they have left | FKs cascade to users and plans |

---

## 9. Scoring engines, rule by rule

Both engines are **deterministic weighted rules, never a model**, and both store
the rules that fired as JSON on the row — so you can always answer "why did this
score 8?".

### 9.1 Lead score (`app/scoring.py`)

Permanently-closed businesses short-circuit to score 0 / priority `EXCLUDED`.

| Rule | Points | Reason string stored |
|---|---:|---|
| No website at all | **+4** | "no website at all (+4)" |
| Website listed but doesn't load | **+4** | "has a website listed but it doesn't load (+4)" |
| Website scores below 4/10 | **+3** | "website scores N/10 - outdated or broken (+3)" |
| Website scores below 7/10 | **+1** | "website scores N/10 - room to improve (+1)" |
| Social but no website | **+2** | "active on social but no website (+2)" |
| Established | **+1** | "N reviews - established business (+1)" *or* "listed with a working phone and address - real business (+1)" |
| **Real business + no working website** | **+2** | "real business with no working website - ideal prospect (+2)" |
| Temporarily closed | **−2** | "temporarily closed (-2)" |
| Reachable phone | **+1** | "reachable phone number (+1)" |

Clamped to 0–10. Then the data-quality cap: **if quality < 3 and score > 5, the
score is forced down to 5** with the reason "capped at 5 - data quality below 3,
not enough to act on".

Bands: **≥8 HOT**, **≥5 WARM**, else **COLD**.

Two of these rules are documented deviations from the original spec, and both
were forced by real data:

1. **The "ideal prospect" combination bonus (+2)** did not exist in the spec. As
   originally written, the ideal lead — a proven real business with no working
   website — topped out around 6–7 and was therefore *never HOT*, so the
   operator's main working queue would have been permanently empty. Worse, it
   couldn't even earn the social bonus, because social discovery reads the
   business's own website. Hence the explicit +2.

2. **"Established" has a no-reviews fallback.** OpenStreetMap carries no review
   counts at all, so a review-based signal makes HOT unreachable on the free
   provider. When `google_reviews is None`, the code falls back to
   `phone_valid AND address` — is this listing actually contactable? Verified
   against live Faridabad data: contactable no-website restaurants score 8 (HOT),
   uncontactable ones score 4 (COLD) — which is the right call for outreach
   anyway.

### 9.2 Website score (`app/website.py`)

Baseline **4** for a site that loads at all, then:

| Condition | Δ |
|---|---:|
| HTTPS | +2 |
| Mobile-friendly (viewport tag) | +2 |
| Loads under 1.5s | +2 |
| Loads under 3.5s | +1 |
| Copyright year ≥ 3 years old | −2 |
| Copyright year 2 years old | −1 |
| Template platform (Wix / GoDaddy / Weebly / Google Business Site) | −1 |
| HTML under 2,000 bytes | −1 |
| No `<title>` | −1 |

Clamped 0–10. Every deduction also appends a human-readable issue string.

### 9.3 Data quality score (`app/validation.py`)

Completeness, 0–10: phone +2, email +2, address +1, website-checked +1,
owner name +2, any social link +1, `phone_valid` +1. Capped at 10.

Used for two things: the score cap in §9.1, and the `min_quality` lead filter.

---

## 10. HTTP API reference

All endpoints are under `/api` except the pages and `/health`. Everything except
the public list requires a valid session cookie.

### Auth
| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/login` | Public. Rate limited to 8 per 5 min per IP. |
| POST | `/api/auth/logout` | |
| GET | `/api/auth/me` | Current user + active subscription. |

### Search, geo, coverage, targets
| Method | Path | Notes |
|---|---|---|
| POST | `/api/search` | Gated. Returns a `search_id` immediately. |
| GET | `/api/search/{id}/status` | Includes the leads for a completed run; resolves through `cached_from`. |
| POST | `/api/search/{id}/cancel` | Stops after the current candidate. |
| GET | `/api/search/history?limit=` | Marks each row `from_cache`. |
| GET | `/api/search/preview` | Coverage status + message for one combo. |
| GET | `/api/geo/reverse?lat=&lng=` | Names a coordinate. |
| GET | `/api/geo/map?limit=&priority=&city=&status=` | 13 columns only. |
| GET | `/api/coverage[?country=]` | All rows + distinct cities and niches. |
| GET | `/api/coverage/{city}` | |
| GET | `/api/providers` | Active + availability of each. |
| GET | `/api/quota` | Google today, by tier; Hunter this month. |
| GET/POST | `/api/targets` | List / add (`INSERT IGNORE` on the unique key). |
| PATCH/DELETE | `/api/targets/{id}` | Toggle `is_active` / delete. |
| POST | `/api/targets/{id}/search-now` | Gated. Forces a fresh scrape. |

### Leads
| Method | Path | Notes |
|---|---|---|
| GET | `/api/leads` | 12 columns by default; `?full=true` for all 56. |
| GET | `/api/leads/export` | CSV, honours all filters, streams. |
| GET | `/api/leads/facets` | Distinct filter values. |
| GET | `/api/leads/{id}` | Full row + interactions + queued messages. |
| PATCH | `/api/leads/{id}` | Only the 23 fields in `EDITABLE_FIELDS`. |
| DELETE | `/api/leads/{id}` | Hard delete incl. history. GDPR/DPDP. |
| POST | `/api/leads/{id}/enrich` | Re-run the pipeline for one lead. |
| POST | `/api/leads/{id}/interactions` | Log a touch; advances status and touchpoints. |
| POST | `/api/leads/{id}/message?template=` | Rendered message + `whatsapp_link` + `mailto:`. |
| POST | `/api/leads/bulk-status` | |
| POST | `/api/leads/bulk-tag` | |
| POST | `/api/leads/dedupe` | Whole-table duplicate sweep. |

### Stats and follow-ups
`/api/stats`, `/api/stats/conversions`, `/api/stats/weekly`, `/api/pipeline`,
`/api/follow-ups/today`, `/api/follow-ups/overdue`,
`/api/follow-ups/upcoming?days=`, `/api/follow-ups/suggested`

### Outreach
`/api/templates` (GET/POST/DELETE), `/api/approvals` (GET/PATCH),
`/api/approvals/{id}/approve|skip`, `/api/approvals/approve-all`,
`/api/approvals/queue`, `/api/approvals/generate`, `/api/approvals/send`,
`/api/suppression` (GET/POST/DELETE), `/api/identity` (GET/POST)

### Automation
`/api/automation/status`, `/api/automation/switch`, `/api/automation/kill`,
`/api/automation/log`, `/api/automation/run/{job}`,
`/api/automation/failures`, `/api/automation/failures/{id}/resolve`,
`/api/automation/failures/resolve-all`, `/api/automation/digest`,
`/api/automation/backups` (GET/POST)

### Users, plans, subscriptions
`/api/users` (GET/POST), `/api/users/{id}` (PATCH/DELETE),
`/api/users/{id}/subscriptions`, `/api/plans` (GET/POST),
`/api/plans/{id}` (PATCH/DELETE), `/api/subscriptions`,
`/api/subscriptions/{id}/approve|reject|cancel`,
`/api/me/subscription`, `/api/me/subscriptions`, `/api/me/subscriptions` (POST),
`/api/settings/payment` (GET/POST)

---

## 11. Configuration reference

Everything is an environment variable read by `app/config.py`. Raising any limit
is a config change and a restart, not a code change.

**Auth** — `ADMIN_USER`, `ADMIN_PASSWORD`, `SECRET_KEY`, `SESSION_HOURS` (168),
`COOKIE_SECURE` (false), `TRUST_PROXY` (false)

**Scale** — `API_RATE_LIMIT` (600/min), `API_RATE_WINDOW` (60),
`LOGIN_RATE_LIMIT` (8), `LOGIN_RATE_WINDOW` (300), `MAX_PAGE_SIZE` (500),
`ENRICH_WORKERS` (8), `HTTP_POOL_SIZE` (32)

**Database** — `MYSQL_HOST`, `MYSQL_PORT` (3306), `MYSQL_USER`, `MYSQL_PASSWORD`,
`MYSQL_DATABASE` (`leadgen`), `MYSQL_CHARSET` (`utf8mb4`),
`MYSQL_CONNECT_TIMEOUT` (10), `MYSQLDUMP_PATH`

**Provider** — `PROVIDER` (`osm`), `GOOGLE_API_KEY`

**Location** — `GEOCODER` (`osm`), `NEARBY_RADIUS_M` (3000),
`MAX_NEARBY_RADIUS_M` (50000)

**Quota** — `MAX_GOOGLE_CALLS_PER_DAY` (500), `MAX_RESULTS_PER_SEARCH` (60),
`QUOTA_WARN_PCT` (80), `ENTERPRISE_FETCH_MIN_SCORE` (5)

**Search cache** — `SEARCH_CACHE_ENABLED` (true), `SEARCH_CACHE_TTL_DAYS` (7)

**Enrichment** — `HTTP_TIMEOUT` (8), `WEBSITE_CACHE_DAYS` (30),
`HUNTER_API_KEY`, `MAX_HUNTER_CALLS_PER_MONTH` (25)

**Outreach** — `DRY_RUN` (**true**), `RESEND_API_KEY`, `MAIL_FROM`,
`MAIL_FROM_NAME`, `MAX_EMAILS_PER_HOUR` (20), `UNSUBSCRIBE_MAILTO`

**Alerts** — `ALERTS_ENABLED` (true), `ALERT_EMAIL` (falls back to `MAIL_FROM`),
`HOT_ALERT_MIN_SCORE` (8)

**Automation** — `AUTOMATION_ENABLED` (false), `DISCOVERY_TIME` (06:00),
`ENRICHMENT_TIME` (06:30), `SCORING_TIME` (07:00), `FOLLOWUP_TIME` (07:30),
`DIGEST_TIME` (08:00), `BACKUP_TIME` (02:00), `TARGETS_PER_RUN` (3),
`BACKUP_KEEP_DAYS` (30)

**Backup** — `BACKUP_REMOTE` (blank = off), `RCLONE_PATH` (`rclone`),
`BACKUP_SYNC_TIMEOUT` (300)

**Server** — `HOST` (127.0.0.1), `PORT` (8000)

### The provider matrix

| Provider | Cost | Data | Notes |
|---|---|---|---|
| `osm` **(default)** | Free, no key, no billing | OpenStreetMap via Overpass + Nominatim. Name, address, often phone, website, and sometimes `contact:instagram`/`contact:facebook` tags. **No ratings or reviews.** | Density varies by region — spot-check your target city. Sends a real User-Agent and sleeps 1s between Nominatim calls per their usage policy. |
| `google` | Per-SKU billing | Google Places API (New). Richest data by far — ratings, reviews, national + international phone, website, business status, hours, primary type. | Needs `GOOGLE_API_KEY`. Staged into 3 field-mask tiers; see below. |
| `mock` | Free, offline | Deterministic fake businesses seeded from `sha256(niche\|city\|area)`. | Same input always produces the same businesses, so re-running a search is a genuine duplicate test. Used by the test suite. |

### Google's staged calling — the cost design

| Stage | Fields | When | Tier |
|---|---|---|---|
| 1 `searchText` | `id`, `displayName`, `formattedAddress`, `location`, `addressComponents` | Every result | Pro |
| 2 `places/{id}` | `nationalPhoneNumber`, `internationalPhoneNumber`, `websiteUri`, `businessStatus`, `googleMapsUri`, `primaryTypeDisplayName` | New places only | Enterprise |
| 3 `places/{id}` | `rating`, `userRatingCount` | Only if provisional score ≥ 5 | Enterprise |

The file header is explicit that the *labels* move between Google's pricing
updates and that the real saving is the staging, not the tier names: never fetch
details for a place you already have, never fetch ratings for a lead that
already looks cold. Verify the field groupings against Google's live pricing
page before scaling up.

---

## 12. Performance engineering

The README documents four things that were genuinely slow, measured, changed and
re-measured. The *causes* are more instructive than the numbers:

| | Before | After |
|---|---|---|
| DB reads | 112/sec | 89,500/sec |
| DB writes | 15/sec | 6,050/sec |
| Enrichment, 12 leads with dead sites | 60.8s | 12.4s |
| `GET /api/leads` at 20 concurrent | 36/sec, 540ms | 141/sec, 138ms |

**1. Thread-local MySQL connections** (`app/db.py:428-474`). A connection can't
be shared across threads, but opening a fresh one per *query* is what actually
costs — each new connection re-does four PRAGMAs. The handle lives on
`threading.local()` and is reused for that thread's lifetime. Request threads,
scheduler threads and the enrichment pool are all bounded and reused, so the
number of live connections stays small. `autocommit=True`; explicit
transactions are taken only where a multi-statement unit needs one (dedup,
quota reserve, lease claim, notification claims).

Two compatibility shims make this invisible to the rest of the codebase:
`TranslateCursor` rewrites the app's `?` placeholders to `%s` at execution time,
and a `Conn` subclass adds sqlite3-style `.execute()`/`.executemany()` to the
PyMySQL connection object. Call sites look exactly like the sqlite3 code they
were written as.

**2. One pooled `httpx.Client`** (`app/http.py:32`). A new client per outbound
call meant a fresh TCP connection *and* a fresh TLS handshake every time —
roughly 100–300ms of pure setup, paid on every website check and every API
call. `httpx.Client` is thread-safe, so one pooled instance with keep-alive
serves the enrichment workers too, and repeat calls to the same host skip the
handshake entirely.

**3. Parallel enrichment** (§6.6) — `ENRICH_WORKERS` default 8, overlapping the
wait on other people's servers.

**4. Skipping `jsonable_encoder`** (`app/fastjson.py`). FastAPI normally pushes a
returned dict through `jsonable_encoder`, which walks every value. That's the
right default for models, dates and Decimals — but these handlers return rows
straight out of MySQL (`str`, `int`, `float`, `None`, the odd list), so the walk
finds nothing to convert and is pure overhead. Measured on a 50-lead page:

```
jsonable_encoder + dumps   12.5 ms
dumps alone                 0.9 ms      → 13x, on the CPU that also runs the event loop
```

`FastJSON` overrides `render()` to call `json.dumps` directly with compact
separators. Returning a `Response` instance makes FastAPI skip the encoder
entirely. `rows_response()` wraps it with a fallback to the standard encoder, so
a new column type can never turn a list endpoint into a 500.

Plus: the list endpoints send the **12 columns the table draws** instead of all
56 — the drawer fetches the full record on open.

Under sustained mixed load (700 requests, 24 concurrent): **154 req/sec at p99
218ms, zero errors.**

### Resilience layers

**Circuit breakers** (`app/http.py:73-105`) — every outbound call names a
breaker. 5 consecutive failures opens it for 15 minutes, and calls then fail
fast with "…is failing repeatedly - paused until 14:22 UTC" instead of hammering
something already down. In-process state, with `breaker_state()` surfaced on the
Control page.

**Retry with jittered backoff** — 429/5xx and transport errors are retried up to
5 attempts with `2**attempt + random(0, 0.5)` seconds of sleep, so parallel
workers don't synchronise into a thundering herd.

**Idempotent searches** — re-running a search that crashed halfway creates no
duplicates, because every candidate goes through dedup *before* anything is
inserted.

**Rate limiter memory hygiene** — `_sweep()` drops keys with no recent hits every
60s. Without it, `_hits` would keep one entry per client IP that has ever called
the API, forever: fine on a laptop, a slow leak on a public instance.

---

## 13. Security model

The threat model is a self-hosted tool holding phone numbers, emails and an
entire sales pipeline, which must not sit on a public URL unauthenticated.

| Control | Implementation |
|---|---|
| **Session** | HMAC-SHA256-signed cookie (`base64(username\|expiry).signature`). No session store to keep in sync. `SESSION_HOURS=168`. `httponly`, `samesite=lax`, `secure` toggled by `COOKIE_SECURE`. |
| **Immediate revocation** | The user row is re-read from MySQL on **every** authenticated request, so a deactivated or deleted account locks out on its next request rather than when its token expires. |
| **Password storage** | PBKDF2-HMAC-SHA256, 200,000 iterations, 16-byte random salt, stored as `salt$digest`. Comparison via `hmac.compare_digest` (constant-time). |
| **Login rate limit** | 8 attempts per 5 minutes per client IP. |
| **API rate limit** | 600 requests/minute per client IP, applied to all `/api/*` before anything else. |
| **`X-Forwarded-For` trust** | Only read when `TRUST_PROXY=true`, and only the **first** entry. Unproxied, the header is attacker-controlled and would make the limiter trivially bypassable. Documented in both the config and the deploy guides. |
| **Role escalation** | Only a `superadmin` can grant or change a privileged role (`users.py:56`, `users.py:83`). |
| **Last-superadmin guard** | A superadmin cannot be deleted if they're the last active one. |
| **Self-deletion guard** | You cannot delete your own account. |
| **Mass assignment** | `PATCH /api/leads/{id}` filters through a fixed 23-field `EDITABLE_FIELDS` whitelist. `EDITABLE_FIELDS` is not a free-form pass-through. |
| **SQL injection** | Parameterised queries everywhere. Identifiers that must be dynamic (sort column, filter clauses) are chosen from hardcoded whitelists (`SORTABLE`, `_build_filters`). |
| **XSS** | All user-supplied values pass through the `esc()` helper before being interpolated into `innerHTML`. Popup HTML on the map escapes too. |
| **Erasure** | `DELETE /api/leads/{id}` removes the lead, its interactions and its queued messages in one action. |
| **Suppression** | Checked immediately before *every* send, and again inside `send_email()` itself. |
| **Outbound safety** | HTTPS enforced via `httpx`; the browser only ever generates `wa.me` deep links for the operator to tap. |
| **Transport** | The app binds loopback; Caddy or Apache terminates TLS. `deploy/leadgen-apache.conf` and `deploy/Caddyfile` both ship the same security headers. |
| **Rate-limit caveat** | In-process, so N uvicorn workers effectively multiply the limit by N. Documented as intentional — the limiter exists to stop runaway loops and casual scraping, not to be an exact quota. |

### Where alerts deliberately bypass the guardrails

`app/notify.py`'s docstring is one of the best pieces of reasoning in the
codebase, and it's worth quoting the reasoning:

Operator alerts take a **different path** from `outreach.send_email`:

- **No unsubscribe footer.** You can't unsubscribe from your own health check.
- **No suppression-list check.** The suppression list protects *prospects*. If
  your own address ever landed on it — easy enough, by replying "stop" to your
  own test message — alerts would go quiet exactly when you needed them, "which
  is the failure this whole section exists to prevent".
- **They send even when `DRY_RUN=true`.** Dry run exists so nothing reaches a
  *prospect* during the trial week. A week of silence from your own health check
  would defeat the point of running the trial at all.

The `job_digest` docstring says the same thing from the other side: "Part 7.7
asks you to read a week of output before going live, which requires the output
to actually arrive."

### Cost safety

- **Quota hard stop** — `check_and_reserve()` runs the cap check and the counter
  increment in one transaction and raises `QuotaExceeded` **before** the call
  goes out. It stops a runaway loop rather than warning after the fact. The
  search is marked `STOPPED_QUOTA` with the reason, and discovery breaks out of
  the target loop for the day.
- **Warn on the approach, not the wall** — at `QUOTA_WARN_PCT` (80%) a daily alert
  fires. The rationale in the code: "by the time the cap is hit the day's
  discovery has already stopped, and knowing an hour earlier is what lets you
  raise the cap or leave it alone on purpose". The alert is raised *outside* the
  reservation transaction, so a mail failure can never roll back a committed
  reservation.
- **Two layers, and the app is the second one** — repeatedly stated in the code,
  the env comments and the UI: a Google Cloud *Billing budget alert* only emails
  you, it does not stop spending. **Only a Requests-per-day Quota limit does.**
  Set both.

### Backups

`app/backup.py`:

- `mysqldump --single-transaction --routines --triggers --default-character-set=utf8mb4`
  — on InnoDB this takes a read snapshot instead of locking tables, so a backup
  taken mid-write is still consistent.
- Password passed via `MYSQL_PWD` in the subprocess env, never on the command
  line (which would be visible in `ps`).
- Non-zero exit → the partial file is deleted, the error logged at `error` level
  and written to `failed_jobs`.
- Retention prune to `BACKUP_KEEP_DAYS` (30).
- `BACKUP_REMOTE` copies the file off-box with `rclone copy`. Failures are
  **logged and returned, never raised** — a remote that is down must not cost
  you the local backup that already succeeded. The failure surfaces in the next
  morning's digest instead. Timeouts, a missing binary, and a non-zero exit are
  all handled separately.
- `GET /api/automation/backups` lists the last files with sizes; the Settings
  page has a **Back up now** button.

---

## 14. Testing

**48 tests** in `tests/test_core.py`, plus `tests/conftest.py`. No network
access, no API key, no mocks of the app's own logic. Runtime is seconds.

`conftest.py` **forces `MYSQL_DATABASE=leadgen_test` and refuses to run against
any other database**, so the suite is safe to run while the app is up.

What they actually cover, by area:

- **Validation** — Indian phone normalisation, international preservation, an
  Indian landline being valid but not WhatsApp-able, disposable/role email
  rejection, quality cap at 10
- **Scoring** — no-website beats good-website, permanently closed excluded, thin
  data capping the score, a contactable no-website business reaching HOT *without*
  review data, an uncontactable lead staying COLD, a dead website not being
  reported as no-website, and "score always explains itself"
- **Dedup** — name normalisation stripping noise words, fuzzy similarity
  catching variants
- **Search + coverage** — creating leads and coverage, re-running creating no
  duplicates, coverage going `EXHAUSTED` after three barren runs, permanently
  closed businesses not being stored
- **The search cache** — all four behaviours in §6.3, including the reachability
  of the alert threshold
- **Outreach** — suppression blocking a recipient, template merge filling every
  field, possessive handling, an observation specific to a no-website lead
- **Quota + alerts** — quota hard-stopping rather than warning, alert claims
  being idempotent, the HOT alert firing once and only above the threshold,
  `test_the_alert_threshold_is_actually_reachable`, the quota alert warning once
  per day on the approach, and — importantly — `test_operator_alerts_ignore_the_suppression_list`
- **Backup** — sync skipped when unconfigured, sync failure never raising, and
  `test_backup_writes_a_restorable_file`
- **Infrastructure** — long jobs starting in a background thread, alert pruning
  only touching old quota claims, static assets revalidating rather than being
  blindly cached, `/health` needing no auth
- **The performance claims** — `test_thread_local_connections_are_reused`,
  `test_each_thread_gets_its_own_connection`,
  `test_list_endpoint_returns_only_the_columns_the_table_draws`,
  `test_fast_json_skips_the_encoder_but_matches_its_output`
- **Security** — the rate limiter pruning stale keys,
  `test_client_ip_only_trusts_forwarded_headers_when_configured`
- **Concurrency** — `test_scheduler_lease_admits_exactly_one_holder`
- **Geolocation** — `test_nearby_search_centres_on_the_coordinate`

Run with:

```bash
.venv/bin/python -m pytest tests -q
```

---

## 15. Deployment

The app **binds loopback only** and a reverse proxy terminates TLS. Nothing is
ever exposed directly, because the app holds the whole pipeline and ships
session cookies.

**Docker (as shipped):**
```bash
docker compose up -d                        # app + MySQL
docker compose --profile tls up -d          # + Caddy, automatic HTTPS
```
`docker-compose.yml` runs `mysql:8.0` with its own named volume and a
`mysqladmin ping` healthcheck the app waits on (`depends_on: condition:
service_healthy`), which is what makes `MYSQL_CONNECT_TIMEOUT=10` matter. Inside
the compose network the app gets `MYSQL_HOST=mysql`. Data and backups are named
volumes. The TLS profile needs `DOMAIN` and `TLS_EMAIL` in `.env`, and the A
record must already point at the box or the ACME challenge fails. Both services
have a healthcheck and capped json-file logging.

**Without Docker:** `deploy/leadgen.service` is a hardened systemd unit (runs as
the `leadgen` system user, not root; `WorkingDirectory`, `EnvironmentFile`,
hardening directives). `systemctl enable --now leadgen`.

**Behind Apache** — the README carries a complete ten-step walkthrough from a
fresh Ubuntu VPS: DNS first, apt + `a2enmod proxy proxy_http headers ssl`,
ufw, a dedicated `leadgen` system user, clone, venv, `.env`, the systemd unit,
the shipped vhost with `sed` for your domain, and certbot *the moment it goes
live*.

One non-obvious warning worth repeating: **do not add a `DocumentRoot`** for the
app vhost. Every request is proxied, and `.env` / `data/` must stay out of
Apache's document tree. Also, `mod_proxy` already forwards `X-Forwarded-For`
via `ProxyAddHeaders` and the app reads its *first* entry — don't additionally
enable `RemoteIPHeader`/`mod_remoteip` for this vhost or the address gets
double-stamped.

**Migration:** `scripts/migrate_sqlite_to_mysql.py` copies a whole old SQLite
database across with IDs preserved so foreign keys survive. Run once, then
delete `data/leadgen.db`.

### Go-live order (from the README, and worth following)

1. `PROVIDER`, `ADMIN_PASSWORD`, `SECRET_KEY` in `.env`
2. `COOKIE_SECURE=true` and `TRUST_PROXY=true` once behind HTTPS
3. Set the Requests-per-day Quota limit in Google Cloud Console
4. Set `ALERT_EMAIL`, and `BACKUP_REMOTE` after `rclone config`. **Run the digest
   job once from the Control page and confirm the mail arrives** — this is the
   channel that tells you when everything else breaks
5. Leave `DRY_RUN=true` for a week and read the approval queue daily
6. Turn the switches on **one at a time**: discovery → enrichment →
   follow-up generation → sending last

---

## 16. Known rough edges and limitations

Documented honestly, both by the project and by this review.

### Not built (from the project)

- **Automated social discovery beyond a business's own website.** Instagram and
  Facebook links are extracted from the site's own HTML — free and reliable.
  Going further needs a search API; scraping a search engine directly is fragile
  and against most of their terms. `stage_social` in `app/enrich.py` is a
  documented extension point that returns `{}` rather than a broken scraper.
- **WhatsApp Business Cloud API.** Click-to-chat only, by design (§7.9).
- **Telegram / WhatsApp alert channels.** Only email is wired up.

### Found while reading the code

- **`AUTOMATION_ENABLED` has no effect on the default master switch.**
  `app/db.py:594-598`: the loop over `DEFAULT_SWITCHES` runs first and sets
  `automation_master` to `"off"`, so the following
  `if get_setting("automation_master") is None` can never be true and the
  `settings.automation_enabled` branch is unreachable. Harmless in practice —
  the Control page is the intended way to turn automation on, and an existing
  DB value is preserved — but setting `AUTOMATION_ENABLED=true` in `.env` will
  not arm the master switch on a fresh install. Use the Control page, or move
  `automation_master` out of `DEFAULT_SWITCHES`.
- **The `FASTJSON` / SQLite comments are stale.** `app/fastjson.py` and
  `app/enrich.py` refer to "SQLite", `app/notify.py`'s `prune()` explains the
  cutoff as a SQLite `datetime('now')` quirk, and `app/enrich.py` mentions "the
  SQLite writer". The storage layer is MySQL. The *reasoning* is still correct
  (the timezone-string mismatch is real), only the noun is wrong.
- **A little dead code, caught by `pyflakes`.** Nothing functional, but worth a
  cleanup pass: `json` and `log_interaction` are imported and never used in
  `app/routers/leads.py`; `query_one` is imported and never used in
  `app/routers/stats.py`; and two locals are assigned then dropped —
  `updates` at `app/routers/leads.py:291` (the SQL below it re-derives
  `first_contacted_date` with `COALESCE`, so the dict is redundant) and `plan` at
  `app/routers/plans.py:84`.
- **The rate limiter is per-process**, so N uvicorn workers multiply the limit by
  N. Documented as intentional; just don't treat it as an exact quota.
- **A multi-worker deployment needs `init_db()` to be race-tolerant.** It is
  (`IF NOT EXISTS` / `INSERT IGNORE` throughout) and drift fixes are guarded by
  an `information_schema` count, so concurrent boots are safe in practice, but
  two workers booting against a brand-new empty database simultaneously is the
  least-tested path in the app.
- **`ENRICH_WORKERS` caps MySQL write contention, not just outbound calls.** The
  enrichment pool is I/O-bound on *other people's* servers, so 8 is conservative
  on a `$4` VPS but could go much higher on a bigger box. It is one env var.

### Things to re-verify yourself

- **Google's field-to-price-tier mapping** in `app/providers/google_places.py`
  moves between Google's pricing updates. The staging pattern is what saves
  money regardless, but check the exact field groupings against the live pricing
  page before scaling.
- **If you change the scoring weights**, re-check `HOT_ALERT_MIN_SCORE`. A test
  (`test_the_alert_threshold_is_actually_reachable`) asserts an ideal prospect
  still clears the threshold, so the suite will tell you.
- **OSM data density varies a lot by region** — excellent in most of Europe and
  urban USA, patchier in parts of India. Spot-check your target city before
  relying on the free provider.

---

## 17. White-labelling (added after the original write-up)

The application is fully white-label: the product name, tagline, landing copy,
support contact, colour scheme and logo/favicon are **runtime configuration**,
not source-code constants. `app/branding.py` is the single resolution point.

**Precedence:** `app_settings` row (set via the admin UI) → `BRAND_*` env var →
built-in default. The DB wins so a rebrand never needs a redeploy; colours are
validated against `^#[0-9a-f]{6}$` and fall back to defaults when a stored value
is bogus or an uploaded logo file has vanished.

**The three pages** (`index.html`, `login.html`, `landing.html`) ship as plain
HTML with `{{PLACEHOLDER}}` markers. `render_page()` substitutes them via regex
and is deliberately strict: an unknown placeholder raises, and a second pass
asserts no `{{` survived — a template key nobody wired up fails loudly instead
of shipping braces to a customer. No template engine, no build step.

**Colours** flow through a generated stylesheet at `/api/branding/theme.css`
(public, 5-minute cache) that is loaded after the two real stylesheets and
re-points the CSS custom properties. The stylesheets themselves now reference
the accent as `rgb(var(--accent-rgb) / alpha)` instead of hardcoded gold rgba()
literals, so one small response re-tints buttons, glows, borders, charts and
selection highlights everywhere. `--on-accent` flips between dark and white
text based on the accent's luma, and a light `BRAND_BACKGROUND` (luma > 0.55)
switches the whole token set to a light ramp (`color-scheme: light`) rather
than leaving dim-grey text on a white page.

**Logo/favicon** upload through `POST /api/branding/logo` into `data/uploads/`
(inside the volume backups and systemd `ReadWritePaths` already cover), are
validated to png/jpeg/webp/svg and ≤1 MB, and are served publicly from
`/api/uploads/{name}` — the filename is checked against what's stored in
`app_settings`, so arbitrary path probes 404. Everything else the brand touches
follows automatically: the startup banner, the FastAPI title, digest and hot-
lead alert emails, the outreach footer via `brand_company_name()`, and the
outbound HTTP User-Agent (resolved per call, since the DB isn't up at import).

**API surface:** `GET/PUT /api/branding`, `POST /api/branding/reset`,
`POST /api/branding/logo` — all admin-gated. Unknown keys are rejected rather
than silently ignored. `app_settings.updated_at` is added by the same schema-
drift mechanism the searches table uses, so pre-branding databases migrate on
next boot.

## 18. Glossary

| Term | Meaning in this app |
|---|---|
| **Niche** | The business category being searched for ("restaurants", "dentists"). |
| **Lead** | One business, with everything known about it and its outreach history. |
| **Candidate** | A business returned by a provider *before* dedup — not yet a lead. |
| **Coverage** | Per `(city, area, niche, country)`: how many times searched, when last, and how many barren runs. |
| **Barren run** | A search that produced zero new leads. Three in a row ⇒ `EXHAUSTED`. |
| **Fingerprint** | SHA-256 of everything defining a result set. The search-cache key. |
| **Snapshot** | The leads a completed search produced, stored in `search_cache`. |
| **Stage 1/2/3** | Provider call tiers: cheap identity → details → ratings. |
| **Enrichment** | The 5-stage analysis that fills a lead out from raw listing data. |
| **Data quality** | 0–10 completeness score. Caps the lead score below 3. |
| **Priority** | `HOT` (≥8) / `WARM` (≥5) / `COLD` / `EXCLUDED` (closed). |
| **Approval queue** | The human gate. Automator writes; only `APPROVED` rows send. |
| **Suppression list** | Addresses/phones that are never contacted, ever. |
| **Dry run** | Log what would have been sent, send nothing. To *prospects*. |
| **Target** | A saved niche+city+area to search on a daily/weekly/monthly schedule. |
| **Switch** | A database-stored on/off for automation or one job. No restart. |
| **Lease** | The database row ensuring exactly one worker runs the scheduler. |
| **Breaker** | Circuit breaker: 5 failures ⇒ 15-minute pause for that dependency. |
| **Quota reserve** | Transactional check-and-increment that blocks the call *before* it goes out. |

---

*This document was written by reading the source of Arthvex LeadGen — 11,044
lines across backend, frontend, deploy config and tests. Where it describes a
design decision, the reasoning is the reasoning in the code comments, which
throughout are unusually good about explaining *why* rather than *what*.*
