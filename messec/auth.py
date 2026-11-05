"""Access rules shared by the views."""

from functools import wraps

from flask import abort, current_app, jsonify, request, url_for
from flask_login import current_user

from .models import CITIZEN, LOST_ACCESS, STATION


def roles_required(*roles):
    """Allow only logged-in users with one of these roles."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if current_user.role not in roles:
                if request.path.startswith("/api/"):
                    return jsonify(error="You don't have access to this."), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def can_view_incident(user, signal):
    """Staff see everything; a station sees incidents it holds or resolved; citizens see their own."""
    if user.is_staff:
        return True
    if user.role == STATION:
        return any(a.station_id == user.station_id and a.status not in LOST_ACCESS for a in signal.assignments)
    if user.role == CITIZEN:
        return signal.citizen_id == user.id
    return False


def role_home(user):
    if user.is_staff:
        return url_for("incidents.dashboard")
    if user.role == STATION:
        return url_for("station.console")
    return url_for("citizen.home")
