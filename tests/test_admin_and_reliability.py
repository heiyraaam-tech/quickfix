import os
import tempfile

from app.extensions import db
from app.models import AuditLog, Booking, Category, ProfessionalProfile, Review, ReviewReport, Service, User
from app.services import bookings as bk
from .conftest import PW, future_slot, login, make_address, make_pro, make_user


def test_admin_approval_workflow(client, app):
    with app.app_context():
        make_user("admin", "admin@example.com")
        pro = make_pro("p@example.com", status="pending")
        pid = pro.id
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        from app.models import Locality
    # a pending pro never matches
    with app.app_context():
        from app.services import matching
        from app.models import Locality
        s = Service.query.filter_by(slug="electrician").one()
        assert matching.eligible_professionals([s.id], Locality.query.filter_by(name="Kothrud").one().id, future_slot(), 60) == []
    login(client, "admin@example.com")
    assert client.post(f"/admin/professionals/{pid}/reject", data={"reason": ""}).status_code == 302
    with app.app_context():
        assert db.session.get(ProfessionalProfile, pid).verification_status == "pending"  # reason is mandatory
    client.post(f"/admin/professionals/{pid}/reject", data={"reason": "Incomplete profile"})
    with app.app_context():
        p = db.session.get(ProfessionalProfile, pid)
        assert p.verification_status == "rejected" and p.rejection_reason == "Incomplete profile"
    client.post(f"/admin/professionals/{pid}/approve")
    with app.app_context():
        assert db.session.get(ProfessionalProfile, pid).verification_status == "approved"
        assert AuditLog.query.filter(AuditLog.action.like("professional.%")).count() == 2
    client.post(f"/admin/professionals/{pid}/suspend", data={"reason": "Complaint"})
    with app.app_context():
        assert db.session.get(ProfessionalProfile, pid).verification_status == "suspended"
    client.post(f"/admin/professionals/{pid}/reactivate")
    with app.app_context():
        assert db.session.get(ProfessionalProfile, pid).verification_status == "approved"


def test_new_professional_not_auto_verified_and_full_pipeline(client, app):
    client.post("/register/professional", data={"name": "New Pro", "email": "new@example.com", "phone": "9811111111", "password": PW, "confirm": PW})
    with app.app_context():
        make_user("admin", "admin@example.com")
        from app.models import Locality
        p = ProfessionalProfile.query.one()
        assert p.verification_status == "pending"
        pid = p.id
        svc_id = Service.query.filter_by(slug="electrician").one().id
        loc_id = Locality.query.filter_by(name="Kothrud").one().id
    r = client.post("/pro/profile", data={"bio": "Electrician", "experience": "4", f"svc_{svc_id}": "on", f"area_{loc_id}": "on"})
    assert r.status_code == 302
    client.post("/pro/availability", data={"action": "status", "availability": "available"})
    hours = {"action": "hours", **{f"on_{d}": "on" for d in range(7)}, **{f"start_{d}": "08:00" for d in range(7)}, **{f"end_{d}": "20:00" for d in range(7)}}
    client.post("/pro/availability", data=hours)
    client.post("/logout")
    login(client, "admin@example.com")
    client.post(f"/admin/professionals/{pid}/approve")
    client.post("/logout")
    # a customer can now find this professional
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        make_address(cust)
    login(client, "c@example.com")
    dt = future_slot()
    d = client.get(f"/api/quote?service=electrician&locality_id={loc_id}&date={dt:%Y-%m-%d}&time={dt:%H:%M}").get_json()
    assert [p["name"] for p in d["providers"]] == ["New Pro"]


def test_professional_rate_override_is_bounded_and_used(client, app):
    with app.app_context():
        make_user("professional", "x@example.com")
        p = make_pro("p@example.com")
        svc = Service.query.filter_by(slug="sweeping-mopping").one()
        sid = svc.id
    login(client, "p@example.com")
    from app.models import Locality
    with app.app_context():
        loc_id = Locality.query.filter_by(name="Kothrud").one().id
        data = {"bio": "", "experience": "3", f"svc_{sid}": "on", f"area_{loc_id}": "on", f"rate_{sid}": "10"}
    assert client.post("/pro/profile", data=data).status_code == 400  # below 50%
    data[f"rate_{sid}"] = "180"
    assert client.post("/pro/profile", data=data).status_code == 302
    with app.app_context():
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        pro = ProfessionalProfile.query.join(User).filter(User.email == "p@example.com").one()
        svc = db.session.get(Service, sid)
        b = bk.create_booking(cust, svc, addr, future_slot(), 120, professional=pro)
        assert b.total_amount == 360  # 180/hr x 2h


def test_admin_protected_endpoints(client, app):
    with app.app_context():
        make_user("customer", "c@example.com")
        p = make_pro("p@example.com", status="pending")
        pid = p.id
    login(client, "c@example.com")
    for method, url in [("get", "/admin"), ("get", "/admin/users"), ("post", f"/admin/professionals/{pid}/approve"),
                        ("get", "/admin/audit"), ("get", "/admin/bookings"), ("post", "/admin/schedules/generate")]:
        assert getattr(client, method)(url).status_code == 403, url
    with app.app_context():
        assert db.session.get(ProfessionalProfile, pid).verification_status == "pending"


def test_admin_user_suspension_and_admin_protection(client, app):
    with app.app_context():
        admin = make_user("admin", "admin@example.com")
        cust = make_user("customer", "c@example.com")
        cid, aid = cust.id, admin.id
    login(client, "admin@example.com")
    client.post(f"/admin/users/{cid}/suspend", data={"reason": "abuse"})
    client.post(f"/admin/users/{aid}/suspend")
    with app.app_context():
        assert db.session.get(User, cid).active is False
        assert db.session.get(User, aid).active is True
        assert AuditLog.query.filter_by(action="user.suspend").count() == 1


def test_admin_service_pricing_and_validation(client, app):
    with app.app_context():
        make_user("admin", "admin@example.com")
        s = Service.query.filter_by(slug="plumber").one()
        sid = s.id
    login(client, "admin@example.com")
    good = {"name": "Plumber", "pricing_model": "visit_estimate", "visit_fee": "250", "base_price": "300", "hourly_rate": "0",
            "min_minutes": "60", "default_minutes": "60", "is_active": "on"}
    assert client.post(f"/admin/services/{sid}", data={**good, "visit_fee": "abc"}).status_code == 200
    assert client.post(f"/admin/services/{sid}", data={**good, "pricing_model": "hourly"}).status_code == 200  # missing rate/options
    assert client.post(f"/admin/services/{sid}", data=good).status_code == 302
    with app.app_context():
        assert db.session.get(Service, sid).visit_fee == 250
    client.post("/admin/localities", data={"name": "Sinhagad Road"})
    client.post("/admin/localities", data={"name": "sinhagad road"})  # duplicate
    from app.models import Locality
    with app.app_context():
        assert Locality.query.filter_by(name="Sinhagad Road").count() == 1


def test_review_moderation_and_reporting(client, app):
    with app.app_context():
        make_user("admin", "admin@example.com")
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        pro = make_pro("p@example.com")
        b = bk.create_booking(cust, Service.query.filter_by(slug="plumber").one(), addr, future_slot(), 60, professional=pro)
        for st in ("accepted", "in_progress", "completed"):
            bk.change_status(b, st, pro.user)
        from app.services.reviews import create_review
        r = create_review(b, cust, 5, "Great")
        rid, ref = r.id, b.reference
    login(client, "p@example.com")
    client.post(f"/reviews/{rid}/report", data={"reason": "Not a real customer"})
    client.post(f"/reviews/{rid}/report", data={"reason": "again"})  # duplicate report
    with app.app_context():
        assert ReviewReport.query.count() == 1
    client.post("/logout")
    login(client, "admin@example.com")
    client.post(f"/admin/reviews/{rid}/hide")
    with app.app_context():
        r = db.session.get(Review, rid)
        assert r.status == "hidden" and r.professional.rating_avg is None
        assert ReviewReport.query.one().status == "resolved"


def test_admin_booking_actions_and_metrics(client, app):
    with app.app_context():
        make_user("admin", "admin@example.com")
        cust = make_user("customer", "c@example.com")
        addr = make_address(cust)
        pro = make_pro("p@example.com")
        b = bk.create_booking(cust, Service.query.filter_by(slug="plumber").one(), addr, future_slot(), 60, professional=pro)
        for st in ("accepted", "in_progress", "completed"):
            bk.change_status(b, st, pro.user)
        ref = b.reference
    login(client, "admin@example.com")
    client.post(f"/admin/bookings/{ref}", data={"action": "paid"})
    page = client.get("/admin").data
    assert b"449" in page and b"Recorded as paid" in page
    client.post(f"/admin/bookings/{ref}", data={"action": "refund"})
    with app.app_context():
        assert Booking.query.one().payment.status == "refunded"
        assert AuditLog.query.filter_by(action="payment.refund").count() == 1


def test_complaints_flow(client, app):
    with app.app_context():
        make_user("admin", "admin@example.com")
        make_user("customer", "c@example.com")
    login(client, "c@example.com")
    assert client.post("/support/complaint", data={"subject": "No show", "message": "Professional never came"}).status_code == 302
    client.post("/logout")
    login(client, "admin@example.com")
    assert b"No show" in client.get("/admin/complaints").data
    from app.models import Complaint
    with app.app_context():
        cid = Complaint.query.one().id
    client.post("/admin/complaints", data={"id": cid, "status": "resolved", "admin_note": "Refunded"})
    with app.app_context():
        assert Complaint.query.one().status == "resolved"


def test_public_pages_and_error_handling(client):
    for url in ("/", "/services", "/services/electrician", "/house-help", "/become-a-professional", "/faq", "/support",
                "/policy/privacy", "/policy/terms", "/policy/cancellation", "/policy/payments", "/policy/safety",
                "/policy/professional-guidelines", "/login", "/register", "/register/professional"):
        assert client.get(url).status_code == 200, url
    r = client.get("/nope")
    assert r.status_code == 404 and b"couldn" in r.data and b"Traceback" not in r.data
    assert client.get("/policy/unknown").status_code == 404
    assert client.get("/services?q=zzzz").status_code == 200
    assert b"Electrician" in client.get("/services?q=fan").data  # keyword search
    assert client.get("/api/search?q=plum").get_json()["results"][0]["name"] == "Plumber"
    assert b"QuickFix" in client.get("/policy/terms").data and b"legal review" in client.get("/policy/terms").data
    assert client.get("/api/me").get_json() == {"authenticated": False}


def test_security_headers_and_no_store(client, app):
    with app.app_context():
        make_user("customer", "c@example.com")
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY" and "default-src 'self'" in h["Content-Security-Policy"]
    login(client, "c@example.com")
    assert client.get("/").headers["Cache-Control"] == "no-store"
    assert b"password_hash" not in client.get("/account").data


def test_no_inline_scripts_for_csp(client):
    html = client.get("/").data.decode()
    import re
    assert not re.search(r"<script(?![^>]*src=)[^>]*>", html)
    assert " onclick=" not in html


def test_database_integrity_constraints(app):
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        make_user("customer", "dup@example.com")
        db.session.add(User(email="dup@example.com", name="X", role="customer", password_hash="x"))
        try:
            db.session.commit()
            assert False
        except IntegrityError:
            db.session.rollback()
        db.session.add(User(email="bad@example.com", name="X", role="superuser", password_hash="x"))
        try:
            db.session.commit()
            assert False
        except IntegrityError:
            db.session.rollback()


def test_migrations_build_the_same_tables(app):
    from flask_migrate import upgrade
    path = os.path.join(tempfile.mkdtemp(), "mig.db")
    from app import create_app
    from config import TestConfig
    class C(TestConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + path
    a = create_app(C)
    with a.app_context():
        upgrade(directory=os.path.join(os.path.dirname(__file__), "..", "migrations"))
        from sqlalchemy import inspect
        tables = set(inspect(db.engine).get_table_names())
        assert {"users", "bookings", "recurring_schedules", "reviews", "payments", "notifications", "audit_logs"} <= tables
        idx = {i["name"] for i in inspect(db.engine).get_indexes("bookings")}
        assert "uq_pro_active_slot" in idx


def test_cli_create_admin_and_seed(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["create-admin", "--email", "boss@example.com", "--name", "Boss"], input="LongPassw0rd1\nLongPassw0rd1\n")
    assert r.exit_code == 0, r.output
    with app.app_context():
        assert User.query.filter_by(email="boss@example.com").one().role == "admin"
    r = runner.invoke(args=["create-admin", "--email", "x@example.com", "--name", "X"], input="short1\nshort1\n")
    assert r.exit_code != 0
    r = runner.invoke(args=["seed-demo"])
    assert r.exit_code == 0 and "Demo" in r.output
    r = runner.invoke(args=["generate-visits"])
    assert r.exit_code == 0
