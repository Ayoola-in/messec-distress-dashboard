"""Checks for submitted form values. Each returns (clean value, error message or None)."""

import re

from .models import STATION_TYPES, User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{6,24}$")
MIN_PASSWORD = 10


def clean_text(value, maximum=255):
    return (value or "").strip()[:maximum]


def check_email(value, *, must_be_new=True):
    email = clean_text(value, 254).lower()
    if not EMAIL_RE.match(email):
        return email, "Enter a valid email address."
    if must_be_new and User.query.filter_by(email=email).first():
        return email, "An account with this email already exists."
    return email, None


def check_phone(value):
    phone = clean_text(value, 30)
    if not PHONE_RE.match(phone):
        return phone, "Enter a valid phone number, for example 0803 000 0000."
    return phone, None


def check_password(password, confirm=None):
    password = password or ""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters for your password."
    if len(password) > 200:
        return "That password is too long."
    if confirm is not None and password != confirm:
        return "The two passwords don't match."
    return None


def parse_coordinates(lat, lng):
    """Return (lat, lng), or None if they are not a usable GPS position."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0) or (lat == 0.0 and lng == 0.0):
        return None
    return round(lat, 6), round(lng, 6)


def parse_station_form(form):
    """Validate the fields shared by the application form and the admin form."""
    errors = {}
    values = {
        "name": clean_text(form.get("name"), 120),
        "type": clean_text(form.get("type"), 20),
        "zone": clean_text(form.get("zone"), 80),
        "address": clean_text(form.get("address")),
        "email": clean_text(form.get("email"), 254).lower() or None,
        "contact_name": clean_text(form.get("contact_name"), 120) or None,
        "registration_ref": clean_text(form.get("registration_ref"), 80) or None,
    }
    if len(values["name"]) < 3:
        errors["name"] = "Enter the station's full name."
    if values["type"] not in STATION_TYPES:
        errors["type"] = "Choose the type of body."
    if not values["zone"]:
        errors["zone"] = "Enter the area this station covers."
    if not values["address"]:
        errors["address"] = "Enter the station's address."
    if values["email"] and not EMAIL_RE.match(values["email"]):
        errors["email"] = "Enter a valid email address."

    values["phone"], phone_error = check_phone(form.get("phone"))
    if phone_error:
        errors["phone"] = phone_error

    position = parse_coordinates(form.get("lat"), form.get("lng"))
    if position is None:
        errors["location"] = "Set the station's location by clicking the map."
        values["lat"], values["lng"] = form.get("lat"), form.get("lng")
    else:
        values["lat"], values["lng"] = position
    return values, errors
