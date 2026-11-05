"""The citizen portal: SOS, profile, emergency contacts, beacons and data rights."""

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, logout_user

from ..auth import roles_required
from ..extensions import db, limiter
from ..models import CITIZEN, CitizenProfile, Device, EmergencyContact, SOS_TYPES, Signal, utcnow
from ..services import accounts, audit
from ..validation import check_phone, clean_text

bp = Blueprint("citizen", __name__)

BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
MAX_CONTACTS = 10
MAX_DEVICES = 10


def _render_home(new_device=None, new_key=None, status=200):
    alerts = Signal.query.filter_by(citizen_id=current_user.id).order_by(Signal.id.desc()).limit(20).all()
    return render_template(
        "citizen/home.html", alerts=alerts, sos_types=SOS_TYPES, blood_groups=BLOOD_GROUPS,
        profile=current_user.profile, new_device=new_device, new_key=new_key,
    ), status


@bp.route("/me")
@roles_required(CITIZEN)
def home():
    return _render_home()


@bp.route("/me/profile", methods=["POST"])
@roles_required(CITIZEN)
def save_profile():
    name = clean_text(request.form.get("full_name"), 120)
    phone, phone_error = check_phone(request.form.get("phone"))
    blood_group = request.form.get("blood_group", "")
    if len(name) < 2 or phone_error or (blood_group and blood_group not in BLOOD_GROUPS):
        flash(phone_error or "Check your name and blood group.", "error")
        return redirect(url_for("citizen.home") + "#profile")

    current_user.full_name = name
    current_user.phone = phone
    profile = current_user.profile or CitizenProfile(user=current_user)
    profile.address = clean_text(request.form.get("address")) or None
    profile.blood_group = blood_group or None
    profile.medical_notes = clean_text(request.form.get("medical_notes"), 1000) or None
    db.session.add(profile)
    db.session.commit()
    flash("Your profile has been saved.", "success")
    return redirect(url_for("citizen.home") + "#profile")


@bp.route("/me/contacts", methods=["POST"])
@roles_required(CITIZEN)
def add_contact():
    name = clean_text(request.form.get("name"), 120)
    phone, phone_error = check_phone(request.form.get("phone"))
    if len(current_user.contacts) >= MAX_CONTACTS:
        flash(f"You can save up to {MAX_CONTACTS} emergency contacts.", "error")
    elif len(name) < 2 or phone_error:
        flash(phone_error or "Enter the contact's name.", "error")
    else:
        db.session.add(EmergencyContact(
            user=current_user, name=name, phone=phone,
            relationship_label=clean_text(request.form.get("relationship"), 60) or None,
        ))
        db.session.commit()
        flash("Emergency contact added.", "success")
    return redirect(url_for("citizen.home") + "#contacts")


@bp.route("/me/contacts/<int:contact_id>/delete", methods=["POST"])
@roles_required(CITIZEN)
def delete_contact(contact_id):
    contact = EmergencyContact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    db.session.delete(contact)
    db.session.commit()
    flash("Emergency contact removed.", "info")
    return redirect(url_for("citizen.home") + "#contacts")


@bp.route("/me/devices", methods=["POST"])
@roles_required(CITIZEN)
def add_device():
    label = clean_text(request.form.get("label"), 80)
    if not label:
        flash("Give your beacon a name.", "error")
        return redirect(url_for("citizen.home") + "#devices")
    if sum(1 for d in current_user.devices if not d.revoked_at) >= MAX_DEVICES:
        flash(f"You can link up to {MAX_DEVICES} beacons.", "error")
        return redirect(url_for("citizen.home") + "#devices")
    key = accounts.new_device_key()
    device = Device(device_uid=accounts.new_device_uid(), label=label, owner=current_user,
                    key_hash=accounts.hash_device_key(key))
    db.session.add(device)
    audit.record("device.linked", device.device_uid)
    db.session.commit()
    # Shown once in this response only. Only the hash is stored.
    return _render_home(new_device=device, new_key=key)


@bp.route("/me/devices/<int:device_id>/revoke", methods=["POST"])
@roles_required(CITIZEN)
def revoke_device(device_id):
    device = Device.query.filter_by(id=device_id, owner_id=current_user.id).first_or_404()
    if not device.revoked_at:
        device.revoked_at = utcnow()
        audit.record("device.revoked", device.device_uid)
        db.session.commit()
    flash("Beacon removed. It can no longer send alerts.", "info")
    return redirect(url_for("citizen.home") + "#devices")


@bp.route("/me/export.json")
@roles_required(CITIZEN)
@limiter.limit("10/hour")
def export_data():
    audit.record("citizen.exported", f"user:{current_user.id}")
    db.session.commit()
    response = jsonify(accounts.export_citizen_data(current_user))
    response.headers["Content-Disposition"] = "attachment; filename=my-messec-data.json"
    return response


@bp.route("/me/delete", methods=["POST"])
@roles_required(CITIZEN)
@limiter.limit("10/hour")
def delete_account():
    if not current_user.check_password(request.form.get("password", "")):
        flash("That password is wrong, so your account was not deleted.", "error")
        return redirect(url_for("citizen.home") + "#privacy")
    if accounts.has_open_incident(current_user):
        flash("You have an open alert. You can delete your account once it has been resolved.", "error")
        return redirect(url_for("citizen.home") + "#privacy")
    user_id = current_user.id
    user = current_user._get_current_object()
    logout_user()
    accounts.delete_citizen(user)
    audit.record("citizen.deleted", f"user:{user_id}")
    db.session.commit()
    flash("Your account and personal details have been deleted.", "info")
    return redirect(url_for("public.landing"))
