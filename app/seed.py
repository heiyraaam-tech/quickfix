"""Catalogue seeding (needed by the app) and clearly labelled DEMO data (development only)."""
from datetime import time

from .extensions import db
from .models import (Address, Category, Locality, ProfessionalArea, ProfessionalProfile, ProfessionalService,
                     Service, User, WorkingHours, now_ist)

LOCALITIES = ["Aundh", "Baner", "Bavdhan", "Deccan Gymkhana", "Hadapsar", "Hinjewadi", "Kalyani Nagar", "Kharadi",
              "Koregaon Park", "Kothrud", "Magarpatta", "Pashan", "Shivajinagar", "Viman Nagar", "Wakad", "Wanowrie"]

# (name, slug, description, icon, [(service fields)])
H = "hourly"
CATALOGUE = [
    ("Repairs", "repairs", "Skilled help for things that stopped working.", "wrench", [
        dict(name="Electrician", slug="electrician", icon="bolt", pricing_model="visit_estimate", visit_fee=199, base_price=250, default_minutes=60, keywords="wiring switch fan light mcb socket", description="Switches, fans, lights, wiring faults."),
        dict(name="Plumber", slug="plumber", icon="drop", pricing_model="visit_estimate", visit_fee=199, base_price=250, default_minutes=60, keywords="tap leak pipe flush geyser", description="Leaks, taps, pipes and fittings."),
        dict(name="Carpenter", slug="carpenter", icon="saw", pricing_model="visit_estimate", visit_fee=249, base_price=350, default_minutes=90, keywords="furniture door hinge wood bed", description="Doors, furniture and fittings."),
        dict(name="General handyman", slug="handyman", icon="wrench", pricing_model=H, hourly_rate=299, duration_options="60,120,180", default_minutes=60, keywords="odd jobs mounting curtain rod assemble", description="Odd jobs by the hour: mounting, assembling, fixing."),
        dict(name="Appliance repair", slug="appliance-repair", icon="plug", pricing_model="visit_estimate", visit_fee=249, base_price=300, default_minutes=60, keywords="ac fridge washing machine microwave", description="Washing machines, fridges, microwaves and more."),
        dict(name="Minor home repairs", slug="minor-home-repairs", icon="wrench", pricing_model="visit_estimate", visit_fee=199, base_price=300, default_minutes=90, keywords="seepage putty crack tiles", description="Small fixes around the house."),
        dict(name="Painting", slug="painting", icon="roller", pricing_model="quote", visit_fee=299, default_minutes=90, keywords="wall paint colour touch up", description="Inspection visit, then a custom quote for the work."),
    ]),
    ("Cleaning", "cleaning", "Cleaning for a room or the whole home.", "sparkle", [
        dict(name="Bathroom cleaning", slug="bathroom-cleaning", icon="sparkle", pricing_model="fixed", base_price=499, default_minutes=60, keywords="toilet washroom tiles", description="One bathroom, scrubbed and sanitised."),
        dict(name="Kitchen cleaning", slug="kitchen-cleaning", icon="sparkle", pricing_model="fixed", base_price=799, default_minutes=90, keywords="chimney stove counter", description="Counters, stove, sink and cabinets."),
        dict(name="Full-home cleaning", slug="full-home-cleaning", icon="home", pricing_model="duration", base_price=1499, hourly_rate=350, min_minutes=180, default_minutes=180, duration_options="180,240,300", keywords="flat apartment house", description="Includes 3 hours; add time if you need it."),
        dict(name="Deep cleaning", slug="deep-cleaning", icon="home", pricing_model="duration", base_price=2499, hourly_rate=400, min_minutes=240, default_minutes=240, duration_options="240,300,360", keywords="thorough scrub sofa", description="Includes 4 hours; add time if you need it."),
        dict(name="Move-in / move-out cleaning", slug="move-cleaning", icon="box", pricing_model="duration", base_price=3499, hourly_rate=450, min_minutes=300, default_minutes=300, duration_options="300,360,420", keywords="shifting vacate", description="Includes 5 hours; add time if you need it."),
    ]),
    ("House help", "house-help", "Help with everyday chores, once or on a regular schedule.", "broom", [
        dict(name="Sweeping and mopping", slug="sweeping-mopping", icon="broom", pricing_model=H, hourly_rate=150, duration_options="60,120,180,240", default_minutes=120, keywords="jhadu pocha floor", description="Floors swept and mopped."),
        dict(name="Dishwashing", slug="dishwashing", icon="dish", pricing_model=H, hourly_rate=150, duration_options="60,120,180,240", default_minutes=60, keywords="bartan utensils", description="Utensils washed and kitchen tidied."),
        dict(name="Laundry and folding", slug="laundry-folding", icon="shirt", pricing_model=H, hourly_rate=160, duration_options="60,120,180,240", default_minutes=120, keywords="clothes kapde iron", description="Wash, dry and fold at home."),
        dict(name="Kitchen assistance", slug="kitchen-assistance", icon="pot", pricing_model=H, hourly_rate=170, duration_options="60,120,180,240", default_minutes=120, keywords="chopping prep cooking help", description="Prep, chopping and clean-up while you cook."),
        dict(name="General household chores", slug="household-chores", icon="home", pricing_model=H, hourly_rate=150, duration_options="60,120,180,240", default_minutes=120, keywords="dusting tidy organise", description="Dusting, tidying and other chores."),
    ]),
]


def seed_catalogue():
    added = 0
    for name in LOCALITIES:
        if not Locality.query.filter_by(name=name).first():
            db.session.add(Locality(name=name))
            added += 1
    for i, (cname, cslug, cdesc, cicon, services) in enumerate(CATALOGUE):
        cat = Category.query.filter_by(slug=cslug).first()
        if not cat:
            cat = Category(name=cname, slug=cslug, description=cdesc, icon=cicon, sort_order=i)
            db.session.add(cat)
            db.session.flush()
        for j, s in enumerate(services):
            if not Service.query.filter_by(slug=s["slug"]).first():
                db.session.add(Service(category_id=cat.id, sort_order=j, **s))
                added += 1
    db.session.commit()
    return added


DEMO_PASSWORD = "Demo@12345"


def seed_demo():
    """DEMO DATA - development only. Every account uses the same well-known password."""
    seed_catalogue()
    made = []
    if not User.query.filter_by(email="demo.customer@example.com").first():
        c = User(email="demo.customer@example.com", name="Demo Customer", phone="9000000001", role="customer")
        c.set_password(DEMO_PASSWORD)
        db.session.add(c)
        db.session.flush()
        db.session.add(Address(user_id=c.id, label="Home", line1="Flat 12, Demo Residency", landmark="Near demo park",
                               locality_id=Locality.query.filter_by(name="Kothrud").first().id, pincode="411038", is_default=True))
        made.append(c.email)
    all_services = Service.query.all()
    kothrud = Locality.query.filter_by(name="Kothrud").first()
    baner = Locality.query.filter_by(name="Baner").first()
    people = [("Demo Pro Anil", "demo.pro1@example.com", "9000000011", "approved", "available", 8),
              ("Demo Pro Sunita", "demo.pro2@example.com", "9000000012", "approved", "available", 5),
              ("Demo Pro Pending", "demo.pending@example.com", "9000000013", "pending", "offline", 2)]
    for name, email, phone, status, avail, years in people:
        if User.query.filter_by(email=email).first():
            continue
        u = User(email=email, name=name, phone=phone, role="professional")
        u.set_password(DEMO_PASSWORD)
        db.session.add(u)
        db.session.flush()
        p = ProfessionalProfile(user_id=u.id, bio="DEMO profile. Experienced, punctual, and tidy.", experience_years=years,
                                verification_status=status, availability_status=avail,
                                approved_at=now_ist() if status == "approved" else None)
        db.session.add(p)
        db.session.flush()
        for s in all_services:
            db.session.add(ProfessionalService(professional_id=p.id, service_id=s.id))
        for loc in (kothrud, baner):
            db.session.add(ProfessionalArea(professional_id=p.id, locality_id=loc.id))
        for d in range(6):
            db.session.add(WorkingHours(professional_id=p.id, weekday=d, start_time=time(8), end_time=time(20)))
        made.append(email)
    db.session.commit()
    return made
