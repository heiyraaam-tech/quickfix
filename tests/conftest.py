import os
import tempfile
from datetime import datetime, time, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models import (Address, Locality, ProfessionalArea, ProfessionalProfile, ProfessionalService, Service, User,
                        WorkingHours, now_ist)
from app.seed import seed_catalogue
from config import TestConfig


@pytest.fixture()
def app():
    path = os.path.join(tempfile.mkdtemp(), "test.db")

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + path

    app = create_app(Cfg)
    with app.app_context():
        db.create_all()
        seed_catalogue()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


PW = "Passw0rd!x"


def make_user(role="customer", email=None, name="Test User", phone=None):
    email = email or f"{role}{User.query.count()}@example.com"
    u = User(email=email, name=name, role=role, phone=phone)
    u.set_password(PW)
    db.session.add(u)
    db.session.commit()
    return u


def make_pro(email="pro@example.com", status="approved", avail="available", localities=("Kothrud",), slugs=None, name="Pro One"):
    u = make_user("professional", email, name, phone=None)
    p = ProfessionalProfile(user_id=u.id, verification_status=status, availability_status=avail, experience_years=3,
                            approved_at=now_ist() if status == "approved" else None)
    db.session.add(p)
    db.session.flush()
    services = Service.query.all() if slugs is None else Service.query.filter(Service.slug.in_(slugs)).all()
    for s in services:
        db.session.add(ProfessionalService(professional_id=p.id, service_id=s.id))
    for n in localities:
        db.session.add(ProfessionalArea(professional_id=p.id, locality_id=Locality.query.filter_by(name=n).one().id))
    for d in range(7):
        db.session.add(WorkingHours(professional_id=p.id, weekday=d, start_time=time(8), end_time=time(20)))
    db.session.commit()
    return p


def make_address(user, locality="Kothrud"):
    a = Address(user_id=user.id, label="Home", line1="Flat 1, Test Apartments", pincode="411038", is_default=True,
                locality_id=Locality.query.filter_by(name=locality).one().id)
    db.session.add(a)
    db.session.commit()
    return a


def login(client, email, password=PW):
    return client.post("/login", data={"identifier": email, "password": password}, follow_redirects=False)


def future_slot(days=2, hour=10):
    d = (now_ist() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return d


def slot_fields(dt):
    return {"date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M")}
