# MesSec: Distress Signal Dashboard

MesSec is a real-time emergency dispatch dashboard. A microcontroller (ESP32) with a GPS module sends a distress signal with its live coordinates. The Flask server assigns the nearest security post, and every open dashboard shows the incident on a map right away over Socket.IO.

```
 ┌──────────────┐  NMEA (UART)  ┌──────────┐  HTTP POST JSON   ┌──────────────┐  Socket.IO  ┌───────────┐
 │  GPS module  │ ────────────▶ │  ESP32   │ ────────────────▶ │  Flask app   │ ──────────▶ │  Browser  │
 │  (NEO-6M)    │               │ + button │  /api/signals/    │  (app.py)    │ new_signal  │ dashboard │
 └──────────────┘               └──────────┘     ingest        └──────────────┘             └───────────┘
```

---

## 1. Project structure

| Path | Purpose |
|---|---|
| `app.py` | Flask + Socket.IO backend (REST API, nearest-unit assignment, live events) |
| `templates/index.html` | Dashboard page |
| `static/js/dashboard.js` | Map, incident queue and live updates (Leaflet + Socket.IO) |
| `static/css/style.css` | Dashboard styling |
| `firmware/messec_esp32_gps/messec_esp32_gps.ino` | Arduino sketch for the ESP32 + GPS beacon |
| `requirements.txt` | Python dependencies |

---

## 2. Run the website (server)

```bash
git clone https://github.com/Ayoola-in/messec-distress-dashboard.git
cd messec-distress-dashboard
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

By default the server makes up **simulated** signals for demos. If you only want the **real readings from the microcontroller**, turn simulation off and set a device key:

**Windows (PowerShell)**
```powershell
$env:MESSEC_SIMULATE = "0"
$env:MESSEC_DEVICE_KEY = "change-me"
python app.py
```

**macOS / Linux**
```bash
MESSEC_SIMULATE=0 MESSEC_DEVICE_KEY=change-me python app.py
```

| Variable | Default | Meaning |
|---|---|---|
| `MESSEC_SIMULATE` | `1` | `0` turns off the fake signal generator so the dashboard shows device data only |
| `MESSEC_DEVICE_KEY` | *(empty)* | When set, every device request must send the same value in the `X-API-Key` header |
| `PORT` | `5000` | HTTP port |

Open **http://localhost:5000** to see the dashboard.

### Find the server's IP address
The ESP32 has to reach the computer running `app.py`, so both must be on the **same Wi-Fi network**.

- Windows: `ipconfig`, then use the **IPv4 Address** of your Wi-Fi adapter (e.g. `192.168.1.100`)
- macOS/Linux: `ifconfig` or `ip a`

The device URL is then `http://192.168.1.100:5000/api/signals/ingest`.

> **Windows firewall:** the first time you run `python app.py`, allow Python on **Private networks**. If you missed the prompt, open port 5000 yourself:
> `netsh advfirewall firewall add rule name="MesSec 5000" dir=in action=allow protocol=TCP localport=5000` (run as Administrator).

---

## 3. Hardware you need

| Part | Notes |
|---|---|
| ESP32 DevKit V1 (or any ESP32 board) | Has built-in Wi-Fi |
| GPS module: u-blox NEO-6M / NEO-7M / NEO-M8N | Any module that outputs NMEA at 9600 baud works |
| GPS ceramic antenna | Usually ships with the module |
| Push button | The panic / distress button |
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

```
   NEO-6M                 ESP32 DevKit
  ┌───────┐              ┌────────────┐
  │  VCC  │──────────────│ 3V3        │
  │  GND  │──────────────│ GND ───────┼──┐
  │  TX   │──────────────│ GPIO16 RX2 │  │   ┌────────┐
  │  RX   │──────────────│ GPIO17 TX2 │  └───┤ BUTTON ├──┐
  └───────┘              │ GPIO4 ─────┼──────┴────────┘  │
                         └────────────┘                  │
                               (button between GPIO4 and GND)
```

---

## 4. Flash the microcontroller

1. Install the **Arduino IDE**.
2. Add the ESP32 boards. Go to *File → Preferences → Additional Boards Manager URLs* and add
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`,
   then go to *Tools → Board → Boards Manager*, search **esp32** (by Espressif) and click Install.
3. Install the libraries from *Sketch → Include Library → Manage Libraries*:
   - **TinyGPSPlus** by Mikal Hart
   - **ArduinoJson** by Benoit Blanchon (version 7)
4. Open `firmware/messec_esp32_gps/messec_esp32_gps.ino` and edit the block at the top:
   ```cpp
   const char* WIFI_SSID     = "YOUR_WIFI_NAME";
   const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
   const char* SERVER_URL    = "http://192.168.1.100:5000/api/signals/ingest"; // your PC's IP
   const char* DEVICE_KEY    = "change-me";        // same as MESSEC_DEVICE_KEY
   const char* DEVICE_ID     = "ESP32-01";
   const char* CALLER_ID     = "0803 000 0000";
   const char* SIGNAL_TYPE   = "Security Breach";
   const char* SEVERITY      = "CRITICAL";         // CRITICAL | HIGH | MEDIUM | LOW
   ```
5. Pick *Tools → Board → ESP32 Dev Module*, choose the right COM port, and click **Upload**.
   (If the upload stalls at `Connecting...`, hold the **BOOT** button on the ESP32.)
6. Open *Tools → Serial Monitor* at **115200 baud**.

---

## 5. Send a real reading to the website

1. Start the server (section 2) and open the dashboard in a browser.
2. Take the GPS antenna **outdoors or to a window**. The first fix can take 30 seconds to a few minutes.
   The Serial Monitor prints lines like:
   ```
   GPS fix:YES  lat:6.524412  lng:3.379205  sats:7
   ```
   The on-board LED blinks while it searches and stays solid once it has a fix.
3. **Press the button.** The Serial Monitor should show:
   ```
   PANIC BUTTON PRESSED
   POST http://192.168.1.100:5000/api/signals/ingest -> 201
   ```
4. The dashboard immediately shows a new incident **at the GPS module's real location**, along with the nearest security post assigned, distance, ETA, a route line and an alert. No page refresh is needed.

---

## 6. The device API (for other microcontrollers)

You can use any board that can make an HTTP request (ESP8266, Arduino + SIM800L/ESP-01, Raspberry Pi Pico W, Raspberry Pi, and so on) as long as it sends this request:

```
POST /api/signals/ingest
Content-Type: application/json
X-API-Key: <MESSEC_DEVICE_KEY>        (only if the server has a key set)
```

```json
{
  "lat": 6.524412,
  "lng": 3.379205,
  "device_id": "ESP32-01",
  "caller_id": "0803 000 0000",
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
| `lat`, `lng` | **yes** | GPS coordinates in decimal degrees. `0,0` is rejected because it means the GPS has no fix |
| `severity` | no | `CRITICAL`, `HIGH`, `MEDIUM` or `LOW` (default `HIGH`) |
| `type` | no | Incident label shown on the dashboard (default `Distress Signal`) |
| `device_id`, `caller_id` | no | Identify who or what sent the alert |
| `signal_strength` | no | 0–100, drives the strength bars |
| `frequency`, `satellites`, `hdop` | no | Extra telemetry |

| Response | Meaning |
|---|---|
| `201` | Accepted. The signal is broadcast to every dashboard |
| `400` | Body is not JSON / wrong `Content-Type` |
| `401` | Wrong or missing `X-API-Key` |
| `422` | Missing or invalid coordinates (no GPS fix) |

### Test without hardware
```bash
curl -X POST http://localhost:5000/api/signals/ingest \
  -H "Content-Type: application/json" -H "X-API-Key: change-me" \
  -d '{"lat":6.5244,"lng":3.3792,"device_id":"TEST","type":"Medical Emergency","severity":"CRITICAL"}'
```
PowerShell:
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:5000/api/signals/ingest `
  -Headers @{ "X-API-Key" = "change-me" } -ContentType "application/json" `
  -Body '{"lat":6.5244,"lng":3.3792,"device_id":"TEST","severity":"CRITICAL"}'
```

---

## 7. Use your own location's security posts

The response posts are defined in `SECURITY_POINTS` at the top of `app.py`, and they are set to Lagos by default. If you deploy somewhere else, replace their `lat`/`lng` with real posts near you. Otherwise the "nearest unit", distance and ETA will be calculated against Lagos.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `No data from GPS - check TX/RX wiring` | Swap the TX/RX wires, check VCC/GND, confirm 9600 baud |
| `No valid GPS fix yet` | Move outdoors, wait a few minutes, check the antenna is plugged in |
| `POST ... -> -1` | ESP32 can't reach the server: wrong IP, different Wi-Fi, or firewall blocking port 5000 |
| `POST ... -> 401` | `DEVICE_KEY` in the sketch doesn't match `MESSEC_DEVICE_KEY` |
| Fake signals still appear | Start the server with `MESSEC_SIMULATE=0` |
| ESP32 won't connect to Wi-Fi | ESP32 only supports **2.4 GHz** Wi-Fi, not 5 GHz |
| Works locally but not over the internet | Deploy `app.py` to a public host (Render, Railway, a VPS) and point `SERVER_URL` at that address. Use `https://` with `WiFiClientSecure` for production |
