# MesSec real platform: design

Date: 2026-11-05. Status: approved by the project owner in chat.

## Goal

Turn the MesSec demo into a real website: data survives restarts, police stations and other
bodies can register, and a dispatcher can assign an incident to a chosen registered station.
ESP32 beacons keep working.

## Architecture

Keep Flask and Socket.IO. Add SQLAlchemy with Alembic migrations (PostgreSQL in production,
SQLite for local use), Flask-Login, CSRF protection and rate limiting. Split `app.py` into a
`messec/` package. Socket.IO needs an always-on server, so production runs one Gunicorn worker
with threads on a host such as Render or Railway.

## Roles

| Role | Can do |
|---|---|
| Admin | Approve, reject, suspend and edit stations; manage users; audit log; everything a dispatcher can do |
| Dispatcher | See all incidents; assign, reassign and close them; history and CSV export |
| Station staff | See only their own station's incidents; accept, decline, update, add notes, resolve |
| Citizen | Profile, emergency contacts, beacons, SOS, own alerts, data export and account deletion |

## Data model

- `User`: email, password hash, name, phone, role, optional station, enabled flag, consent time.
- `CitizenProfile`, `EmergencyContact`: citizen details shown to responders.
- `Station`: name, type (police, hospital, fire, ambulance, other), status (pending, approved,
  rejected, suspended), zone, address, location, contacts.
- `Device`: beacon owned by a citizen, with its own key stored only as a hash. Can be revoked.
- `Signal`: the incident. Uses an optimistic version column so concurrent assignments cannot
  both win.
- `Assignment`: one row per station tasked, with method (manual or auto), distance and estimated
  ETA. Keeps the full history.
- `IncidentEvent`: timeline and notes. `AuditLog`: administrative actions.

## Incident flow

Statuses: `unassigned`, `assigned`, `accepted`, `en_route`, `on_scene`, `resolved`.

1. A beacon or citizen SOS creates an `unassigned` signal.
2. A dispatcher clicks Assign and picks from approved stations, sorted by distance, with the
   recommended one highlighted. Recommended means the nearest suitable, non-busy station.
3. The station accepts or declines. Declining needs a reason and returns the incident to the
   queue. Status moves forward only; a station must accept before it can progress.
4. Only people resolve incidents. The old timer is removed. Dispatchers may close an incident at
   any stage (for example a false alarm) with a required note.
5. Reassigning moves the incident to the new station and removes the old station's access.
6. Safety net: a `CRITICAL` signal unassigned for `MESSEC_AUTO_ASSIGN_SECONDS` (default 60) goes
   to the nearest suitable station. A background sweep reads the database, so the check survives
   restarts.
   - Medical goes to hospital or ambulance. Fire goes to fire service. Everything else goes to
     police.
   - Stations that declined are skipped. If no suitable station exists, any approved station is
     used. If none exists, dispatchers are alerted once and the sweep keeps retrying.
7. A station that has not accepted within `MESSEC_STATION_ACK_SECONDS` (default 120) is flagged
   on the dispatcher dashboard. It is not reassigned automatically.

Suspending a station returns its active incidents to the queue.

## Registration

- Public application form creates a `pending` station and its first staff login. The login does
  not work until an admin approves the station.
- Admins can also add stations directly and add more staff.
- Citizens self-register with an explicit consent checkbox.
- First admin: `flask create-admin`.
- The old Lagos posts are invented, so they load only through `flask seed-demo`.

## Beacons

`POST /api/signals/ingest` accepts a per-device key (linked to the owner) or the legacy shared
`MESSEC_DEVICE_KEY` (shown as unlinked). A key is now always required. Firmware gains HTTPS
support.

## Live updates and privacy

- Sockets require login and join rooms: `staff`, `station:<id>`, `citizen:<user id>`.
- Citizen personal details go only to staff and the station currently responsible.
- Citizens receive a reduced view of their own incidents.
- Disabled users and suspended stations are disconnected immediately.
- Nigeria Data Protection Act 2023: consent at sign-up, JSON data export, and account deletion
  that anonymises kept incident records. Deletion is refused during an active incident.

## Security

Hashed passwords; sessions invalidated on password change; CSRF on all browser writes; rate
limits on login, registration, SOS and ingest; secret key required in production; no open CORS;
security headers and CSP; open-redirect protection; CSV formula escaping.

## Extras in scope

Browser SOS using phone GPS with a map fallback; incident timeline and notes; searchable history
with CSV export; ETAs labelled as estimates; pytest suite.

## Simulation

Off by default. Simulated signals are flagged and labelled in the UI, the dashboard shows a demo
banner, and the generator stops at 20 open simulated signals.

## Out of scope

SMS and email alerts (next phase); self-service password reset (an admin issues a link instead);
native mobile apps.

## Testing

pytest covers permissions, registration and approval, assignment and races, escalation, status
rules, beacon ingest, socket room isolation and data deletion. The UI is checked in a real browser.
