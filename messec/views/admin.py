"""Admin pages: station approvals, users and the audit log."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..auth import roles_required
from ..extensions import db
from ..models import (
    ADMIN, APPROVED, AuditLog, CITIZEN, DISPATCHER, PENDING, REJECTED, ROLE_LABELS, STATION, SUSPENDED, Station,
    User, utcnow,
)
from ..services import accounts, audit, incidents, realtime
from ..validation import check_email, check_password, check_phone, clean_text, parse_station_form

bp = Blueprint("admin", __name__, url_prefix="/admin")

CREATABLE_ROLES = (ADMIN, DISPATCHER, STATION)


def _mark_reviewed(station, status, note=None):
    station.status = status
    station.review_note = note
    station.reviewed_at = utcnow()


@bp.route("/stations")
@roles_required(ADMIN)
def stations():
    rows = Station.query.order_by(Station.created_at.desc()).all()
    return render_template(
        "admin/stations.html",
        pending=[s for s in rows if s.status == PENDING],
        others=[s for s in rows if s.status != PENDING],
    )


@bp.route("/stations/new", methods=["GET", "POST"])
@roles_required(ADMIN)
def new_station():
    if request.method == "GET":
        return render_template("admin/station_form.html", station=None, values={}, errors={})
    values, errors = parse_station_form(request.form)
    if errors:
        return render_template("admin/station_form.html", station=None, values=values, errors=errors), 422
    station = Station(**values)
    _mark_reviewed(station, APPROVED, "Added by admin")
    db.session.add(station)
    db.session.flush()
    audit.record("station.created", station.code, station.name)
    db.session.commit()
    realtime.publish_stations()
    flash(f"{station.name} has been added. Add staff logins for it under Users.", "success")
    return redirect(url_for("admin.stations"))


@bp.route("/stations/<int:station_id>/edit", methods=["GET", "POST"])
@roles_required(ADMIN)
def edit_station(station_id):
    station = db.get_or_404(Station, station_id)
    if request.method == "GET":
        values = {f: getattr(station, f) for f in ("name", "type", "zone", "address", "lat", "lng", "phone", "email",
                                                   "contact_name", "registration_ref")}
        return render_template("admin/station_form.html", station=station, values=values, errors={})
    values, errors = parse_station_form(request.form)
    if errors:
        return render_template("admin/station_form.html", station=station, values=values, errors=errors), 422
    for field, value in values.items():
        setattr(station, field, value)
    audit.record("station.edited", station.code, station.name)
    db.session.commit()
    realtime.publish_stations()
    flash(f"{station.name} has been updated.", "success")
    return redirect(url_for("admin.stations"))


@bp.route("/stations/<int:station_id>/<action>", methods=["POST"])
@roles_required(ADMIN)
def review_station(station_id, action):
    station = db.get_or_404(Station, station_id)
    note = clean_text(request.form.get("note")) or None

    if action == "approve" and station.status in (PENDING, REJECTED, SUSPENDED):
        _mark_reviewed(station, APPROVED, note)
        message = f"{station.name} is approved and can now receive incidents."
    elif action == "reject" and station.status == PENDING:
        _mark_reviewed(station, REJECTED, note)
        message = f"{station.name}'s application was rejected."
    elif action == "suspend" and station.status == APPROVED:
        _mark_reviewed(station, SUSPENDED, note)
        message = f"{station.name} is suspended. Its open incidents went back to the queue."
    else:
        flash("That action isn't available for this station.", "error")
        return redirect(url_for("admin.stations"))

    returned = incidents.withdraw_station(station) if action == "suspend" else []
    audit.record(f"station.{action}", station.code, note)
    db.session.commit()

    if action == "suspend":
        realtime.disconnect_users([u.id for u in station.staff])
    for signal in returned:
        realtime.publish_incident(signal, removed_station_ids=[station.id])
    realtime.publish_stations()
    flash(message, "success")
    return redirect(url_for("admin.stations"))


@bp.route("/users", methods=["GET", "POST"])
@roles_required(ADMIN)
def users():
    errors, values, reset_link = {}, {}, None
    if request.method == "POST":
        values = {
            "full_name": clean_text(request.form.get("full_name"), 120),
            "role": request.form.get("role", ""),
            "station_id": request.form.get("station_id", type=int),
        }
        if len(values["full_name"]) < 2:
            errors["full_name"] = "Enter the person's name."
        values["email"], email_error = check_email(request.form.get("email"))
        if email_error:
            errors["email"] = email_error
        phone = clean_text(request.form.get("phone"), 30)
        if phone:
            values["phone"], phone_error = check_phone(phone)
            if phone_error:
                errors["phone"] = phone_error
        if values["role"] not in CREATABLE_ROLES:
            errors["role"] = "Choose a role."
        station = db.session.get(Station, values["station_id"]) if values["station_id"] else None
        if values["role"] == STATION and (station is None or station.status != APPROVED):
            errors["station_id"] = "Station staff must belong to an approved station."

        if not errors:
            user = User(email=values["email"], full_name=values["full_name"], phone=phone or None,
                        role=values["role"], station=station if values["role"] == STATION else None)
            # Unusable random password: the person sets their own through the link.
            user.set_password(accounts.secrets.token_urlsafe(32))
            db.session.add(user)
            db.session.flush()
            audit.record("user.created", f"user:{user.id}", f"{user.role} {user.email}")
            db.session.commit()
            reset_link = url_for("public.reset_password", token=accounts.make_reset_token(user), _external=True)
            flash(f"{user.full_name}'s account is ready. Send them the link below to set a password.", "success")
            values = {}

    return _render_users(values, errors, reset_link), 422 if errors else 200


def _render_users(values, errors, reset_link=None):
    role_filter = request.args.get("role", "")
    query = User.query.filter(User.deleted_at.is_(None))
    if role_filter in ROLE_LABELS:
        query = query.filter(User.role == role_filter)
    return render_template(
        "admin/users.html", users=query.order_by(User.created_at.desc()).limit(500).all(), values=values,
        errors=errors, reset_link=reset_link, role_filter=role_filter, creatable_roles=CREATABLE_ROLES,
        approved_stations=Station.query.filter_by(status=APPROVED).order_by(Station.name).all(),
    )


@bp.route("/users/<int:user_id>/<action>", methods=["POST"])
@roles_required(ADMIN)
def user_action(user_id, action):
    user = db.get_or_404(User, user_id)
    if user.deleted_at:
        flash("That account has been deleted.", "error")
        return redirect(url_for("admin.users"))
    if action in ("disable", "enable"):
        if user.id == current_user.id:
            flash("You can't change your own account's access.", "error")
            return redirect(url_for("admin.users"))
        user.enabled = action == "enable"
        audit.record(f"user.{action}d", f"user:{user.id}", user.email)
        db.session.commit()
        if action == "disable":
            realtime.disconnect_users([user.id])
        flash(f"{user.full_name}'s account is {'enabled' if user.enabled else 'disabled'}.", "success")
        return redirect(url_for("admin.users"))
    if action == "reset":
        audit.record("user.reset_link", f"user:{user.id}", user.email)
        db.session.commit()
        link = url_for("public.reset_password", token=accounts.make_reset_token(user), _external=True)
        flash(f"Send {user.full_name} the link below. It works once and expires in 24 hours.", "success")
        return _render_users({}, {}, link)
    flash("Unknown action.", "error")
    return redirect(url_for("admin.users"))


@bp.route("/audit")
@roles_required(ADMIN)
def audit_log():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = AuditLog.query.order_by(AuditLog.id.desc()).paginate(page=page, per_page=100, error_out=False)
    return render_template("admin/audit.html", pagination=pagination)
