"""Dispatcher dashboard, incident detail and searchable history."""

import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, Response, abort, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from ..auth import can_view_incident, roles_required
from ..extensions import db
from ..models import (
    ADMIN, Assignment, DISPATCHER, LOST_ACCESS, SEVERITIES, SIGNAL_STATUSES, STATION, Signal, Station,
)
from ..services import audit

bp = Blueprint("incidents", __name__)

PAGE_SIZE = 50
CSV_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def signal_from_public_id(public_id):
    """Look up 'SIG-0012', or return None."""
    digits = public_id[4:] if public_id.upper().startswith("SIG-") else ""
    if not digits.isdigit():
        return None
    return db.session.get(Signal, int(digits))


@bp.route("/dispatch")
@roles_required(ADMIN, DISPATCHER)
def dashboard():
    return render_template("dashboard.html")


@bp.route("/incidents/<public_id>")
@roles_required(ADMIN, DISPATCHER, STATION)
def detail(public_id):
    signal = signal_from_public_id(public_id)
    if signal is None:
        abort(404)
    if not can_view_incident(current_user, signal):
        abort(403)
    return render_template("incidents/detail.html", signal=signal, assignment=signal.current_assignment)


def _parse_date(value):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d")
    except ValueError:
        return None


def _history_query(args):
    query = Signal.query
    if current_user.role == STATION:
        query = query.filter(Signal.assignments.any(
            (Assignment.station_id == current_user.station_id) & Assignment.status.notin_(LOST_ACCESS)
        ))

    status, severity = args.get("status", ""), args.get("severity", "")
    if status in SIGNAL_STATUSES:
        query = query.filter(Signal.status == status)
    if severity in SEVERITIES:
        query = query.filter(Signal.severity == severity)
    date_from, date_to = _parse_date(args.get("from")), _parse_date(args.get("to"))
    if date_from:
        query = query.filter(Signal.received_at >= date_from)
    if date_to:
        query = query.filter(Signal.received_at < date_to + timedelta(days=1))

    text = args.get("q", "").strip()
    if text:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions = [
            Signal.type.ilike(like, escape="\\"),
            Signal.caller_id.ilike(like, escape="\\"),
            Signal.device_uid.ilike(like, escape="\\"),
            Signal.current_station.has(Station.name.ilike(like, escape="\\")),
        ]
        by_id = signal_from_public_id(text)
        if by_id is not None:
            conditions.append(Signal.id == by_id.id)
        query = query.filter(or_(*conditions))
    return query.order_by(Signal.id.desc())


@bp.route("/incidents")
@roles_required(ADMIN, DISPATCHER, STATION)
def history():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = _history_query(request.args).paginate(page=page, per_page=PAGE_SIZE, error_out=False)
    filters = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("incidents/history.html", pagination=pagination, filters=filters,
                           severities=SEVERITIES, statuses=SIGNAL_STATUSES,
                           csv_url=url_for("incidents.export_csv", **filters))


def _csv_cell(value):
    """Stop spreadsheet apps running device- or user-supplied text as a formula."""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(CSV_FORMULA_CHARS) else text


@bp.route("/incidents/export.csv")
@roles_required(ADMIN, DISPATCHER, STATION)
def export_csv():
    audit.record("incidents.exported", None, str(dict(request.args))[:500])
    db.session.commit()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Incident", "Received (UTC)", "Severity", "Type", "Status", "Station", "Station type",
                     "Assignment method", "Distance km", "Estimated ETA min", "Caller", "Device",
                     "Latitude", "Longitude", "Simulated", "Resolved (UTC)"])
    query = _history_query(request.args).options(selectinload(Signal.assignments).joinedload(Assignment.station))
    for signal in query:
        assignment = signal.current_assignment
        station = assignment.station if assignment else None
        writer.writerow([_csv_cell(v) for v in (
            signal.public_id, signal.received_at.isoformat(timespec="seconds"), signal.severity, signal.type,
            signal.status_label, station.name if station else None, station.type_label if station else None,
            assignment.method if assignment else None, assignment.distance_km if assignment else None,
            assignment.eta_minutes if assignment else None, signal.caller_id, signal.device_uid,
            signal.lat, signal.lng, "yes" if signal.is_simulated else "no",
            signal.resolved_at.isoformat(timespec="seconds") if signal.resolved_at else None,
        )])
    return Response(buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=messec-incidents.csv"})
