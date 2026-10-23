# Arthvex LeadGen — Android app

A native Android companion for the self-hosted **Arthvex LeadGen** CRM in this
repository. It talks to the existing FastAPI backend (same account, same
database) and is built for the one job you do on a phone: **call leads and send
them WhatsApp messages without opening the web dashboard.**

## What it does

- **Leads list** with search, priority filters, status filter and pagination.
- **One-tap Call** — opens the dialer with the lead's number.
- **WhatsApp composer** — opens the chat with a pre-filled message; templates
  configured in the web app (`/api/templates?type=whatsapp`) are available as
  chips, and the message stays editable before sending.
- **Automatic CRM logging** — opening the dialer or a WhatsApp chat writes a
  `call` / `whatsapp` interaction to the lead. The backend also moves a `NEW`
  lead to `CONTACTED` on the first logged touchpoint.
- **Follow-up queue** — Today / Overdue / Upcoming / Suggested tabs with
  Call / WhatsApp / Done actions.
- **Lead detail** — status pipeline chips, follow-up quick dates, notes,
  contact details, interaction timeline and manual logging.
- **Dashboard** — lead counts, pipeline bars and automation counters from the
  server's `/api/stats`.
- **Settings** — change server URL, test the connection, toggle auto-logging,
  open the full web dashboard, sign out.
- **Persistent session** — the FastAPI `session` cookie is stored so you do not
  sign in again every time the app is reopened.

Lead discovery, enrichment, approvals, plans and admin tools remain in the web
app. This client is deliberately focused on working leads.

## Requirements

- Android 7.0 (API 24) or newer.
- The LeadGen server must be reachable from the phone:
  - Emulator: `http://10.0.2.2:8000`
  - Real phone on the same Wi-Fi: `http://<your-computer-LAN-IP>:8000`
  - Public deploy: `https://leads.example.com`

For a real phone, make sure the server is listening on `0.0.0.0` (not just
`127.0.0.1`) and your firewall allows the port. `HOST=127.0.0.1` in `.env` will
only work from the same machine; set `HOST=0.0.0.0` for LAN access.

## Build

The project is standard Gradle + Kotlin + Jetpack Compose. Open the `android/`
folder in Android Studio, or build from the CLI:

```bash
cd android
./gradlew :app:assembleDebug
```

The APK lands at:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

Install it with:

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

or copy the APK to the phone and open it (allow "install unknown apps" for your
file manager).

### Release build

```bash
cd android
./gradlew :app:assembleRelease
```

The release variant is unsigned. Configure a signing config in
`app/build.gradle.kts` (or use Android Studio's *Generate Signed Bundle / APK*)
before distributing it.

## First run

1. Start the LeadGen server (`python run.py`).
2. Open the app, enter the server URL, and tap **Test connection**.
3. Sign in with the same credentials as the web app.
   - Regular users: leave **Administrator account** off.
   - `admin` / `superadmin` users: turn it on (the backend has separate login
     doors for users and admins).
4. Pick a lead and use **Call** or **WhatsApp**.

## How calling and WhatsApp work

- **Call** uses `Intent.ACTION_DIAL`, so it opens the dialer with the number
  filled in. Android requires the final tap; this is also why the app asks for
  no phone permission at all.
- **WhatsApp** opens `https://wa.me/<number>?text=<pre-filled message>`. If
  WhatsApp is installed it opens the chat; otherwise a browser shows the link.
  WhatsApp has no supported way for a third-party app to silently send a
  message, so the final **Send** tap stays with you.
- Auto-logging is on by default and can be turned off in **Settings**.

## Security notes

- The app uses the server's normal session cookie. It is stored in the app's
  private `SharedPreferences` and is never uploaded anywhere except back to
  your configured server.
- Cleartext HTTP is allowed so a self-hosted LAN server works out of the box.
  Use HTTPS (and set `COOKIE_SECURE=true` on the server) for anything exposed
  to the internet.
- The app trusts user-installed CA certificates, which is useful for a
  self-signed certificate on a home server.
- Backup is disabled for the app so the session cookie is not included in
  device backups.

## Project layout

```
android/
  app/src/main/java/com/arthvex/leadgen/
    data/        OkHttp client, cookie persistence, API repository, prefs
    model/       kotlinx.serialization models matching the FastAPI responses
    ui/          Compose screens, theme, components, WhatsApp composer
    ui/utils/    call / WhatsApp / email intents, date helpers
    MainActivity.kt, LeadApp.kt
  app/src/main/AndroidManifest.xml
  app/build.gradle.kts
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not reach server" | Check the URL, that the server is running, and that `HOST=0.0.0.0` for LAN access. Test `http://<ip>:8000/health` in the phone's browser. |
| Sign-in says "Invalid username or password" | Same credentials as the web app. Admins must enable the **Administrator account** switch. |
| "Administrators sign in at /admin" | You used the user door with an admin account — enable the admin switch. |
| WhatsApp shows "No WhatsApp-capable number" | The lead has no mobile number or the server marked it as a landline. Use Call or fix the number in the web app. |
| Calls open the dialer but nothing is logged | Auto-logging is off in Settings, or the server rejected the interaction (check you are online). |

## API endpoints used

`POST /api/auth/login`, `POST /api/auth/admin/login`, `GET /api/auth/me`,
`POST /api/auth/logout`, `GET /api/leads`, `GET /api/leads/{id}`,
`PATCH /api/leads/{id}`, `POST /api/leads/{id}/interactions`,
`GET /api/templates`, `POST /api/leads/{id}/message`,
`GET /api/follow-ups/{today|overdue|upcoming|suggested}`, `GET /api/stats`,
`GET /health`.
