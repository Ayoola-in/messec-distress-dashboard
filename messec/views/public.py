"""Public pages and account access: landing, login, registration and station applications."""

from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import role_home
from ..extensions import db, limiter
from ..models import APPROVED, CITIZEN, CitizenProfile, PENDING, REJECTED, STATION, SUSPENDED, Station, User, utcnow
from ..services import accounts, audit
from ..validation import check_email, check_password, check_phone, clean_text, parse_station_form

bp = Blueprint("public", __name__)

_DUMMY_HASH = generate_password_hash("not-a-real-password")


def _safe_next(target):
    """Only follow redirects within this site."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return None
    return target


def _blocked_message(user):
    if user.role == STATION and user.station is not None:
        status = user.station.status
        if status == PENDING:
            return "Your station's application is still waiting for approval."
        if status == REJECTED:
            return "Your station's application was not approved. Contact the MesSec administrator."
        if status == SUSPENDED:
            return "Your station has been suspended. Contact the MesSec administrator."
    return "This account has been disabled. Contact the MesSec administrator."


@bp.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(role_home(current_user))
    return render_template("landing.html")


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@bp.route("/healthz")
def healthz():
    db.session.execute(db.text("SELECT 1"))
    return {"status": "ok"}


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(role_home(current_user))
    if request.method == "GET":
        return render_template("auth/login.html", next_url=request.args.get("next", ""))

    email = clean_text(request.form.get("email"), 254).lower()
    password = request.form.get("password", "")
    next_url = request.form.get("next", "")
    user = User.query.filter_by(email=email).first()
    # Check a hash either way, so response time doesn't reveal which emails exist.
    valid = check_password_hash(user.password_hash if user else _DUMMY_HASH, password)
    if not user or not valid:
        flash("Wrong email or password.", "error")
        return render_template("auth/login.html", email=email, next_url=next_url), 401
    if not user.is_active:
        flash(_blocked_message(user), "error")
        return render_template("auth/login.html", email=email, next_url=next_url), 403

    login_user(user)
    session.permanent = True
    user.last_login_at = utcnow()
    db.session.commit()
    return redirect(_safe_next(next_url) or role_home(user))


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("public.login"))


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(role_home(current_user))
    if request.method == "GET":
        return render_template("auth/register.html", values={}, errors={})

    errors = {}
    values = {
        "full_name": clean_text(request.form.get("full_name"), 120),
        "address": clean_text(request.form.get("address")),
    }
    if len(values["full_name"]) < 2:
        errors["full_name"] = "Enter your full name."
    values["email"], email_error = check_email(request.form.get("email"))
    if email_error:
        errors["email"] = email_error
    values["phone"], phone_error = check_phone(request.form.get("phone"))
    if phone_error:
        errors["phone"] = phone_error
    password = request.form.get("password", "")
    password_error = check_password(password, request.form.get("confirm", ""))
    if password_error:
        errors["password"] = password_error
    if not request.form.get("consent"):
        errors["consent"] = "You need to agree before we can store your details."
    if errors:
        return render_template("auth/register.html", values=values, errors=errors), 422

    user = User(email=values["email"], full_name=values["full_name"], phone=values["phone"],
                role=CITIZEN, consent_at=utcnow())
    user.set_password(password)
    user.profile = CitizenProfile(address=values["address"] or None)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    session.permanent = True
    flash("Your account is ready. Add your emergency contacts so responders can reach them.", "success")
    return redirect(url_for("citizen.home"))


@bp.route("/stations/apply", methods=["GET", "POST"])
@limiter.limit("5/hour", methods=["POST"])
def apply_station():
    if request.method == "GET":
        return render_template("auth/apply.html", values={}, errors={})

    values, errors = parse_station_form(request.form)
    values["login_name"] = clean_text(request.form.get("login_name"), 120)
    if len(values["login_name"]) < 2:
        errors["login_name"] = "Enter the name of the person applying."
    values["login_email"], email_error = check_email(request.form.get("login_email"))
    if email_error:
        errors["login_email"] = email_error
    password = request.form.get("password", "")
    password_error = check_password(password, request.form.get("confirm", ""))
    if password_error:
        errors["password"] = password_error
    if errors:
        return render_template("auth/apply.html", values=values, errors=errors), 422

    login_name, login_email = values.pop("login_name"), values.pop("login_email")
    station = Station(status=PENDING, **values)
    db.session.add(station)
    db.session.flush()
    user = User(email=login_email, full_name=login_name, phone=values["phone"], role=STATION, station=station)
    user.set_password(password)
    db.session.add(user)
    audit.record("station.applied", station.code, station.name)
    db.session.commit()
    return redirect(url_for("public.apply_done"))


@bp.route("/stations/apply/done")
def apply_done():
    return render_template("auth/apply_done.html")


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
@limiter.limit("10/hour", methods=["POST"])
def change_password():
    if request.method == "GET":
        return render_template("auth/password.html", errors={})
    errors = {}
    if not current_user.check_password(request.form.get("current", "")):
        errors["current"] = "That isn't your current password."
    password = request.form.get("password", "")
    password_error = check_password(password, request.form.get("confirm", ""))
    if password_error:
        errors["password"] = password_error
    if errors:
        return render_template("auth/password.html", errors=errors), 422
    user = current_user._get_current_object()
    user.set_password(password)
    audit.record("account.password_changed", f"user:{user.id}")
    db.session.commit()
    login_user(user)  # keep this session; other sessions are now signed out
    session.permanent = True
    flash("Your password has been changed.", "success")
    return redirect(role_home(user))


@bp.route("/reset/<token>", methods=["GET", "POST"])
@limiter.limit("10/hour", methods=["POST"])
def reset_password(token):
    user = accounts.user_from_reset_token(token)
    if user is None:
        return render_template("error.html", code=400,
                               message="This reset link has expired or has already been used."), 400
    if request.method == "GET":
        return render_template("auth/reset.html", errors={}, token=token)
    password = request.form.get("password", "")
    password_error = check_password(password, request.form.get("confirm", ""))
    if password_error:
        return render_template("auth/reset.html", errors={"password": password_error}, token=token), 422
    user.set_password(password)
    audit.record("account.password_reset", f"user:{user.id}")
    db.session.commit()
    flash("Your password has been set. You can log in now.", "success")
    return redirect(url_for("public.login"))
