"""Incident rules: creating signals, assignment, status changes and the auto-assign safety net."""

import math
from datetime import timedelta

from flask import current_app
from sqlalchemy.orm.exc import StaleDataError

from ..extensions import db
from ..models import (
    ACCEPTED, ACTIVE_ASSIGNMENT, APPROVED, ASSIGNED, Assignment, DECLINED, EN_ROUTE, IncidentEvent, ON_SCENE,
    REASSIGNED, RESOLVED, STATUS_LABELS, Signal, Station, UNASSIGNED, WITHDRAWN, utcnow,
)
from . import realtime

MINUTES_PER_KM = 2.5  # rough urban estimate over straight-line distance

MEDICAL_WORDS = ("medical", "ambulance", "injur", "health")
FIRE_WORDS = ("fire", "flood", "smoke")

PROGRESS = (ACCEPTED, EN_ROUTE, ON_SCENE, RESOLVED)


class IncidentError(Exception):
    """An action that the rules do not allow, with the HTTP status to report it as."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def haversine(lat1, lng1, lat2, lng2):
    """Great-circle distance in kilometres."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def suitable_types(signal_type):
    """Which station types should respond to an incident of this type."""
    text = (signal_type or "").lower()
    if any(word in text for word in MEDICAL_WORDS):
        return ("hospital", "ambulance")
    if any(word in text for word in FIRE_WORDS):
        return ("fire",)
    return ("police",)


def _commit():
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        raise IncidentError("This incident was just changed by someone else. Refresh and try again.", 409)


def _event(signal, kind, message, actor=None):
    db.session.add(IncidentEvent(signal=signal, kind=kind, message=message[:1000], actor=actor))


def create_signal(**fields):
    """Store a new signal in the queue and alert dispatchers."""
    now = utcnow()
    signal = Signal(status=UNASSIGNED, received_at=now, unassigned_since=now, **fields)
    db.session.add(signal)
    db.session.flush()
    _event(signal, "received", f"Signal received: {signal.source_label}.")
    db.session.commit()
    realtime.publish_incident(signal, is_new=True)
    return signal


def _declined_station_ids(signal):
    return {a.station_id for a in signal.assignments if a.status == DECLINED}


def candidates(signal):
    """Approved stations sorted by distance, best match first, for the assign dialog."""
    busy = realtime.busy_counts()
    wanted = suitable_types(signal.type)
    declined = _declined_station_ids(signal)
    rows = []
    for station in Station.query.filter_by(status=APPROVED).all():
        distance = haversine(signal.lat, signal.lng, station.lat, station.lng)
        rows.append({
            "id": station.id,
            "code": station.code,
            "name": station.name,
            "type": station.type,
            "type_label": station.type_label,
            "zone": station.zone,
            "phone": station.phone,
            "distance_km": round(distance, 2),
            "eta_minutes": _eta(distance),
            "active_count": busy.get(station.id, 0),
            "suitable": station.type in wanted,
            "declined": station.id in declined,
            "is_current": station.id == signal.current_station_id,
            "recommended": False,
        })
    rows.sort(key=lambda r: r["distance_km"])
    selectable = [r for r in rows if not r["is_current"] and not r["declined"]]
    best = next((r for r in selectable if r["suitable"] and not r["active_count"]), None) \
        or next((r for r in selectable if r["suitable"]), None)
    if best:
        best["recommended"] = True
    return rows


def _eta(distance_km):
    return max(1, round(distance_km * MINUTES_PER_KM))


def assign(signal, station, actor=None, method="manual"):
    """Task a station with an incident, taking it away from any previous station."""
    if signal.status == RESOLVED:
        raise IncidentError("This incident is already resolved.", 409)
    if station.status != APPROVED:
        raise IncidentError("Only approved stations can receive incidents.", 422)
    if signal.current_station_id == station.id:
        raise IncidentError(f"{station.name} already has this incident.", 409)

    previous = [a for a in signal.assignments if a.status in ACTIVE_ASSIGNMENT]
    now = utcnow()
    for old in previous:
        old.status = REASSIGNED
        old.ended_at = now

    distance = haversine(signal.lat, signal.lng, station.lat, station.lng)
    assignment = Assignment(
        signal=signal, station=station, assigned_by=actor, method=method, status=ASSIGNED,
        distance_km=round(distance, 2), eta_minutes=_eta(distance),
    )
    db.session.add(assignment)
    signal.status = ASSIGNED
    signal.current_station_id = station.id
    signal.unassigned_since = None
    signal.auto_assign_failed_at = None

    verb = "Reassigned" if previous else "Assigned"
    how = "automatically" if method == "auto" else f"by {actor.full_name}"
    _event(signal, "assigned", f"{verb} to {station.name} ({station.type_label}) {how}.", actor)
    _commit()
    realtime.publish_incident(signal, removed_station_ids=[a.station_id for a in previous])
    return assignment


def _responsible_assignment(signal, station_id):
    assignment = signal.current_assignment
    if assignment is None or assignment.station_id != station_id or assignment.status not in ACTIVE_ASSIGNMENT:
        raise IncidentError("This incident is not assigned to your station.", 403)
    return assignment


def advance(signal, user, new_status, note=None):
    """A station moves its incident forward: accepted, en route, on scene, resolved."""
    if new_status not in PROGRESS:
        raise IncidentError("Unknown status.", 422)
    assignment = _responsible_assignment(signal, user.station_id)
    if assignment.status == ASSIGNED and new_status != ACCEPTED:
        raise IncidentError("Accept the incident first.", 409)
    current_rank = PROGRESS.index(assignment.status) if assignment.status in PROGRESS else -1
    if PROGRESS.index(new_status) <= current_rank:
        raise IncidentError(f"This incident is already {STATUS_LABELS[assignment.status].lower()}.", 409)

    now = utcnow()
    assignment.status = new_status
    signal.status = new_status
    if new_status == ACCEPTED:
        assignment.accepted_at = now
    if new_status == RESOLVED:
        assignment.ended_at = now
        signal.resolved_at = now
    message = f"{assignment.station.name}: {STATUS_LABELS[new_status].lower()}."
    if note:
        message += f" {note}"
    _event(signal, "status", message, user)
    _commit()
    realtime.publish_incident(signal)


def _return_to_queue(signal, assignment, end_status):
    now = utcnow()
    assignment.status = end_status
    assignment.ended_at = now
    signal.status = UNASSIGNED
    signal.current_station_id = None
    signal.unassigned_since = now
    signal.auto_assign_failed_at = None


def decline(signal, user, reason):
    """A station hands an incident back to the queue, with a reason."""
    reason = (reason or "").strip()
    if not reason:
        raise IncidentError("Give a reason for declining.", 422)
    assignment = _responsible_assignment(signal, user.station_id)
    if assignment.status not in (ASSIGNED, ACCEPTED):
        raise IncidentError("You can only decline before your team is en route.", 409)
    assignment.decline_reason = reason[:255]
    station_id = assignment.station_id
    _return_to_queue(signal, assignment, DECLINED)
    _event(signal, "declined", f"{assignment.station.name} declined: {reason}", user)
    _commit()
    realtime.publish_incident(signal, removed_station_ids=[station_id])


def close(signal, actor, note):
    """A dispatcher closes an incident at any stage, such as a false alarm."""
    note = (note or "").strip()
    if not note:
        raise IncidentError("Add a note saying why this incident is being closed.", 422)
    if signal.status == RESOLVED:
        raise IncidentError("This incident is already resolved.", 409)
    now = utcnow()
    for assignment in signal.assignments:
        if assignment.status in ACTIVE_ASSIGNMENT:
            assignment.status = RESOLVED
            assignment.ended_at = now
    signal.status = RESOLVED
    signal.resolved_at = now
    signal.unassigned_since = None
    _event(signal, "closed", f"Closed by {actor.full_name}: {note}", actor)
    _commit()
    realtime.publish_incident(signal)


def add_note(signal, user, message):
    message = (message or "").strip()
    if not message:
        raise IncidentError("Write a note first.", 422)
    _event(signal, "note", message, user)
    # A note changes no Signal column, so a concurrent assignment cannot conflict with it.
    db.session.commit()


def withdraw_station(station):
    """Return a suspended station's open incidents to the queue. The caller has not committed yet."""
    assignments = Assignment.query.filter(
        Assignment.station_id == station.id, Assignment.status.in_(ACTIVE_ASSIGNMENT)
    ).all()
    for assignment in assignments:
        _return_to_queue(assignment.signal, assignment, WITHDRAWN)
        _event(assignment.signal, "withdrawn", f"Returned to the queue: {station.name} was suspended.")
    return [a.signal for a in assignments]


def choose_auto_station(signal):
    """Nearest suitable station that has not declined; idle stations first; any approved station as a last resort."""
    declined = _declined_station_ids(signal)
    stations = [s for s in Station.query.filter_by(status=APPROVED).all() if s.id not in declined]
    if not stations:
        return None
    busy = realtime.busy_counts()
    wanted = suitable_types(signal.type)

    def distance(station):
        return haversine(signal.lat, signal.lng, station.lat, station.lng)

    suitable = [s for s in stations if s.type in wanted]
    idle = [s for s in suitable if not busy.get(s.id)]
    return min(idle or suitable or stations, key=distance)


def auto_assign_overdue(now=None):
    """Safety net: assign critical signals nobody has picked up in time. Returns the number assigned."""
    now = now or utcnow()
    cutoff = now - timedelta(seconds=current_app.config["MESSEC_AUTO_ASSIGN_SECONDS"])
    overdue = Signal.query.filter(
        Signal.status == UNASSIGNED,
        Signal.severity == "CRITICAL",
        Signal.unassigned_since <= cutoff,
    ).order_by(Signal.unassigned_since).all()

    assigned = 0
    for signal in overdue:
        station = choose_auto_station(signal)
        if station is None:
            if signal.auto_assign_failed_at is None:
                signal.auto_assign_failed_at = now
                _event(signal, "auto_failed", "Automatic assignment failed: no station is available.")
                try:
                    _commit()
                except IncidentError:
                    continue
                realtime.publish_auto_assign_failed(signal)
                realtime.publish_incident(signal)
            continue
        try:
            assign(signal, station, actor=None, method="auto")
            assigned += 1
        except IncidentError:
            continue  # a dispatcher got there first
    return assigned
