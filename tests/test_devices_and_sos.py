"""Beacon ingest and the citizen SOS."""

import pytest

from messec.extensions import db
from messec.models import Device, Signal
from messec.services import accounts

GOOD = {"lat": 6.5244, "lng": 3.3792, "device_id": "ESP32-01", "caller_id": "0803 000 0000",
        "type": "Security Breach", "severity": "CRITICAL", "signal_strength": 78, "frequency": 2437,
        "satellites": 7, "hdop": 0.9}


def ingest(client, payload=None, key="shared-key"):
    headers = {"X-API-Key": key} if key else {}
    return client.post("/api/signals/ingest", json=GOOD if payload is None else payload, headers=headers)


def link_device(user, revoked=False):
    key = accounts.new_device_key()
    device = Device(device_uid="BCN-TEST", label="Bag", owner=user, key_hash=accounts.hash_device_key(key))
    db.session.add(device)
    db.session.commit()
    return device, key


def test_shared_key_creates_unlinked_signal(client):
    response = ingest(client)
    assert response.status_code == 201
    signal = Signal.query.one()
    assert signal.source == "unlinked_device"
    assert signal.status == "unassigned"
    assert signal.citizen_id is None
    assert signal.device_uid == "ESP32-01"
    assert signal.severity == "CRITICAL"
    assert signal.satellites == 7


@pytest.mark.parametrize("key", [None, "wrong"])
def test_bad_key_rejected(client, key):
    assert ingest(client, key=key).status_code == 401
    assert Signal.query.count() == 0


def test_no_shared_key_configured_rejects_everything(app, client):
    app.config["MESSEC_DEVICE_KEY"] = ""
    assert ingest(client, key=None).status_code == 401
    assert ingest(client, key="").status_code == 401


def test_linked_device_attaches_owner(client, citizen):
    device, key = link_device(citizen)
    assert ingest(client, key=key).status_code == 201
    signal = Signal.query.one()
    assert signal.source == "device"
    assert signal.citizen_id == citizen.id
    assert signal.caller_id == citizen.phone  # the owner's real number, not what the device claims
    assert signal.device_uid == "BCN-TEST"
    assert device.last_seen_at is not None


def test_revoked_device_rejected(client, citizen):
    device, key = link_device(citizen)
    login_client = client
    device.revoked_at = device.created_at
    db.session.commit()
    assert ingest(login_client, key=key).status_code == 401


def test_only_key_hash_is_stored(login, citizen):
    response = login(citizen).post("/me/devices", data={"label": "My bag"})
    assert response.status_code == 200
    device = Device.query.one()
    assert device.key_hash.encode() not in response.data  # the hash never leaves the server
    body = response.data.decode()
    key = body[body.index("msk_"):].split("<")[0].strip()
    assert accounts.hash_device_key(key) == device.key_hash
    assert b"msk_" not in login(citizen).get("/me").data  # shown only once


@pytest.mark.parametrize("payload,status", [
    ({"lng": 3.3}, 422), ({"lat": 6.5}, 422), ({"lat": 0, "lng": 0}, 422),
    ({"lat": 91, "lng": 3}, 422), ({"lat": "x", "lng": "y"}, 422),
    ({"lat": float("nan"), "lng": 3}, 422), ([], 400),
])
def test_bad_payloads(client, payload, status):
    assert ingest(client, payload).status_code == status


def test_non_json_body(client):
    response = client.post("/api/signals/ingest", data="hello", headers={"X-API-Key": "shared-key"})
    assert response.status_code == 400


def test_odd_optional_fields_are_tolerated(client):
    response = ingest(client, {"lat": 6.5, "lng": 3.4, "severity": "nonsense", "signal_strength": 500,
                               "frequency": "abc", "satellites": None, "type": "x" * 500})
    assert response.status_code == 201
    signal = Signal.query.one()
    assert signal.severity == "HIGH"
    assert signal.signal_strength == 100
    assert signal.frequency is None
    assert len(signal.type) == 60


def test_ingest_works_with_csrf_enabled(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    assert ingest(client).status_code == 201


def test_sos_creates_alert_with_citizen_details(login, citizen):
    me = login(citizen)
    response = me.post("/api/sos", json={"lat": 6.5, "lng": 3.4, "type": "Medical Emergency", "description": "Help"})
    assert response.status_code == 201
    signal = Signal.query.one()
    assert signal.source == "web_sos"
    assert signal.severity == "CRITICAL"
    assert signal.citizen_id == citizen.id
    assert signal.caller_id == citizen.phone


def test_sos_needs_location(login, citizen):
    assert login(citizen).post("/api/sos", json={"type": "Fire Outbreak"}).status_code == 422
    assert Signal.query.count() == 0


def test_repeat_sos_returns_open_alert(login, citizen):
    me = login(citizen)
    first = me.post("/api/sos", json={"lat": 6.5, "lng": 3.4})
    second = me.post("/api/sos", json={"lat": 6.5, "lng": 3.4})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json["already_open"] is True
    assert Signal.query.count() == 1


def test_only_citizens_can_sos(login, dispatcher):
    assert login(dispatcher).post("/api/sos", json={"lat": 6.5, "lng": 3.4}).status_code == 403


def test_citizen_view_hides_responder_data(login, citizen, new_signal, make_station, dispatcher):
    from messec.services import incidents
    signal = new_signal(citizen_id=citizen.id)
    incidents.assign(signal, make_station(), actor=dispatcher)
    alert = login(citizen).get("/api/me/alerts").json["alerts"][0]
    assert alert["status"] == "assigned"
    assert alert["station"]["name"] == "Eko Command"
    assert "citizen" not in alert and "caller_id" not in alert and "device_id" not in alert


def test_simulation_off_by_default(login, dispatcher):
    assert login(dispatcher).post("/api/signals/simulate").status_code == 403


def test_simulated_signals_are_flagged(app, login, dispatcher):
    app.config["MESSEC_SIMULATE"] = True
    response = login(dispatcher).post("/api/signals/simulate")
    assert response.status_code == 201
    assert response.json["signal"]["is_simulated"] is True
