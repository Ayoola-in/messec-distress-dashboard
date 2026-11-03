"""
MesSec — Distress Signal Dashboard Backend
Flask + Flask-SocketIO server
"""

import math
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# ── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "messec_secret_2024"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Runtime configuration (environment variables) ───────────────────────────
# MESSEC_SIMULATE=0      -> turn OFF the fake signal generator (use real hardware only)
# MESSEC_DEVICE_KEY=...  -> shared secret the microcontroller must send in the
#                           "X-API-Key" header when posting to /api/signals/ingest
SIMULATE = os.environ.get("MESSEC_SIMULATE", "1").strip().lower() not in ("0", "false", "no", "off")
DEVICE_API_KEY = os.environ.get("MESSEC_DEVICE_KEY", "").strip()
PORT = int(os.environ.get("PORT", "5000"))

# ── Security Points (Pre-defined posts) ─────────────────────────────────────
SECURITY_POINTS = [
    {"id": "SP-01", "name": "Eko Command",        "lat":  6.5244, "lng":  3.3792, "status": "available", "zone": "Lagos Island"},
    {"id": "SP-02", "name": "Adeola Odeku Post",  "lat":  6.4531, "lng":  3.3958, "status": "available", "zone": "Victoria Island"},
    {"id": "SP-03", "name": "Alausa Base",        "lat":  6.5855, "lng":  3.3210, "status": "available", "zone": "Ikeja"},
    {"id": "SP-04", "name": "Gberefu Outpost",    "lat":  6.4698, "lng":  3.2378, "status": "available", "zone": "Badagry"},
    {"id": "SP-05", "name": "Oko-Oba Station",    "lat":  6.6018, "lng":  3.3515, "status": "available", "zone": "Agege"},
    {"id": "SP-06", "name": "Ojuelegba Unit",     "lat":  6.5355, "lng":  3.4125, "status": "available", "zone": "Surulere"},
    {"id": "SP-07", "name": "Ikate Patrol",       "lat":  6.4481, "lng":  3.4723, "status": "available", "zone": "Lekki"},
    {"id": "SP-08", "name": "Igando Guard",       "lat":  6.5721, "lng":  3.2960, "status": "available", "zone": "Alimosho"},
]

# ── In-memory store ──────────────────────────────────────────────────────────
signals = []
assignments = []
signal_counter = {"count": 0}

SIGNAL_TYPES = [
    "Medical Emergency",
    "Fire Outbreak",
    "Security Breach",
    "Vehicle Accident",
    "Armed Robbery",
    "Kidnapping Alert",
    "Flash Flooding",
    "Missing Person",
]

# Common Nigerian mobile prefixes (MTN, Glo, Airtel, 9mobile)
NG_MOBILE_PREFIXES = [
    "0803", "0806", "0813", "0816", "0703", "0706", "0903", "0906",  # MTN
    "0805", "0807", "0815", "0705", "0905",                          # Glo
    "0802", "0808", "0812", "0701", "0708", "0902", "0907",          # Airtel
    "0809", "0817", "0818", "0909", "0908",                          # 9mobile
]

SEVERITY_LEVELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEVERITY_WEIGHTS = [0.15, 0.30, 0.35, 0.20]

LAT_MIN, LAT_MAX = 6.40, 6.65
LNG_MIN, LNG_MAX = 3.20, 3.55

# ── Helper Functions ─────────────────────────────────────────────────────────

def haversine(lat1, lng1, lat2, lng2):
    """Return great-circle distance in kilometres between two GPS points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_security_point(lat, lng):
    """Return the nearest available security point to the given GPS coords."""
    available = [sp for sp in SECURITY_POINTS if sp["status"] == "available"]
    if not available:
        available = SECURITY_POINTS[:]
    return min(available, key=lambda sp: haversine(lat, lng, sp["lat"], sp["lng"]), default=None)


def generate_signal():
    """Synthesise a random distress signal."""
    signal_counter["count"] += 1
    sig_id = f"SIG-{signal_counter['count']:04d}"
    lat = round(random.uniform(LAT_MIN, LAT_MAX), 6)
    lng = round(random.uniform(LNG_MIN, LNG_MAX), 6)
    severity = random.choices(SEVERITY_LEVELS, weights=SEVERITY_WEIGHTS, k=1)[0]
    return {
        "id": sig_id,
        "type": random.choice(SIGNAL_TYPES),
        "severity": severity,
        "lat": lat,
        "lng": lng,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "frequency": round(random.uniform(88.0, 108.0), 2),
        "signal_strength": random.randint(40, 99),
        "caller_id": f"{random.choice(NG_MOBILE_PREFIXES)} {random.randint(0, 999):03d} {random.randint(0, 9999):04d}",
    }


def assign_signal(signal):
    """Find nearest security point and create an assignment record."""
    nearest = find_nearest_security_point(signal["lat"], signal["lng"])
    distance = haversine(signal["lat"], signal["lng"], nearest["lat"], nearest["lng"]) if nearest else None
    eta_minutes = round(distance * 2.5) if distance else None

    assignment = {
        "id": str(uuid.uuid4()),
        "signal_id": signal["id"],
        "security_point": nearest.copy() if nearest else None,
        "distance_km": round(distance, 2) if distance else None,
        "eta_minutes": eta_minutes,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "status": "responding",
    }

    if nearest:
        for sp in SECURITY_POINTS:
            if sp["id"] == nearest["id"]:
                sp["status"] = "responding"
                break

    return assignment


def dispatch_signal(sig):
    """Store a signal, assign the nearest unit, broadcast it and schedule resolution."""
    signals.append(sig)
    asgn = assign_signal(sig)
    assignments.append(asgn)
    sig["status"] = "assigned"

    socketio.emit("new_signal", {"signal": sig, "assignment": asgn})
    socketio.emit("security_points_update", {"points": SECURITY_POINTS})

    resolve_after = (asgn["eta_minutes"] or 5) * 60 + random.randint(30, 120)
    threading.Thread(
        target=resolve_signal,
        args=(sig["id"], asgn["id"], resolve_after),
        daemon=True,
    ).start()
    return sig, asgn


def build_device_signal(payload):
    """Validate a JSON payload sent by a microcontroller and turn it into a signal.

    Returns (signal, None) on success or (None, error_message) on failure.
    """
    try:
        lat = float(payload["lat"])
        lng = float(payload["lng"])
    except (KeyError, TypeError, ValueError):
        return None, "'lat' and 'lng' are required numeric fields"
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0) or (lat == 0.0 and lng == 0.0):
        return None, "GPS coordinates are out of range or the GPS has no fix yet"

    severity = str(payload.get("severity", "HIGH")).upper()
    if severity not in SEVERITY_LEVELS:
        severity = "HIGH"

    def num(key, default, cast=float):
        try:
            return cast(payload.get(key, default))
        except (TypeError, ValueError):
            return default

    signal_counter["count"] += 1
    device_id = str(payload.get("device_id", "DEVICE"))[:40]
    return {
        "id": f"SIG-{signal_counter['count']:04d}",
        "type": str(payload.get("type", "Distress Signal"))[:60],
        "severity": severity,
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "frequency": round(num("frequency", 0.0), 2),
        "signal_strength": max(0, min(100, num("signal_strength", 0, int))),
        "caller_id": str(payload.get("caller_id", device_id))[:40],
        "device_id": device_id,
        "satellites": num("satellites", None, int),
        "hdop": num("hdop", None),
        "source": "device",
    }, None


# ── Background Signal Generator ──────────────────────────────────────────────

def signal_generator_thread():
    """Emit a new distress signal every 8-18 seconds."""
    while True:
        interval = random.uniform(8, 18)
        time.sleep(interval)

        dispatch_signal(generate_signal())


def resolve_signal(signal_id, assignment_id, delay_seconds):
    """Mark a signal as resolved and free the security point."""
    time.sleep(delay_seconds)

    sp_id = None
    for s in signals:
        if s["id"] == signal_id:
            s["status"] = "resolved"
            s["resolved_at"] = datetime.now(timezone.utc).isoformat()
            break

    for a in assignments:
        if a["id"] == assignment_id:
            a["status"] = "resolved"
            sp_id = a["security_point"]["id"] if a["security_point"] else None
            break

    if sp_id:
        for sp in SECURITY_POINTS:
            if sp["id"] == sp_id:
                sp["status"] = "available"
                break

    socketio.emit("signal_resolved", {"signal_id": signal_id, "assignment_id": assignment_id})
    socketio.emit("security_points_update", {"points": SECURITY_POINTS})


# ── REST Endpoints ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def get_state():
    stats = {
        "total_signals": len(signals),
        "pending": sum(1 for s in signals if s["status"] == "pending"),
        "assigned": sum(1 for s in signals if s["status"] == "assigned"),
        "resolved": sum(1 for s in signals if s["status"] == "resolved"),
        "available_units": sum(1 for sp in SECURITY_POINTS if sp["status"] == "available"),
        "responding_units": sum(1 for sp in SECURITY_POINTS if sp["status"] == "responding"),
    }
    return jsonify({
        "signals": signals[-50:],
        "assignments": assignments[-50:],
        "security_points": SECURITY_POINTS,
        "stats": stats,
    })


@app.route("/api/signals/manual", methods=["POST"])
def manual_signal():
    sig, asgn = dispatch_signal(generate_signal())
    return jsonify({"signal": sig, "assignment": asgn})


@app.route("/api/signals/ingest", methods=["POST"])
def ingest_device_signal():
    """Endpoint the microcontroller (ESP32 + GPS) posts real distress readings to."""
    if DEVICE_API_KEY and request.headers.get("X-API-Key", "") != DEVICE_API_KEY:
        return jsonify({"error": "invalid or missing X-API-Key"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "body must be JSON with Content-Type: application/json"}), 400

    sig, err = build_device_signal(payload)
    if err:
        return jsonify({"error": err}), 422

    sig, asgn = dispatch_signal(sig)
    return jsonify({"ok": True, "signal": sig, "assignment": asgn}), 201


# ── SocketIO Events ──────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    emit("connected", {"message": "Connected to MesSec server"})


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if SIMULATE:
        for _ in range(3):
            sig = generate_signal()
            signals.append(sig)
            asgn = assign_signal(sig)
            assignments.append(asgn)
            sig["status"] = "assigned"

        gen_thread = threading.Thread(target=signal_generator_thread, daemon=True)
        gen_thread.start()
        print("[MesSec] Simulation ON  - fake signals will be generated (set MESSEC_SIMULATE=0 to disable)")
    else:
        print("[MesSec] Simulation OFF - waiting for device readings on POST /api/signals/ingest")
    if DEVICE_API_KEY:
        print("[MesSec] Device API key required (X-API-Key header)")

    socketio.run(app, host="0.0.0.0", port=PORT, debug=False, allow_unsafe_werkzeug=True)
