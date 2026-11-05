"""The console station staff use to receive and work their incidents."""

from flask import Blueprint, render_template
from flask_login import current_user

from ..auth import roles_required
from ..models import STATION

bp = Blueprint("station", __name__)


@bp.route("/station")
@roles_required(STATION)
def console():
    return render_template("station.html", station=current_user.station)
