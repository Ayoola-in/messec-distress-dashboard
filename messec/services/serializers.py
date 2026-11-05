"""JSON views of incidents. Who may see what is decided here."""

from datetime import timedelta

from flask import current_app

from .. import models
from ..models import iso


def station_brief(station):
    return {
        "id": station.id,
        "code": station.code,
        "name": station.name,
        "type": station.type,
        "type_label": station.type_label,
        "zone": station.zone,
        "phone": station.phone,
        "lat": station.lat,
        "lng": station.lng,
    }


def assignment_json(assignment):
    if assignment is None:
        return None
    return {
        "id": assignment.id,
        "signal_id": assignment.signal.public_id,
        "station": station_brief(assignment.station),
        "method": assignment.method,
        "status": assignment.status,
        "distance_km": assignment.distance_km,
        "eta_minutes": assignment.eta_minutes,
        "assigned_at": iso(assignment.assigned_at),
        "accepted_at": iso(assignment.accepted_at),
    }


def _citizen_json(signal):
    citizen = signal.citizen
    if citizen is None or citizen.deleted_at:
        return None
    profile = citizen.profile
    return {
        "name": citizen.full_name,
        "phone": citizen.phone,
        "address": profile.address if profile else None,
        "blood_group": profile.blood_group if profile else None,
        "medical_notes": profile.medical_notes if profile else None,
        "emergency_contacts": [
            {"name": c.name, "phone": c.phone, "relationship": c.relationship_label}
            for c in citizen.contacts
        ],
    }


def _auto_assign_at(signal):
    if signal.status != models.UNASSIGNED or signal.severity != "CRITICAL" or not signal.unassigned_since:
        return None
    seconds = current_app.config["MESSEC_AUTO_ASSIGN_SECONDS"]
    return iso(signal.unassigned_since + timedelta(seconds=seconds))


def responder_record(signal):
    """Full record for dispatchers and the station responsible for the incident."""
    return {
        "signal": {
            "id": signal.public_id,
            "type": signal.type,
            "severity": signal.severity,
            "status": signal.status,
            "status_label": signal.status_label,
            "lat": signal.lat,
            "lng": signal.lng,
            "description": signal.description,
            "timestamp": iso(signal.received_at),
            "resolved_at": iso(signal.resolved_at),
            "caller_id": signal.caller_id,
            "device_id": signal.device_uid,
            "source": signal.source,
            "is_simulated": signal.is_simulated,
            "frequency": signal.frequency,
            "signal_strength": signal.signal_strength,
            "satellites": signal.satellites,
            "hdop": signal.hdop,
            "auto_assign_at": _auto_assign_at(signal),
            "auto_assign_failed": signal.auto_assign_failed_at is not None,
            "citizen": _citizen_json(signal),
        },
        "assignment": assignment_json(signal.current_assignment),
    }


def citizen_record(signal):
    """What a citizen sees about their own alert: no responder-internal data."""
    assignment = signal.current_assignment
    station = assignment.station if assignment else None
    return {
        "id": signal.public_id,
        "type": signal.type,
        "severity": signal.severity,
        "status": signal.status,
        "status_label": signal.status_label,
        "lat": signal.lat,
        "lng": signal.lng,
        "timestamp": iso(signal.received_at),
        "resolved_at": iso(signal.resolved_at),
        "station": {"name": station.name, "type_label": station.type_label, "phone": station.phone} if station else None,
        "eta_minutes": assignment.eta_minutes if assignment else None,
    }
