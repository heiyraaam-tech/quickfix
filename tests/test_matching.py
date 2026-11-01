from datetime import time, timedelta

from app.extensions import db
from app.models import Locality, Service, TimeOff, WorkingHours
from app.services import bookings as bk, matching
from .conftest import future_slot, make_address, make_pro, make_user


def ids(*slugs):
    return [s.id for s in Service.query.filter(Service.slug.in_(slugs)).all()]


def loc(name):
    return Locality.query.filter_by(name=name).one().id


def test_matching_filters(app):
    with app.app_context():
        start = future_slot()
        ok = make_pro("ok@example.com")
        make_pro("pending@example.com", status="pending")
        make_pro("rejected@example.com", status="rejected")
        make_pro("suspended@example.com", status="suspended")
        make_pro("offline@example.com", avail="offline")
        make_pro("busy@example.com", avail="busy")
        make_pro("baner@example.com", localities=("Baner",))
        make_pro("noskill@example.com", slugs=["plumber"])
        found = matching.eligible_professionals(ids("electrician"), loc("Kothrud"), start, 60)
        assert [p.id for p in found] == [ok.id]


def test_working_hours_and_time_off(app):
    with app.app_context():
        p = make_pro("p@example.com")
        s = ids("electrician")
        day = future_slot(hour=7)
        assert not matching.is_eligible(p, s, loc("Kothrud"), day, 60)[0]  # before 08:00
        assert not matching.is_eligible(p, s, loc("Kothrud"), future_slot(hour=19).replace(minute=30), 60)[0]  # ends after 20:00
        assert matching.is_eligible(p, s, loc("Kothrud"), future_slot(hour=10), 60)[0]
        db.session.add(TimeOff(professional_id=p.id, start_at=future_slot(hour=9), end_at=future_slot(hour=12)))
        db.session.commit()
        assert not matching.is_eligible(p, s, loc("Kothrud"), future_slot(hour=10), 60)[0]
        # a weekday with no working hours
        wh = p.hours_for(future_slot(hour=15).weekday())
        db.session.delete(wh)
        db.session.commit()
        db.session.refresh(p)
        assert not matching.is_eligible(p, s, loc("Kothrud"), future_slot(hour=15), 60)[0]


def test_availability_conflicts_and_alternatives(app):
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        p = make_pro("p@example.com")
        svc = Service.query.filter_by(slug="electrician").one()
        start = future_slot(hour=10)
        b = bk.create_booking(cust, svc, addr, start, 60, professional=p)
        bk.change_status(b, "accepted", p.user)
        assert matching.eligible_professionals([svc.id], loc("Kothrud"), start, 60) == []
        assert matching.eligible_professionals([svc.id], loc("Kothrud"), start + timedelta(hours=1), 60) == [p]
        alts = matching.alternative_slots([svc.id], loc("Kothrud"), start, 60)
        assert alts and start not in alts


def test_busy_status_blocks_direct_booking(app):
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        p = make_pro("p@example.com", avail="busy")
        svc = Service.query.filter_by(slug="electrician").one()
        try:
            bk.create_booking(cust, svc, addr, future_slot(), 60, professional=p)
            assert False, "should have been refused"
        except bk.BookingError:
            pass


def test_pending_pro_cannot_accept(app):
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        p = make_pro("p@example.com")
        svc = Service.query.filter_by(slug="electrician").one()
        b = bk.create_booking(cust, svc, addr, future_slot(), 60)  # open request
        p.verification_status = "pending"
        db.session.commit()
        try:
            bk.change_status(b, "accepted", p.user)
            assert False
        except bk.BookingError:
            pass
        p.verification_status = "approved"
        db.session.commit()
        bk.change_status(b, "accepted", p.user)  # claims the open request
        assert b.professional_id == p.id and b.status == "accepted"


def test_open_request_visible_only_to_eligible(app):
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        near = make_pro("near@example.com")
        far = make_pro("far@example.com", localities=("Baner",))
        svc = Service.query.filter_by(slug="electrician").one()
        bk.create_booking(cust, svc, addr, future_slot(), 60)
        assert len(matching.open_requests_for(near)) == 1
        assert matching.open_requests_for(far) == []
