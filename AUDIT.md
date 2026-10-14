# ARTHVEX LeadGen — Audit & Upgrade Plan

Source of truth: the code as of commit `ec60bb3`. Baseline: 83 tests passing
(MariaDB, `leadgen_test`). After Phase 1: 107 passing.

## CURRENT_FEATURES (verified in code)
Auth (HMAC cookie, pbkdf2, roles user/admin/superadmin), per-IP rate limit,
providers (osm, google_places, mock; `Provider` Protocol in `providers/base.py`),
search + cache + history + GPS + coverage + targets, dedup, website check,
Hunter email enrichment, validation, deterministic 0-10 score + HOT/WARM/COLD,
pipeline (8 stages), interactions, templates, approval queue, Resend sending with
suppression/dry-run/hourly cap, WhatsApp click-to-chat, APScheduler automation
with a DB lease, quotas, alerts, mysqldump backups (+rclone), plans/subscriptions
(manual), white-label branding, ~92 endpoints, vanilla-JS UI.

## MISSING (vs. the 83-section spec)
Multi-tenancy/workspaces/RBAC permissions, contacts/companies, deep audit,
opportunity/intent/competitor engines, multi-score model, campaigns/sequences,
inbox + reply classification, tasks, meetings, proposals/quotes/deals, AI layer,
job queue abstraction, API keys, webhooks, payment providers, MFA/password reset,
audit log, saved searches/segments, import/export formats, feature flags,
metrics, field-level lineage.

## SECURITY_RISKS
| # | Risk | Status |
|---|------|--------|
| 1 | **SSRF**: lead website URLs fetched with `follow_redirects=True`, no address checks | **Fixed Phase 1** (`app/netsafe.py`) |
| 2 | No security headers | **Fixed Phase 1** (CSP deferred: inline scripts) |
| 3 | `/health` leaked driver error text | **Fixed Phase 1** |
| 4 | **No tenant isolation**: every authenticated user sees/edits all leads, templates, suppression, settings | **Fixed Phase 2** (workspaces; see MIGRATION_NOTES.md) |
| 5 | Stateless session cookie: no revocation/logout-everywhere; 7-day lifetime | Open — Phase 12 |
| 6 | No CSRF token (SameSite=Lax only; JSON API) | Open — Phase 12 |
| 7 | No MFA, password reset, audit log | Open — Phase 12 |
| 8 | In-process rate limiter (per worker) | Documented, acceptable single-node |
| 9 | DNS-rebinding TOCTOU in SSRF guard | Documented in `netsafe.py` |

## TECHNICAL_DEBT
Schema drift was patched by ad-hoc `_ensure_*` functions → replaced by an ordered
`schema_migrations` runner (Phase 1). All timestamps are VARCHAR(40) ISO strings
(no native DATETIME; leave alone, indexes/queries depend on it). Raw SQL with `?`
translation; no ORM (keep). `app.js` is one 2.2k-line file. `README`/
`DETAILED_DESCRIPTION.md`/`feature.md` overlap. Local dev DB lacks the
`leadgen` test user the README assumes (tests run with `.env` creds and
`MYSQL_DATABASE=leadgen_test`).

## PLAN (phased; each phase ends with a green suite)
1. **Stabilise** — SSRF guard, headers, health, migration runner. *(done)*
2. *(done, 129 tests)* **Foundation schema** — `organizations`, `workspaces`, `workspace_members`;
   add nullable `workspace_id` to leads/templates/targets/etc., backfill to a
   default workspace; repository-layer scoping helper so no query bypasses it.
   Do this *before* new entities so everything is born tenant-scoped.
3. *(done, 172 tests)* Data-confidence + lineage (`field_sources`), contacts, email & social intelligence. **Companies entity deferred** (a lead is still the business; parent-org/multi-location modelling comes with CRM 2.0).
4. *(done, 207 tests)* Deep website audit + opportunity engine (`app/audit.py`, `app/opportunities.py`).
5. *(done, 234 tests)* Multi-score + intent signals + competitor comparison + stored explanations (`leadscore.py`, `signals.py`, `competitors.py`); rule score untouched.
6. CRM 2.0: configurable pipelines, tasks, unified timeline.
7. Campaigns/sequences, reply classification, suppression integration.
8. Meetings, proposals/quotes, deals + revenue reports.
9. AI layer (queue, cache, usage; grounded prompts only).
10. RBAC permissions, API keys, webhooks, billing providers.
11. `JobQueue` abstraction (local thread backend first, Redis optional).
12. MFA, sessions table, CSRF, audit log.
13–15. Analytics, tests, docs/deploy.

Honest scale note: the full spec is many person-months. Phases 2+ are each
substantial; features depending on external accounts (Razorpay/Stripe, calendar,
LLM, Hunter-class providers, social APIs) will be built to a provider interface
and report **BLOCKED_BY_EXTERNAL_PROVIDER** rather than fake success.

## Phase 2 status — known gaps (deliberate, tracked)
- No workspace switcher in the UI yet (API supports `X-Workspace-Id`).
- Subscriptions/quotas are still per *user*, provider quota (`api_usage`) is
  global; org-level billing and per-workspace metering arrive with Phase 10.
- Automation switches are install-wide (superadmin). Per-workspace switches later.
- Daily digest / HOT alerts go to the install operator and span all workspaces.
- `users.role` is still the permission authority; membership role is stored but
  the RBAC permission matrix is Phase 10.
- The migration was verified on synthetic legacy data in a scratch DB, not on
  your real `leadgen` database.

## Phase 3 status — what is and is not real
Real: per-field provenance (provider / website / Hunter / manual / legacy) with
confidence, freshness (TTL-based FRESH/AGING/STALE/EXPIRED) and a 0-100 data
confidence score; nightly aging job; decision-maker contacts from user entry,
the site's own schema.org Person data, and Hunter; email classification
(PERSONAL/GENERIC/ROLE x UNVERIFIED/VERIFIED/INFERRED/INVALID/DISPOSABLE),
pattern inference (always INFERRED, never sendable), Hunter verification
(quota reserved first); social profile registry from the business's own links.
Not done / BLOCKED: follower counts, post frequency and last-activity need
official platform APIs (columns exist, stay NULL); Google Business / Bing / other
discovery providers; MX/SMTP-level verification without a provider; Provider
`fetch_hours/categories/socials`; no UI for any of this yet (API only);
Hunter's monthly cap is still one global pool shared by all workspaces.

## Phase 4 status
Real: SSRF-safe audit of homepage + robots/sitemap + bounded same-host link sample
+ TLS probe; ~45 structured checks (technical/SEO/performance/conversion/content),
technology detection, per-category and overall 0-100 scores, history; PageSpeed
Insights (LCP/CLS/TBT) when keyed, quota reserved first; 15 opportunity types with
evidence, confidence, impact, solution, editable per-workspace value ranges;
OPEN/RESOLVED/DISMISSED/WON lifecycle (fixed sites auto-resolve, dismissals stick);
pipeline stage gated by lead score and audit freshness; opportunity pipeline and
summary endpoints.
Limits: homepage-centric (not a crawl); real INP is field data (TBT used as the lab
proxy, labelled); technology/feature detection is signature-based and says
"not detected" rather than "absent"; trust-signal check is a keyword heuristic;
DNS-rebinding window between validation and TLS probe documented; no UI yet;
competitor comparison (spec section 15) is not started.

## Phase 5 status
Real: 8 component scores (lead quality, digital gap, contactability, business value,
buying intent, decision maker, outreach fit, data confidence) with per-component
reasons/inputs/confidence; Overall Opportunity Score = weight-normalised mean of the
*available* components (unavailable ones excluded, never scored 0), tier, stated
confidence with its formula, full explanation stored, history on change, per-workspace
weights; 9 evidence-backed intent signals with state/event lifecycle and decay;
competitor comparison from the workspace's own leads (+ pinning, on-demand audits,
factual gap statements, "insufficient data" below 2 audited competitors); recompute
after enrichment, edits, audits and nightly; leads list filter/sort by the new scores.
NOT implemented (need external feeds; listed by the API as unavailable, never
faked): new/recently-opened business, hiring signals, new service/branch, social
activity, review-velocity growth (needs periodic rating refresh history).
Limits: business_value is a proxy (reviews + estimated project value, no revenue
data); decision-maker likelihood is a title-keyword heuristic; weights/tiers are
defaults to tune against your own win/loss data (Phase 8 deals will provide it); no UI.
