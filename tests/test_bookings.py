from datetime import timedelta

import pytest

from app.extensions import db
from app.models import Booking, Notification, Payment, Review, Service
from app.services import bookings as bk
from app.services.bookings import BookingError
from app.models import now_ist
from .conftest import PW, future_slot, login, make_address, make_pro, make_user, slot_fields


def setup_world(app, pro_kwargs=None):
    cust = make_user("customer", "cust@example.com", name="Cust Omer")
    addr = make_address(cust)
    pro = make_pro(**(pro_kwargs or {}))
    return cust, addr, pro


def post_booking(client, slug, addr_id, dt, pro_id="any", **extra):
    data = {"address_id": addr_id, "professional_id": pro_id, "minutes": "", **slot_fields(dt), **extra}
    return client.post(f"/book/{slug}", data=data, follow_redirects=False)


def test_server_side_pricing_ignores_client_price(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        aid = addr.id
    login(client, "cust@example.com")
    r = post_booking(client, "electrician", aid, future_slot(), total_amount="1", price="1", service_charge="0")
    assert r.status_code == 302
    with app.app_context():
        b = Booking.query.one()
        assert (b.visit_fee, b.service_charge, b.total_amount) == (199, 250, 449)
        assert b.is_estimate and b.payment.status == "unpaid" and b.payment.amount == 449


def test_pricing_models(app):
    from app.services.pricing import calculate, PricingError
    with app.app_context():
        get = lambda s: Service.query.filter_by(slug=s).one()
        assert calculate(get("bathroom-cleaning"))["total"] == 499
        assert calculate(get("sweeping-mopping"), 180)["total"] == 450  # 150/hr x 3h
        full = get("full-home-cleaning")
        assert calculate(full, 180)["total"] == 1499
        assert calculate(full, 300)["total"] == 1499 + 700  # two extra hours at 350
        q = calculate(get("painting"))
        assert q["is_quote"] and q["total"] == 299
        with pytest.raises(PricingError):
            calculate(get("sweeping-mopping"), 90)


def test_invalid_bookings_rejected(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        aid = addr.id
    login(client, "cust@example.com")
    assert post_booking(client, "electrician", aid, now_ist() - timedelta(days=1)).status_code == 400  # past
    assert post_booking(client, "electrician", aid, now_ist() + timedelta(days=200)).status_code == 400  # too far
    assert post_booking(client, "electrician", 99999, future_slot()).status_code == 400  # unknown address
    assert client.post("/book/electrician", data={"address_id": aid, "date": "garbage", "time": "x"}).status_code == 400
    assert client.post("/book/does-not-exist").status_code == 404
    with app.app_context():
        assert Booking.query.count() == 0


def test_cannot_book_with_someone_elses_address(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        other = make_user("customer", "other@example.com")
        oaddr = make_address(other)
        oid = oaddr.id
    login(client, "cust@example.com")
    assert post_booking(client, "electrician", oid, future_slot()).status_code == 400


def test_customer_booking_isolation(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        make_user("customer", "other@example.com")
        aid = addr.id
    login(client, "cust@example.com")
    post_booking(client, "electrician", aid, future_slot())
    with app.app_context():
        ref = Booking.query.one().reference
    assert client.get(f"/bookings/{ref}").status_code == 200
    client.post("/logout")
    login(client, "other@example.com")
    assert client.get(f"/bookings/{ref}").status_code == 404
    assert client.post(f"/bookings/{ref}/cancel", data={"reason": "x"}).status_code == 404
    assert client.post(f"/bookings/{ref}/review", data={"rating": "5"}).status_code == 404
    with app.app_context():
        assert Booking.query.one().status == "pending"


def test_professional_authorization_and_privacy(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        other = make_pro("pro2@example.com", localities=("Baner",), name="Other Pro")  # different area
        aid, pid = addr.id, pro.id
    login(client, "cust@example.com")
    post_booking(client, "electrician", aid, future_slot(), pro_id=pid)
    with app.app_context():
        ref = Booking.query.one().reference
    client.post("/logout")
    login(client, "pro2@example.com")
    assert client.get(f"/pro/jobs/{ref}").status_code == 404
    assert client.post(f"/pro/jobs/{ref}/status", data={"status": "accepted"}).status_code == 404
    client.post("/logout")
    login(client, "pro@example.com")
    r = client.get(f"/pro/jobs/{ref}")
    assert r.status_code == 200 and b"Flat 1, Test Apartments" in r.data


def test_open_request_hides_address_until_accepted(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        aid = addr.id
    login(client, "cust@example.com")
    post_booking(client, "electrician", aid, future_slot())
    with app.app_context():
        ref = Booking.query.one().reference
    client.post("/logout")
    login(client, "pro@example.com")
    r = client.get(f"/pro/jobs/{ref}")
    assert r.status_code == 200 and b"Flat 1, Test Apartments" not in r.data and b"Cust Omer" not in r.data
    client.post(f"/pro/jobs/{ref}/status", data={"status": "accepted"})
    r = client.get(f"/pro/jobs/{ref}")
    assert b"Flat 1, Test Apartments" in r.data


def test_full_customer_journey_to_review(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        aid, pid = addr.id, pro.id
    login(client, "cust@example.com")
    r = post_booking(client, "electrician", aid, future_slot(), pro_id=pid, details="fan not working")
    ref = r.headers["Location"].rsplit("/", 1)[1]
    assert b"Pending" in client.get(f"/bookings/{ref}").data
    # cannot review before completion
    client.post(f"/bookings/{ref}/review", data={"rating": "5"})
    with app.app_context():
        assert Review.query.count() == 0
    client.post("/logout")

    login(client, "pro@example.com")
    for st in ("accepted", "on_the_way", "in_progress"):
        client.post(f"/pro/jobs/{ref}/status", data={"status": st})
    client.post(f"/pro/jobs/{ref}/status", data={"status": "completed", "final_amount": "520"})
    client.post(f"/pro/jobs/{ref}/paid")
    with app.app_context():
        b = Booking.query.one()
        assert b.status == "completed" and b.final_amount == 520 and b.payment.status == "paid" and b.payment.amount == 520
        assert [h.to_status for h in b.history] == ["pending", "accepted", "on_the_way", "in_progress", "completed"]
        assert b.accepted_at and b.started_at and b.completed_at
    client.post("/logout")

    login(client, "cust@example.com")
    assert b"Completed" in client.get(f"/bookings/{ref}").data
    client.post(f"/bookings/{ref}/review", data={"rating": "4", "comment": "Quick and tidy"})
    client.post(f"/bookings/{ref}/review", data={"rating": "1", "comment": "again"})  # duplicate
    with app.app_context():
        assert Review.query.count() == 1
        r = Review.query.one()
        assert r.rating == 4 and r.professional.rating_avg == 4.0 and r.professional.rating_count == 1
    assert b"Quick and tidy" in client.get("/").data


def test_only_booking_customer_can_review(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        make_user("customer", "other@example.com")
        b = bk.create_booking(cust, Service.query.filter_by(slug="plumber").one(), addr, future_slot(), 60, professional=pro)
        for st in ("accepted", "in_progress", "completed"):
            bk.change_status(b, st, pro.user)
        ref = b.reference
        from app.services.reviews import create_review
        other = make_user("customer", "o2@example.com")
        with pytest.raises(BookingError):
            create_review(b, other, 5, "x")


def test_status_transitions_enforced(app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        b = bk.create_booking(cust, Service.query.filter_by(slug="plumber").one(), addr, future_slot(), 60, professional=pro)
        with pytest.raises(BookingError):
            bk.change_status(b, "completed", pro.user)  # pending -> completed
        with pytest.raises(BookingError):
            bk.change_status(b, "in_progress", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(b, "accepted", cust)  # customers cannot accept
        bk.change_status(b, "accepted", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(b, "pending", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(b, "rejected", pro.user)  # accepted -> rejected not allowed
        bk.change_status(b, "in_progress", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(b, "cancelled", cust)  # too late online
        bk.change_status(b, "completed", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(b, "cancelled", cust)


def test_cancellation_rules(app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        svc = Service.query.filter_by(slug="plumber").one()
        b = bk.create_booking(cust, svc, addr, future_slot(), 60, professional=pro)
        with pytest.raises(BookingError):
            bk.change_status(b, "cancelled", cust, note="")  # reason required
        bk.change_status(b, "cancelled", cust, note="Plans changed")
        assert b.status == "cancelled" and b.cancel_reason == "Plans changed" and b.cancelled_at and not b.late_cancellation
        assert b.payment.status == "void"
        # late cancellation of an accepted booking is flagged
        b2 = bk.create_booking(cust, svc, addr, now_ist() + timedelta(minutes=90), 60, professional=pro)
        b2.scheduled_start = now_ist() + timedelta(minutes=90)
        bk.change_status(b2, "accepted", pro.user)
        bk.change_status(b2, "cancelled", cust, note="Sorry")
        assert b2.late_cancellation


def test_double_booking_prevented(app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        svc = Service.query.filter_by(slug="plumber").one()
        start = future_slot()
        a = bk.create_booking(cust, svc, addr, start, 60, professional=pro)
        bk.change_status(a, "accepted", pro.user)
        # direct booking for an overlapping slot is refused
        with pytest.raises(BookingError):
            bk.create_booking(cust, svc, addr, start + timedelta(minutes=30), 60, professional=pro)
        # a back-to-back slot is fine
        c = bk.create_booking(cust, svc, addr, start + timedelta(minutes=60), 60, professional=pro)
        assert c.status == "pending"
        # two pending requests for the same slot: only the first acceptance wins
        d = bk.create_booking(cust, svc, addr, start + timedelta(days=1), 60, professional=pro)
        e = bk.create_booking(cust, svc, addr, start + timedelta(days=1, minutes=30), 60, professional=pro)
        bk.change_status(d, "accepted", pro.user)
        with pytest.raises(BookingError):
            bk.change_status(e, "accepted", pro.user)
        db.session.rollback()


def test_database_blocks_identical_active_slot(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        cust, addr, pro = setup_world(app)
        svc = Service.query.filter_by(slug="plumber").one()
        start = future_slot()
        a = bk.create_booking(cust, svc, addr, start, 60, professional=pro)
        b = bk.create_booking(cust, svc, addr, start, 60, professional=pro)
        a.status = "accepted"
        db.session.commit()
        b.status = "accepted"  # bypass the service layer on purpose
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_reschedule(app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        svc = Service.query.filter_by(slug="plumber").one()
        b = bk.create_booking(cust, svc, addr, future_slot(), 60, professional=pro)
        bk.change_status(b, "accepted", pro.user)
        bk.reschedule(b, cust, future_slot(days=3, hour=14))
        assert b.status == "pending" and b.scheduled_start.hour == 14 and b.accepted_at is None
        assert b.scheduled_end - b.scheduled_start == timedelta(minutes=60)
        with pytest.raises(BookingError):
            bk.reschedule(b, cust, now_ist() + timedelta(minutes=5))
        other = make_user("customer", "x@example.com")
        with pytest.raises(BookingError):
            bk.reschedule(b, other, future_slot(days=4))


def test_notifications_created(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        svc = Service.query.filter_by(slug="plumber").one()
        b = bk.create_booking(cust, svc, addr, future_slot(), 60, professional=pro)
        bk.change_status(b, "accepted", pro.user)
        titles = [n.title for n in Notification.query.filter_by(user_id=cust.id)]
        assert "Booking created" in titles and "Booking accepted" in titles
        assert Notification.query.filter_by(user_id=pro.user_id, title="New booking request").count() == 1
    login(client, "cust@example.com")
    assert client.get("/api/notifications/count").get_json()["unread"] == 2
    client.post("/notifications/read")
    assert client.get("/api/notifications/count").get_json()["unread"] == 0


def test_quote_api_lists_eligible_pros_and_alternatives(client, app):
    with app.app_context():
        cust, addr, pro = setup_world(app)
        from app.models import Locality
        loc = Locality.query.filter_by(name="Kothrud").one().id
        baner = Locality.query.filter_by(name="Baner").one().id
    login(client, "cust@example.com")
    dt = future_slot()
    q = f"/api/quote?service=electrician&locality_id={loc}&date={dt:%Y-%m-%d}&time={dt:%H:%M}"
    d = client.get(q).get_json()
    assert d["price"]["total"] == 449 and d["checked"] and len(d["providers"]) == 1
    d = client.get(q.replace(f"locality_id={loc}", f"locality_id={baner}")).get_json()
    assert d["providers"] == [] and d["alternatives"] == []  # nobody serves Baner
    night = future_slot(hour=23)
    d = client.get(f"/api/quote?service=electrician&locality_id={loc}&date={night:%Y-%m-%d}&time=23:00").get_json()
    assert d["providers"] == [] and len(d["alternatives"]) > 0  # outside working hours -> alternatives offered
