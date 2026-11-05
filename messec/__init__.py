"""MesSec: distress signal dispatch platform."""

import threading

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import build_config
from .extensions import csrf, db, limiter, login_manager, migrate, socketio

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com https://cdn.socket.io https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https://tile.openstreetmap.org https://unpkg.com",
    "connect-src 'self' ws: wss:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_app(overrides=None):
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
        instance_relative_config=True,
    )
    app.config.update(build_config(app.instance_path))
    if overrides:
        app.config.update(overrides)

    if app.config["MESSEC_TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.session_protection = "basic"
    login_manager.login_view = "public.login"
    login_manager.login_message = "Please log in to continue."
    socketio.init_app(app)

    from . import models  # noqa: F401  (registers the tables with SQLAlchemy)
    from .services import realtime  # noqa: F401  (registers the socket handlers)
    from .views import admin, api, citizen, incidents, public, station

    for blueprint in (public.bp, incidents.bp, station.bp, citizen.bp, admin.bp, api.bp):
        app.register_blueprint(blueprint)

    _register_error_handlers(app)
    _register_template_helpers(app)
    _register_security_headers(app)

    from .cli import register_commands
    register_commands(app)

    return app


def _wants_json():
    return request.path.startswith("/api/")


def _register_error_handlers(app):
    @login_manager.unauthorized_handler
    def unauthorized():
        if _wants_json():
            return jsonify(error="Please log in."), 401
        return redirect(url_for("public.login", next=request.full_path.rstrip("?")))

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        message = "Your session expired. Reload the page and try again."
        if _wants_json():
            return jsonify(error=message), 400
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        if _wants_json():
            return jsonify(error=error.description), error.code
        messages = {
            403: "You don't have access to this page.",
            404: "This page doesn't exist.",
            429: "Too many attempts. Please wait a moment and try again.",
        }
        return render_template("error.html", code=error.code,
                               message=messages.get(error.code, error.description)), error.code

    @app.errorhandler(Exception)
    def server_error(error):
        app.logger.exception("Unhandled error", exc_info=error)
        db.session.rollback()
        message = "Something went wrong on our side."
        if _wants_json():
            return jsonify(error=message), 500
        return render_template("error.html", code=500, message=message), 500


def _register_template_helpers(app):
    from .models import ROLE_LABELS, STATION_TYPES, STATUS_LABELS, iso

    @app.context_processor
    def inject_constants():
        return {
            "ROLE_LABELS": ROLE_LABELS,
            "STATION_TYPES": STATION_TYPES,
            "STATUS_LABELS": STATUS_LABELS,
            "simulate_on": app.config["MESSEC_SIMULATE"],
        }

    app.jinja_env.filters["iso"] = iso

    @app.template_filter("when")
    def when(value):
        return value.strftime("%d %b %Y, %H:%M UTC") if value else "—"


def _register_security_headers(app):
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        # OpenStreetMap rejects tile requests that have no Referer, so "same-origin" blanks the map.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        if app.config["MESSEC_PRODUCTION"]:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def start_background_tasks(app):
    """Run the auto-assign sweep, and the simulator in demo mode. Call once per server process."""
    from .services.background import escalation_loop, simulator_loop

    threading.Thread(target=escalation_loop, args=(app,), daemon=True, name="messec-escalation").start()
    if app.config["MESSEC_SIMULATE"]:
        threading.Thread(target=simulator_loop, args=(app,), daemon=True, name="messec-simulator").start()
