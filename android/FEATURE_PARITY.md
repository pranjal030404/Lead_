# Web ↔ Android feature parity

Reference: `app/static/app.js` (17 hash routes) and `app/routers/*`.

**Status key.** `C` = implemented, request/response contract verified against a
local copy of the backend (curl with the app's payloads; real responses decoded by
unit tests). `I` = implemented, compiles, not exercised on a device. Nothing is
marked "device-verified": the Android emulator segfaults in this environment
(exit 139) and no phone was attached. Production data was never touched.

| # | Web route | Android screen | Features | Status |
|---|---|---|---|---|
| 1 | `/dashboard` | Dashboard tab | KPI tiles, banners (quota / failures / approvals), conversion funnel, API budget, by-niche, by-city, pipeline bars | C |
| 2 | `/search` | More → Search | niche/city/area/country, provider, max results, near-me + radius, coverage preview, plan/quota banner, live progress + cancel, history, results | C (start/status/cancel contracts), I (UI) |
| 3 | `/leads` | Leads tab | text search, priority/status/city/niche/website filters, quick filters, sort, paging, bulk status, CSV export (share sheet), duplicate merge | C |
| 4 | lead drawer | Lead detail | call / WhatsApp / email / maps / social links, status, follow-up, notes, re-enrich, delete, log interaction (+status, follow-up), timeline, message generator + queue, queued list, score reasons, website analysis, intelligence (scores, signals, competitors, opportunities, contacts, emails, socials, audit) | C / I |
| 5 | `/map` | More → Map | priority filter, find me, markers, open lead | I |
| 6 | `/followups` | Follow-ups tab | overdue / today / upcoming / cadence, call, WhatsApp, done, +3d snooze, log | C |
| 7 | `/pipeline` | More → Pipeline | stage board | I |
| 8 | `/coverage` | More → Coverage | searched cells | I |
| 9 | `/approvals` | More → Approve sends | edit, approve, skip, approve all, generate follow-ups, send approved | C |
| 10 | `/automation` | More → Control | switches, kill switch, run job, failures + resolve, log, backups | C |
| 11 | `/reports` | More → Reports | weekly, outreach, by niche/city, cost per hot lead, funnel | C |
| 12 | `/myplan` | More → My Plan | current plan + usage, plans, buy (only when self-serve is on and no plan), history | C |
| 13 | `/targets` | More → Targets | add (area/country/frequency), pause/resume, search now, delete | C |
| 14 | `/templates` | More → Templates | list, create/edit (type, stage, market), delete, merge-field hint | C |
| 15 | `/users` (admin) | More → Users | list, create, edit, assign plan, delete | C |
| 16 | `/plans` (admin) | More → Plans | plans CRUD, orders approve/reject/cancel, self-serve toggle | C |
| 17 | `/settings` (admin) | More → Settings | sender identity, suppression list, provider & quota, backups, full branding (name, suffix, tagline, 3 colours, landing copy, company, support, footer), logo/favicon upload (png/jpg/webp/svg ≤1 MB), reset, sign out | C (branding PUT + hex validation), I (image upload) |

Auth: `/login` (users) and `/admin` (admins). Backend answers an admin at the user
door with `403 "Administrators sign in at /admin."`; the app follows exactly that
response to the admin door and never retries on 401.

## Production hardening
- Server fixed per build type (`BuildConfig.SERVER_URL`): release = `https://lead.arthvex.co.in`; no URL field, no test button.
- Release: cleartext off, system CAs only, R8 shrink, no logging, no `10.0.2.2`/localhost strings in dex (checked).
- Session cookie encrypted at rest (Android Keystore AES-GCM); backups disabled; 401 on any call clears the session and returns to sign-in; network failure at launch offers Retry instead of signing out.
- Permissions: INTERNET, coarse/fine location (near-me search and map only).
- Debug build points at `http://10.0.2.2:8000` for local testing only.

## Not done / blockers
- Device/emulator run: blocked (emulator crashes with SIGSEGV here).
- Real-account run against production: no credentials provided; not attempted.
- Signing: APK is signed with the Android **debug** key. Create a release keystore and a `signingConfig` before any store or public distribution.
- Web-only: Google-Maps JS rendering (Android uses its own marker canvas), `landing` marketing page.
