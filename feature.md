# Arthvex LeadGen — Feature & Architecture Reference

Every feature in the application, what it does, and the mechanics behind it.
Written against the source in this repository; file references point at the
module that implements each behaviour.

---

## Table of contents

1. [What the application is](#1-what-the-application-is)
2. [How it works at a high level](#2-how-it-works-at-a-high-level)
3. [The lead pipeline, end to end](#3-the-lead-pipeline-end-to-end)
4. [Features — discovery & search](#4-features--discovery--search)
5. [Features — leads, dedup, enrichment, scoring](#5-features--leads-dedup-enrichment-scoring)
6. [Features — map, pipeline & follow-ups](#6-features--map-pipeline--follow-ups)
7. [Features — outreach](#7-features--outreach)
8. [Features — the Lead Automator](#8-features--the-lead-automator)
9. [Features — quota, alerts & backups](#9-features--quota-alerts--backups)
10. [Features — accounts, roles, plans](#10-features--accounts-roles-plans)
11. [Features — white-label branding](#11-features--white-label-branding)
12. [Features — the UI surface](#12-features--the-ui-surface)
13. [Data model](#13-data-model)
14. [HTTP API reference](#14-http-api-reference)
15. [Configuration reference](#15-configuration-reference)
16. [Performance engineering](#16-performance-engineering)
17. [Deployment & operations](#17-deployment--operations)
18. [Tests](#18-tests)
19. [Deliberate deviations & known limits](#19-deliberate-deviations--known-limits)

---

## 1. What the application is

A self-hosted lead-generation workspace for a web studio. You point it at a
market (niche + city, or your own GPS coordinates), it discovers local
businesses, enriches each one with contact data and a free analysis of their
existing website, scores them 0–10 on how good a sales prospect they are, tracks
every interaction you log, and prepares outreach that a human must approve
before anything is sent.

Stack: **FastAPI + MySQL 8 + vanilla JS**. No build step, no SPA framework, no
paid API required to start, and it runs on a small VPS. Multi-user, with
subscription plans that gate who may search.

Two design rules run through the whole codebase:

- **Cheapest-first, always.** Free HTML scraping before paid API calls, staged
  detail fetching before ratings, caching before anything leaves the process.
- **Nothing automated ever reaches a prospect without approval.** The automator
  *generates* messages into a queue; only sending reads from it, and only rows
  a human marked APPROVED.

---

## 2. How it works at a high level

### 2.1 Runtime shape

One process. `uvicorn` serves HTTP; an APScheduler `BackgroundScheduler` runs
inside the same process for the nightly jobs. Long work (searches, enrichment,
scoring, follow-up generation) happens on daemon threads so the request that
triggered it returns immediately.

```
Browser ──► FastAPI (app/main.py)
               │  auth middleware  → rate limit → verify signed cookie → load user row from MySQL
               │  routers/*        → JSON API
               │  Branding.render_page → HTML pages (placeholders filled per request)
               └── static/         → index.html, app.js, styles.css (hash router SPA)

APScheduler (app/automation.py)
   ├── leader lease in MySQL → only one worker runs cron jobs
   └── jobs: discovery 06:00 · enrichment 06:30 · scoring 07:00
             follow-ups 07:30 · digest 08:00 · backup 02:00 · sending every 30 min

MySQL (app/db.py)  ← thread-local pooled connections, one shared per thread
```

### 2.2 Request lifecycle

`app/main.py:68` installs one HTTP middleware that does everything auth-related:

1. If the path starts with `/api/`, apply the per-IP rate limit
   (`API_RATE_LIMIT`, default 600/min) → `429` if exceeded.
2. If the path is public (`/landing`, `/login`, `/admin`, `/health`,
   `/api/auth/login`, `/api/auth/admin/login`, `/static/*`, `/api/uploads/*`,
   `/api/branding/theme.css`) → pass through, with `Cache-Control: no-cache` on
   static assets and HTML so a deploy is picked up on the next reload.
3. Otherwise verify the signed session cookie, then **re-read the user row from
   MySQL** so a deactivated or deleted account is locked out on its very next
   request rather than whenever the token happens to expire.
4. Unauthenticated page requests redirect: `/` → `/landing` (public marketing
   page), everything else → `/login`. Unauthenticated API calls get `401`.

### 2.3 Database access

`app/db.py` is the only module that touches the database.

- **Thread-local connections** (`get_conn()`), reused across queries instead of
  a fresh connection (and its PRAGMAs/handshake) per call. `autocommit=True` by
  default; explicit `START TRANSACTION` is used only where a multi-statement
  unit needs it (dedup+merge, quota reservation, scheduler lease, alert claims).
- **One SQL dialect.** Every query is written with SQLite-style `?`
  placeholders; `TranslateCursor` (`app/db.py:53`) rewrites them to `%s` for
  PyMySQL, so nothing above `db.py` knows which database it is talking to.
- **Schema at boot.** `init_db()` creates missing tables, adds later columns to
  databases created by older builds (`searches.user_id`, `searches.cached_from`,
  `app_settings.updated_at`), seeds the 8 pipeline stages, the default switches,
  the outreach templates, and the first `superadmin` from `.env`.
- **Activity logging is first-class.** `log_automation()` and `log_failure()`
  write every job action and every error to tables the UI reads back as the
  activity feed and the unresolved-failures list.

### 2.4 Configuration

`app/config.py` reads `.env` into a single `settings` object with typed
accessors (`_bool`, `_int`, `_float`, `_str`). Anything the operator left blank
is generated at boot (`ensure_runtime_secrets`) so a first run always works, and
the startup banner says loudly what was generated and that a generated
`SECRET_KEY` will sign everyone out on the next restart.

---

## 3. The lead pipeline, end to end

This is the core loop. One search, start to finish, is `run_search()` in
`app/search_service.py:262`.

```
POST /api/search  (or a scheduled target)
        │
        ├─ 1. Entitlement gate     subscription.check_search_entitlement
        ├─ 2. Cache probe          search_service.find_cached_search
        │        └─ hit? → reuse previous search's leads, 0 provider calls, return
        └─ 3. provider.search()    cheap discovery fields for every result
                 │
                 ├─ 4. dedup          dedup.find_duplicate → merge or insert
                 ├─ 5. fetch_details  only for places we definitely don't have
                 ├─ 6. fetch_ratings  only if provisional score ≥ ENTERPRISE_FETCH_MIN_SCORE
                 ├─ 7. insert lead    scored at insert time
                 ├─ 8. enrich_many    bounded thread pool, 5 stages per lead
                 ├─ 9. notify         batched HOT-lead alert
                 └─ 10. finalize      search row + coverage row + cache snapshot
                                        + charge 1 search to the user's plan
```

The design is **idempotent**: re-running a search that crashed halfway creates
no duplicates, because every candidate passes through the dedup check before
anything is inserted.

### Staged fetching — the cost control

The provider interface (`app/providers/base.py`) is deliberately three-stage so
expensive fields are only fetched for places that survived cheap filters:

| Stage | Method | Called for | Carries |
|---|---|---|---|
| 1 | `search()` | every result | name, place_id, address, coords, maybe phone/website |
| 2 | `fetch_details()` | new places only | phone, website, business status |
| 3 | `fetch_ratings()` | leads already scoring ≥ 5 | rating, review count |

On free providers (`osm`, `mock`) stages 2 and 3 are no-ops — the one query
returns everything it has, or there are no ratings to fetch. On Google Places
this is what keeps the bill proportional to lead quality rather than to the
size of the result list.

### Data flow of a single lead

```
provider row
   → normalise_phone / whatsapp_number      app/validation.py
   → score_lead()                           app/scoring.py      (provisional, gates stage 3)
   → INSERT leads (status NEW, enrichment_status pending)
   → enrich_lead():
        stage_website  reachability · HTTPS · mobile · speed · platform ·
                      copyright-year staleness · contact routes · socials · mailto emails
        stage_social   (documented extension point — links already found in stage 1)
        stage_email    site's contact/about pages → optional Hunter.io for HOT leads only
        stage_validate re-normalise phone/email, recompute wa.me link
        stage_score    final deterministic score + reason list
   → log_interaction / status transitions   app/outreach.py
```

---

## 4. Features — discovery & search

### 4.1 Manual search — the Search page

`POST /api/search` (`app/routers/search.py:41`) and the `#/search` route in
`app/static/app.js:258`.

Fields: niche, city, area (optional), country (optional), max results,
provider override, and an `enrich` flag. The endpoint:

1. Rejects an empty niche (`400`).
2. Runs the subscription entitlement check (`403` with a specific reason:
   `no_active_subscription` vs `search_limit_reached`).
3. Caps `max_results` for gated users by their plan (`subscription.max_results_for`).
4. Probes the search cache.
5. If it's a cache miss on the Google provider and today's quota is exhausted,
   returns `429` before starting — a cache hit is allowed through, because
   hitting the cap is exactly when the cache earns its keep.
6. Creates the `searches` row and returns immediately with `search_id`; the work
   runs on a daemon thread.

The UI polls `/api/search/{id}/status` every 1.2s while `RUNNING`, renders the
live progress string the worker writes (`"12/40 checked — 5 new leads"`), offers
a **Cancel** button that flips a flag checked between candidates, and on
completion shows a summary plus the found leads with their score chips.

Search history is a table on the same page: id, niche, where, found / new /
dupes, status (with a `cached` marker), timestamp. Clicking a row jumps to
`#/leads?search=<id>`.

### 4.2 Search near me (GPS)

`app/static/app.js:386` → `POST /api/search` with `lat`, `lng`, `radius_m`.

The browser's geolocation drives the search; you pick a radius (1–25 km). You
never type a city — the server **reverse-geocodes the point** (`app/geo.py`) and
fills city/area/country in itself, so the lead records, the search log and the
coverage map all read as places instead of decimal pairs. The response echoes
back the resolved city/area/radius and the UI fills the form fields in.

Radius is clamped to `MAX_NEARBY_RADIUS_M` (default 50 km). Providers that
support point search skip geocoding entirely.

Geocoding backends, cheapest first:

| `GEOCODER` | Backend | Cost |
|---|---|---|
| `osm` (default) | Nominatim reverse | free, no key |
| `google` | Google Geocoding API | billed per call, needs `GOOGLE_API_KEY` |

Nominatim's policy (real User-Agent, ≤1 req/sec) is respected: the User-Agent
is derived from the white-label brand name in `app/http.py:_user_agent`, and the
OSM provider sleeps 1s after geocoding.

### 4.3 Shared search cache

`app/search_service.py:99` (`search_fingerprint`) and `:129`
(`find_cached_search`).

The exact query is hashed with SHA-256 over: niche, city, area, country,
provider, and the point/radius. Results are snapshotted into `search_cache`,
pointed at the search that produced them.

- **Hit** → the new search row records `cached_from = <source id>`, copies the
  hot/warm/cold counts, logs "0 API calls", and still charges the searching
  user's plan one search (it *is* one of their searches).
- `max_results` is deliberately **not** in the fingerprint — it's a page size,
  not content. Two users on different plans share the snapshot; each gets the
  top-N they asked for.
- Only `triggered_by="manual"` searches consult the cache. Scheduled and target
  runs always scrape fresh, so the daily sweep refreshes the snapshot.
- A snapshot older than `SEARCH_CACHE_TTL_DAYS` (default 7) is a miss, and
  `created_at` resets on every refresh so the TTL measures from the last scrape.
- A cache entry whose producing search did not reach `COMPLETED` is refused.
- The history row and the status endpoint both expose `from_cache` and
  `cache_source_search_id`, and the UI shows a "served from cache — 0 API calls"
  banner.

### 4.4 Coverage map — knowing where to look next

`app/search_service.py:37` (`coverage_status`) + `GET /api/coverage`.

Every search updates a `search_coverage` row keyed by city + area + niche +
country, tracking times searched, total leads found, new leads last search,
barren runs, and last searched. Status is derived:

| Status | Rule | Meaning |
|---|---|---|
| `NEVER` | never searched | highest priority |
| `FRESH` | searched < 7 days ago | probably nothing new yet |
| `STALE` | 7–30 days, or >30 days | new businesses may have appeared |
| `EXHAUSTED` | 3 consecutive barren runs | stop burning quota here |

`GET /api/search/preview` returns the same verdict plus a human sentence, and the
Search page shows it live as you blur each field — so you know before spending
a call whether a combo is worth running.

The `#/coverage` page renders a city × niche matrix, each cell coloured by status
with the lead count in it and a tooltip with search/run details.

### 4.5 Targets — recurring discovery

`/api/targets` + the `#/targets` page (`app/static/app.js:1565`).

A target is a saved niche + city (+ area/country) with a max-results cap and a
frequency: `daily`, `weekly`, `monthly`, or `manual`. Each target row also shows
its live coverage status, cumulative leads found, last run, and active/paused
state, with **Search now**, **Pause/Resume**, and delete actions.

The discovery job picks up targets that are due (`next_search_date <= today`),
ordered by least-recently-searched first, limited to `TARGETS_PER_RUN` (default
3). If a target's coverage is `EXHAUSTED`, the automator **deactivates it and
logs why** rather than scraping a barren query forever.

### 4.6 Providers

`PROVIDER` in `.env` picks where businesses come from. `GET /api/providers` lists
what's available and whether it's ready (the Search page disables the Google
option when no key is set).

| Provider | Cost | Notes |
|---|---|---|
| `osm` (default) | Free, no key | OpenStreetMap via Overpass. Name, address, often phone and website. **No ratings or reviews.** Density varies by region — spot-check your city. |
| `google` | Per-SKU billing | Google Places API (New). Richest data: ratings, reviews, place ids, hours. |
| `mock` | Free, offline | Deterministic fake businesses for demos and tests — same niche+city always returns the same businesses, so re-running a search is a genuine duplicate test. |

The OSM provider (`app/providers/osm.py`) maps ~40 niche keywords onto OSM tag
filters (`restaurant` → `["amenity"="restaurant"]`, `dentist` → `amenity=dentist`
or `healthcare=dentist`, …), falls back to a name-regex match for unknown
niches, geocodes the city (or uses the given point), then queries Overpass with
an `around:<radius>` filter.

---

## 5. Features — leads, dedup, enrichment, scoring

### 5.1 The Leads page

`GET /api/leads` (`app/routers/leads.py:78`) and `#/leads`
(`app/static/app.js:473`).

Filters: free-text `q` (name / phone / email / address), priority, status, city,
niche, country, source, has-website, tag, minimum data quality, enrichment
status. Sortable by created/updated/score/name/quality/follow-up date. Paginated
with a `MAX_PAGE_SIZE` ceiling from config.

Quick filters: **Hot leads**, **No website**, **Never contacted**.

The list endpoints deliberately send only the 12 columns the table renders
(`LIST_COLUMNS`), not all 56 — a 50-row page drops from ~86 KB to a fraction of
that. `?full=true` returns the whole row; the drawer fetches that on open.
See [§16](#16-performance-engineering).

**Bulk actions**: select rows → bulk status change, bulk tag, clear selection.
**Export CSV** (`/api/leads/export`) streams a full-row export of the current
filter, doubling as the manual "get everything out" safety net.
**Find duplicates** (`/api/leads/dedupe`) sweeps the whole table for duplicates
that predate a rule change.

### 5.2 The lead drawer — four tabs

Clicking any lead opens a side drawer (`app/static/app.js:655`).

**Details** — one-tap action buttons (Call `tel:`, WhatsApp `wa.me`, Email
`mailto:`, Maps, **Re-enrich**, **Delete**), editable status and follow-up date,
and the full record: owner, phone (+ unvalidated flag), email, address, website,
Instagram/Facebook/LinkedIn, Google rating + review count, touchpoints, last
contacted, found date, enrichment status and any enrichment error, plus free-text
notes.

**Outreach** — generate a message from any template for that lead (subject +
body rendered from the merge context), copy it, or queue it for approval.
Generated text includes a pre-filled WhatsApp click-to-chat link.

**Timeline** — every logged interaction newest-first: date, type, automated flag,
subject, content, outcome, next step.

**Analysis** — *"Why this lead scored 8/10"*: the list of scoring rules that
fired, then the website analysis (score, platform, HTTPS, mobile-friendliness,
load time, copyright year, when it was checked) and the human-readable issue
list. A lead with no website gets an explicit "this is the easiest pitch you can
make" note.

### 5.3 Deduplication

`app/dedup.py`. Four checks, cheapest and most certain first:

1. `place_id` — exact, certainly the same business.
2. `phone` — same phone = same business even if the name differs.
3. exact `name` + `city` after normalisation.
4. fuzzy name ≥ 80% within the same city.

**No duplicate ever creates a second lead.** Instead `merge_into_existing()`
fills in *blanks only* on the record you already have — a re-search never
overwrites data you have or edited. Each filled field is logged.

Two refinements worth knowing:

- `similarity()` blends character similarity (`SequenceMatcher`) with **token
  containment**. Plain string similarity scores `"Sharma's Dental"` vs
  `"Sharma Dental Clinic"` at 0.74 and misses it, because the second name is
  simply longer; containment catches it.
- `normalise_name()` strips possessives *before* tokenising (`sharma's` →
  `sharma`), because a stray one-letter `s` token badly skews containment, and
  drops noise words (`the`, `co`, `pvt`, `centre`, `services`, …).
- **Two different phone numbers block a fuzzy merge**, so chain branches
  (`Domino's Sector 15` vs `Domino's Sector 21`) never collapse into one lead.

`dedupe_existing_leads()` applies the same rules as a whole-table sweep:
interactions are re-pointed at the survivor, the duplicate row is deleted, and a
merge report is returned.

### 5.4 Enrichment

`app/enrich.py`. Five stages, each writing its result back immediately, so a lead
that fails at stage 3 keeps everything stages 1–2 found.
`enrichment_status` moves `pending → partial → complete` so the dashboard shows
progress rather than a lead that looks unfinished.

**Stage 1 — website analysis** (`app/website.py`, all free, plain HTTP + regex):

| Check | How |
|---|---|
| Reachability | HTTP status; a dead site is a *finding*, not an error |
| HTTPS | final URL scheme after redirects |
| Mobile-friendly | `<meta name="viewport">` present |
| Load speed | wall-clock ms of the request |
| Platform | signature match against WordPress, Wix, Squarespace, Shopify, Webflow, Google Business Site, GoDaddy Builder, Weebly, Next.js, React |
| Staleness | copyright year in the HTML vs current year |
| Contact routes | presence of a form, and `/contact`, `/contact-us`, `/about`, `/about-us` |
| Socials | Instagram / Facebook / LinkedIn URLs in the markup (share/plugin URLs filtered out) |
| Emails | `mailto:` addresses plus addresses in text, minus disposable/vendor domains |

Website score starts at 4 for a site that loads at all, then ±2 for HTTPS,
mobile-friendliness and speed, −2/−1 for a stale copyright year, −1 for
template platforms, −1 for near-empty HTML, −1 for a missing page title. Issues
are written as complete sentences that finish the phrase *"your site …"* — they
are pasted straight into outreach copy.

Results are cached for `WEBSITE_CACHE_DAYS` (default 30) via `website_checked_at`,
so re-enriching an unchanged lead costs nothing.

**Stage 2 — social discovery.** Social links scraped from the business's own
website in stage 1. Going beyond that needs a search API; rather than scrape a
search engine (fragile, and against most of their terms), `stage_social()` is a
documented, deliberately empty extension point.

**Stage 3 — email discovery.** Cheapest first: the site's own contact/about
pages. If that finds nothing *and* the lead is HOT *and* `HUNTER_API_KEY` is
set, it tries Hunter.io once, reserving against the monthly cap first and
skipping cleanly (with a log line) when the cap is reached.

**Stage 4 — validation.** `app/validation.py`: phone normalisation (Indian
numbers reduced to the bare 10-digit national form; landlines accepted, since
established businesses often list only one; anything under 7 digits invalid),
email syntax plus disposable-domain and role-address rejection (`noreply@`,
`postmaster@`), and the WhatsApp number recomputed from the normalised phone —
otherwise a corrected number leaves a stale `wa.me` link behind. Indian
landlines deliberately get **no** WhatsApp button, because the link opens a dead
chat.

**Stage 5 — scoring.**

Batching: `enrich_many()` runs a bounded thread pool (`ENRICH_WORKERS`, default
8). Enrichment is almost entirely *waiting on other people's web servers*, and a
dead site burns the full timeout, so sequentially 60 leads means 60 timeouts end
to end. Each lead writes its own results, so one bad lead can't sink the batch.

Bulk: `enrich_pending()` takes everything still `pending`/`partial`.
`rescore_all()` recomputes every score after a rules change.

### 5.5 Scoring

`app/scoring.py` — deterministic weighted rules, never a model. Every lead stores
the list of rules that fired in `score_reasons`, so you can always see why it
scored what it did.

| Rule | Points |
|---|---|
| No website at all | +4 |
| Website listed but doesn't load | +4 |
| Website scores under 4/10 | +3 |
| Website scores 4–6/10 | +1 |
| Social but no website | +2 |
| Established business (see below) | +1 |
| **Real business + no working website** (ideal prospect) | +2 |
| Temporarily closed | −2 |
| Reachable phone number | +1 |

Permanently closed businesses score 0 and are marked `EXCLUDED`. Scores are
clamped to 0–10; a lead with a data-quality score under 3 is capped at 5
("not enough to act on").

**Established** prefers review data (≥20 reviews) but **falls back to
contactability** (valid phone + address) when review data is absent — OpenStreetMap
carries no review counts at all, so a review-only signal would make HOT
permanently unreachable on the free provider. Verified against live data:
contactable no-website restaurants score 8 (HOT), uncontactable ones score 4
(COLD).

Bands: **HOT** 8–10, **WARM** 5–7, **COLD** 0–4.

`observation_for()` and `website_issue_for()` turn the analysis into one human
sentence for outreach copy: *"you're active on social but don't have a website
yet"*, *"your site has no HTTPS"*.

### 5.6 Lead editing & deletion

`PATCH /api/leads/{id}` accepts only `EDITABLE_FIELDS` (status, tags, notes,
owner, phone, email, whatsapp, follow-up date, priority, estimated budget,
business size, …) — an allow-list, so no other column is writable through the
API. Anything unknown is rejected with the list of allowed fields.

`DELETE /api/leads/{id}` is a **hard delete**, including interactions and queued
messages, for GDPR / DPDP erasure requests. Not recoverable outside a backup.

---

## 6. Features — map, pipeline & follow-ups

### 6.1 Map

`#/map` (`app/static/app.js:1110`), backed by `GET /api/geo/map`.

Every lead with coordinates, trimmed to what a marker needs (name, coords,
score, priority, status, city/area, phone, website, niche) rather than the full
56-column row. Filter by priority, city or status; limit up to 5000.

- Marker **colour** = priority (HOT red, WARM amber, COLD grey), **size** = score.
- Clicking a pin opens a popup with contact details and an *Open lead* link that
  jumps into the drawer; there's also a jump straight to Google Maps.
- *Find me* centres the map on the browser's position (a distinct marker).

The map is the **Google Maps JavaScript API**, loaded lazily on first visit to
the page (`loading=async`, with a 15s timeout and a readable error rather than a
blank box). This is the one surface that can't be vendored — Google's API is only
served from their CDN — so it needs `GOOGLE_MAPS_JS_KEY` with billing enabled and
a referrer restriction. Optional `GOOGLE_MAPS_MAP_ID` enables cloud-based dark
styling; without one the app falls back to a legacy dark styles array (passing
both throws, so exactly one is ever set). **Everything else in the app works with
no key and no internet.**

Navigating away calls `disposeMap()` to tear down the canvas, listeners and
markers before the container is replaced.

### 6.2 Pipeline

Stages seeded in `app/db.py:40`, editable in the `pipeline_stages` table:

`NEW → CONTACTED → REPLIED → MEETING_SET → PROPOSAL_SENT → NEGOTIATION → WON`
plus `LOST`, each with a colour and an auto-follow-up interval.

`#/` (`app/static/app.js:1257`) renders one column per stage with counts and the
top 25 leads by score in each; clicking a card opens the lead. Status changes
happen automatically when you log an interaction (`NEW` → `CONTACTED` on first
contact) or can be set by hand.

### 6.3 Follow-ups

`GET /api/follow-ups/{today,overdue,upcoming,suggested}` + `#/followups`
(`app/static/app.js:923`), four lists:

- **Overdue** — follow-up date in the past, not WON/LOST. Marked red.
- **Due today**.
- **Cadence says these are due** — leads whose `last_contacted_date` is 3, 7, 14
  or 21+ days old, with the recommended action for that step (Day 3 → WhatsApp,
  Day 7 → call, Day 14 → final email, Day 21 → mark Lost). This works *without*
  an explicit follow-up date set.
- **Next 7 days**.

Dated rows get **Done** (clear the date) and **+3d** (snooze). Cadence rows only
get *Log*, deliberately: they're computed from `last_contacted_date`, so a
date-setting button there would appear to do nothing.

### 6.4 Interactions

`POST /api/leads/{id}/interactions` records any touchpoint — email, call, WhatsApp
or note — with subject, content, outcome, next step, optional follow-up date, and
an optional status override. It updates the lead in one statement: status
transition, first/last contacted dates, `total_touchpoints + 1`, contact method,
and follow-up date (only if one wasn't already set).

---

## 7. Features — outreach

### 7.1 Templates

`GET/POST/DELETE /api/templates` + `#/templates`. Seeded with 8 templates
(`app/seed.py`): cold email, Day 3 / 7 / 14 follow-ups, WhatsApp first-touch and
follow-up, a cold **call script**, and a proposal email.

Merge fields available in subject and body:

```
{business_name} {business_possessive} {owner_name} {city} {area} {niche}
{phone} {email} {website_url} {observation} {website_issue}
{portfolio_url} {sender_name} {sender_company}
```

`{observation}` and `{website_issue}` are generated per lead from the analysis,
so the email opens by naming that business's actual problem. `{business_possessive}`
handles the English possessive correctly (`Django Meals'` vs `Sharma Dental's`)
because a mangled possessive in a subject line is the first thing a recipient
sees. The API reports unfilled fields so a typo in a template is visible rather
than shipping a literal `{observation}` to a prospect.

### 7.2 The approval queue — the human gate

`/api/approvals` + `#/approvals` (`app/static/app.js:1327`).

`queue_message()` renders a template against a lead and writes a row into
`approval_queue` with status `PENDING`. The page shows every pending message with
an editable subject and body, plus **Approve**, **Skip**, **Open lead**, and
**Approve all**. Edits are only possible while a row is `PENDING` or `APPROVED`.

Hard rules, all in `app/outreach.py`:

- **Generation never sends.** `generate_followups()` only writes to the queue.
- **Sending only reads `APPROVED` rows.** `send_approved()` selects
  `status='APPROVED' AND channel='email'` — nothing else can send, ever.
- **The suppression list is checked immediately before every send**, not when
  the message was generated days earlier. A recipient suppressed in between is
  marked `SKIPPED` with the reason recorded, not sent.
- Queueing refuses to create a second message for the same lead + step while
  one is `PENDING`/`APPROVED`/`SENT`.

### 7.3 Sending

`send_approved()` (`app/outreach.py:321`):

1. Computes the remaining hourly budget: `MAX_EMAILS_PER_HOUR` (default 20)
   minus sends in the last hour. Zero budget → stop with an explanation.
2. For each approved row: re-check suppression → send → mark `SENT` and log an
   interaction, or mark `FAILED` with the error and record a failed job.
3. Logs a summary.

Delivery is Resend (`RESEND_API_KEY`), through the shared HTTP client with
retries and a circuit breaker. Every email carries an opt-out footer
("Reply STOP or email … and I'll remove you immediately") and
`List-Unsubscribe` / `List-Unsubscribe-Post: One-Click` headers.

**Hard bounces auto-suppress.** A `400`/`422` whose detail mentions an invalid
address adds the recipient to the suppression list, so a dead address is never
retried.

### 7.4 Dry run

`DRY_RUN=true` (the default) makes `send_email()` log
`[DRY RUN] would email …` instead of sending, while still marking the row `SENT`
and logging the interaction with outcome `dry-run` — so the queue drains and you
can judge the copy without touching a real person. Leave it on for a week and
read the approval queue daily before going live. The Approvals and Automation
pages both display a banner while it's on.

### 7.5 WhatsApp is click-to-chat only

`whatsapp_link()` builds `https://wa.me/<number>?text=<pre-encoded message>` for
you to tap. The app **never** automates sends against a personal number — that
is the single biggest account-ban risk in a tool like this, so it isn't built.
WhatsApp templates are stored and queued like any other channel, but they exist
to be opened and sent by hand.

### 7.6 Suppression list

`GET/POST/DELETE /api/suppression`, surfaced on the Settings page. Case-insensitive
match on value + kind (email / phone), with a reason (`manual`, `hard bounce /
invalid address`) and a timestamp. Every entry is logged to the activity feed.

### 7.7 Identity

`GET/POST /api/identity` — company name and portfolio URL, edited from Settings
and merged into every template as `{sender_company}` and `{portfolio_url}`.
Company name falls back to the white-label brand, so a rebranded install signs
its outreach correctly without touching this setting. Sender name/email and the
current dry-run state are displayed read-only from `.env`.

### 7.8 Follow-up cadence

`CADENCE` in `app/outreach.py:22`: Day 3, 7, 14 → matching template; Day 21
(`GIVE_UP_DAYS`) → status `LOST` with an auto-appended note. `generate_followups()`
walks leads in `CONTACTED`, picks the furthest step they're actually due, chooses
email when an address exists and WhatsApp otherwise, and queues it. Every
generated message and every auto-lost lead is logged.

---

## 8. Features — the Lead Automator

`app/automation.py` + `#/automation` (`app/static/app.js:1409`).

### 8.1 Scheduler

APScheduler `BackgroundScheduler` in the app's own process. Default plan (all UTC,
all overridable in `.env`):

| Job | Default | Does |
|---|---|---|
| `backup` | 02:00 | `mysqldump` + retention + optional off-site sync |
| `discovery` | 06:00 | run due targets |
| `enrichment` | 06:30 | enrich everything `pending`/`partial` |
| `scoring` | 07:00 | re-score recently updated leads |
| `followups` | 07:30 | generate follow-ups **into the queue** |
| `digest` | 08:00 | health check + daily digest email |
| `sending` | every 30 min | send already-**approved** messages, rate-limited |

`misfire_grace_time` and `coalesce` are set so a job that was missed (host asleep,
container restarted) runs once on the next start instead of being skipped or
run repeatedly.

### 8.2 Multi-worker safety — the scheduler lease

Running `uvicorn --workers N` used to mean N schedulers all running the morning
search. `scheduler_lease` (`app/automation.py:306`) fixes that: every worker runs
a `_leader_tick` every 45s that takes or renews a 120-second lease inside a
transaction that takes the write lock *before* reading, so two workers starting at
the same instant can't both conclude the lease is free. Only the winner gets the
real cron jobs; the rest stand by and take over within ~2 minutes if the holder
dies, and hand the lease back on a clean shutdown so a standby picks up
immediately.

### 8.3 Switches

Stored in `app_settings`, so flipping one takes effect on the next tick without a
restart:

| Switch | Default | Effect |
|---|---|---|
| `automation_master` | off (on if `AUTOMATION_ENABLED=true`) | one flip stops every scheduled job |
| `job_discovery` | on | runs due targets each morning |
| `job_enrichment` | on | website checks, validation, scoring |
| `job_followup_generation` | on | writes messages into the approval queue — **never sends** |
| `job_sending` | off | sends messages you already approved, rate-limited |

A job checks the master switch *and* its own switch before doing anything. The
Control page renders each as a toggle with an explanation, plus a
**Kill switch — stop everything** button that is always available even if
individual jobs are misbehaving.

`Run now` on any job passes `force=True`, so an explicit operator action never
silently no-ops just because the schedule is paused. Long-running jobs are
dispatched to a background thread and the page tells you to watch the activity
feed, rather than holding the HTTP request open.

### 8.4 Activity feed & failures

Every job action goes to `automation_log` (job, level, message, timestamp); every
caught error goes to `failed_jobs`. The Control page shows today's counters
(searches, leads found, enriched, awaiting approval, sent), the switch set, each
job's next run time, unresolved failures with the actual error text and a *Mark
all reviewed* button, and the recent activity feed with error/warn colouring.
Failures surface as a red banner on the dashboard too.

### 8.5 Daily digest

`build_digest()` composes a plain-text morning report: new leads (24h), new HOT
leads, awaiting approval, follow-ups due, unresolved errors, API calls vs cap,
last backup (and last off-site sync), and a per-search breakdown. It is logged to
the activity feed *and* emailed.

The digest runs **even when automation is off** — so a silent morning genuinely
means something is broken.

---

## 9. Features — quota, alerts & backups

### 9.1 Quota hard stop

`app/quota.py`. This is the **second** layer of protection; the first is a
Requests-per-day Quota limit in the Google Cloud Console (a Cloud Billing budget
alert only emails you — it does not stop spending).

`check_and_reserve(service, tier, n)` runs in a transaction: it sums today's (or
this month's, for Hunter) usage, and if `used + n > cap` it **rolls back and
raises `QuotaExceeded` before the call goes out**. A runaway loop stops rather
than warns. `QuotaExceeded` propagates out of `run_search`, which marks the run
`STOPPED_QUOTA`, logs it, and stops the day's discovery cleanly.

`usage_summary()` powers the dashboard bar, the Automation page, Settings and
`GET /api/quota`, broken down by tier so you can see which stage is spending.

### 9.2 Operator alerts

`app/notify.py`. Three things the system tells you without you going looking.

These take a **deliberately different path** from outreach, and the module
docstring is explicit about why:

- **No unsubscribe footer** — you can't unsubscribe from your own health check.
- **No suppression-list check** — the suppression list protects *prospects*. If
  your own address ever landed on it, alerts would go quiet exactly when you
  needed them.
- **They send during `DRY_RUN`** — a trial week you can't see the results of
  isn't a test.

| Alert | Fires | Dedup key |
|---|---|---|
| New HOT lead | a lead scores ≥ `HOT_ALERT_MIN_SCORE` (default 8) | `hot:<lead_id>` — once, ever |
| API budget | usage crosses `QUOTA_WARN_PCT` (default 80%) | `quota:google:<date>` — once a day |
| Daily digest | every morning | not deduplicated — its *absence* is the signal |

Every alert is **claimed** through an atomic `INSERT IGNORE` into `alerts_sent`
*before* it goes out, so two workers racing on the same key can't both win and a
lead can never be alerted twice. HOT alerts are batched into one email — ten
separate alerts for one overnight run is how an operator learns to ignore alerts.
Alerts are always written to the activity feed even when email isn't configured,
so an alert is never lost just because mail isn't set up yet. `prune()` drops
old quota keys after 90 days.

### 9.3 Circuit breakers, timeouts, backoff

`app/http.py`. Every outbound call in the app goes through one place.

- **One pooled `httpx.Client`** for the whole process (keep-alive; sized by
  `HTTP_POOL_SIZE`, default 32), instead of a new client — and a fresh TCP + TLS
  handshake — per call.
- **Timeout** on everything (`HTTP_TIMEOUT`, default 8s).
- **Exponential backoff with jitter** (1s, 2s, 4s, 8s…) on 429/5xx, capped
  attempts, so parallel workers don't sync up.
- **Circuit breaker per dependency** (`breaker` name: `website_check`,
  `overpass`, `nominatim`, `google_places`, `google_geocode`, `resend`,
  `hunter`). Five consecutive failures → paused for 15 minutes instead of
  hammering something already down. Current state is shown on the Automation
  page via `breaker_state()`.

### 9.4 Backups

`app/backup.py`. Nightly `mysqldump --single-transaction`, so the dump is
consistent even if a search is mid-write (on InnoDB that takes a read snapshot
instead of locking tables). Retention `BACKUP_KEEP_DAYS` (default 30). A failed
dump deletes the partial file and logs the error.

`BACKUP_REMOTE` pushes each night's file off-server with `rclone copy`. Failures
are logged and returned, **never raised** — a remote that's down must not cost you
the local backup that already succeeded; the digest surfaces it the next morning
so it can't stay broken quietly.

Backups can be run on demand from Settings (`Back up now`) and listed with sizes
and timestamps. Off-server sync is off by default because it needs an rclone
remote configured first.

### 9.5 CSV export as a safety net

`GET /api/leads/export` streams a full-row CSV of the current filter — the manual
"get everything out" path that doesn't depend on a backup job having run.

---

## 10. Features — accounts, roles, plans

### 10.1 Authentication

`app/security.py` + `app/routers/auth.py`.

- **Signed-cookie sessions** (HMAC-SHA256 over `username|expires`, base64url).
  No session store to keep in sync; the user row is still read from MySQL on
  every authenticated request so deactivation is immediate.
- **PBKDF2-SHA256, 200k iterations**, per-user salt, `hmac.compare_digest`
  comparison.
- **Login rate limit**: 8 attempts per 5 minutes per IP.
- **API rate limit**: 600/min per IP, in-process sliding window with a periodic
  sweep of idle keys (otherwise the dict grows one entry per client IP forever).
- `client_ip()` reads `X-Forwarded-For` **only** when `TRUST_PROXY=true`, and
  takes the first entry. Untrusted, the header is attacker-controlled and would
  make the limiter trivially bypassable.
- `COOKIE_SECURE` off by default so local HTTP dev can still sign in (a secure
  cookie is silently dropped over plain HTTP, which looks like a broken login).
  Turn it on behind HTTPS.

### 10.2 Two sign-in doors

| Page | Endpoint | Accepts |
|---|---|---|
| `/login` | `POST /api/auth/login` | `user` only (admins get a 403 pointing at `/admin`) |
| `/admin` | `POST /api/auth/admin/login` | `admin` and `superadmin` only |

Both doors are role-floored endpoints. The user door rejects `admin` and
`superadmin` with a 403 telling them to sign in at `/admin`, so an operator who
lands on the wrong page is steered to the right one instead of being let in. A
**wrong password** gets the same 401 as an unknown user, so neither door lets an
attacker enumerate accounts. A regular user's **correct** credentials on the
admin door get a 403 telling them to sign in at `/login` — they have already
proven ownership of the account, so the guidance leaks nothing and stops a
freshly created user from reading "Invalid username or password" as "my account
doesn't work". Signed-in visitors are routed to the door that matches their
role: admins from `/login` to `/admin`, regular users from `/admin` to
`/login`.

### 10.3 Roles

`user` < `admin` < `superadmin`, enforced per request by the `require_role`
FastAPI dependency (`app/security.py:62`). Only a `superadmin` can grant or
change a privileged role; the API refuses otherwise. Sidebar entries and routes
are hidden in the UI, but **the server-side check is what actually enforces it**
— Users, Plans and Branding all require admin or superadmin.

Guards: you cannot delete your own account, and the system refuses to delete or
deactivate the last active superadmin.

### 10.4 Subscription plans

`app/subscription.py` + `app/routers/plans.py` + `#/plans`, `#/myplan`.

A plan carries name, description, price, currency, `period_days`, `search_limit`
and `max_results_per_search`. A subscription is the user's live claim on a plan:
period start/end, searches used, channel (`admin` or `self`), payment status.

Gating rules (`check_search_entitlement`):

- `superadmin` is **never** gated.
- Everyone else needs an *active* subscription with searches remaining.
- Each **completed** search counts one, so the user always sees the full picture.
- A cache hit still counts — it is one of their searches.

Result caps are layered: `min(requested, plan cap, global
MAX_RESULTS_PER_SEARCH)`. Both manual search and "Search now" on a target go
through the same gate.

Ordering is deliberately simple: **one active plan per user** — any new purchase
or assignment expires the previous active subscription, so "the active
subscription" never has to rank competing plans.

Two ways to activate:

- **Admin assigns** directly from the Users page (optional period override).
- **Self-serve**, when the `payment_enabled` toggle is on: a user orders from
  *My Plan*, the order sits `pending`/`unpaid`, and an admin approves it (or
  rejects it) from the Plans page. With the toggle off, buy buttons hide and the
  endpoint rejects the order.

The Plans page lists plans, all subscriptions with approve/reject/cancel actions,
the self-serve toggle and pending-order count. *My Plan* shows the active plan
with a usage bar, the plan catalogue, and the user's own order history.

---

## 11. Features — white-label branding

`app/branding.py`. Every customer-facing surface resolves through this module
instead of being written into the source. Rebranding is config, not a fork.

| Surface | Source key |
|---|---|
| Sidebar wordmark, sign-in, tab title, startup banner | `brand_name` / `brand_short_name` |
| Dimmed second word in the wordmark | `brand_short_name` (default = last word) |
| Sign-in tagline | `brand_tagline` |
| Landing headline / sub-headline | `brand_landing_title` / `brand_landing_subtitle` |
| Footer line, support contact | `brand_footer` / `brand_support_email` |
| Outreach signature and email footer | `brand_company_name` (falls back to the legacy `company_name` setting) |
| Colours everywhere | `brand_accent`, `brand_accent_2`, `brand_background` |
| Logo + favicon | uploaded from Settings, stored in `data/uploads/` |

Mechanics:

- `get_branding()` is **one query**, then fills from defaults. DB values are
  validated — a hand-edited bogus hex colour or a deleted logo file falls back to
  the default instead of breaking every page.
- Pages are `{{placeholder}}` templates rendered per request by plain
  `str.replace`-style substitution. `render_page()` is **strict**: an unknown
  placeholder key raises, and any leftover `{{...}}` raises. Shipping raw braces
  to a customer is worse than a 500 in development.
- Theme changes ship as one small cached stylesheet (`/api/branding/theme.css`,
  `max-age=300`) that re-points the CSS custom properties the existing
  stylesheets are written against. Accent-tinted literals read
  `rgb(var(--accent-rgb) / a)`, so setting the rgb triple re-tints glows, borders
  and highlights automatically.
- **A light background flips the text ramp** automatically (perceived luma
  > 0.55) and sets `color-scheme: light`, so the UI stays readable.
- Uploads: png/jpg/webp/svg, ≤1 MB, stored in `data/uploads/`, served from
  `/api/uploads/{filename}` — which only resolves filenames the admin actually
  uploaded. Previous files are deleted on re-upload.
- `brand_logo` / `brand_favicon` only render if the file actually exists on disk.
- Everything is **per database**: point a second deploy at a second MySQL schema
  and you have a second, independently-branded product from the same image.
  Digest emails, hot-lead alerts and the outbound HTTP User-Agent follow the
  brand automatically.
- Two ways to set it: the admin **Settings → White-label branding** panel
  (immediate, wins over `.env`, with *Reset to default* that also removes uploads),
  or `BRAND_*` variables in `.env` for scripted per-customer deploys.

---

## 12. Features — the UI surface

`app/static/` — no build step, plain ES2020 in `app.js`, one hash-routed SPA at
`index.html`.

### 12.1 Public pages

| Route | File | What it is |
|---|---|---|
| `/landing` | `landing.html` | Marketing page: headline, feature grid, four-step explainer, self-hosting section, plan tiers, CTA. Public. |
| `/login` | `login.html` | Sign-in for user accounts. Admins are redirected to `/admin`. Public. |
| `/admin` | `admin.html` | Administrator door. Public (it *is* a login form); any signed-in user is redirected to `/`. |
| `/` | `index.html` | The app. Requires a session. |
| `/health` | — | `{"status":"ok"}` after a `SELECT 1`, `503` on failure. Unauthenticated, safe for an uptime monitor. |

### 12.2 The app — pages by route

| Route | Page | What it does |
|---|---|---|
| `#/dashboard` | Dashboard | 5 stat tiles, warning banners (quota, failures, pending approvals), conversion funnel, API budget bar, by-niche and by-city tables |
| `#/search` | Search | Search form with live coverage preview, Search near me + radius, live progress with cancel, results table, search history |
| `#/leads` | Leads | Filter bar + quick filters, paginated sortable table, bulk status/tag, CSV export, dedupe sweep, lead drawer |
| `#/map` | Map | Google Maps with priority-coloured, score-sized pins, popups, Find me |
| `#/followups` | Follow-ups | Overdue / due today / cadence-suggested / next 7 days |
| `#/pipeline` | Pipeline | Kanban-style stage columns with top leads |
| `#/coverage` | Coverage map | City × niche matrix coloured FRESH/STALE/EXHAUSTED/NEVER |
| `#/approvals` | Approve sends | The approval queue; generate, edit, approve, skip, approve all, send now |
| `#/automation` | Control | Master + per-job switches, kill switch, job schedule and *Run now*, today's counters, unresolved failures, activity feed |
| `#/reports` | Reports | 7-day totals, conversion between stages, cost per hot lead, by-niche and by-city performance |
| `#/myplan` | My Plan | Active plan + usage bar, plan catalogue, buy buttons, order history |
| `#/targets` | Targets | Saved niche+city combos with coverage, frequency, pause/resume, search now |
| `#/templates` | Templates | Edit every outreach template in place |
| `#/users` | Users *(admin)* | Accounts, roles, active toggle, assign plan, delete |
| `#/plans` | Plans *(admin)* | Plan CRUD, subscriptions with approve/reject/cancel, self-serve toggle |
| `#/settings` | Settings *(admin)* | Branding, landing copy, outreach identity, provider & quota, suppression list, backups, users/plans links, sign out |

### 12.3 UX details worth knowing

- **Nav badges** — lead count, follow-ups due and pending approvals refresh every
  60 seconds.
- **Toast + skeleton loaders** — every route shows skeletons while fetching and
  errors render as a banner instead of a blank page.
- **Debounced coverage preview** on the Search form; the page shows the verdict
  as you blur fields, before spending anything.
- **Everything is escaped** — `esc()` on every interpolation, including table
  values and `onclick` arguments.
- **Empty states everywhere** — each table explains what to do next rather than
  rendering an empty grid.
- **Map teardown on navigation** — the canvas and its listeners are disposed
  before the container is replaced.

---

## 13. Data model

`app/db.py`, 19 tables, InnoDB / `utf8mb4_unicode_ci`.

| Table | Holds | Notable |
|---|---|---|
| `leads` | The core record — per-user private leads | `owner_user_id` (0 = system/unowned); `UNIQUE KEY uq_leads_place_owner (workspace_id, owner_user_id, place_id)`, plus indexes on status, priority, city, niche, follow_up_date, (name, city), phone, enrichment_status, search_id, (workspace_id, owner_user_id) |
| `interactions` | Every logged touchpoint | type, direction, subject, content, outcome, next step, automated flag |
| `searches` | One row per search run | progress string, counts, api_calls, status, `user_id`, `cached_from`, error_message |
| `search_cache` | Query fingerprint → source search id | the shared cache; TTL from `created_at` |
| `search_coverage` | city+area+niche+country | times_searched, total_leads_found, new_leads_last_search, barren_runs, status |
| `targets` | Recurring discovery definitions | auto_search cadence, next/last searched, is_active |
| `templates` | Outreach copy | type, stage, subject, body, target_market, language |
| `pipeline_stages` | The 8 stages | position, colour, auto_follow_up_days |
| `suppression_list` | Never contact again | value, kind, reason |
| `approval_queue` | The human gate | channel, template_name, step, to_address, subject, body, status, reviewed_at, sent_at, error_message |
| `api_usage` | Every metered call | day, service, tier, calls (the quota ledger) |
| `failed_jobs` | Caught errors | job, error, ref_id, payload, resolved |
| `automation_log` | The activity feed | job, level, message |
| `app_settings` | Runtime config | key/value: switches, brand keys, identity, payment mode |
| `scheduler_lease` | Single row | owner, expires_at |
| `alerts_sent` | Alert dedup | alert_key, kind, created_at |
| `users` | Accounts | password_hash, role, is_active |
| `plans` | Plan catalogue | price, currency, period_days, search_limit, max_results_per_search |
| `user_subscriptions` | User ↔ plan claims | status, period, searches_used, channel, payment_status, FK to users ON DELETE CASCADE |

Seed data created at first boot: 8 pipeline stages, default switches, 8 outreach
templates, and the first `superadmin` from `ADMIN_USER` / `ADMIN_PASSWORD`.

---

## 14. HTTP API reference

All routes are under `/api` except the pages and `/health`. Everything except
the public list requires a valid session cookie.

**Auth** — `POST /api/auth/login`, `POST /api/auth/admin/login`,
`POST /api/auth/logout`, `GET /api/auth/me`

**Search** — `POST /api/search`, `GET /api/search/{id}/status`,
`POST /api/search/{id}/cancel`, `GET /api/search/history`,
`GET /api/search/preview`, `GET /api/coverage`, `GET /api/coverage/{city}`,
`GET /api/providers`, `GET /api/quota`, `GET /api/geo/reverse`, `GET /api/geo/map`

**Targets** — `GET/POST /api/targets`, `PATCH/DELETE /api/targets/{id}`,
`POST /api/targets/{id}/search-now`

**Leads** — `GET /api/leads`, `GET /api/leads/export`, `GET /api/leads/facets`,
`GET /api/leads/{id}`, `PATCH /api/leads/{id}`, `DELETE /api/leads/{id}`,
`POST /api/leads/{id}/enrich`, `POST /api/leads/{id}/interactions`,
`GET /api/leads/{id}/interactions`, `POST /api/leads/{id}/message`,
`POST /api/leads/bulk-status`, `POST /api/leads/bulk-tag`,
`POST /api/leads/dedupe`

**Stats** — `GET /api/stats`, `GET /api/pipeline`, `GET /api/stats/conversions`,
`GET /api/stats/weekly`, `GET /api/follow-ups/{today,overdue,upcoming,suggested}`

**Templates & outreach** — `GET/POST /api/templates`,
`DELETE /api/templates/{id}`, `GET/POST /api/approvals`,
`PATCH /api/approvals/{id}`, `POST /api/approvals/{id}/{approve,skip}`,
`POST /api/approvals/{approve-all,generate,send,queue}`,
`GET/POST/DELETE /api/suppression`, `GET/POST /api/identity`

**Automation** — `GET /api/automation/status`, `POST /api/automation/switch`,
`POST /api/automation/kill`, `GET /api/automation/log`,
`POST /api/automation/run/{job}`, `GET /api/automation/failures`,
`POST /api/automation/failures/{id}/resolve`,
`POST /api/automation/failures/resolve-all`, `GET /api/automation/digest`,
`GET/POST /api/automation/backups`

**Users & plans** — `GET/POST /api/users`, `PATCH/DELETE /api/users/{id}`,
`GET/POST /api/plans`, `PATCH/DELETE /api/plans/{id}`,
`GET /api/subscriptions`, `POST /api/users/{id}/subscriptions`,
`GET /api/me/subscription`, `GET /api/me/subscriptions`,
`POST /api/me/subscriptions`,
`POST /api/subscriptions/{id}/{approve,reject,cancel}`,
`GET/POST /api/settings/payment`

**Branding** — `GET /api/branding`, `PUT /api/branding`,
`POST /api/branding/reset`, `POST /api/branding/logo`, `GET /api/branding/theme.css`
(public), `GET /api/uploads/{filename}` (public)

---

## 15. Configuration reference

Everything is a `.env` variable — raising a limit is a config change and a
restart, not a rewrite.

**Core** — `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`,
`MYSQL_DATABASE`, `MYSQL_CHARSET`, `MYSQLDUMP_PATH`, `MYSQL_CONNECT_TIMEOUT`,
`HOST`, `PORT`

**Auth** — `ADMIN_USER`, `ADMIN_PASSWORD`, `SECRET_KEY`, `SESSION_HOURS`
(168), `COOKIE_SECURE`, `TRUST_PROXY`, `LOGIN_RATE_LIMIT` (8),
`LOGIN_RATE_WINDOW` (300), `API_RATE_LIMIT` (600), `API_RATE_WINDOW` (60),
`MAX_PAGE_SIZE` (500)

**Providers & geo** — `PROVIDER` (`osm`), `GOOGLE_API_KEY`, `GOOGLE_MAPS_JS_KEY`,
`GOOGLE_MAPS_MAP_ID`, `GEOCODER` (`osm`), `NEARBY_RADIUS_M` (3000),
`MAX_NEARBY_RADIUS_M` (50000)

**Quota & search** — `MAX_GOOGLE_CALLS_PER_DAY` (500),
`MAX_RESULTS_PER_SEARCH` (60), `QUOTA_WARN_PCT` (80),
`ENTERPRISE_FETCH_MIN_SCORE` (5), `SEARCH_CACHE_ENABLED` (true),
`SEARCH_CACHE_TTL_DAYS` (7)

**Enrichment** — `HTTP_TIMEOUT` (8), `HTTP_POOL_SIZE` (32), `ENRICH_WORKERS` (8),
`WEBSITE_CACHE_DAYS` (30), `HUNTER_API_KEY`, `MAX_HUNTER_CALLS_PER_MONTH` (25)

**Outreach** — `DRY_RUN` (**true**), `RESEND_API_KEY`, `MAIL_FROM`,
`MAIL_FROM_NAME`, `MAX_EMAILS_PER_HOUR` (20), `UNSUBSCRIBE_MAILTO`

**Alerts** — `ALERTS_ENABLED` (true), `ALERT_EMAIL`, `HOT_ALERT_MIN_SCORE` (8)

**Automation** — `AUTOMATION_ENABLED` (false), `TARGETS_PER_RUN` (3),
`DISCOVERY_TIME` (06:00), `ENRICHMENT_TIME` (06:30), `SCORING_TIME` (07:00),
`FOLLOWUP_TIME` (07:30), `DIGEST_TIME` (08:00), `BACKUP_TIME` (02:00)

**Backups** — `BACKUP_KEEP_DAYS` (30), `BACKUP_REMOTE`, `RCLONE_PATH`,
`BACKUP_SYNC_TIMEOUT` (300)

**Branding** — `BRAND_NAME`, `BRAND_SHORT_NAME`, `BRAND_TAGLINE`,
`BRAND_LANDING_TITLE`, `BRAND_LANDING_SUBTITLE`, `BRAND_FOOTER`,
`BRAND_SUPPORT_EMAIL`, `BRAND_COMPANY_NAME`, `BRAND_ACCENT`,
`BRAND_ACCENT_2`, `BRAND_BACKGROUND`

---

## 16. Performance engineering

Four things were genuinely slow. Each was measured, changed, and measured again:

| | Before | After |
|---|---|---|
| DB reads | 112/sec | 89,500/sec |
| DB writes | 15/sec | 6,050/sec |
| Enrichment, 12 leads with dead sites | 60.8s | 12.4s |
| `GET /api/leads` at 20 concurrent | 36/sec, 540ms | 141/sec, 138ms |

What caused each, and what fixed it:

1. **Thread-local MySQL connections** reused across queries instead of a fresh
   connection (and its handshake/PRAGMAs) per call. `autocommit=True`; explicit
   transactions only where a multi-statement unit needs them.
2. **One pooled `httpx.Client`** instead of a new client per outbound call — every
   website check used to pay a fresh TCP + TLS handshake.
3. **Bounded parallel enrichment** (`ENRICH_WORKERS`) instead of strictly
   sequential — enrichment is almost all waiting on other people's servers, and a
   dead site burns the full timeout.
4. **`FastJSON`** (`app/fastjson.py`): list endpoints return a `Response`
   directly, skipping FastAPI's `jsonable_encoder`, which cost 13× plain
   `json.dumps` (12.5 ms per page) to walk values that were already primitives.
   A `TypeError`/`ValueError` fallback means a new column type can never turn a
   list endpoint into a 500. Combined with sending 12 columns instead of 56
   (`?full=true` for the whole row), this is where most of the win came from.

Under sustained mixed load — 700 requests, 24 concurrent — it holds 154 req/sec
at a p99 of 218ms with zero errors.

Two operational notes: the rate limiter is **per process**, so N workers
effectively multiply the limit by N (it's there to stop runaway loops, not to be
an exact quota). And behind a proxy, `TRUST_PROXY=true` is required, or every
visitor shares one bucket keyed on the proxy's IP — but leave it off when the
app is exposed directly, since `X-Forwarded-For` is client-controlled.

---

## 17. Deployment & operations

**Docker** — `docker compose up -d` runs the app and MySQL 8 (with a healthcheck
the app waits on, and `MYSQL_HOST=mysql` inside the compose network). Adding
`--profile tls` adds Caddy for automatic HTTPS; set `DOMAIN` and `TLS_EMAIL`
first and point the A record at the box or the ACME challenge fails. The app binds
loopback (`127.0.0.1:8000`) and the proxy terminates TLS — this app holds phone
numbers, emails and session cookies, so it is never exposed directly. Data and
backups are named volumes.

**systemd** — `deploy/leadgen.service` is a hardened unit (non-root `User`,
`WorkingDirectory`, `EnvironmentFile`, restricted `ReadWritePaths`). Update later
is `git pull` → `pip install -r requirements.txt` → `systemctl restart`.

**Apache** — `deploy/leadgen-apache.conf` ships the same security headers as
`deploy/Caddyfile`: TLS, reverse proxy to `127.0.0.1:8000`. Do not add a
`DocumentRoot` for the app vhost — every request is proxied and `.env` / `data/`
must stay out of Apache's document tree. Don't also enable `RemoteIPHeader` /
`mod_remoteip`; mod_proxy already forwards `X-Forwarded-For` and the app reads
its first entry, so the address would get double-stamped.

**Migration** — MySQL is the only backend. Coming from SQLite?
`scripts/migrate_sqlite_to_mysql.py` copies the whole old database across with
IDs preserved (so foreign keys survive); run once, then delete
`data/leadgen.db`. Schema drift on older databases is applied automatically at
boot by `init_db()`.

**Going live, in order:** `PROVIDER` / `ADMIN_PASSWORD` / `SECRET_KEY` →
`COOKIE_SECURE=true` + `TRUST_PROXY=true` behind HTTPS → set a Requests-per-day
Quota limit in Google Cloud → set `ALERT_EMAIL`, then `BACKUP_REMOTE` after
`rclone config`, and run the digest once to confirm mail actually arrives →
leave `DRY_RUN=true` for a week and read the approval queue daily → turn the
switches on one at a time: discovery, enrichment, follow-up generation, sending
last.

---

## 18. Tests

83 tests across three files, no network and no API key required:

| File | Tests | Covers |
|---|---|---|
| `tests/test_core.py` | 48 | scoring rules and the HOT threshold, dedup (including the fuzzy/chain-branch cases), phone & email validation, quota reservation, templates & merge fields, suppression, the search cache fingerprint and TTL, coverage status, follow-up cadence, fastjson |
| `tests/test_branding.py` | 24 | brand resolution and precedence, placeholder rendering, theme CSS generation, upload validation |
| `tests/test_admin_login.py` | 11 | role-gated admin login, identical rejection for users, rate limiting, session token round-trip |

Tests force `MYSQL_DATABASE=leadgen_test` and **refuse to run against any other
database**, so they're safe to run while the app is up.

```bash
.venv/bin/python -m pytest tests -q
```

---

## 19. Deliberate deviations & known limits

Four places where the original blueprint's spec doesn't survive contact with real
data. All four are deliberate and the code says so at the point of the change.

1. **Scoring weights reach the HOT band.** As originally specified, the ideal
   prospect tops out around 6–7 and is never HOT, so the operator's main working
   queue stays empty — and a business with no website can't earn the social bonus,
   because social discovery reads the business's own website. Added an explicit
   *real business + no working website = ideal prospect (+2)* combination bonus.

2. **"Established" has a fallback when there are no reviews.** OSM carries no
   review counts, so a review-based signal makes HOT unreachable on the free
   provider. When review data is absent, the code falls back to whether the
   listing is contactable (valid phone + address).

3. **Fuzzy matching needed token containment.** `"Sharma's Dental"` vs
   `"Sharma Dental Clinic"` scores 0.74 on plain string similarity and is missed,
   because the second name is longer. `similarity()` blends character similarity
   with token containment; `normalise_name()` strips possessives first.

4. **The hot-lead alert fires at 8, not 9.** Nothing can score 9 — the only rule
   that could lift a lead past 8 is the social bonus, and social links come from
   the website the ideal prospect doesn't have. Every HOT lead in practice sits at
   exactly 8. A threshold of 9 would ship an alert that silently never fires,
   which is the precise failure the alert exists to prevent. A test asserts an
   ideal prospect still clears the threshold, so changing the weights tells you
   to re-check it.

**Not built, honestly:**

- **Automated social discovery beyond a business's own website.** Instagram and
  Facebook links are extracted from the site's HTML (free, reliable). Going
  further needs a search API; scraping a search engine directly is fragile and
  against most of their terms, so `stage_social()` is a documented extension point
  rather than a broken scraper.
- **WhatsApp Business Cloud API.** Click-to-chat only, by design — automating
  sends against a personal number is the fastest route to a ban.
- **Telegram / WhatsApp alert channels.** Offered as alternatives to email in the
  original spec; only email is wired up.
- **In-app payments.** Self-serve ordering creates a pending order with a payment
  reference for an admin to approve; there's no gateway integration, so a payment
  provider is the natural next step.

**One thing to re-check before scaling:** the Google Places field-to-price-tier
mapping in `app/providers/google_places.py` moves between Google's pricing
updates. The staged calling pattern is what saves money regardless — never fetch
details for a place you already have, never fetch ratings for a lead that already
looks cold — but verify the exact field groupings against Google's live pricing
page before scaling up.