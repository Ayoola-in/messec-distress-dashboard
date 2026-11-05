"""Live updates over Socket.IO, sent only to the rooms allowed to see them.

Rooms: 'staff' (admins and dispatchers), 'station:<id>', 'citizen:<user id>'.
"""

import threading

from flask import current_app, request
from flask_login import current_user
from flask_socketio import join_room

from ..extensions import db, socketio
from ..models import (
    ACTIVE_ASSIGNMENT, APPROVED, Assignment, CITIZEN, OPEN_STATUSES, RESOLVED, STATION, Signal, Station, UNASSIGNED,
)
from .serializers import citizen_record, responder_record, station_brief

_sessions_lock = threading.Lock()
_sessions = {}  # user id -> set of socket ids, used to disconnect disabled users


@socketio.on("connect")
def on_connect():
    if not current_user.is_authenticated:
        return False
    if current_user.is_staff:
        join_room("staff")
    elif current_user.role == STATION:
        join_room(f"station:{current_user.station_id}")
    elif current_user.role == CITIZEN:
        join_room(f"citizen:{current_user.id}")
    with _sessions_lock:
        _sessions.setdefault(current_user.id, set()).add(request.sid)
    return True


@socketio.on("disconnect")
def on_disconnect(*_args):
    with _sessions_lock:
        for sids in _sessions.values():
            sids.discard(request.sid)


def disconnect_users(user_ids):
    """Immediately cut off live updates for users who lost access."""
    for user_id in user_ids:
        with _sessions_lock:
            sids = list(_sessions.get(user_id, ()))
        for sid in sids:
            socketio.server.disconnect(sid, namespace="/")


def busy_counts():
    rows = (
        db.session.query(Assignment.station_id, db.func.count(Assignment.id))
        .filter(Assignment.status.in_(ACTIVE_ASSIGNMENT))
        .group_by(Assignment.station_id)
        .all()
    )
    return dict(rows)


def stations_json():
    counts = busy_counts()
    stations = Station.query.filter_by(status=APPROVED).order_by(Station.name).all()
    return [
        {**station_brief(s), "active_count": counts.get(s.id, 0),
         "status": "responding" if counts.get(s.id) else "available"}
        for s in stations
    ]


def stats_json():
    rows = dict(db.session.query(Signal.status, db.func.count(Signal.id)).group_by(Signal.status).all())
    return {
        "total_signals": sum(rows.values()),
        "unassigned": rows.get(UNASSIGNED, 0),
        "active": sum(rows.get(s, 0) for s in OPEN_STATUSES if s != UNASSIGNED),
        "resolved": rows.get(RESOLVED, 0),
    }


def publish_incident(signal, *, is_new=False, removed_station_ids=()):
    """Send an incident's new state to everyone entitled to it. Call after commit."""
    record = responder_record(signal)
    staff_payload = {
        **record,
        "is_new": is_new,
        "stats": stats_json(),
        "ack_seconds": current_app.config["MESSEC_STATION_ACK_SECONDS"],
    }
    socketio.emit("incident_update", staff_payload, to="staff")

    assignment = signal.current_assignment
    if assignment is not None:
        socketio.emit("incident_update", record, to=f"station:{assignment.station_id}")
    for station_id in removed_station_ids:
        if assignment is None or station_id != assignment.station_id:
            socketio.emit("incident_removed", {"signal_id": signal.public_id}, to=f"station:{station_id}")

    if signal.citizen_id:
        socketio.emit("alert_update", citizen_record(signal), to=f"citizen:{signal.citizen_id}")

    publish_stations()


def publish_stations():
    socketio.emit("stations_update", {"stations": stations_json()}, to="staff")


def publish_auto_assign_failed(signal):
    socketio.emit("auto_assign_failed", {"signal_id": signal.public_id, "type": signal.type}, to="staff")
