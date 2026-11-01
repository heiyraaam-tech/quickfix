import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..auth.decorators import role_required
from ..extensions import db
from ..models import (AuditLog, Booking, BOOKING_STATUSES, Category, Complaint, Locality, Payment, ProfessionalProfile,
                      RecurringSchedule, Review, ReviewReport, Service, User, now_ist)
from ..services import bookings as bk, payments, recurring, reviews
from ..services.bookings import BookingError
from ..services.notify import audit, notify
from ..validators import clean

bp = Blueprint("admin", __name__, url_prefix="/admin")
admin_only = role_required("admin")


def _back(default="admin.dashboard"):
    return redirect(request.referrer or url_for(default))


@bp.route("")
@admin_only
def dashboard():
    counts = dict(db.session.query(Booking.status, func.count()).group_by(Booking.status).all())
    paid = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.status == "paid").scalar()
    owed = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).join(Booking).filter(Payment.status == "unpaid", Booking.status == "completed").scalar()
    stats = {
        "customers": User.query.filter_by(role="customer").count(),
        "professionals": ProfessionalProfile.query.count(),
        "pending_pros": ProfessionalProfile.query.filter_by(verification_status="pending").count(),
        "bookings": sum(counts.values()), "completed": counts.get("completed", 0),
        "open_complaints": Complaint.query.filter(Complaint.status != "resolved").count(),
        "open_reports": ReviewReport.query.filter_by(status="open").count(),
        "paid": paid, "owed": owed, "active_schedules": RecurringSchedule.query.filter_by(status="active").count(),
    }
    return render_template("admin/dashboard.html", stats=stats, counts=counts, statuses=BOOKING_STATUSES)


@bp.route("/users")
@admin_only
def users():
    role = request.args.get("role", "")
    q = User.query.order_by(User.created_at.desc())
    if role in ("customer", "professional", "admin"):
        q = q.filter_by(role=role)
    return render_template("admin/users.html", users=q.limit(300).all(), role=role)


@bp.route("/users/<int:uid>/<action>", methods=["POST"])
@admin_only
def user_action(uid, action):
    u = db.session.get(User, uid) or abort(404)
    if u.id == current_user.id or u.role == "admin":
        flash("Administrator accounts can't be changed from the website.", "error")
        return _back("admin.users")
    if action not in ("suspend", "reactivate"):
        abort(404)
    u.active = action == "reactivate"
    audit(current_user, f"user.{action}", "user", u.id, clean(request.form.get("reason"), 300))
    db.session.commit()
    flash(f"{u.name} {'reactivated' if u.active else 'suspended'}.", "success")
    return _back("admin.users")


@bp.route("/professionals")
@admin_only
def professionals():
    status = request.args.get("status", "")
    q = ProfessionalProfile.query.order_by(ProfessionalProfile.submitted_at.desc())
    if status:
        q = q.filter_by(verification_status=status)
    return render_template("admin/professionals.html", pros=q.all(), status=status)


@bp.route("/professionals/<int:pid>/<action>", methods=["POST"])
@admin_only
def professional_action(pid, action):
    p = db.session.get(ProfessionalProfile, pid) or abort(404)
    reason = clean(request.form.get("reason"), 400)
    if action == "approve":
        if not p.offerings or not p.areas:
            flash("This application has no services or service areas yet.", "error")
            return _back("admin.professionals")
        p.verification_status, p.approved_at, p.rejection_reason = "approved", now_ist(), None
        p.user.active = True
        notify(p.user_id, "Application approved", "You can now receive booking requests. Set your availability to Available.", "/pro/availability")
    elif action in ("reject", "suspend"):
        if not reason:
            flash("A reason is required.", "error")
            return _back("admin.professionals")
        p.verification_status = "rejected" if action == "reject" else "suspended"
        p.rejection_reason = reason
        if action == "suspend":
            p.availability_status = "offline"
        notify(p.user_id, "Application rejected" if action == "reject" else "Account suspended", reason, "/pro/profile")
    elif action == "reactivate":
        if p.verification_status != "suspended":
            abort(400)
        p.verification_status, p.rejection_reason = "approved", None
        notify(p.user_id, "Account reactivated", "You can receive bookings again.", "/pro")
    else:
        abort(404)
    audit(current_user, f"professional.{action}", "professional", p.id, reason)
    db.session.commit()
    flash(f"{p.user.name}: {action} done.", "success")
    return _back("admin.professionals")


@bp.route("/services")
@admin_only
def services():
    return render_template("admin/services.html", categories=Category.query.order_by(Category.sort_order).all())


def _service_form(s):
    f = request.form
    model = f.get("pricing_model", "")
    if model not in ("fixed", "visit_estimate", "hourly", "duration", "quote"):
        raise ValueError("Choose a pricing model.")
    nums = {}
    for k in ("visit_fee", "base_price", "hourly_rate", "min_minutes", "default_minutes"):
        v = f.get(k, "0").strip() or "0"
        if not v.isdigit() or int(v) > 1000000:
            raise ValueError(f"{k.replace('_', ' ').capitalize()} must be a whole number.")
        nums[k] = int(v)
    opts = f.get("duration_options", "").replace(" ", "")
    if opts and not re.fullmatch(r"\d+(,\d+)*", opts):
        raise ValueError("Duration options must be minutes separated by commas, e.g. 60,120,180.")
    if model in ("hourly", "duration"):
        durations = [int(x) for x in opts.split(",") if x]
        if not durations or nums["default_minutes"] not in durations or nums["hourly_rate"] <= 0:
            raise ValueError("Hourly and duration services need an hourly rate, duration options, and a default that is one of them.")
    if nums["default_minutes"] < 15:
        raise ValueError("Default duration must be at least 15 minutes.")
    s.name, s.description = clean(f.get("name"), 100), clean(f.get("description"), 400)
    if len(s.name) < 2:
        raise ValueError("Enter a service name.")
    s.keywords, s.pricing_model, s.duration_options = clean(f.get("keywords"), 300), model, opts
    s.is_active = bool(f.get("is_active"))
    for k, v in nums.items():
        setattr(s, k, v)


@bp.route("/services/<int:sid>", methods=["GET", "POST"])
@admin_only
def service_edit(sid):
    s = db.session.get(Service, sid) or abort(404)
    if request.method == "POST":
        try:
            _service_form(s)
            audit(current_user, "service.update", "service", s.id, s.name)
            db.session.commit()
            flash("Service saved.", "success")
            return redirect(url_for("admin.services"))
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("admin/service_edit.html", s=s, categories=Category.query.all())


@bp.route("/services/new", methods=["POST"])
@admin_only
def service_new():
    cat = db.session.get(Category, int(request.form["category_id"])) if request.form.get("category_id", "").isdigit() else None
    name = clean(request.form.get("name"), 100)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not cat or len(name) < 2 or not slug or Service.query.filter_by(slug=slug).first():
        flash("Choose a category and a new, unique service name.", "error")
        return redirect(url_for("admin.services"))
    s = Service(category_id=cat.id, name=name, slug=slug, pricing_model="quote", visit_fee=0, default_minutes=60, is_active=False)
    db.session.add(s)
    audit(current_user, "service.create", "service", None, name)
    db.session.commit()
    flash("Service created (inactive). Set its pricing, then activate it.", "success")
    return redirect(url_for("admin.service_edit", sid=s.id))


@bp.route("/categories/<int:cid>/toggle", methods=["POST"])
@admin_only
def category_toggle(cid):
    c = db.session.get(Category, cid) or abort(404)
    c.is_active = not c.is_active
    audit(current_user, "category.toggle", "category", c.id, f"active={c.is_active}")
    db.session.commit()
    return _back("admin.services")


@bp.route("/localities", methods=["GET", "POST"])
@admin_only
def localities():
    if request.method == "POST":
        name = clean(request.form.get("name"), 80)
        if len(name) < 3 or Locality.query.filter(func.lower(Locality.name) == name.lower()).first():
            flash("Enter a new locality name.", "error")
        else:
            db.session.add(Locality(name=name))
            audit(current_user, "locality.add", "locality", None, name)
            db.session.commit()
            flash("Locality added.", "success")
        return redirect(url_for("admin.localities"))
    return render_template("admin/localities.html", items=Locality.query.order_by(Locality.name).all())


@bp.route("/localities/<int:lid>/toggle", methods=["POST"])
@admin_only
def locality_toggle(lid):
    l = db.session.get(Locality, lid) or abort(404)
    l.is_active = not l.is_active
    audit(current_user, "locality.toggle", "locality", l.id, f"{l.name} active={l.is_active}")
    db.session.commit()
    return redirect(url_for("admin.localities"))


@bp.route("/bookings")
@admin_only
def bookings():
    status = request.args.get("status", "")
    q = Booking.query.order_by(Booking.scheduled_start.desc())
    if status in BOOKING_STATUSES:
        q = q.filter_by(status=status)
    if (ref := clean(request.args.get("q"), 12)):
        q = q.filter(Booking.reference.contains(ref.upper()))
    return render_template("admin/bookings.html", items=q.limit(200).all(), status=status, statuses=BOOKING_STATUSES)


@bp.route("/bookings/<ref>", methods=["GET", "POST"])
@admin_only
def booking_detail(ref):
    b = Booking.query.filter_by(reference=ref).first_or_404()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "status":
                bk.change_status(b, request.form.get("status", ""), current_user, clean(request.form.get("note"), 300))
            elif action == "paid":
                payments.mark_paid(b, current_user)
            elif action == "refund":
                payments.refund(b, current_user)
            flash("Updated.", "success")
        except (BookingError, payments.PaymentError) as e:
            db.session.rollback()
            flash(str(e), "error")
        return redirect(url_for("admin.booking_detail", ref=ref))
    from ..services.bookings import TRANSITIONS
    return render_template("admin/booking_detail.html", b=b, next_statuses=sorted(TRANSITIONS.get(b.status, [])))


@bp.route("/complaints", methods=["GET", "POST"])
@admin_only
def complaints():
    if request.method == "POST":
        c = db.session.get(Complaint, int(request.form.get("id", 0))) or abort(404)
        if request.form.get("status") in ("open", "in_review", "resolved"):
            c.status, c.admin_note = request.form["status"], clean(request.form.get("admin_note"), 500)
            audit(current_user, "complaint.update", "complaint", c.id, c.status)
            db.session.commit()
            flash("Complaint updated.", "success")
        return redirect(url_for("admin.complaints"))
    return render_template("admin/complaints.html", items=Complaint.query.order_by(Complaint.status == "resolved", Complaint.created_at.desc()).all())


@bp.route("/reviews")
@admin_only
def review_list():
    reports = ReviewReport.query.order_by(ReviewReport.status, ReviewReport.created_at.desc()).all()
    return render_template("admin/reviews.html", reviews=Review.query.order_by(Review.created_at.desc()).limit(100).all(), reports=reports)


@bp.route("/reviews/<int:rid>/<action>", methods=["POST"])
@admin_only
def review_action(rid, action):
    r = db.session.get(Review, rid) or abort(404)
    if action not in ("hide", "show"):
        abort(404)
    reviews.set_visibility(r, current_user, action == "show")
    ReviewReport.query.filter_by(review_id=r.id, status="open").update({"status": "resolved"})
    db.session.commit()
    return _back("admin.review_list")


@bp.route("/reports/<int:rid>/resolve", methods=["POST"])
@admin_only
def report_resolve(rid):
    r = db.session.get(ReviewReport, rid) or abort(404)
    r.status = "resolved"
    audit(current_user, "report.resolve", "review", r.review_id)
    db.session.commit()
    return _back("admin.review_list")


@bp.route("/schedules/generate", methods=["POST"])
@admin_only
def generate_visits():
    n = recurring.generate_all()
    flash(f"Generated {n} missing visits.", "success")
    return _back()


@bp.route("/audit")
@admin_only
def audit_log():
    return render_template("admin/audit.html", items=AuditLog.query.order_by(AuditLog.id.desc()).limit(300).all())
