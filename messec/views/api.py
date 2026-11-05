"""JSON API used by the dashboards, plus the beacon ingest endpoint."""

import hmac

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from ..auth import can_view_incident, roles_required
from ..extensions import csrf, db, limiter
from ..models import (
    ADMIN, CITIZEN, DISPATCHER, Device, OPEN_STATUSES, RESOLVED, SEVERITIES, SOS_TYPES, STATION, Signal, Station,
    utcnow,
)
from ..services import accounts, incidents, realtime
from ..services.background import simulated_signal_fields
from ..services.incidents import IncidentError
from ..services.serializers import citizen_record, responder_record
from ..validation import clean_text, parse_coordinates
from .incidents import signal_from_public_id

bp = Blueprint("api", __name__, url_prefix="/api")

RECENT_RESOLVED = 100


@bp.errorhandler(IncidentError)
def incident_error(error):
    return jsonify(error=error.message), error.status


def _json_body():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _signal_or_error(public_id):
    """Fetch a signal the current user may see. Hides existence from those who may not."""
    signal = signal_from_public_id(public_id)
    if signal is None or not can_view_incident(current_user, signal):
        raise IncidentError("Incident not found.", 404)
    return signal


def _records_payload(query):
    return [responder_record(s) for s in query]


# ── Dispatchers ──────────────────────────────────────────────────────────────

@bp.route("/state")
@roles_required(ADMIN, DISPATCHER)
def state():
    open_signals = Signal.query.filter(Signal.status.in_(OPEN_STATUSES)).order_by(Signal.id).all()
    resolved = Signal.query.filter_by(status=RESOLVED).order_by(Signal.id.desc()).limit(RECENT_RESOLVED).all()
    return jsonify(
        records=_records_payload(sorted(resolved + open_signals, key=lambda s: s.id)),
        stations=realtime.stations_json(),
        stats=realtime.stats_json(),
        ack_seconds=current_app.config["MESSEC_STATION_ACK_SECONDS"],
    )


@bp.route("/signals/<public_id>/candidates")
@roles_required(ADMIN, DISPATCHER)
def signal_candidates(public_id):
    return jsonify(stations=incidents.candidates(_signal_or_error(public_id)))


@bp.route("/signals/<public_id>/assign", methods=["POST"])
@roles_required(ADMIN, DISPATCHER)
def assign_signal(public_id):
    signal = _signal_or_error(public_id)
    station_id = _json_body().get("station_id")
    station = db.session.get(Station, station_id) if isinstance(station_id, int) else None
    if station is None:
        raise IncidentError("Choose a registered station.", 422)
    assignment = incidents.assign(signal, station, actor=current_user._get_current_object())
    return jsonify(responder_record(assignment.signal))


@bp.route("/signals/<public_id>/close", methods=["POST"])
@roles_required(ADMIN, DISPATCHER)
def close_signal(public_id):
    signal = _signal_or_error(public_id)
    incidents.close(signal, current_user._get_current_object(), clean_text(_json_body().get("note"), 500))
    return jsonify(responder_record(signal))


@bp.route("/signals/simulate", methods=["POST"])
@roles_required(ADMIN, DISPATCHER)
def simulate_signal():
    if not current_app.config["MESSEC_SIMULATE"]:
        raise IncidentError("Simulated signals are turned off on this server.", 403)
    return jsonify(responder_record(incidents.create_signal(**simulated_signal_fields()))), 201


# ── Stations ─────────────────────────────────────────────────────────────────

@bp.route("/station/incidents")
@roles_required(STATION)
def station_incidents():
    station_id = current_user.station_id
    held = [s for s in Signal.query.filter(Signal.current_station_id == station_id).order_by(Signal.id.desc()).limit(200)
            if can_view_incident(current_user, s)]
    return jsonify(records=_records_payload(held), station=realtime.station_brief(current_user.station))


@bp.route("/signals/<public_id>/status", methods=["POST"])
@roles_required(STATION)
def update_status(public_id):
    signal = _signal_or_error(public_id)
    body = _json_body()
    incidents.advance(signal, current_user._get_current_object(), body.get("status"),
                      clean_text(body.get("note"), 500) or None)
    return jsonify(responder_record(signal))


@bp.route("/signals/<public_id>/decline", methods=["POST"])
@roles_required(STATION)
def decline_signal(public_id):
    signal = _signal_or_error(public_id)
    incidents.decline(signal, current_user._get_current_object(), clean_text(_json_body().get("reason")))
    return jsonify(ok=True)


# ── Shared ───────────────────────────────────────────────────────────────────

@bp.route("/signals/<public_id>/notes", methods=["POST"])
@roles_required(ADMIN, DISPATCHER, STATION)
def add_note(public_id):
    signal = _signal_or_error(public_id)
    incidents.add_note(signal, current_user._get_current_object(), clean_text(_json_body().get("message"), 1000))
    return jsonify(ok=True), 201


# ── Citizens ─────────────────────────────────────────────────────────────────

@bp.route("/sos", methods=["POST"])
@roles_required(CITIZEN)
@limiter.limit("10/minute")
def sos():
    body = _json_body()
    position = parse_coordinates(body.get("lat"), body.get("lng"))
    if position is None:
        raise IncidentError("We couldn't read your location. Choose it on the map and try again.", 422)
    existing = Signal.query.filter(
        Signal.citizen_id == current_user.id, Signal.status.in_(OPEN_STATUSES), Signal.source == "web_sos",
    ).order_by(Signal.id.desc()).first()
    if existing is not None:
        return jsonify(alert=citizen_record(existing), already_open=True)

    signal_type = body.get("type")
    if signal_type not in SOS_TYPES:
        signal_type = "Security Breach"
    signal = incidents.create_signal(
        type=signal_type, severity=SOS_TYPES[signal_type], source="web_sos", lat=position[0], lng=position[1],
        description=clean_text(body.get("description"), 500) or None,
        caller_id=current_user.phone, citizen_id=current_user.id,
    )
    return jsonify(alert=citizen_record(signal), already_open=False), 201


@bp.route("/me/alerts")
@roles_required(CITIZEN)
def my_alerts():
    signals = Signal.query.filter_by(citizen_id=current_user.id).order_by(Signal.id.desc()).limit(20).all()
    return jsonify(alerts=[citizen_record(s) for s in signals])


# ── Beacons ──────────────────────────────────────────────────────────────────

def _ingest_rate_key():
    return request.headers.get("X-API-Key", "") or request.remote_addr


def _authenticate_device():
    """Return (device or None, is_allowed). Devices have no session, so the key is the login."""
    key = request.headers.get("X-API-Key", "")
    if not key:
        return None, False
    device = Device.query.filter_by(key_hash=accounts.hash_device_key(key)).first()
    if device is not None:
        return (device, True) if not device.revoked_at and device.owner.is_active else (None, False)
    shared = current_app.config["MESSEC_DEVICE_KEY"]
    return None, bool(shared) and hmac.compare_digest(key.encode(), shared.encode())


def _number(payload, key, cast=float):
    try:
        return cast(payload[key])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


@bp.route("/signals/ingest", methods=["POST"])
@csrf.exempt
@limiter.limit("60/minute", key_func=_ingest_rate_key)
def ingest():
    device, allowed = _authenticate_device()
    if not allowed:
        return jsonify(error="invalid or missing X-API-Key"), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="body must be JSON with Content-Type: application/json"), 400

    position = parse_coordinates(payload.get("lat"), payload.get("lng"))
    if position is None:
        return jsonify(error="GPS coordinates are missing, out of range, or the GPS has no fix yet"), 422

    severity = str(payload.get("severity", "HIGH")).upper()
    if severity not in SEVERITIES:
        severity = "HIGH"
    strength = _number(payload, "signal_strength", int)

    fields = {
        "type": clean_text(str(payload.get("type", "Distress Signal")), 60) or "Distress Signal",
        "severity": severity,
        "lat": position[0],
        "lng": position[1],
        "frequency": _number(payload, "frequency"),
        "signal_strength": max(0, min(100, strength)) if strength is not None else None,
        "satellites": _number(payload, "satellites", int),
        "hdop": _number(payload, "hdop"),
    }
    if device is not None:
        device.last_seen_at = utcnow()
        owner = device.owner
        fields.update(source="device", device_uid=device.device_uid, citizen_id=owner.id, caller_id=owner.phone)
    else:
        device_uid = clean_text(str(payload.get("device_id", "DEVICE")), 40)
        fields.update(source="unlinked_device", device_uid=device_uid,
                      caller_id=clean_text(str(payload.get("caller_id", device_uid)), 60))

    signal = incidents.create_signal(**fields)
    return jsonify(ok=True, signal_id=signal.public_id, status=signal.status), 201
