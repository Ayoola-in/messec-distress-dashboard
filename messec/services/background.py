"""Background loops: the auto-assign sweep and the demo signal simulator."""

import random
import time

from ..extensions import db
from ..models import OPEN_STATUSES, SEVERITIES, Signal
from . import incidents

MAX_OPEN_SIMULATED = 20

SIMULATED_TYPES = (
    "Medical Emergency", "Fire Outbreak", "Security Breach", "Vehicle Accident",
    "Armed Robbery", "Kidnapping Alert", "Flash Flooding", "Missing Person",
)
SEVERITY_WEIGHTS = (0.15, 0.30, 0.35, 0.20)
LAT_RANGE, LNG_RANGE = (6.40, 6.65), (3.20, 3.55)


def escalation_loop(app):
    interval = app.config["MESSEC_SWEEP_SECONDS"]
    while True:
        time.sleep(interval)
        with app.app_context():
            try:
                incidents.auto_assign_overdue()
            except Exception:  # the safety net must survive a database hiccup
                app.logger.exception("Auto-assign sweep failed")
                db.session.rollback()
            finally:
                db.session.remove()


def simulated_signal_fields():
    return {
        "type": random.choice(SIMULATED_TYPES),
        "severity": random.choices(SEVERITIES, weights=SEVERITY_WEIGHTS, k=1)[0],
        "source": "simulated",
        "is_simulated": True,
        "lat": round(random.uniform(*LAT_RANGE), 6),
        "lng": round(random.uniform(*LNG_RANGE), 6),
        "caller_id": "Simulated",
        "frequency": round(random.uniform(88.0, 108.0), 2),
        "signal_strength": random.randint(40, 99),
    }


def simulator_loop(app):
    while True:
        time.sleep(random.uniform(20, 45))
        with app.app_context():
            try:
                open_count = Signal.query.filter(
                    Signal.is_simulated.is_(True), Signal.status.in_(OPEN_STATUSES)
                ).count()
                if open_count < MAX_OPEN_SIMULATED:
                    incidents.create_signal(**simulated_signal_fields())
            except Exception:
                app.logger.exception("Simulator failed")
                db.session.rollback()
            finally:
                db.session.remove()
