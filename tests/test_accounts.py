"""Registration, approval, login, roles and citizen data rights."""

import pytest

from messec.extensions import db
from messec.models import (
    APPROVED, AuditLog, CITIZEN, Device, DISPATCHER, EmergencyContact, PENDING, Signal, Station, User,
)
from messec.services import accounts, incidents

from .conftest import PASSWORD

STATION_FORM = {
    "name": "Ikeja Division", "type": "police", "zone": "Ikeja", "address": "1 Obafemi Awolowo Way",
    "phone": "0803 000 0000", "lat": "6.6", "lng": "3.35",
    "login_name": "Ada Officer", "login_email": "ada@police.example", "password": PASSWORD, "confirm": PASSWORD,
}


def apply(client, **overrides):
    return client.post("/stations/apply", data={**STATION_FORM, **overrides})


def test_station_application_starts_pending_and_cannot_log_in(client):
    assert apply(client).status_code == 302
    station = Station.query.one()
    assert station.status == PENDING
    response = client.post("/login", data={"email": "ada@police.example", "password": PASSWORD})
    assert response.status_code == 403
    assert b"waiting for approval" in response.data


def test_wrong_password_for_pending_station_reveals_nothing(client):
    apply(client)
    response = client.post("/login", data={"email": "ada@police.example", "password": "wrong-password"})
    assert response.status_code == 401
    assert b"approval" not in response.data


def test_pending_station_is_not_a_candidate(client, login, dispatcher, new_signal):
    apply(client)
    signal = new_signal()
    assert login(dispatcher).get(f"/api/signals/{signal.public_id}/candidates").json["stations"] == []


def test_admin_approval_lets_station_log_in(client, login, admin):
    apply(client)
    station = Station.query.one()
    assert login(admin).post(f"/admin/stations/{station.id}/approve").status_code == 302
    db.session.expire_all()
    assert station.status == APPROVED
    response = client.post("/login", data={"email": "ada@police.example", "password": PASSWORD})
    assert response.status_code == 302
    assert client.get("/station").status_code == 200
    assert AuditLog.query.filter_by(action="station.approve").count() == 1


@pytest.mark.parametrize("field,value", [
    ("type", "bank"), ("lat", "999"), ("lat", ""), ("phone", "abc"), ("name", ""),
    ("login_email", "not-an-email"), ("password", "short"),
])
def test_invalid_applications_are_rejected(client, field, value):
    overrides = {field: value}
    if field == "password":
        overrides["confirm"] = value
    assert apply(client, **overrides).status_code == 422
    assert Station.query.count() == 0


def test_duplicate_email_is_rejected(client, citizen):
    assert apply(client, login_email=citizen.email).status_code == 422


def test_non_admin_cannot_approve(client, login, dispatcher):
    apply(client)
    station = Station.query.one()
    assert login(dispatcher).post(f"/admin/stations/{station.id}/approve").status_code == 403
    db.session.expire_all()
    assert station.status == PENDING


def test_suspending_returns_incidents_and_blocks_access(login, admin, dispatcher, make_station, staff_of, new_signal):
    station = make_station()
    staff = login(staff_of(station))
    signal = new_signal()
    incidents.assign(signal, station, actor=dispatcher)
    assert staff.get("/station").status_code == 200

    login(admin).post(f"/admin/stations/{station.id}/suspend")
    db.session.expire_all()
    assert signal.status == "unassigned"
    assert signal.assignments[0].status == "withdrawn"
    assert staff.get("/station").status_code == 302  # session no longer counts as logged in


def test_admin_can_add_station_directly(login, admin):
    data = {k: v for k, v in STATION_FORM.items() if k not in ("login_name", "login_email", "password", "confirm")}
    assert login(admin).post("/admin/stations/new", data=data).status_code == 302
    assert Station.query.one().status == APPROVED


def test_admin_created_user_sets_password_through_link(client, login, admin):
    response = login(admin).post("/admin/users", data={
        "full_name": "Desk Officer", "email": "desk@example.com", "role": DISPATCHER,
    })
    assert response.status_code == 200
    user = User.query.filter_by(email="desk@example.com").one()
    token = accounts.make_reset_token(user)
    assert client.post(f"/reset/{token}", data={"password": PASSWORD, "confirm": PASSWORD}).status_code == 302
    assert client.post("/login", data={"email": user.email, "password": PASSWORD}).status_code == 302
    # The link is single-use.
    assert client.get(f"/reset/{token}").status_code == 400


def test_admin_cannot_create_citizen_or_staff_for_unapproved_station(login, admin, make_station):
    desk = login(admin)
    pending = make_station(status=PENDING)
    assert desk.post("/admin/users", data={"full_name": "X Y", "email": "a@b.co", "role": CITIZEN}).status_code == 422
    assert desk.post("/admin/users", data={"full_name": "X Y", "email": "a@b.co", "role": "station",
                                           "station_id": pending.id}).status_code == 422


def test_disabled_user_loses_access(login, admin, dispatcher):
    desk = login(dispatcher)
    assert desk.get("/dispatch").status_code == 200
    login(admin).post(f"/admin/users/{dispatcher.id}/disable")
    assert desk.get("/dispatch").status_code == 302


def test_admin_cannot_disable_self(login, admin):
    login(admin).post(f"/admin/users/{admin.id}/disable")
    db.session.expire_all()
    assert admin.enabled is True


def test_password_change_signs_out_other_sessions(login, dispatcher):
    first, second = login(dispatcher), login(dispatcher)
    new_password = "another-long-password"
    response = first.post("/account/password", data={
        "current": PASSWORD, "password": new_password, "confirm": new_password,
    })
    assert response.status_code == 302
    assert first.get("/dispatch").status_code == 200
    assert second.get("/dispatch").status_code == 302


def test_login_does_not_follow_external_next(client, dispatcher):
    response = client.post("/login", data={"email": dispatcher.email, "password": PASSWORD,
                                           "next": "https://evil.example/"})
    assert "evil" not in response.headers["Location"]
    client.post("/logout")
    response = client.post("/login", data={"email": dispatcher.email, "password": PASSWORD, "next": "//evil.example"})
    assert "evil" not in response.headers["Location"]


def test_citizen_registration_requires_consent(client):
    data = {"full_name": "Bola A", "email": "bola@example.com", "phone": "0803 000 0000",
            "password": PASSWORD, "confirm": PASSWORD}
    assert client.post("/register", data=data).status_code == 422
    assert client.post("/register", data={**data, "consent": "on"}).status_code == 302
    user = User.query.filter_by(email="bola@example.com").one()
    assert user.role == CITIZEN
    assert user.consent_at is not None
    assert user.password_hash != PASSWORD


@pytest.mark.parametrize("path,allowed", [
    ("/dispatch", {"admin", "dispatcher"}),
    ("/admin/stations", {"admin"}),
    ("/admin/users", {"admin"}),
    ("/admin/audit", {"admin"}),
    ("/station", {"station"}),
    ("/me", {"citizen"}),
    ("/incidents", {"admin", "dispatcher", "station"}),
    ("/api/state", {"admin", "dispatcher"}),
])
def test_role_access_matrix(client, login, make_user, make_station, path, allowed):
    station = make_station()
    assert client.get(path).status_code in (302, 401)  # anonymous
    for role in ("admin", "dispatcher", "station", "citizen"):
        user = make_user(role, station=station if role == "station" else None)
        status = login(user).get(path).status_code
        assert (status == 200) == (role in allowed), f"{role} on {path} got {status}"


def test_history_is_scoped_to_station(login, dispatcher, make_station, staff_of, new_signal):
    mine, other = make_station("Mine"), make_station("Other")
    incidents.assign(new_signal(type="Mine Type"), mine, actor=dispatcher)
    incidents.assign(new_signal(type="Other Type"), other, actor=dispatcher)
    staff = login(staff_of(mine))
    page = staff.get("/incidents").data
    assert b"Mine Type" in page and b"Other Type" not in page
    export = staff.get("/incidents/export.csv").data
    assert b"Mine Type" in export and b"Other Type" not in export
    assert b"Other Type" in login(dispatcher).get("/incidents/export.csv").data


def test_csv_escapes_formulas(login, dispatcher, new_signal):
    new_signal(caller_id="=HYPERLINK(1)")
    export = login(dispatcher).get("/incidents/export.csv").data
    assert b"'=HYPERLINK(1)" in export


def test_history_search_treats_wildcards_literally(login, dispatcher, new_signal):
    new_signal(type="Fire Outbreak")
    desk = login(dispatcher)
    assert b"Fire Outbreak" in desk.get("/incidents?q=fire").data
    assert b"Fire Outbreak" not in desk.get("/incidents?q=%25").data


def test_citizen_export_contains_only_own_data(login, citizen, make_user, new_signal):
    other = make_user(CITIZEN)
    db.session.add(EmergencyContact(user=citizen, name="Mum", phone="0803 333 3333"))
    db.session.commit()
    new_signal(citizen_id=citizen.id, type="Mine")
    new_signal(citizen_id=other.id, type="Theirs")
    data = login(citizen).get("/me/export.json").json
    assert data["account"]["email"] == citizen.email
    assert data["emergency_contacts"][0]["name"] == "Mum"
    assert [a["type"] for a in data["alerts"]] == ["Mine"]


def test_citizen_cannot_touch_others_contacts_or_devices(login, citizen, make_user):
    other = make_user(CITIZEN)
    contact = EmergencyContact(user=other, name="Theirs", phone="0803 333 3333")
    device = Device(device_uid="BCN-1", label="x", owner=other, key_hash="a" * 64)
    db.session.add_all([contact, device])
    db.session.commit()
    me = login(citizen)
    assert me.post(f"/me/contacts/{contact.id}/delete").status_code == 404
    assert me.post(f"/me/devices/{device.id}/revoke").status_code == 404


def test_delete_account_anonymises_and_keeps_incident(login, citizen, new_signal, dispatcher):
    db.session.add(EmergencyContact(user=citizen, name="Mum", phone="0803 333 3333"))
    db.session.commit()
    signal = new_signal(citizen_id=citizen.id, description="Near my house", caller_id=citizen.phone)
    incidents.close(signal, dispatcher, "Handled")
    email = citizen.email
    me = login(citizen)

    assert me.post("/me/delete", data={"password": "wrong-password"}).status_code == 302
    assert User.query.filter_by(email=email).count() == 1

    assert me.post("/me/delete", data={"password": PASSWORD}).status_code == 302
    assert User.query.filter_by(email=email).count() == 0
    assert EmergencyContact.query.count() == 0
    db.session.expire_all()
    kept = db.session.get(Signal, signal.id)
    assert kept.citizen_id is None
    assert kept.caller_id == "[deleted]"
    assert kept.description is None
    assert me.get("/me").status_code == 302


def test_delete_blocked_during_open_incident(login, citizen, new_signal):
    new_signal(citizen_id=citizen.id)
    email = citizen.email
    login(citizen).post("/me/delete", data={"password": PASSWORD})
    assert User.query.filter_by(email=email).count() == 1


def test_security_headers_present(client):
    response = client.get("/")
    assert "Content-Security-Policy" in response.headers
    assert response.headers["X-Frame-Options"] == "DENY"


def test_csrf_blocks_forms_when_enabled(app, dispatcher):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    assert client.post("/login", data={"email": dispatcher.email, "password": PASSWORD}).status_code == 400


def test_production_requires_secret_key(monkeypatch, tmp_path):
    from messec.config import ConfigError, build_config
    monkeypatch.setenv("MESSEC_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ConfigError):
        build_config(tmp_path)


def test_production_refuses_example_device_key(monkeypatch, tmp_path):
    from messec.config import ConfigError, build_config
    monkeypatch.setenv("MESSEC_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("MESSEC_DEVICE_KEY", "change-me")
    with pytest.raises(ConfigError):
        build_config(tmp_path)


def test_postgres_url_is_normalised(monkeypatch, tmp_path):
    from messec.config import build_config
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")
    assert build_config(tmp_path)["SQLALCHEMY_DATABASE_URI"] == "postgresql+psycopg://u:p@host/db"
