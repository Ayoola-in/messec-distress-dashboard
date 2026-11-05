/*
 * MesSec — ESP32 + GPS distress beacon
 * -------------------------------------
 * Reads the GPS module (NEO-6M / NEO-7M / NEO-M8N or any NMEA GPS) and, when the
 * panic button is pressed, POSTs the live coordinates to the MesSec dashboard:
 *
 *     POST https://<YOUR_SITE>/api/signals/ingest   (JSON)
 *
 * Getting a key: log in to MesSec as a citizen, open "My beacons", choose
 * "Link a beacon" and paste the key below. Alerts then arrive with your details.
 *
 * Wiring (ESP32 DevKit V1):
 *   GPS VCC  -> 3V3 (or VIN/5V if your module board has a regulator)
 *   GPS GND  -> GND
 *   GPS TX   -> GPIO16 (ESP32 RX2)
 *   GPS RX   -> GPIO17 (ESP32 TX2)
 *   Button   -> GPIO4 and GND   (uses internal pull-up, pressed = LOW)
 *   LED      -> GPIO2 (on-board LED on most DevKits)
 *
 * Libraries (Arduino IDE -> Library Manager):
 *   - TinyGPSPlus  by Mikal Hart
 *   - ArduinoJson  by Benoit Blanchon (v7)
 *   (WiFi.h and HTTPClient.h come with the ESP32 board package)
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>

// ───────────── EDIT THESE ─────────────
const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
// Your live site, or http://<your-PC-IP>:5000/api/signals/ingest for local testing
const char* SERVER_URL    = "https://your-messec-site.example/api/signals/ingest";
// Key from "My beacons" on the website. A server's MESSEC_DEVICE_KEY also works, but
// those alerts show as "unlinked", with no person attached.
const char* DEVICE_KEY    = "PASTE_YOUR_DEVICE_KEY_HERE";
const char* DEVICE_ID     = "ESP32-01";           // only used with the shared key
const char* CALLER_ID     = "0803 000 0000";      // only used with the shared key
const char* SIGNAL_TYPE   = "Security Breach";    // e.g. Medical Emergency, Fire Outbreak ...
const char* SEVERITY      = "CRITICAL";           // CRITICAL | HIGH | MEDIUM | LOW
// PEM certificate of the authority behind your HTTPS site. Without it the beacon still
// encrypts its traffic but can't prove it's talking to your server, so someone on the
// same network could steal the key. Fill this in before real use.
const char* ROOT_CA       = "";
// ──────────────────────────────────────

#define GPS_RX_PIN   16     // ESP32 pin that receives GPS TX
#define GPS_TX_PIN   17     // ESP32 pin that sends to GPS RX
#define GPS_BAUD     9600   // NEO-6M default
#define BUTTON_PIN   4
#define LED_PIN      2

const unsigned long COOLDOWN_MS = 10000;   // min time between two alerts

TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
unsigned long lastSent = 0;

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? " connected, IP " + WiFi.localIP().toString()
                                               : " FAILED (will retry)");
}

// Map WiFi RSSI (-100..-40 dBm) to 0..100 for the dashboard strength bars
int rssiToPercent(int rssi) {
  if (rssi <= -100) return 0;
  if (rssi >= -40) return 100;
  return (rssi + 100) * 100 / 60;
}

bool sendDistress() {
  if (!gps.location.isValid() || gps.location.age() > 5000) {
    Serial.println("No valid GPS fix yet - move the antenna outdoors / near a window.");
    return false;
  }
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return false;

  JsonDocument doc;
  doc["device_id"]       = DEVICE_ID;
  doc["caller_id"]       = CALLER_ID;
  doc["type"]            = SIGNAL_TYPE;
  doc["severity"]        = SEVERITY;
  doc["lat"]             = gps.location.lat();
  doc["lng"]             = gps.location.lng();
  doc["satellites"]      = gps.satellites.isValid() ? (int)gps.satellites.value() : 0;
  doc["hdop"]            = gps.hdop.isValid() ? gps.hdop.hdop() : 0;
  doc["signal_strength"] = rssiToPercent(WiFi.RSSI());
  doc["frequency"]       = 2400 + 5 * WiFi.channel();   // WiFi channel centre freq (MHz)

  String body;
  serializeJson(doc, body);

  HTTPClient http;
  WiFiClientSecure secureClient;
  if (strncmp(SERVER_URL, "https://", 8) == 0) {
    if (strlen(ROOT_CA) > 0) {
      secureClient.setCACert(ROOT_CA);
    } else {
      Serial.println("WARNING: ROOT_CA is empty, so the server's identity isn't being checked.");
      secureClient.setInsecure();
    }
    http.begin(secureClient, SERVER_URL);
  } else {
    http.begin(SERVER_URL);
  }
  http.addHeader("Content-Type", "application/json");
  if (strlen(DEVICE_KEY) > 0) http.addHeader("X-API-Key", DEVICE_KEY);
  int code = http.POST(body);
  Serial.printf("POST %s -> %d\n%s\n", SERVER_URL, code, http.getString().c_str());
  http.end();
  return code == 201;
}

void setup() {
  Serial.begin(115200);
  gpsSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  connectWiFi();
  Serial.println("MesSec beacon ready. Press the button to send a distress signal.");
}

void loop() {
  // Feed every NMEA character from the GPS into the parser
  while (gpsSerial.available()) gps.encode(gpsSerial.read());

  // Blink LED while searching for satellites, solid when fixed
  digitalWrite(LED_PIN, gps.location.isValid() ? HIGH : (millis() / 500) % 2);

  static unsigned long lastLog = 0;
  if (millis() - lastLog > 5000) {
    lastLog = millis();
    if (gps.charsProcessed() < 10)
      Serial.println("No data from GPS - check TX/RX wiring and baud rate.");
    else
      Serial.printf("GPS fix:%s  lat:%.6f  lng:%.6f  sats:%d\n",
                    gps.location.isValid() ? "YES" : "no",
                    gps.location.lat(), gps.location.lng(), (int)gps.satellites.value());
  }

  if (digitalRead(BUTTON_PIN) == LOW && millis() - lastSent > COOLDOWN_MS) {
    delay(50);                                  // debounce
    if (digitalRead(BUTTON_PIN) == LOW) {
      Serial.println("PANIC BUTTON PRESSED");
      if (sendDistress()) lastSent = millis();
    }
  }
}
