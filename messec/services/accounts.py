"""Account helpers: password reset links, device keys, and citizen data rights."""

import hashlib
import secrets

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..extensions import db
from ..models import CITIZEN, OPEN_STATUSES, Signal, User, iso, utcnow
from .serializers import citizen_record

RESET_MAX_AGE = 24 * 60 * 60


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="messec-password-reset")


def make_reset_token(user):
    # Tied to the current password, so the link stops working once it has been used.
    return _reset_serializer().dumps({"uid": user.id, "fp": user.password_hash[-12:]})


def user_from_reset_token(token):
    try:
        data = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.deleted_at or user.password_hash[-12:] != data.get("fp"):
        return None
    return user


def hash_device_key(key):
    # Keys are long random values, so a plain fast hash is the right tool here.
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_device_key():
    return "msk_" + secrets.token_urlsafe(32)


def new_device_uid():
    return "BCN-" + secrets.token_hex(4).upper()


def export_citizen_data(user):
    profile = user.profile
    signals = Signal.query.filter_by(citizen_id=user.id).order_by(Signal.id).all()
    return {
        "exported_at": iso(utcnow()),
        "account": {
            "name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "created_at": iso(user.created_at),
            "consent_given_at": iso(user.consent_at),
        },
        "profile": {
            "address": profile.address if profile else None,
            "blood_group": profile.blood_group if profile else None,
            "medical_notes": profile.medical_notes if profile else None,
        },
        "emergency_contacts": [
            {"name": c.name, "phone": c.phone, "relationship": c.relationship_label} for c in user.contacts
        ],
        "devices": [
            {"device_id": d.device_uid, "label": d.label, "created_at": iso(d.created_at),
             "last_seen_at": iso(d.last_seen_at), "revoked_at": iso(d.revoked_at)} for d in user.devices
        ],
        "alerts": [citizen_record(s) for s in signals],
    }


def has_open_incident(user):
    return Signal.query.filter(Signal.citizen_id == user.id, Signal.status.in_(OPEN_STATUSES)).count() > 0


def delete_citizen(user):
    """Erase a citizen's personal data. Incident records stay, with the person removed from them."""
    if user.role != CITIZEN:
        raise ValueError("Only citizen accounts can be deleted this way.")
    for signal in Signal.query.filter_by(citizen_id=user.id).all():
        signal.citizen_id = None
        signal.caller_id = "[deleted]"
        signal.description = None
        signal.device_uid = None
    user.profile = None
    user.contacts = []
    user.devices = []
    user.full_name = "Deleted user"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.phone = None
    user.enabled = False
    user.deleted_at = utcnow()
    user.set_password(secrets.token_urlsafe(32))
