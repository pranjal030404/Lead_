# Migration notes

Migrations live in `app/db.py` (`MIGRATIONS`, applied in order at boot, recorded
in `schema_migrations`). Each step is idempotent and non-destructive.

## v2 / Phase 2 — workspaces (migrations 4 and 5)

**Back up first** (`POST /api/automation/backups` as superadmin, or `mysqldump`).
The upgrade runs automatically on the next start.

What happens to existing data:
- A `Default Organization` / `Default Workspace` is created.
- Every existing user becomes a member (superadmin -> owner, admin -> admin,
  user -> member), so current behaviour ("everyone sees everything") is preserved
  until you create more workspaces.
- `workspace_id` is added to `leads, interactions, searches, search_cache,
  search_coverage, targets, templates, suppression_list, approval_queue` and
  backfilled (interactions/approval_queue from their lead; the rest to the
  default workspace), then made `NOT NULL` with **no default** and a foreign key.
- `failed_jobs` / `automation_log` get a *nullable* `workspace_id` (NULL = system).
- Global unique keys become per-workspace: `leads(place_id)`, `search_cache
  (fingerprint)`, `search_coverage`, `targets`, `templates(name)`,
  `suppression_list`. The old key is dropped only after its replacement exists.

Behaviour changes to know about:
- Search cache is no longer shared between workspaces (it would leak one
  tenant's leads to another). Expect more provider calls with several tenants.
- Install-wide controls are now **superadmin only**: automation switches/kill,
  backups, digest, plan create/edit/delete, payment toggle.
- Workspace admins see and manage only members of their own workspace.
- `/api/*` returns 403 for a signed-in user who belongs to no workspace.
- Select a workspace per request with `X-Workspace-Id` (must be a membership).

Rollback (only safe before a second workspace holds data — otherwise it merges
tenants): per table drop `fk_<table>_ws`, `<old_unique>_ws`, `idx_<table>_ws`,
re-add the old unique key, `ALTER TABLE ... DROP COLUMN workspace_id`; then drop
`workspace_members, workspaces, organizations` and delete versions 4 and 5 from
`schema_migrations`. Restoring the pre-upgrade backup is the simpler path.

## v2 / Phase 3 — lineage, contacts, email & social intelligence (migrations 6, 7)

Additive only; no existing column or row is changed except four new nullable
columns on `leads` (`data_confidence_score`, `freshness_status`,
`last_verified_at`, `next_verification_at`).

- New tables: `field_sources`, `contacts`, `email_addresses`, `social_profiles`
  (all tenant-scoped; the last three cascade with their lead).
- Migration 7 backfills a lineage row for every populated tracked field of every
  existing lead: `source` = the lead's recorded source (or `unknown`),
  `source_type = legacy`, `confidence = 0.5`, `observed_at` = lead creation time,
  never verified. Scores fill in on the next scoring run (nightly) or enrichment.
  Existing leads are therefore reported honestly as "legacy, unverified".
- Behaviour change: outreach now refuses addresses classified INVALID,
  DISPOSABLE or INFERRED, at queue time **and** again at send time.
- Rollback: drop the four tables, drop the four `leads` columns, delete versions
  6 and 7 from `schema_migrations`.

## v2 / Phase 4 — website audits and opportunities (migration 8)

Additive only: new tables `website_audits` (history kept; findings stored as JSON)
and `lead_opportunities`, plus four columns on `leads` (`website_audit_score`,
`website_audited_at`, `opportunity_count`, `opportunity_value_est`). Both tables
are tenant-scoped and cascade with the lead. Existing leads have no audit until
enrichment or "Audit now" runs; leads with no website get a NO_WEBSITE
opportunity the next time enrichment touches them.

New settings (all optional, see `.env.example`): `DEEP_AUDIT_*`, `AUDIT_*`,
`PAGESPEED_API_KEY`, `MAX_PAGESPEED_CALLS_PER_DAY`.

Behaviour changes: enrichment gains a final stage that runs the deep audit for
leads scoring >= `DEEP_AUDIT_MIN_SCORE` (default 5) and always refreshes
opportunities. Set `DEEP_AUDIT_ENABLED=false` to restore the old request volume.
Quota alerts are now sent only for the Google Places quota (not for PageSpeed).

Rollback: DROP TABLE lead_opportunities, website_audits; drop the four `leads`
columns; delete version 8 from `schema_migrations`.

## v2 / Phase 5 — scores, intent signals, competitors (migration 9)

Additive only: tables `lead_scores` (current scores + full explanation, one row per
lead), `lead_score_history`, `lead_signals`, `lead_competitors`, plus five `leads`
columns (`overall_score`, `opportunity_tier`, `score_confidence`, `intent_score`,
`scored_at`). All tenant-scoped; all cascade with the lead.

The deterministic rule score (`lead_score`, `lead_priority`) is **not modified**.
`opportunity_tier` (HOT >= 75, WARM >= 55) is separate from `lead_priority`.
Existing leads have no overall score until the nightly scoring job (non-forced run:
leads never scored or changed in the last day; "Run now" scores everything), an
enrichment pass, or `POST /api/leads/{id}/scores/recompute`.
`/api/leads` gains `min_overall`, `min_intent`, `tier` filters and sorting by
`overall_score`, `intent_score`, `website_audit_score`, `opportunity_value_est`.

Rollback: DROP TABLE lead_competitors, lead_signals, lead_score_history, lead_scores;
drop the five `leads` columns; delete version 9 from `schema_migrations`.
