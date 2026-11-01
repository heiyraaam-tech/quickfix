from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from ..auth.decorators import role_required
from ..content import DRAFT, FAQS, POLICIES
from ..extensions import db
from ..models import Booking, Category, Complaint, Locality, Review, Service
from ..services.notify import notify
from ..validators import clean

bp = Blueprint("main", __name__)


def active_categories():
    return Category.query.filter_by(is_active=True).order_by(Category.sort_order).all()


@bp.route("/")
def home():
    cats = active_categories()
    popular = Service.query.join(Category).filter(Service.is_active.is_(True), Category.is_active.is_(True),
                                                  Service.slug.in_(["electrician", "plumber", "bathroom-cleaning", "full-home-cleaning", "sweeping-mopping", "deep-cleaning"])).all()
    house_help = Service.query.join(Category).filter(Category.slug == "house-help", Service.is_active.is_(True)).order_by(Service.sort_order).all()
    reviews = Review.query.filter_by(status="visible").order_by(Review.created_at.desc()).limit(3).all()
    return render_template("home.html", categories=cats, popular=popular, house_help=house_help, reviews=reviews, faqs=FAQS[:5])


@bp.route("/services")
def services():
    q = clean(request.args.get("q"), 60).lower()
    cat = request.args.get("category", "")
    query = Service.query.join(Category).filter(Service.is_active.is_(True), Category.is_active.is_(True))
    if cat:
        query = query.filter(Category.slug == cat)
    items = query.order_by(Category.sort_order, Service.sort_order).all()
    if q:
        items = [s for s in items if q in f"{s.name} {s.keywords} {s.description} {s.category.name}".lower()]
    return render_template("services.html", services=items, q=q, cat=cat, categories=active_categories())


@bp.route("/services/<slug>")
def service_detail(slug):
    s = Service.query.filter_by(slug=slug, is_active=True).first_or_404()
    from ..services.pricing import calculate
    return render_template("service_detail.html", service=s, price=calculate(s, s.default_minutes))


@bp.route("/house-help")
def house_help():
    items = Service.query.join(Category).filter(Category.slug == "house-help", Service.is_active.is_(True)).order_by(Service.sort_order).all()
    return render_template("house_help.html", services=items)


@bp.route("/become-a-professional")
def become_pro():
    return render_template("become_pro.html")


@bp.route("/faq")
def faq():
    return render_template("faq.html", faqs=FAQS)


@bp.route("/policy/<slug>")
def policy(slug):
    if slug not in POLICIES:
        abort(404)
    title, paras = POLICIES[slug]
    return render_template("policy.html", title=title, paras=paras, draft=DRAFT, policies=POLICIES)


@bp.route("/support")
def support():
    bookings = []
    if current_user.is_authenticated and current_user.role == "customer":
        bookings = Booking.query.filter_by(customer_id=current_user.id).order_by(Booking.created_at.desc()).limit(20).all()
    return render_template("support.html", bookings=bookings)


@bp.route("/support/complaint", methods=["POST"])
@role_required("customer", "professional")
def complaint():
    subject, message = clean(request.form.get("subject"), 120), clean(request.form.get("message"), 2000)
    if len(subject) < 3 or len(message) < 10:
        flash("Add a subject and describe the problem in a few words.", "error")
        return redirect(url_for("main.support"))
    booking = None
    ref = clean(request.form.get("booking_ref"), 12)
    if ref:
        booking = Booking.query.filter_by(reference=ref).first()
        from ..services.bookings import can_view
        if not booking or not can_view(current_user, booking):
            booking = None
    db.session.add(Complaint(user_id=current_user.id, booking_id=booking.id if booking else None, subject=subject, message=message))
    db.session.commit()
    flash("Thanks. Your report was sent to the QuickFix team.", "success")
    return redirect(url_for("main.support"))


# ---- small JSON endpoints used by the page scripts ----
@bp.route("/api/locality", methods=["POST"])
def set_locality():
    data = request.get_json(silent=True) or {}
    loc = db.session.get(Locality, data["id"]) if isinstance(data.get("id"), int) else None
    if loc and loc.is_active:
        session["locality_id"] = loc.id
        return jsonify(ok=True, name=loc.name)
    session.pop("locality_id", None)
    return jsonify(ok=True, name=None)


@bp.route("/api/search")
def search():
    q = clean(request.args.get("q"), 40).lower()
    if len(q) < 2:
        return jsonify(results=[])
    items = Service.query.join(Category).filter(Service.is_active.is_(True), Category.is_active.is_(True)).all()
    hits = [s for s in items if q in f"{s.name} {s.keywords}".lower()][:6]
    return jsonify(results=[{"name": s.name, "category": s.category.name, "url": url_for("main.service_detail", slug=s.slug)} for s in hits])


@bp.route("/api/me")
def me():
    if not current_user.is_authenticated:
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, name=current_user.name, role=current_user.role)
