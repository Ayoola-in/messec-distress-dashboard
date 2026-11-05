"""Database models. All datetimes are stored as naive UTC."""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

# Roles
ADMIN, DISPATCHER, STATION, CITIZEN = "admin", "dispatcher", "station", "citizen"
STAFF_ROLES = (ADMIN, DISPATCHER)
ROLE_LABELS = {ADMIN: "Admin", DISPATCHER: "Dispatcher", STATION: "Station staff", CITIZEN: "Citizen"}

# Station types and lifecycle
STATION_TYPES = {
    "police": "Police station",
    "hospital": "Hospital",
    "fire": "Fire service",
    "ambulance": "Ambulance service",
    "other": "Other body",
}
PENDING, APPROVED, REJECTED, SUSPENDED = "pending", "approved", "rejected", "suspended"

# Signal lifecycle, in the order an incident moves through it
UNASSIGNED, ASSIGNED, ACCEPTED, EN_ROUTE, ON_SCENE, RESOLVED = (
    "unassigned", "assigned", "accepted", "en_route", "on_scene", "resolved",
)
SIGNAL_STATUSES = (UNASSIGNED, ASSIGNED, ACCEPTED, EN_ROUTE, ON_SCENE, RESOLVED)
STATUS_LABELS = {
    UNASSIGNED: "Unassigned",
    ASSIGNED: "Awaiting station",
    ACCEPTED: "Accepted",
    EN_ROUTE: "En route",
    ON_SCENE: "On scene",
    RESOLVED: "Resolved",
}
OPEN_STATUSES = (UNASSIGNED, ASSIGNED, ACCEPTED, EN_ROUTE, ON_SCENE)
ACTIVE_ASSIGNMENT = (ASSIGNED, ACCEPTED, EN_ROUTE, ON_SCENE)

# Assignment end states that also end the station's access to the incident
DECLINED, REASSIGNED, WITHDRAWN = "declined", "reassigned", "withdrawn"
LOST_ACCESS = (DECLINED, REASSIGNED, WITHDRAWN)

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

SOURCE_LABELS = {
    "device": "Linked beacon",
    "unlinked_device": "Unlinked beacon",
    "web_sos": "Phone SOS",
    "simulated": "Simulated",
}

# Incident types offered in the citizen SOS form, with their severity
SOS_TYPES = {
    "Medical Emergency": "CRITICAL",
    "Fire Outbreak": "CRITICAL",
    "Armed Robbery": "CRITICAL",
    "Kidnapping Alert": "CRITICAL",
    "Security Breach": "CRITICAL",
    "Vehicle Accident": "HIGH",
    "Flash Flooding": "HIGH",
    "Missing Person": "MEDIUM",
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    role = db.Column(db.String(20), nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    consent_at = db.Column(db.DateTime)
    deleted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    station = db.relationship("Station", back_populates="staff")
    profile = db.relationship("CitizenProfile", uselist=False, back_populates="user", cascade="all, delete-orphan")
    contacts = db.relationship("EmergencyContact", back_populates="user", cascade="all, delete-orphan",
                               order_by="EmergencyContact.id")
    devices = db.relationship("Device", back_populates="owner", cascade="all, delete-orphan", order_by="Device.id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        """Flask-Login treats inactive users as logged out on every request."""
        if not self.enabled or self.deleted_at:
            return False
        if self.role == STATION:
            return bool(self.station and self.station.status == APPROVED)
        return True

    def get_id(self):
        # Including part of the hash signs out other sessions on password change.
        return f"{self.id}:{self.password_hash[-12:]}"

    @property
    def is_staff(self):
        return self.role in STAFF_ROLES

    @property
    def initials(self):
        parts = self.full_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or "?"


@login_manager.user_loader
def load_user(session_id):
    user_id, _, fingerprint = str(session_id).partition(":")
    if not user_id.isdigit():
        return None
    user = db.session.get(User, int(user_id))
    if user is None or user.password_hash[-12:] != fingerprint:
        return None
    return user


class CitizenProfile(db.Model):
    __tablename__ = "citizen_profiles"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    address = db.Column(db.String(255))
    blood_group = db.Column(db.String(5))
    medical_notes = db.Column(db.Text)

    user = db.relationship("User", back_populates="profile")


class EmergencyContact(db.Model):
    __tablename__ = "emergency_contacts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    relationship_label = db.Column(db.String(60))

    user = db.relationship("User", back_populates="contacts")


class Station(db.Model):
    __tablename__ = "stations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=PENDING, index=True)
    zone = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    phone = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(254))
    contact_name = db.Column(db.String(120))
    registration_ref = db.Column(db.String(80))
    review_note = db.Column(db.String(255))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    staff = db.relationship("User", back_populates="station")

    @property
    def code(self):
        return f"ST-{self.id:04d}"

    @property
    def type_label(self):
        return STATION_TYPES.get(self.type, self.type)


class Device(db.Model):
    __tablename__ = "devices"

    id = db.Column(db.Integer, primary_key=True)
    device_uid = db.Column(db.String(40), unique=True, nullable=False)
    label = db.Column(db.String(80), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    key_hash = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)

    owner = db.relationship("User", back_populates="devices")


class Signal(db.Model):
    """One distress signal and the incident that follows from it."""

    __tablename__ = "signals"

    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(60), nullable=False)
    severity = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=UNASSIGNED, index=True)
    source = db.Column(db.String(20), nullable=False)  # device | unlinked_device | web_sos | simulated
    is_simulated = db.Column(db.Boolean, nullable=False, default=False)
    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(500))
    caller_id = db.Column(db.String(60))
    device_uid = db.Column(db.String(40))
    citizen_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    current_station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), index=True)
    frequency = db.Column(db.Float)
    signal_strength = db.Column(db.Integer)
    satellites = db.Column(db.Integer)
    hdop = db.Column(db.Float)
    received_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    unassigned_since = db.Column(db.DateTime)
    auto_assign_failed_at = db.Column(db.DateTime)
    resolved_at = db.Column(db.DateTime)

    citizen = db.relationship("User", foreign_keys=[citizen_id])
    current_station = db.relationship("Station", foreign_keys=[current_station_id])
    assignments = db.relationship("Assignment", back_populates="signal", order_by="Assignment.id",
                                  cascade="all, delete-orphan")
    events = db.relationship("IncidentEvent", back_populates="signal", order_by="IncidentEvent.id",
                             cascade="all, delete-orphan")

    # Optimistic lock: a commit based on a stale read raises StaleDataError, so a
    # dispatcher and the auto-assign sweep cannot both assign the same signal.
    __mapper_args__ = {"version_id_col": version}

    @property
    def public_id(self):
        return f"SIG-{self.id:04d}"

    @property
    def status_label(self):
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def source_label(self):
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def current_assignment(self):
        """The assignment now responsible, or the one that resolved the incident."""
        for assignment in reversed(self.assignments):
            if assignment.status in ACTIVE_ASSIGNMENT or assignment.status == RESOLVED:
                return assignment
        return None


class Assignment(db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    signal_id = db.Column(db.Integer, db.ForeignKey("signals.id"), nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    method = db.Column(db.String(10), nullable=False)  # manual | auto
    status = db.Column(db.String(20), nullable=False, default=ASSIGNED, index=True)
    distance_km = db.Column(db.Float)
    eta_minutes = db.Column(db.Integer)
    decline_reason = db.Column(db.String(255))
    assigned_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    accepted_at = db.Column(db.DateTime)
    ended_at = db.Column(db.DateTime)

    signal = db.relationship("Signal", back_populates="assignments")
    station = db.relationship("Station")
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])


class IncidentEvent(db.Model):
    """Timeline entry for an incident. Notes are events with kind='note'."""

    __tablename__ = "incident_events"

    id = db.Column(db.Integer, primary_key=True)
    signal_id = db.Column(db.Integer, db.ForeignKey("signals.id"), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    kind = db.Column(db.String(20), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    signal = db.relationship("Signal", back_populates="events")
    actor = db.relationship("User", foreign_keys=[actor_id])


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False, index=True)
    target = db.Column(db.String(60))
    detail = db.Column(db.String(500))
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    actor = db.relationship("User", foreign_keys=[actor_id])
