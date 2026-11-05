"""Live updates must reach only the people entitled to them."""

from flask import g

from messec.extensions import db, socketio
from messec.services import incidents


def socket_for(app, flask_client):
    g.pop("_login_user", None)  # see the note in conftest.py
    socket = socketio.test_client(app, flask_test_client=flask_client)
    g.pop("_login_user", None)
    return socket


def events(socket, name):
    return [e["args"][0] for e in socket.get_received() if e["name"] == name]


def test_anonymous_socket_is_refused(app, client):
    assert not socket_for(app, client).is_connected()


def test_new_signal_goes_to_staff_only(app, login, dispatcher, citizen, make_station, staff_of, new_signal):
    station = make_station()
    desk = socket_for(app, login(dispatcher))
    other_citizen = socket_for(app, login(citizen))
    station_socket = socket_for(app, login(staff_of(station)))

    new_signal()

    assert len(events(desk, "incident_update")) == 1
    assert events(station_socket, "incident_update") == []
    assert events(other_citizen, "alert_update") == []


def test_citizen_details_reach_only_the_responsible_station(app, login, dispatcher, citizen, make_station, staff_of,
                                                            new_signal):
    assigned, other = make_station("Assigned"), make_station("Other")
    assigned_socket = socket_for(app, login(staff_of(assigned)))
    other_socket = socket_for(app, login(staff_of(other)))

    signal = new_signal(citizen_id=citizen.id)
    incidents.assign(signal, assigned, actor=dispatcher)

    received = events(assigned_socket, "incident_update")
    assert received[0]["signal"]["citizen"]["medical_notes"] == "Asthma"
    assert events(other_socket, "incident_update") == []


def test_reassign_tells_old_station_to_remove_it(app, login, dispatcher, make_station, staff_of, new_signal):
    first, second = make_station("First"), make_station("Second")
    first_socket = socket_for(app, login(staff_of(first)))
    signal = new_signal()
    incidents.assign(signal, first, actor=dispatcher)
    first_socket.get_received()

    incidents.assign(signal, second, actor=dispatcher)

    assert events(first_socket, "incident_removed") == [{"signal_id": signal.public_id}]


def test_citizen_gets_reduced_view_of_own_alert_only(app, login, citizen, make_user, new_signal):
    mine = socket_for(app, login(citizen))
    other = socket_for(app, login(make_user("citizen")))

    new_signal(citizen_id=citizen.id)

    received = events(mine, "alert_update")
    assert len(received) == 1
    assert "citizen" not in received[0] and "caller_id" not in received[0]
    assert events(other, "alert_update") == []


def test_stations_update_is_staff_only(app, login, dispatcher, make_station, staff_of, new_signal):
    station = make_station()
    desk = socket_for(app, login(dispatcher))
    staff = socket_for(app, login(staff_of(station)))
    new_signal()
    assert events(desk, "stations_update")
    assert events(staff, "stations_update") == []


def test_suspended_station_is_disconnected(app, login, admin, make_station, staff_of):
    station = make_station()
    staff_client = login(staff_of(station))
    staff_socket = socket_for(app, staff_client)
    assert staff_socket.is_connected()

    login(admin).post(f"/admin/stations/{station.id}/suspend")

    db.session.expire_all()
    assert station.status == "suspended"
    assert not staff_socket.is_connected()
    assert not socket_for(app, staff_client).is_connected()  # cannot reconnect either
