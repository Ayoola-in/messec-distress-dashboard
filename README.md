# MesSec: Distress Signal Dispatch

MesSec connects people in distress with a control room and registered police stations, hospitals, fire services and other responding bodies. A signal comes from an ESP32 GPS beacon or a phone's SOS button. A dispatcher assigns it to a station, the station responds, and the person sees the progress live. Everything is saved to a database.

```
 ESP32 + GPS ──┐
               ├─▶ Flask + database ──Socket.IO──▶ Dispatcher dashboard
 Phone SOS ────┘                                   Station console
                                                   Citizen's alert status
```

> MesSec doesn't replace emergency services. Keep telling users to call 112 if they're in immediate danger.

---

## 1. Who does what

| Role | How they join | What they can do |
|---|---|---|
| **Admin** | `create-admin` command | Approve, reject and suspend stations; manage users; read the audit log; everything a dispatcher can do |
| **Dispatcher** | Created by an admin | See all incidents; assign, reassign and close them; search history; export CSV |
| **Station staff** | Station applies at `/stations/apply`, or an admin creates them | See only their station's incidents; accept, decline, update, add notes, resolve |
| **Citizen** | Signs up at `/register` | Keep their details and emergency contacts, link beacons, send an SOS, track their alert, export or delete their data |

### How an incident moves

1. A signal arrives as **unassigned**.
2. A dispatcher clicks **Assign station** and picks from approved stations, sorted by distance, with the nearest suitable one marked **Recommended**.
3. The station **accepts** or **declines**. Declining needs a reason and puts the incident back in the queue.
4. The station moves it through **en route**, **on scene** and **resolved**. Only people resolve incidents.

**Safety net:** a critical signal left unassigned for 60 seconds goes automatically to the nearest suitable station. Medical goes to a hospital or ambulance service, fire goes to the fire service, and everything else goes to the police. Stations that declined are skipped. If a station hasn't accepted after 2 minutes, the dashboard flags it so a dispatcher can phone them or reassign.

---

## 2. Run it on your computer

```bash
git clone https://github.com/Ayoola-in/messec-distress-dashboard.git
cd messec-distress-dashboard
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

Create the database and your admin account:

```bash
flask --app messec:create_app db upgrade
flask --app messec:create_app create-admin
```

Start the server and open **http://localhost:5000**:

```bash
python app.py
```

Local data is stored in `instance/messec.db`.

### Try it with demo data

To explore without real stations or hardware:

```bash
flask --app messec:create_app seed-demo    # adds invented Lagos stations
```

Then start the server with simulated signals on:

```powershell
$env:MESSEC_SIMULATE = "1"; python app.py      # Windows PowerShell
```
```bash
MESSEC_SIMULATE=1 python app.py                # macOS / Linux
```

Simulated incidents are labelled, and the dashboard shows a demo banner. Don't use demo mode or demo stations on a real deployment.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 3. Settings

Copy `.env.example` to `.env`, or set these on your host.

| Variable | Default | Meaning |
|---|---|---|
| `MESSEC_ENV` | `development` | Set to `production` on a live site. Turns on secure cookies and refuses unsafe settings |
| `SECRET_KEY` | Random, saved locally | **Required in production.** Keeps logins secure |
| `DATABASE_URL` | Local SQLite file | **Use PostgreSQL in production** |
| `MESSEC_DEVICE_KEY` | *(empty)* | Shared key for beacons not linked to a citizen. Empty means only per-beacon keys work |
| `MESSEC_SIMULATE` | `0` | `1` turns on simulated signals and the demo banner |
| `MESSEC_AUTO_ASSIGN_SECONDS` | `60` | Wait before an unassigned critical signal is auto-assigned |
| `MESSEC_STATION_ACK_SECONDS` | `120` | Wait before an unaccepted assignment is flagged |
| `MESSEC_TRUST_PROXY` | On in production | Trust the host's proxy headers. Turn it off if the app isn't behind a proxy |
| `PORT` | `5000` | HTTP port |

---

## 4. Put it online

MesSec needs an **always-on server**, because live updates hold a connection open. Render and Railway both work, as does any VPS. Vercel and other serverless hosts won't work.

1. Create a **PostgreSQL** database on your host.
2. Create a web service from this repository. The included `Procfile` applies database migrations and starts the server with:
   ```
   gunicorn -w 1 --threads 100 wsgi:app
   ```
   Keep it at **one worker**. Live updates and the auto-assign check run inside that process.
3. Set `MESSEC_ENV=production`, `SECRET_KEY` and `DATABASE_URL`.
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"     # makes a SECRET_KEY
   ```
4. In your host's shell, run `flask --app messec:create_app create-admin`.
5. Log in, then approve or add your stations.

**Before you go live:**

- Serve the site over HTTPS. Browsers only share GPS with secure sites, so the SOS button needs it.
- Back up the database. It holds people's personal data and your incident records.
- Get advice on your duties under the **Nigeria Data Protection Act 2023**, and fill in the contact details on the privacy page (`templates/privacy.html`).
- Arrange a way to reach stations that aren't on the site. See section 8.

---

## 5. Build a beacon

### Parts

| Part | Notes |
|---|---|
| ESP32 DevKit V1 (or any ESP32 board) | Has built-in Wi-Fi |
| GPS module: u-blox NEO-6M / NEO-7M / NEO-M8N | Any module that outputs NMEA at 9600 baud works |
| GPS ceramic antenna | Usually ships with the module |
| Push button | The panic button |
| Jumper wires, breadboard, USB cable | |

### Wiring

| GPS module pin | ESP32 pin |
|---|---|
| VCC | 3V3 (or VIN/5V if your GPS board has a regulator, as most NEO-6M boards do) |
| GND | GND |
| TX | **GPIO16** (RX2) |
| RX | **GPIO17** (TX2) |

| Other | ESP32 pin |
|---|---|
| Push button leg 1 | **GPIO4** |
| Push button leg 2 | **GND** (the sketch enables the internal pull-up) |
| Status LED | GPIO2 (the on-board LED on most boards) |

> TX on the GPS goes to RX on the ESP32, and RX goes to TX. They cross over.

### Flash it

1. Install the **Arduino IDE**.
2. Go to *File → Preferences → Additional Boards Manager URLs* and add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`,
   then go to *Tools → Board → Boards Manager*, search **esp32** (by Espressif) and click Install.
3. From *Sketch → Include Library → Manage Libraries*, install **TinyGPSPlus** by Mikal Hart and **ArduinoJson** by Benoit Blanchon (version 7).
4. Log in to MesSec as a citizen, go to **My beacons**, choose **Link a beacon** and copy the key. It's only shown once.
5. Open `firmware/messec_esp32_gps/messec_esp32_gps.ino` and edit the block at the top:
   ```cpp
   const char* WIFI_SSID     = "YOUR_WIFI_NAME";
   const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
   const char* SERVER_URL    = "https://your-messec-site.example/api/signals/ingest";
   const char* DEVICE_KEY    = "the key you copied";
   const char* SIGNAL_TYPE   = "Security Breach";
   const char* SEVERITY      = "CRITICAL";         // CRITICAL | HIGH | MEDIUM | LOW
   const char* ROOT_CA       = "";                 // your site's CA certificate, for real use
   ```
6. Pick *Tools → Board → ESP32 Dev Module*, choose the COM port, and click **Upload**. If it stalls at `Connecting...`, hold the **BOOT** button.
7. Open *Tools → Serial Monitor* at **115200 baud**, and take the antenna outdoors or to a window. The first fix can take a few minutes. The LED blinks while searching and stays solid once it has a fix.
8. **Press the button.** The Serial Monitor shows `POST ... -> 201`, and the alert appears on the dashboard straight away.

**Testing against your own computer:** set `SERVER_URL` to `http://<your-PC-IP>:5000/api/signals/ingest` (find the IP with `ipconfig` or `ifconfig`). Both devices must be on the same Wi-Fi network, and your firewall must allow port 5000.

---

## 6. The device API

Any board that can make an HTTP request can send alerts:

```
POST /api/signals/ingest
Content-Type: application/json
X-API-Key: <per-beacon key, or MESSEC_DEVICE_KEY>
```

```json
{
  "lat": 6.524412,
  "lng": 3.379205,
  "type": "Security Breach",
  "severity": "CRITICAL",
  "signal_strength": 78,
  "frequency": 2437,
  "satellites": 7,
  "hdop": 0.9
}
```

| Field | Required | Description |
|---|---|---|
| `lat`, `lng` | **yes** | GPS coordinates in decimal degrees. `0,0` is rejected because it means no GPS fix |
| `severity` | no | `CRITICAL`, `HIGH`, `MEDIUM` or `LOW` (default `HIGH`) |
| `type` | no | Incident label (default `Distress Signal`). This also decides which type of station is suggested |
| `device_id`, `caller_id` | no | Only used with the shared key. Per-beacon keys use the owner's registered details |
| `signal_strength` | no | 0–100 |
| `frequency`, `satellites`, `hdop` | no | Extra telemetry |

| Response | Meaning |
|---|---|
| `201` | Accepted and sent to dispatchers |
| `400` | Body isn't JSON, or the `Content-Type` is wrong |
| `401` | Wrong, missing or revoked key |
| `422` | Missing or invalid coordinates |
| `429` | More than 60 requests a minute from this key |

**Test without hardware** (PowerShell):
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:5000/api/signals/ingest `
  -Headers @{ "X-API-Key" = "your-key" } -ContentType "application/json" `
  -Body '{"lat":6.5244,"lng":3.3792,"type":"Medical Emergency","severity":"CRITICAL"}'
```

---

## 7. Project structure

| Path | Purpose |
|---|---|
| `app.py` / `wsgi.py` | Start the server, locally and in production |
| `messec/models.py` | Database tables |
| `messec/services/incidents.py` | Incident rules: assignment, status changes, auto-assign |
| `messec/services/realtime.py` | Live updates, and who may receive them |
| `messec/services/serializers.py` | What each role sees about an incident |
| `messec/views/` | Pages and the JSON API |
| `messec/cli.py` | `create-admin` and `seed-demo` commands |
| `migrations/` | Database schema history |
| `templates/`, `static/` | Pages, styles and browser code |
| `firmware/` | ESP32 beacon sketch |
| `tests/` | Automated tests |

After changing `messec/models.py`, create a migration with `flask --app messec:create_app db migrate -m "what changed"` and apply it with `db upgrade`.

---

## 8. Known limits

- **No SMS or email yet.** Station staff only get alerts while the console is open, so a closed console means a missed alert until the 2-minute flag prompts a dispatcher to phone them. This should be the next feature.
- **Password resets go through an admin**, who generates a one-time link.
- **ETAs are rough estimates** from straight-line distance, not road routes or traffic.
- **One server process only.** To run several, you'd need Redis for live updates and rate limits.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `No data from GPS - check TX/RX wiring` | Swap the TX/RX wires, check VCC/GND, confirm 9600 baud |
| `No valid GPS fix yet` | Move outdoors, wait a few minutes, check the antenna is plugged in |
| `POST ... -> -1` | The ESP32 can't reach the server: wrong address, different Wi-Fi, or a firewall on port 5000 |
| `POST ... -> 401` | `DEVICE_KEY` is wrong or the beacon was removed. Link it again to get a new key |
| ESP32 won't connect to Wi-Fi | The ESP32 only supports **2.4 GHz** Wi-Fi |
| SOS can't get the location | The site must use HTTPS, or the person can tap their position on the map instead |
| Can't log in as a new station | An admin has to approve it first, under **Stations** |
| Forgot admin password | Run `create-admin` to make another admin, then send yourself a reset link from **Users** |
| Server won't start in production | Check the error: `SECRET_KEY` must be set and `MESSEC_DEVICE_KEY` can't be `change-me` |
