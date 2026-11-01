from datetime import date, datetime, time, timedelta

import pytest

from app.extensions import db
from app.models import Booking, RecurringSchedule, Service, now_ist
from app.services import bookings as bk, recurring
from app.services.bookings import BookingError
from .conftest import future_slot, login, make_address, make_pro, make_user, slot_fields


def world(app):
    cust = make_user("customer", "c@example.com")
    addr = make_address(cust)
    pro = make_pro("p@example.com")
    return cust, addr, pro


def svc(slug="sweeping-mopping"):
    return Service.query.filter_by(slug=slug).one()


def next_weekdays(n=14):
    return now_ist().date() + timedelta(days=2), now_ist().date() + timedelta(days=n)


def test_one_time_house_help_via_form(client, app):
    with app.app_context():
        cust, addr, pro = world(app)
        aid = addr.id
    login(client, "c@example.com")
    dt = future_slot()
    r = client.post("/book-house-help", data={"task": ["sweeping-mopping", "dishwashing"], "mode": "once", "minutes": "120",
                    "address_id": aid, "professional_id": "any", "instructions": "Mind the cat", **slot_fields(dt)})
    assert r.status_code == 302, r.data
    with app.app_context():
        b = Booking.query.one()
        assert b.tasks == ["Sweeping and mopping", "Dishwashing"]
        assert b.duration_minutes == 120 and b.duration_charge == 300 and b.total_amount == 300  # 150/hr x 2h
        assert b.schedule_id is None and b.status == "pending"


def test_duration_based_price_and_invalid_duration(client, app):
    with app.app_context():
        cust, addr, pro = world(app)
        aid = addr.id
    login(client, "c@example.com")
    base = {"task": ["laundry-folding"], "mode": "once", "address_id": aid, "professional_id": "any", **slot_fields(future_slot())}
    assert client.post("/book-house-help", data={**base, "minutes": "240"}).status_code == 302
    assert client.post("/book-house-help", data={**base, "minutes": "90"}).status_code == 400
    assert client.post("/book-house-help", data={**base, "minutes": "120", "task": []}).status_code == 400
    with app.app_context():
        assert Booking.query.one().total_amount == 640  # 160 x 4h; highest rate applies to combos


def test_combined_tasks_use_highest_rate(app):
    with app.app_context():
        cust, addr, pro = world(app)
        b = bk.create_booking(cust, svc("kitchen-assistance"), addr, future_slot(), 60,
                              extra_services=[svc("dishwashing")], tasks=["Kitchen assistance", "Dishwashing"])
        assert b.total_amount == 170


def test_recurring_schedule_generates_correct_visits(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(20)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 2, 4], time(9, 0), 120, start, end, ["Sweeping and mopping"])
        visits = Booking.query.filter_by(schedule_id=s.id).order_by(Booking.scheduled_start).all()
        expected = [start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() in (0, 2, 4)]
        assert [v.scheduled_start.date() for v in visits] == expected
        assert all(v.scheduled_start.time() == time(9, 0) and v.status == "pending" and v.professional_id is None for v in visits)
        assert all(v.awaiting_availability and v.total_amount == 300 for v in visits)


def test_no_duplicate_visits(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(20)
        s = recurring.create_schedule(cust, svc(), [], addr, [1, 3], time(9, 0), 60, start, end, [])
        n = Booking.query.filter_by(schedule_id=s.id).count()
        assert recurring.generate_visits(s) == 0
        assert recurring.generate_visits(s) == 0
        assert recurring.generate_all() == 0
        assert Booking.query.filter_by(schedule_id=s.id).count() == n


def test_unique_constraint_backs_up_generation(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(10)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, end, [])
        v = s.visits[0]
        with pytest.raises(IntegrityError):
            bk.build_booking(cust, svc(), addr, v.scheduled_start + timedelta(hours=1), 60, schedule=s, original_start=v.original_start)
        db.session.rollback()


def test_horizon_extends_over_time(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start = now_ist().date() + timedelta(days=1)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, start + timedelta(days=60), [])
        first = Booking.query.filter_by(schedule_id=s.id).count()
        assert first <= 29
        added = recurring.generate_visits(s, today=now_ist().date() + timedelta(days=20))
        assert added > 0
        assert recurring.generate_visits(s, today=now_ist().date() + timedelta(days=20)) == 0


def test_cancelled_schedule_generates_nothing_and_cancels_visits(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(20)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, end, [])
        n = Booking.query.filter_by(schedule_id=s.id).count()
        assert recurring.cancel_schedule(s, cust, "moving") == n
        assert all(v.status == "cancelled" for v in s.visits)
        assert recurring.generate_visits(s, today=now_ist().date() + timedelta(days=30)) == 0
        assert Booking.query.filter_by(schedule_id=s.id).count() == n
        with pytest.raises(BookingError):
            recurring.cancel_schedule(s, cust, "again")


def test_cancelling_one_visit_does_not_regenerate_it(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(14)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, end, [])
        v = s.visits[2]
        bk.change_status(v, "cancelled", cust, note="away")
        n = Booking.query.filter_by(schedule_id=s.id).count()
        recurring.generate_visits(s)
        assert Booking.query.filter_by(schedule_id=s.id).count() == n


def test_rescheduling_a_visit_is_not_regenerated(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(14)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, end, [])
        v = s.visits[3]
        n = Booking.query.filter_by(schedule_id=s.id).count()
        bk.reschedule(v, cust, v.scheduled_start.replace(hour=15))
        recurring.generate_visits(s)
        assert Booking.query.filter_by(schedule_id=s.id).count() == n
        assert v.scheduled_start.hour == 15 and v.original_start.hour == 9


def test_update_schedule_replaces_future_visits(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(20)
        s = recurring.create_schedule(cust, svc(), [], addr, [0], time(9, 0), 60, start, end, [])
        old = [v.id for v in s.visits]
        recurring.update_schedule(s, cust, [1], time(11, 0), end)
        live = [v for v in s.visits if v.status != "cancelled"]
        assert live and all(v.scheduled_start.weekday() == 1 and v.scheduled_start.hour == 11 for v in live)
        assert all(v.status == "cancelled" for v in s.visits if v.id in old)


def test_schedule_validation(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(10)
        for args in (([], time(9), 60, start, end), ([1], time(9), 60, end, start), ([1], time(9), 60, start, start + timedelta(days=200)),
                     ([1], time(9), 60, now_ist().date() - timedelta(days=3), end), ([1], time(9), 45, start, end)):
            with pytest.raises(BookingError):
                recurring.create_schedule(cust, svc(), [], addr, args[0], args[1], args[2], args[3], args[4], [])
        db.session.rollback()
        other = make_user("customer", "o@example.com")
        with pytest.raises(BookingError):
            recurring.create_schedule(other, svc(), [], addr, [1], time(9), 60, start, end, [])  # not their address
        assert RecurringSchedule.query.count() == 0


def test_recurring_flow_future_visits_display_and_isolation(client, app):
    with app.app_context():
        cust, addr, pro = world(app)
        make_user("customer", "o@example.com")
        aid = addr.id
    login(client, "c@example.com")
    start, end = next_weekdays(14)
    r = client.post("/book-house-help", data={"task": ["dishwashing"], "mode": "recurring", "weekday": ["0", "3"], "minutes": "60",
                    "time": "09:00", "start_date": start.isoformat(), "end_date": end.isoformat(), "address_id": aid})
    assert r.status_code == 302
    page = client.get(r.headers["Location"])
    assert page.status_code == 200 and b"Awaiting a professional" in page.data and b"Upcoming visits" in page.data
    assert b"confirmed only when a professional accepts" in page.data
    ref = r.headers["Location"].rsplit("/", 1)[1]
    client.post("/logout")
    login(client, "o@example.com")
    assert client.get(f"/schedules/{ref}").status_code == 404
    assert client.post(f"/schedules/{ref}/cancel").status_code == 404


def test_pro_accepts_series(app):
    with app.app_context():
        cust, addr, pro = world(app)
        start, end = next_weekdays(14)
        s = recurring.create_schedule(cust, svc(), [], addr, [0, 1, 2, 3, 4, 5, 6], time(9, 0), 60, start, end, [])
        ok, skipped = recurring.accept_series(s, pro.user)
        assert ok == len(s.visits) and skipped == 0
        assert all(v.status == "accepted" and v.professional_id == pro.id for v in s.visits)
