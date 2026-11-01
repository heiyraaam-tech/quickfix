from app.models import ProfessionalProfile, User
from .conftest import PW, login, make_user

REG = {"name": "Asha Rao", "email": "asha@example.com", "phone": "98765 43210", "password": PW, "confirm": PW}


def test_app_starts_and_home_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Your home, taken care of." in r.data
    assert b"Log in" in r.data and b"Sign up" in r.data


def test_customer_registration_logs_in_and_header_updates(client, app):
    r = client.post("/register", data=REG, follow_redirects=True)
    assert r.status_code == 200
    assert b"My bookings" in r.data and b"Asha" in r.data and b"Log out" in r.data
    assert b"Sign up" not in r.data
    with app.app_context():
        u = User.query.filter_by(email="asha@example.com").one()
        assert u.role == "customer" and u.phone == "9876543210"
        assert u.password_hash != PW and PW not in u.password_hash


def test_header_state_survives_reload_and_logout(client):
    client.post("/register", data=REG)
    assert b"Log out" in client.get("/").data  # reload
    r = client.post("/logout", follow_redirects=True)
    assert b"Log in" in r.data and b"Log out" not in r.data
    assert b"Log in" in client.get("/").data
    assert client.get("/bookings").status_code == 302  # session really gone


def test_professional_registration_creates_pending_profile(client, app):
    r = client.post("/register/professional", data={**REG, "email": "pro@example.com"}, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        p = ProfessionalProfile.query.one()
        assert p.verification_status == "pending" and p.availability_status == "offline"
        assert p.user.role == "professional"


def test_role_cannot_be_supplied_by_browser(client, app):
    client.post("/register", data={**REG, "role": "admin"})
    with app.app_context():
        assert User.query.filter_by(email="asha@example.com").one().role == "customer"


def test_duplicate_registration_rejected(client):
    client.post("/register", data=REG)
    client.post("/logout")
    r = client.post("/register", data=REG)
    assert r.status_code == 400 and b"already exists" in r.data
    r = client.post("/register", data={**REG, "email": "other@example.com"})
    assert b"phone number already exists" in r.data


def test_registration_validation(client):
    for bad in ({"email": "nope"}, {"password": "short", "confirm": "short"}, {"phone": "12345"}, {"name": ""}, {"confirm": "different1"}):
        r = client.post("/register", data={**REG, **bad})
        assert r.status_code == 400, bad


def test_login_valid_invalid_and_by_phone(client, app):
    with app.app_context():
        make_user("customer", "c@example.com", phone="9123456789")
    assert client.post("/login", data={"identifier": "c@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/login", data={"identifier": "nobody@example.com", "password": PW}).status_code == 401
    r = login(client, "C@Example.com")
    assert r.status_code == 302
    client.post("/logout")
    r = client.post("/login", data={"identifier": "+91 91234 56789", "password": PW})
    assert r.status_code == 302


def test_open_redirect_is_blocked(client, app):
    with app.app_context():
        make_user("customer", "c@example.com")
    r = client.post("/login?next=https://evil.example/", data={"identifier": "c@example.com", "password": PW})
    assert "evil" not in r.headers["Location"]


def test_suspended_user_cannot_log_in_and_loses_session(client, app):
    with app.app_context():
        u = make_user("customer", "c@example.com")
        uid = u.id
    login(client, "c@example.com")
    assert client.get("/bookings").status_code == 200
    with app.app_context():
        from app.extensions import db
        db.session.get(User, uid).active = False
        db.session.commit()
    assert client.get("/bookings").status_code == 302
    assert login(client, "c@example.com").status_code == 403


def test_role_authorization(client, app):
    with app.app_context():
        make_user("customer", "c@example.com")
        make_user("professional", "p@example.com")
        make_user("admin", "a@example.com")
    login(client, "c@example.com")
    assert client.get("/admin").status_code == 403
    assert client.get("/pro").status_code == 403
    client.post("/logout")
    login(client, "p@example.com")
    assert client.get("/bookings").status_code == 403
    assert client.get("/admin/users").status_code == 403
    client.post("/logout")
    assert client.get("/admin").status_code == 302  # anonymous -> login
    login(client, "a@example.com")
    assert client.get("/admin").status_code == 200


def test_api_requires_login_with_json_401(client):
    r = client.get("/api/quote?service=electrician")
    assert r.status_code == 401 and r.get_json()["error"] == "login_required"


def test_csrf_enforced_when_enabled(app):
    from app import create_app
    from config import TestConfig
    class C(TestConfig):
        WTF_CSRF_ENABLED = True
        SQLALCHEMY_DATABASE_URI = app.config["SQLALCHEMY_DATABASE_URI"]
    a = create_app(C)
    r = a.test_client().post("/login", data={"identifier": "x@example.com", "password": "x"})
    assert r.status_code == 400


def test_login_rate_limit(app):
    from app import create_app
    from config import TestConfig
    class C(TestConfig):
        RATELIMIT_ENABLED = True
        SQLALCHEMY_DATABASE_URI = app.config["SQLALCHEMY_DATABASE_URI"]
        RATELIMIT_STORAGE_URI = "memory://"
    from app.extensions import limiter
    a = create_app(C)
    limiter.reset()
    c = a.test_client()
    codes = [c.post("/login", data={"identifier": "x@example.com", "password": "bad"}).status_code for _ in range(12)]
    assert 429 in codes
    limiter.enabled = False


def test_admin_cannot_be_created_from_public_signup(client):
    r = client.post("/register", data={**REG, "email": "admin@example.com"})
    assert client.get("/admin").status_code == 403
