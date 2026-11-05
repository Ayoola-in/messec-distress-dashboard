"""Assignment, status rules and the auto-assign safety net."""

from datetime import timedelta

import pytest

from messec.extensions import db
from messec.models import PENDING, Signal, utcnow
from messec.services import incidents
from messec.services.incidents import IncidentError


def assign_via_api(client, signal, station):
    return client.post(f"/api/signals/{signal.public_id}/assign", json={"station_id": station.id})


def test_new_signal_waits_unassigned(new_signal):
    signal = new_signal()
    assert signal.status == "unassigned"
    assert signal.assignments == []
    assert signal.unassigned_since is not None


def test_dispatcher_assigns_chosen_station_not_nearest(login, dispatcher, new_signal, make_station):
    make_station("Near", lat=6.5, lng=3.4)
    far = make_station("Far", lat=6.9, lng=3.9)
    signal = new_signal()
    response = assign_via_api(login(dispatcher), signal, far)
    assert response.status_code == 200
    assert response.json["assignment"]["station"]["name"] == "Far"
    db.session.expire_all()
    assert signal.status == "assigned"
    assert signal.current_station_id == far.id
    assert signal.assignments[0].method == "manual"
    assert signal.assignments[0].assigned_by_id == dispatcher.id


def test_cannot_assign_to_unapproved_station(login, dispatcher, new_signal, make_station):
    pending = make_station("Pending", status=PENDING)
    response = assign_via_api(login(dispatcher), new_signal(), pending)
    assert response.status_code == 422


def test_assign_unknown_station_is_rejected(login, dispatcher, new_signal):
    response = login(dispatcher).post(f"/api/signals/{new_signal().public_id}/assign", json={"station_id": 999})
    assert response.status_code == 422


def test_reassign_removes_old_station_access(login, dispatcher, new_signal, make_station, staff_of):
    first, second = make_station("First"), make_station("Second")
    first_staff = login(staff_of(first))
    desk = login(dispatcher)
    signal = new_signal()
    assign_via_api(desk, signal, first)
    assert first_staff.get(f"/incidents/{signal.public_id}").status_code == 200

    assert assign_via_api(desk, signal, second).status_code == 200
    assert first_staff.get(f"/incidents/{signal.public_id}").status_code == 403
    response = first_staff.post(f"/api/signals/{signal.public_id}/status", json={"status": "accepted"})
    assert response.status_code == 404
    db.session.expire_all()
    assert [a.status for a in signal.assignments] == ["reassigned", "assigned"]


def test_assigning_same_station_twice_conflicts(login, dispatcher, new_signal, make_station):
    station = make_station()
    desk = login(dispatcher)
    signal = new_signal()
    assign_via_api(desk, signal, station)
    assert assign_via_api(desk, signal, station).status_code == 409


def test_concurrent_assignment_only_one_wins(app, new_signal, make_station, dispatcher):
    """Two people act on the same read of a signal: the stale one must lose."""
    first, second = make_station("First"), make_station("Second")
    signal_id = new_signal().id

    other_session = db.session.session_factory()
    try:
        stale = other_session.get(Signal, signal_id)
        assert stale.status == "unassigned"

        incidents.assign(db.session.get(Signal, signal_id), first, actor=dispatcher)

        stale.status = "assigned"
        stale.current_station_id = second.id
        from sqlalchemy.orm.exc import StaleDataError
        with pytest.raises(StaleDataError):
            other_session.commit()
        other_session.rollback()
    finally:
        other_session.close()
    db.session.expire_all()
    assert db.session.get(Signal, signal_id).current_station_id == first.id


def test_station_lifecycle_in_order(login, dispatcher, new_signal, make_station, staff_of):
    station = make_station()
    staff = login(staff_of(station))
    signal = new_signal()
    assign_via_api(login(dispatcher), signal, station)
    url = f"/api/signals/{signal.public_id}/status"

    assert staff.post(url, json={"status": "en_route"}).status_code == 409  # must accept first
    for status in ("accepted", "en_route", "on_scene", "resolved"):
        assert staff.post(url, json={"status": status}).status_code == 200
    assert staff.post(url, json={"status": "accepted"}).status_code == 403  # no longer active
    db.session.expire_all()
    assert signal.status == "resolved"
    assert signal.resolved_at is not None


def test_status_cannot_go_backwards(login, dispatcher, new_signal, make_station, staff_of):
    station = make_station()
    staff = login(staff_of(station))
    signal = new_signal()
    assign_via_api(login(dispatcher), signal, station)
    url = f"/api/signals/{signal.public_id}/status"
    staff.post(url, json={"status": "accepted"})
    staff.post(url, json={"status": "on_scene"})
    assert staff.post(url, json={"status": "en_route"}).status_code == 409


def test_other_station_cannot_touch_incident(login, dispatcher, new_signal, make_station, staff_of):
    mine, other = make_station("Mine"), make_station("Other")
    signal = new_signal()
    assign_via_api(login(dispatcher), signal, mine)
    outsider = login(staff_of(other))
    assert outsider.post(f"/api/signals/{signal.public_id}/status", json={"status": "accepted"}).status_code == 404
    assert outsider.post(f"/api/signals/{signal.public_id}/notes", json={"message": "hi"}).status_code == 404
    assert outsider.get(f"/incidents/{signal.public_id}").status_code == 403


def test_decline_needs_reason_and_requeues(login, dispatcher, new_signal, make_station, staff_of):
    station = make_station()
    staff = login(staff_of(station))
    signal = new_signal()
    assign_via_api(login(dispatcher), signal, station)
    url = f"/api/signals/{signal.public_id}/decline"
    assert staff.post(url, json={"reason": "  "}).status_code == 422
    assert staff.post(url, json={"reason": "No vehicles"}).status_code == 200
    db.session.expire_all()
    assert signal.status == "unassigned"
    assert signal.current_station_id is None
    assert signal.unassigned_since is not None
    assert signal.assignments[0].decline_reason == "No vehicles"
    assert staff.get(f"/incidents/{signal.public_id}").status_code == 403


def test_dispatcher_close_requires_note(login, dispatcher, new_signal):
    desk = login(dispatcher)
    signal = new_signal()
    url = f"/api/signals/{signal.public_id}/close"
    assert desk.post(url, json={"note": ""}).status_code == 422
    assert desk.post(url, json={"note": "False alarm"}).status_code == 200
    db.session.expire_all()
    assert signal.status == "resolved"


def test_recommended_is_nearest_suitable_idle(login, dispatcher, new_signal, make_station):
    make_station("Nearest police", "police", lat=6.5, lng=3.4)
    far_hospital = make_station("Far hospital", "hospital", lat=6.7, lng=3.6)
    signal = new_signal(type="Medical Emergency")
    rows = login(dispatcher).get(f"/api/signals/{signal.public_id}/candidates").json["stations"]
    assert [r["name"] for r in rows] == ["Nearest police", "Far hospital"]  # sorted by distance
    assert [r["id"] for r in rows if r["recommended"]] == [far_hospital.id]


@pytest.mark.parametrize("signal_type,expected", [
    ("Medical Emergency", ("hospital", "ambulance")),
    ("Fire Outbreak", ("fire",)),
    ("Armed Robbery", ("police",)),
    ("Distress Signal", ("police",)),
])
def test_suitable_types(signal_type, expected):
    assert incidents.suitable_types(signal_type) == expected


def overdue(signal, seconds=120):
    signal.unassigned_since = utcnow() - timedelta(seconds=seconds)
    db.session.commit()


def test_overdue_critical_auto_assigns_nearest_suitable(new_signal, make_station):
    make_station("Near police", "police", lat=6.5, lng=3.4)
    hospital = make_station("Hospital", "hospital", lat=6.6, lng=3.5)
    signal = new_signal(type="Medical Emergency", severity="CRITICAL")
    overdue(signal)
    assert incidents.auto_assign_overdue() == 1
    assert signal.current_station_id == hospital.id
    assert signal.assignments[0].method == "auto"


def test_recent_critical_is_left_for_dispatcher(new_signal, make_station):
    make_station()
    new_signal(severity="CRITICAL")
    assert incidents.auto_assign_overdue() == 0


def test_non_critical_never_auto_assigns(new_signal, make_station):
    make_station()
    signal = new_signal(severity="HIGH")
    overdue(signal, 100000)
    assert incidents.auto_assign_overdue() == 0
    assert signal.status == "unassigned"


def test_auto_assign_skips_station_that_declined(login, dispatcher, new_signal, make_station, staff_of):
    near = make_station("Near", lat=6.5, lng=3.4)
    far = make_station("Far", lat=6.8, lng=3.8)
    staff = login(staff_of(near))
    signal = new_signal(severity="CRITICAL")
    assign_via_api(login(dispatcher), signal, near)
    staff.post(f"/api/signals/{signal.public_id}/decline", json={"reason": "Busy"})
    db.session.expire_all()
    overdue(signal)
    assert incidents.auto_assign_overdue() == 1
    assert signal.current_station_id == far.id


def test_decline_restarts_the_auto_assign_timer(login, dispatcher, new_signal, make_station, staff_of):
    near = make_station("Near", lat=6.5, lng=3.4)
    make_station("Far", lat=6.8, lng=3.8)
    staff = login(staff_of(near))
    signal = new_signal(severity="CRITICAL")
    overdue(signal)
    assign_via_api(login(dispatcher), signal, near)
    staff.post(f"/api/signals/{signal.public_id}/decline", json={"reason": "Busy"})
    db.session.expire_all()
    assert incidents.auto_assign_overdue() == 0  # dispatcher gets a fresh window


def test_auto_assign_prefers_idle_station(new_signal, make_station, dispatcher):
    near = make_station("Near", lat=6.5, lng=3.4)
    far = make_station("Far", lat=6.8, lng=3.8)
    incidents.assign(new_signal(), near, actor=dispatcher)
    signal = new_signal(severity="CRITICAL")
    overdue(signal)
    incidents.auto_assign_overdue()
    assert signal.current_station_id == far.id


def test_auto_assign_falls_back_to_any_approved_station(new_signal, make_station):
    police = make_station("Only police", "police")
    signal = new_signal(type="Fire Outbreak", severity="CRITICAL")
    overdue(signal)
    assert incidents.auto_assign_overdue() == 1
    assert signal.current_station_id == police.id


def test_no_station_flags_failure_once_then_recovers(new_signal, make_station):
    signal = new_signal(severity="CRITICAL")
    overdue(signal)
    assert incidents.auto_assign_overdue() == 0
    first_flag = signal.auto_assign_failed_at
    assert first_flag is not None
    incidents.auto_assign_overdue()
    assert signal.auto_assign_failed_at == first_flag
    assert sum(e.kind == "auto_failed" for e in signal.events) == 1

    make_station()
    assert incidents.auto_assign_overdue() == 1
    assert signal.status == "assigned"
    assert signal.auto_assign_failed_at is None


def test_resolved_signal_cannot_be_assigned(new_signal, make_station, dispatcher):
    signal = new_signal()
    incidents.close(signal, dispatcher, "False alarm")
    with pytest.raises(IncidentError):
        incidents.assign(signal, make_station(), actor=dispatcher)
