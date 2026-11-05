import os

import pytest
from flask import g

from messec import create_app
from messec.extensions import db
from messec.models import ADMIN, APPROVED, CITIZEN, CitizenProfile, DISPATCHER, STATION, Station, User, utcnow

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Real hashing is deliberately slow; tests hash hundreds of passwords."""
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr("messec.models.generate_password_hash",
                        lambda password: generate_password_hash(password, method="pbkdf2:sha256:1"))


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        # Set TEST_DATABASE_URL to run the suite against PostgreSQL.
        "SQLALCHEMY_DATABASE_URI": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}",
        "SQLALCHEMY_ENGINE_OPTIONS": {},
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "MESSEC_DEVICE_KEY": "shared-key",
        "MESSEC_AUTO_ASSIGN_SECONDS": 60,
    })

    # Tests hold one app context open to use the database directly, so requests share `g`.
    # Clear Flask-Login's cached user so each client is seen as itself.
    @app.before_request
    def forget_cached_user():
        g.pop("_login_user", None)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_station(app):
    def _make(name="Eko Command", kind="police", lat=6.52, lng=3.38, status=APPROVED):
        station = Station(name=name, type=kind, status=status, zone="Lagos", address="1 Road", lat=lat, lng=lng,
                          phone="0803 000 0000")
        db.session.add(station)
        db.session.commit()
        return station
    return _make


@pytest.fixture
def make_user(app):
    def _make(role, email=None, station=None):
        email = email or f"{role}-{User.query.count()}@example.com"
        user = User(email=email, full_name=f"{role.title()} Person", phone="0803 111 1111", role=role,
                    station=station, consent_at=utcnow() if role == CITIZEN else None)
        user.set_password(PASSWORD)
        if role == CITIZEN:
            user.profile = CitizenProfile(address="2 Street", blood_group="O+", medical_notes="Asthma")
        db.session.add(user)
        db.session.commit()
        return user
    return _make


@pytest.fixture
def login(app):
    """Return a fresh test client logged in as the given user."""
    def _login(user):
        client = app.test_client()
        response = client.post("/login", data={"email": user.email, "password": PASSWORD})
        assert response.status_code == 302, response.data
        return client
    return _login


@pytest.fixture
def admin(make_user):
    return make_user(ADMIN)


@pytest.fixture
def dispatcher(make_user):
    return make_user(DISPATCHER)


@pytest.fixture
def citizen(make_user):
    return make_user(CITIZEN)


@pytest.fixture
def staff_of(make_user):
    def _make(station):
        return make_user(STATION, station=station)
    return _make


@pytest.fixture
def new_signal(app):
    from messec.services import incidents

    def _make(**overrides):
        fields = {"type": "Armed Robbery", "severity": "HIGH", "source": "device", "lat": 6.5, "lng": 3.4,
                  "caller_id": "0803 222 2222"}
        fields.update(overrides)
        return incidents.create_signal(**fields)
    return _make
