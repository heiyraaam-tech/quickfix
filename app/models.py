"""QuickFix database models.

Money is stored as whole Indian rupees (integers). All datetimes are naive
Asia/Kolkata local time, because the launch market is a single time zone.
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, func, text
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db

IST = timezone(timedelta(hours=5, minutes=30))

ACTIVE_STATUSES = ("accepted", "on_the_way", "in_progress")
OPEN_STATUSES = ("pending",) + ACTIVE_STATUSES
FINAL_STATUSES = ("rejected", "completed", "cancelled")
BOOKING_STATUSES = OPEN_STATUSES + FINAL_STATUSES
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def now_ist():
    return datetime.now(IST).replace(tzinfo=None)


_REF_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def make_reference(prefix):
    return f"{prefix}-" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(6))


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(15), unique=True, nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="customer")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    last_login_at = db.Column(db.DateTime)

    addresses = db.relationship("Address", back_populates="user", lazy="dynamic")
    professional = db.relationship("ProfessionalProfile", back_populates="user", uselist=False)

    __table_args__ = (CheckConstraint("role IN ('customer','professional','admin')", name="ck_user_role"),)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):  # Flask-Login hook: deactivated users cannot log in
        return bool(self.active)

    @property
    def first_name(self):
        return (self.name or "").split(" ")[0]

    def __repr__(self):
        return f"<User {self.id} {self.role}>"


class Locality(db.Model):
    __tablename__ = "localities"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Address(db.Model):
    __tablename__ = "addresses"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    label = db.Column(db.String(40), nullable=False, default="Home")
    line1 = db.Column(db.String(200), nullable=False)
    landmark = db.Column(db.String(120))
    locality_id = db.Column(db.Integer, db.ForeignKey("localities.id"), nullable=False)
    pincode = db.Column(db.String(6), nullable=False)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    deleted = db.Column(db.Boolean, nullable=False, default=False)  # soft delete: bookings keep a reference

    user = db.relationship("User", back_populates="addresses")
    locality = db.relationship("Locality")

    @property
    def full(self):
        parts = [self.line1, self.landmark, self.locality.name, f"Pune {self.pincode}"]
        return ", ".join(p for p in parts if p)


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(300), default="")
    icon = db.Column(db.String(40), default="wrench")
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    services = db.relationship("Service", back_populates="category", order_by="Service.sort_order")


class Service(db.Model):
    __tablename__ = "services"
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(400), default="")
    keywords = db.Column(db.String(300), default="")
    icon = db.Column(db.String(40), default="wrench")
    # fixed | visit_estimate | hourly | duration | quote
    pricing_model = db.Column(db.String(20), nullable=False, default="fixed")
    visit_fee = db.Column(db.Integer, nullable=False, default=0)
    base_price = db.Column(db.Integer, nullable=False, default=0)
    hourly_rate = db.Column(db.Integer, nullable=False, default=0)
    min_minutes = db.Column(db.Integer, nullable=False, default=60)
    default_minutes = db.Column(db.Integer, nullable=False, default=60)
    duration_options = db.Column(db.String(60), default="")
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    category = db.relationship("Category", back_populates="services")

    __table_args__ = (
        CheckConstraint("pricing_model IN ('fixed','visit_estimate','hourly','duration','quote')", name="ck_service_model"),
    )

    @property
    def durations(self):
        return [int(x) for x in (self.duration_options or "").split(",") if x.strip().isdigit()]

    @property
    def is_house_help(self):
        return self.category.slug == "house-help"

    @property
    def needs_duration_choice(self):
        return self.pricing_model in ("hourly", "duration")


class ProfessionalProfile(db.Model):
    __tablename__ = "professional_profiles"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    bio = db.Column(db.String(1000), default="")
    experience_years = db.Column(db.Integer, default=0)
    # pending | approved | rejected | suspended
    verification_status = db.Column(db.String(20), nullable=False, default="pending")
    rejection_reason = db.Column(db.String(400))
    # available | busy | offline
    availability_status = db.Column(db.String(20), nullable=False, default="offline")
    submitted_at = db.Column(db.DateTime, default=now_ist)
    approved_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="professional")
    offerings = db.relationship("ProfessionalService", back_populates="professional", cascade="all, delete-orphan")
    areas = db.relationship("ProfessionalArea", back_populates="professional", cascade="all, delete-orphan")
    working_hours = db.relationship("WorkingHours", back_populates="professional", cascade="all, delete-orphan")
    time_off = db.relationship("TimeOff", back_populates="professional", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("verification_status IN ('pending','approved','rejected','suspended')", name="ck_pro_verif"),
        CheckConstraint("availability_status IN ('available','busy','offline')", name="ck_pro_avail"),
    )

    @property
    def is_approved(self):
        return self.verification_status == "approved" and self.user.is_active

    def hours_for(self, weekday):
        return next((w for w in self.working_hours if w.weekday == weekday), None)

    def offering_for(self, service_id):
        return next((o for o in self.offerings if o.service_id == service_id), None)

    def serves_locality(self, locality_id):
        return any(a.locality_id == locality_id for a in self.areas)

    @property
    def rating_avg(self):
        v = db.session.query(func.avg(Review.rating)).filter(
            Review.professional_id == self.id, Review.status == "visible").scalar()
        return round(float(v), 1) if v else None

    @property
    def rating_count(self):
        return db.session.query(func.count(Review.id)).filter(
            Review.professional_id == self.id, Review.status == "visible").scalar() or 0


class ProfessionalService(db.Model):
    __tablename__ = "professional_services"
    id = db.Column(db.Integer, primary_key=True)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    rate_override = db.Column(db.Integer)  # optional own rate (bounded by pricing rules)
    professional = db.relationship("ProfessionalProfile", back_populates="offerings")
    service = db.relationship("Service")
    __table_args__ = (UniqueConstraint("professional_id", "service_id", name="uq_pro_service"),)


class ProfessionalArea(db.Model):
    __tablename__ = "professional_areas"
    id = db.Column(db.Integer, primary_key=True)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), nullable=False)
    locality_id = db.Column(db.Integer, db.ForeignKey("localities.id"), nullable=False)
    professional = db.relationship("ProfessionalProfile", back_populates="areas")
    locality = db.relationship("Locality")
    __table_args__ = (UniqueConstraint("professional_id", "locality_id", name="uq_pro_area"),)


class WorkingHours(db.Model):
    __tablename__ = "working_hours"
    id = db.Column(db.Integer, primary_key=True)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)  # Monday = 0
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    professional = db.relationship("ProfessionalProfile", back_populates="working_hours")
    __table_args__ = (
        UniqueConstraint("professional_id", "weekday", name="uq_pro_weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_wh_weekday"),
        CheckConstraint("start_time < end_time", name="ck_wh_order"),
    )


class TimeOff(db.Model):
    __tablename__ = "time_off"
    id = db.Column(db.Integer, primary_key=True)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), nullable=False)
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(120))
    professional = db.relationship("ProfessionalProfile", back_populates="time_off")


class RecurringSchedule(db.Model):
    """The rule. Real visits are Booking rows that point back here."""
    __tablename__ = "recurring_schedules"
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(12), unique=True, nullable=False, default=lambda: make_reference("RS"))
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    address_id = db.Column(db.Integer, db.ForeignKey("addresses.id"), nullable=False)
    preferred_professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"))
    weekdays = db.Column(db.String(20), nullable=False)  # "0,2,4"
    start_time = db.Column(db.Time, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    tasks_json = db.Column(db.Text, default="[]")
    service_ids = db.Column(db.String(100), nullable=False)
    instructions = db.Column(db.String(1000), default="")
    status = db.Column(db.String(12), nullable=False, default="active")  # active | cancelled | ended
    cancel_reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)

    customer = db.relationship("User")
    service = db.relationship("Service")
    address = db.relationship("Address")
    preferred_professional = db.relationship("ProfessionalProfile")
    visits = db.relationship("Booking", back_populates="schedule", order_by="Booking.scheduled_start")

    __table_args__ = (CheckConstraint("status IN ('active','cancelled','ended')", name="ck_sched_status"),)

    @property
    def weekday_list(self):
        return [int(x) for x in self.weekdays.split(",") if x != ""]

    @property
    def weekday_names(self):
        return ", ".join(WEEKDAYS[d][:3] for d in self.weekday_list)

    @property
    def tasks(self):
        return json.loads(self.tasks_json or "[]")


class Booking(db.Model):
    __tablename__ = "bookings"
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(12), unique=True, nullable=False, default=lambda: make_reference("QF"))
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    schedule_id = db.Column(db.Integer, db.ForeignKey("recurring_schedules.id"), index=True)
    address_id = db.Column(db.Integer, db.ForeignKey("addresses.id"), nullable=False)
    locality_id = db.Column(db.Integer, db.ForeignKey("localities.id"), nullable=False, index=True)
    address_text = db.Column(db.String(400), nullable=False)  # snapshot at booking time
    service_ids = db.Column(db.String(100), nullable=False)  # every service the pro must offer
    tasks_json = db.Column(db.Text, default="[]")

    scheduled_start = db.Column(db.DateTime, nullable=False, index=True)
    scheduled_end = db.Column(db.DateTime, nullable=False)
    original_start = db.Column(db.DateTime, nullable=False)  # never changes; stops regenerated visits
    duration_minutes = db.Column(db.Integer, nullable=False)

    details = db.Column(db.String(1000), default="")
    instructions = db.Column(db.String(1000), default="")
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)

    visit_fee = db.Column(db.Integer, nullable=False, default=0)
    service_charge = db.Column(db.Integer, nullable=False, default=0)
    duration_charge = db.Column(db.Integer, nullable=False, default=0)
    total_amount = db.Column(db.Integer, nullable=False, default=0)  # estimate or fixed total
    final_amount = db.Column(db.Integer)  # set on completion
    is_estimate = db.Column(db.Boolean, nullable=False, default=False)
    is_quote = db.Column(db.Boolean, nullable=False, default=False)
    price_note = db.Column(db.String(300), default="")

    cancel_reason = db.Column(db.String(300))
    cancelled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    late_cancellation = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    updated_at = db.Column(db.DateTime, nullable=False, default=now_ist, onupdate=now_ist)
    accepted_at = db.Column(db.DateTime)
    started_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    cancelled_at = db.Column(db.DateTime)

    customer = db.relationship("User", foreign_keys=[customer_id])
    professional = db.relationship("ProfessionalProfile")
    service = db.relationship("Service")
    schedule = db.relationship("RecurringSchedule", back_populates="visits")
    address = db.relationship("Address")
    locality = db.relationship("Locality")
    payment = db.relationship("Payment", back_populates="booking", uselist=False)
    review = db.relationship("Review", back_populates="booking", uselist=False)
    history = db.relationship("BookingStatusHistory", back_populates="booking", order_by="BookingStatusHistory.id")

    __table_args__ = (
        CheckConstraint(f"status IN {BOOKING_STATUSES!r}".replace("[", "(").replace("]", ")"), name="ck_booking_status"),
        UniqueConstraint("schedule_id", "original_start", name="uq_schedule_visit"),
        # Database-level backstop against double booking: one professional cannot hold two
        # active jobs starting at the same moment. Overlap checks happen in services/matching.py.
        Index("uq_pro_active_slot", "professional_id", "scheduled_start", unique=True,
              sqlite_where=text("professional_id IS NOT NULL AND status IN ('accepted','on_the_way','in_progress')")),
    )

    @property
    def tasks(self):
        return json.loads(self.tasks_json or "[]")

    @property
    def amount_due(self):
        return self.final_amount if self.final_amount is not None else self.total_amount

    @property
    def is_confirmed(self):
        return self.status in ACTIVE_STATUSES or self.status == "completed"

    @property
    def awaiting_availability(self):
        return self.status == "pending" and self.professional_id is None

    @property
    def status_label(self):
        if self.awaiting_availability:
            return "Awaiting a professional"
        return {"pending": "Pending", "accepted": "Accepted", "rejected": "Rejected", "on_the_way": "On the way",
                "in_progress": "In progress", "completed": "Completed", "cancelled": "Cancelled"}[self.status]


class BookingStatusHistory(db.Model):
    __tablename__ = "booking_status_history"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False, index=True)
    from_status = db.Column(db.String(20))
    to_status = db.Column(db.String(20), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    booking = db.relationship("Booking", back_populates="history")
    actor = db.relationship("User")


class Review(db.Model):
    __tablename__ = "reviews"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    professional_id = db.Column(db.Integer, db.ForeignKey("professional_profiles.id"), nullable=False, index=True)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(1000), default="")
    status = db.Column(db.String(10), nullable=False, default="visible")  # visible | hidden
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    booking = db.relationship("Booking", back_populates="review")
    customer = db.relationship("User")
    professional = db.relationship("ProfessionalProfile")
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_rating"),
        CheckConstraint("status IN ('visible','hidden')", name="ck_review_status"),
    )


class ReviewReport(db.Model):
    __tablename__ = "review_reports"
    id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("reviews.id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="open")  # open | resolved
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    review = db.relationship("Review")
    reporter = db.relationship("User")
    __table_args__ = (UniqueConstraint("review_id", "reporter_id", name="uq_report_once"),)


class Payment(db.Model):
    """Payment status is deliberately separate from booking status."""
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), unique=True, nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(20), nullable=False, default="cash")
    provider = db.Column(db.String(20), nullable=False, default="manual")  # future: "razorpay", ...
    provider_ref = db.Column(db.String(100))
    status = db.Column(db.String(10), nullable=False, default="unpaid")  # unpaid | paid | refunded | void
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    booking = db.relationship("Booking", back_populates="payment")
    __table_args__ = (CheckConstraint("status IN ('unpaid','paid','refunded','void')", name="ck_payment_status"),)


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.String(300), default="")
    link = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)


class Complaint(db.Model):
    __tablename__ = "complaints"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"))
    subject = db.Column(db.String(120), nullable=False)
    message = db.Column(db.String(2000), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="open")  # open | in_review | resolved
    admin_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist)
    user = db.relationship("User")
    booking = db.relationship("Booking")
    __table_args__ = (CheckConstraint("status IN ('open','in_review','resolved')", name="ck_complaint_status"),)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(60), nullable=False)
    target_type = db.Column(db.String(40))
    target_id = db.Column(db.Integer)
    detail = db.Column(db.String(400))
    created_at = db.Column(db.DateTime, nullable=False, default=now_ist, index=True)
    actor = db.relationship("User")
