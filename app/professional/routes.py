from datetime import datetime, time, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func

from ..auth.decorators import role_required
from ..extensions import db
from ..models import (Booking, Category, Locality, ProfessionalArea, ProfessionalService, RecurringSchedule, Service,
                      TimeOff, WorkingHours, now_ist)
from ..services import bookings as bk, matching, payments, pricing, recurring
from ..services.bookings import BookingError
from ..validators import clean

bp = Blueprint("professional", __name__, url_prefix="/pro")
pro_only = role_required("professional")


def me():
    return current_user.professional


def _job(ref):
    b = Booking.query.filter_by(reference=ref).first_or_404()
    if not bk.can_view(current_user, b):
        abort(404)
    return b


@bp.route("")
@pro_only
def dashboard():
    p = me()
    direct = Booking.query.filter_by(professional_id=p.id, status="pending").order_by(Booking.scheduled_start).all()
    open_reqs = matching.open_requests_for(p)
    upcoming = (Booking.query.filter(Booking.professional_id == p.id, Booking.status.in_(("accepted", "on_the_way", "in_progress")))
                .order_by(Booking.scheduled_start).limit(10).all())
    done = Booking.query.filter_by(professional_id=p.id, status="completed").count()
    return render_template("pro/dashboard.html", p=p, direct=direct, open_reqs=open_reqs, upcoming=upcoming, done=done)


@bp.route("/jobs")
@pro_only
def jobs():
    p, tab = me(), request.args.get("tab", "upcoming")
    statuses = {"upcoming": ("accepted", "on_the_way", "in_progress"), "requests": ("pending",),
                "completed": ("completed",), "closed": ("cancelled", "rejected")}.get(tab, ("accepted", "on_the_way", "in_progress"))
    items = Booking.query.filter(Booking.professional_id == p.id, Booking.status.in_(statuses)).order_by(Booking.scheduled_start.desc() if tab in ("completed", "closed") else Booking.scheduled_start).all()
    open_reqs = matching.open_requests_for(p) if tab == "requests" else []
    return render_template("pro/jobs.html", items=items, open_reqs=open_reqs, tab=tab)


@bp.route("/jobs/<ref>")
@pro_only
def job_detail(ref):
    b = _job(ref)
    mine = me() and b.professional_id == me().id
    return render_template("pro/job_detail.html", b=b, mine=mine)


@bp.route("/jobs/<ref>/status", methods=["POST"])
@pro_only
def job_status(ref):
    b = _job(ref)
    new = request.form.get("status", "")
    amount = request.form.get("final_amount", "").strip()
    try:
        bk.change_status(b, new, current_user, clean(request.form.get("note"), 300),
                         final_amount=int(amount) if amount.isdigit() else (None if not amount else _bad_amount()))
        flash("Job updated.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("professional.job_detail", ref=ref))


def _bad_amount():
    raise BookingError("Enter the final amount as a whole number of rupees.")


@bp.route("/jobs/<ref>/paid", methods=["POST"])
@pro_only
def job_paid(ref):
    b = _job(ref)
    try:
        payments.mark_paid(b, current_user)
        flash("Marked as paid (cash).", "success")
    except payments.PaymentError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("professional.job_detail", ref=ref))


@bp.route("/schedules/<ref>/accept-all", methods=["POST"])
@pro_only
def accept_series(ref):
    s = RecurringSchedule.query.filter_by(reference=ref).first_or_404()
    sample = next((v for v in s.visits if v.status == "pending" and bk.can_view(current_user, v)), None)
    if not sample:
        abort(404)
    ok, skipped = recurring.accept_series(s, current_user)
    flash(f"Accepted {ok} visits." + (f" {skipped} could not be accepted because of clashes." if skipped else ""), "success" if ok else "error")
    return redirect(url_for("professional.jobs", tab="upcoming"))


@bp.route("/profile", methods=["GET", "POST"])
@pro_only
def profile():
    p = me()
    services = Service.query.join(Category).filter(Service.is_active.is_(True)).order_by(Category.sort_order, Service.sort_order).all()
    localities = Locality.query.filter_by(is_active=True).order_by(Locality.name).all()
    if request.method == "POST":
        bio, years = clean(request.form.get("bio"), 1000), request.form.get("experience", "0")
        errors = []
        if not years.isdigit() or int(years) > 60:
            errors.append("Experience must be a number of years between 0 and 60.")
        chosen = [s for s in services if request.form.get(f"svc_{s.id}")]
        areas = [l for l in localities if request.form.get(f"area_{l.id}")]
        if not chosen:
            errors.append("Choose at least one service you offer.")
        if not areas:
            errors.append("Choose at least one locality you serve.")
        rates = {}
        for s in chosen:
            raw = request.form.get(f"rate_{s.id}", "").strip()
            if raw:
                b = pricing.rate_bounds(s)
                if not raw.isdigit() or not b or not (b[0] <= int(raw) <= b[1]):
                    errors.append(f"{s.name}: your rate must be between ₹{b[0]} and ₹{b[1]}." if b else f"{s.name}: custom rates are not available.")
                else:
                    rates[s.id] = int(raw)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("pro/profile.html", p=p, services=services, localities=localities), 400
        p.bio, p.experience_years = bio, int(years)
        old = {o.service_id: o for o in p.offerings}
        for sid, o in list(old.items()):
            if sid not in {s.id for s in chosen}:
                db.session.delete(o)
        for s in chosen:
            if s.id in old:
                old[s.id].rate_override = rates.get(s.id)
            else:
                db.session.add(ProfessionalService(professional_id=p.id, service_id=s.id, rate_override=rates.get(s.id)))
        keep = {l.id for l in areas}
        for a in list(p.areas):
            if a.locality_id not in keep:
                db.session.delete(a)
        have = {a.locality_id for a in p.areas}
        for l in areas:
            if l.id not in have:
                db.session.add(ProfessionalArea(professional_id=p.id, locality_id=l.id))
        if p.verification_status == "rejected":
            p.verification_status, p.rejection_reason = "pending", None
            flash("Profile saved and sent for review again.", "success")
        else:
            flash("Profile saved.", "success")
        db.session.commit()
        return redirect(url_for("professional.profile"))
    return render_template("pro/profile.html", p=p, services=services, localities=localities)


@bp.route("/availability", methods=["GET", "POST"])
@pro_only
def availability():
    p = me()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "status":
            if request.form.get("availability") in ("available", "busy", "offline"):
                p.availability_status = request.form["availability"]
                db.session.commit()
                flash("Status updated.", "success")
        elif action == "hours":
            try:
                for d in range(7):
                    existing = p.hours_for(d)
                    if request.form.get(f"on_{d}"):
                        st = datetime.strptime(request.form.get(f"start_{d}", ""), "%H:%M").time()
                        en = datetime.strptime(request.form.get(f"end_{d}", ""), "%H:%M").time()
                        if st >= en:
                            raise ValueError(WEEKDAYSX[d])
                        if existing:
                            existing.start_time, existing.end_time = st, en
                        else:
                            db.session.add(WorkingHours(professional_id=p.id, weekday=d, start_time=st, end_time=en))
                    elif existing:
                        db.session.delete(existing)
                db.session.commit()
                flash("Working hours saved.", "success")
            except ValueError as e:
                db.session.rollback()
                flash("Check the working hours: the start time must be before the end time." + (f" ({e})" if str(e) else ""), "error")
        elif action == "timeoff":
            try:
                s = datetime.strptime(request.form.get("start", ""), "%Y-%m-%dT%H:%M")
                e = datetime.strptime(request.form.get("end", ""), "%Y-%m-%dT%H:%M")
                if e <= s:
                    raise ValueError
                db.session.add(TimeOff(professional_id=p.id, start_at=s, end_at=e, reason=clean(request.form.get("reason"), 120)))
                db.session.commit()
                flash("Time off added.", "success")
            except ValueError:
                flash("Enter a valid start and end; the end must be after the start.", "error")
        return redirect(url_for("professional.availability"))
    return render_template("pro/availability.html", p=p, hours={w.weekday: w for w in p.working_hours},
                           time_off=sorted([t for t in p.time_off if t.end_at > now_ist()], key=lambda t: t.start_at))


WEEKDAYSX = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@bp.route("/timeoff/<int:tid>/delete", methods=["POST"])
@pro_only
def timeoff_delete(tid):
    t = db.session.get(TimeOff, tid)
    if not t or t.professional_id != me().id:
        abort(404)
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for("professional.availability"))


@bp.route("/earnings")
@pro_only
def earnings():
    p = me()
    done = Booking.query.filter_by(professional_id=p.id, status="completed").order_by(Booking.completed_at.desc()).all()
    paid = sum(b.amount_due for b in done if b.payment and b.payment.status == "paid")
    owed = sum(b.amount_due for b in done if b.payment and b.payment.status == "unpaid")
    return render_template("pro/earnings.html", done=done, paid=paid, owed=owed)
