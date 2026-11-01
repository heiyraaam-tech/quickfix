import json
from datetime import date, datetime, time, timedelta

from werkzeug.datastructures import MultiDict
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..auth.decorators import role_required
from ..extensions import db
from ..models import (Address, Booking, Category, Locality, Notification, ProfessionalProfile, RecurringSchedule,
                      Review, Service, WEEKDAYS, now_ist)
from ..services import bookings as bk, matching, pricing, recurring, reviews
from ..services.bookings import BookingError
from ..validators import clean, normalize_phone, password_problem, valid_pincode

bp = Blueprint("customer", __name__)
customer_only = role_required("customer")


def _addresses():
    return current_user.addresses.filter_by(deleted=False).order_by(Address.is_default.desc(), Address.id).all()


def _address_from_form():
    """Use a saved address or create one inline. Returns an Address or raises BookingError."""
    aid = request.form.get("address_id", "")
    if aid and aid != "new":
        a = db.session.get(Address, int(aid)) if aid.isdigit() else None
        if not a or a.user_id != current_user.id or a.deleted:
            raise BookingError("Choose one of your saved addresses.")
        return a
    line1, pin = clean(request.form.get("line1"), 200), clean(request.form.get("pincode"), 6)
    loc = db.session.get(Locality, int(request.form["locality_id"])) if request.form.get("locality_id", "").isdigit() else None
    if len(line1) < 5 or not loc or not loc.is_active or not valid_pincode(pin):
        raise BookingError("Enter your house/flat details, locality and a 6-digit pincode.")
    first = current_user.addresses.filter_by(deleted=False).count() == 0
    a = Address(user_id=current_user.id, label=clean(request.form.get("label"), 40) or "Home", line1=line1,
                landmark=clean(request.form.get("landmark"), 120), locality_id=loc.id, pincode=pin, is_default=first)
    db.session.add(a)
    db.session.flush()
    return a


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- quote API
@bp.route("/api/quote")
@customer_only
def quote():
    """Server-calculated price + eligible professionals for the booking form."""
    svc = Service.query.filter_by(slug=request.args.get("service", ""), is_active=True).first()
    if not svc:
        return jsonify(error="Unknown service."), 404
    extra = Service.query.filter(Service.slug.in_(request.args.getlist("task")), Service.is_active.is_(True)).all()
    minutes = _int(request.args.get("minutes"), svc.default_minutes)
    if svc.is_house_help and extra:
        svc = max([svc, *extra], key=lambda s: s.hourly_rate)
        extra = [s for s in [*extra] if s.id != svc.id]
    try:
        price = pricing.calculate(svc, minutes)
    except pricing.PricingError as e:
        return jsonify(error=str(e)), 400
    out = {"price": price, "service": svc.name, "providers": [], "alternatives": [], "checked": False}
    loc_id, date_s, time_s = _int(request.args.get("locality_id")), request.args.get("date"), request.args.get("time")
    if loc_id and date_s and time_s:
        try:
            start = bk.parse_dt(date_s, time_s)
            bk.validate_slot(start)
        except BookingError as e:
            out["error"] = str(e)
            return jsonify(out)
        ids = [svc.id] + [s.id for s in extra]
        pros = matching.eligible_professionals(ids, loc_id, start, minutes)
        out["checked"] = True
        out["providers"] = []
        for p in pros:
            off = p.offering_for(svc.id)
            pp = pricing.calculate(svc, minutes, off.rate_override if off else None)
            out["providers"].append({"id": p.id, "name": p.user.name, "experience": p.experience_years or 0,
                                     "rating": p.rating_avg, "reviews": p.rating_count, "total": pp["total"]})
        if not pros:
            out["alternatives"] = [{"date": a.strftime("%Y-%m-%d"), "time": a.strftime("%H:%M"), "label": a.strftime("%a %d %b, %I:%M %p")}
                                   for a in matching.alternative_slots(ids, loc_id, start, minutes)]
    return jsonify(out)


# ---------------------------------------------------------------- booking
@bp.route("/book/<slug>", methods=["GET", "POST"])
@customer_only
def book(slug):
    if slug == "house-help":
        return redirect(url_for("customer.book_house_help"))
    svc = Service.query.filter_by(slug=slug, is_active=True).first_or_404()
    if svc.is_house_help:
        return redirect(url_for("customer.book_house_help", task=svc.slug))
    if request.method == "POST":
        try:
            start = bk.parse_dt(request.form.get("date"), request.form.get("time"))
            minutes = _int(request.form.get("minutes"), svc.default_minutes)
            address = _address_from_form()
            pro = _chosen_pro()
            booking = bk.create_booking(current_user, svc, address, start, minutes, professional=pro,
                                        details=clean(request.form.get("details"), 1000),
                                        instructions=clean(request.form.get("instructions"), 1000))
            flash(f"Booking {booking.reference} created. It is confirmed once a professional accepts it.", "success")
            return redirect(url_for("customer.booking_detail", ref=booking.reference))
        except BookingError as e:
            db.session.rollback()
            flash(str(e), "error")
            return render_template("book.html", service=svc, addresses=_addresses(), localities=_localities(), form=request.form), 400
    return render_template("book.html", service=svc, addresses=_addresses(), localities=_localities(), form={})


def _localities():
    return Locality.query.filter_by(is_active=True).order_by(Locality.name).all()


def _chosen_pro():
    pid = request.form.get("professional_id", "any")
    if pid in ("", "any"):
        return None
    pro = db.session.get(ProfessionalProfile, int(pid)) if pid.isdigit() else None
    if not pro:
        raise BookingError("That professional could not be found.")
    return pro


@bp.route("/book-house-help", methods=["GET", "POST"])
@customer_only
def book_house_help():
    tasks = Service.query.join(Category).filter(Category.slug == "house-help", Service.is_active.is_(True)).order_by(Service.sort_order).all()
    ctx = dict(tasks=tasks, addresses=_addresses(), localities=_localities(), preselect=request.args.get("task", ""),
               today=now_ist().date().isoformat())
    if request.method == "POST":
        try:
            chosen = [t for t in tasks if t.slug in request.form.getlist("task")]
            if not chosen:
                raise BookingError("Choose at least one task.")
            primary = max(chosen, key=lambda s: s.hourly_rate)  # one hourly rate: the highest among the chosen tasks
            extra = [t for t in chosen if t.id != primary.id]
            minutes = _int(request.form.get("minutes"), 0)
            address = _address_from_form()
            names = [t.name for t in chosen]
            instr = clean(request.form.get("instructions"), 1000)
            start_time = datetime.strptime(request.form.get("time", ""), "%H:%M").time() if request.form.get("time") else None
            if start_time is None:
                raise BookingError("Choose a start time.")
            if request.form.get("mode") == "recurring":
                days = [int(x) for x in request.form.getlist("weekday") if x.isdigit()]
                sd = datetime.strptime(request.form.get("start_date", ""), "%Y-%m-%d").date()
                ed = datetime.strptime(request.form.get("end_date", ""), "%Y-%m-%d").date()
                pro = _chosen_pro() if request.form.get("professional_id") not in (None, "", "any") else None
                sched = recurring.create_schedule(current_user, primary, extra, address, days, start_time, minutes, sd, ed,
                                                  names, instr, pro)
                flash(f"Schedule {sched.reference} created. Each visit is confirmed only once a professional accepts it.", "success")
                return redirect(url_for("customer.schedule_detail", ref=sched.reference))
            start = datetime.combine(datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date(), start_time)
            booking = bk.create_booking(current_user, primary, address, start, minutes, professional=_chosen_pro(),
                                        tasks=names, extra_services=extra, instructions=instr)
            flash(f"Booking {booking.reference} created. It is confirmed once a professional accepts it.", "success")
            return redirect(url_for("customer.booking_detail", ref=booking.reference))
        except ValueError:
            db.session.rollback()
            flash("Check the dates and times you entered.", "error")
        except BookingError as e:
            db.session.rollback()
            flash(str(e), "error")
        return render_template("book_house_help.html", form=request.form, **ctx), 400
    return render_template("book_house_help.html", form=MultiDict(), **ctx)


# ---------------------------------------------------------------- my bookings
def _own_booking(ref):
    b = Booking.query.filter_by(reference=ref).first_or_404()
    if b.customer_id != current_user.id:
        abort(404)  # do not reveal that someone else's booking exists
    return b


@bp.route("/bookings")
@customer_only
def my_bookings():
    now = now_ist()
    allb = Booking.query.filter_by(customer_id=current_user.id).order_by(Booking.scheduled_start).all()
    upcoming = [b for b in allb if b.status in ("pending", "accepted", "on_the_way", "in_progress")]
    past = sorted([b for b in allb if b not in upcoming], key=lambda b: b.scheduled_start, reverse=True)
    scheds = RecurringSchedule.query.filter_by(customer_id=current_user.id, status="active").all()
    return render_template("bookings.html", upcoming=upcoming, past=past, schedules=scheds, tab=request.args.get("tab", "upcoming"))


@bp.route("/bookings/<ref>")
@customer_only
def booking_detail(ref):
    b = _own_booking(ref)
    can_cancel = b.status in ("pending", "accepted")
    return render_template("booking_detail.html", b=b, can_cancel=can_cancel, can_reschedule=can_cancel and b.scheduled_start - now_ist() >= timedelta(hours=bk.LATE_CANCEL_HOURS))


@bp.route("/bookings/<ref>/cancel", methods=["POST"])
@customer_only
def cancel(ref):
    b = _own_booking(ref)
    try:
        bk.change_status(b, "cancelled", current_user, clean(request.form.get("reason"), 300))
        flash("Booking cancelled.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.booking_detail", ref=ref))


@bp.route("/bookings/<ref>/reschedule", methods=["POST"])
@customer_only
def reschedule(ref):
    b = _own_booking(ref)
    try:
        bk.reschedule(b, current_user, bk.parse_dt(request.form.get("date"), request.form.get("time")))
        flash("Rescheduled. Your professional needs to confirm the new time.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.booking_detail", ref=ref))


@bp.route("/bookings/<ref>/review", methods=["POST"])
@customer_only
def review(ref):
    b = _own_booking(ref)
    try:
        reviews.create_review(b, current_user, _int(request.form.get("rating"), 0), request.form.get("comment"))
        flash("Thanks for your review.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.booking_detail", ref=ref))


@bp.route("/reviews/<int:rid>/report", methods=["POST"])
@role_required("customer", "professional")
def report_review(rid):
    r = db.session.get(Review, rid) or abort(404)
    try:
        reviews.report_review(r, current_user, request.form.get("reason"))
        flash("Thanks. A moderator will look at this review.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(request.referrer or url_for("main.home"))


# ---------------------------------------------------------------- schedules
def _own_schedule(ref):
    s = RecurringSchedule.query.filter_by(reference=ref).first_or_404()
    if s.customer_id != current_user.id:
        abort(404)
    return s


@bp.route("/schedules/<ref>")
@customer_only
def schedule_detail(ref):
    s = _own_schedule(ref)
    recurring.generate_visits(s)  # idempotent top-up; there is no background scheduler
    now = now_ist()
    upcoming = [v for v in s.visits if v.status in ("pending", "accepted", "on_the_way", "in_progress") and v.scheduled_end >= now]
    past = [v for v in s.visits if v not in upcoming]
    return render_template("schedule_detail.html", s=s, upcoming=upcoming, past=past, today=now.date().isoformat())


@bp.route("/schedules/<ref>/cancel", methods=["POST"])
@customer_only
def cancel_schedule(ref):
    s = _own_schedule(ref)
    try:
        n = recurring.cancel_schedule(s, current_user, clean(request.form.get("reason"), 300))
        flash(f"Schedule cancelled. {n} upcoming visits were cancelled.", "success")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.schedule_detail", ref=ref))


@bp.route("/schedules/<ref>/update", methods=["POST"])
@customer_only
def update_schedule(ref):
    s = _own_schedule(ref)
    try:
        days = [int(x) for x in request.form.getlist("weekday") if x.isdigit()]
        st = datetime.strptime(request.form.get("time", ""), "%H:%M").time()
        ed = datetime.strptime(request.form.get("end_date", ""), "%Y-%m-%d").date()
        n = recurring.update_schedule(s, current_user, days, st, ed)
        flash(f"Schedule updated. {n} new visits created; changed visits need a professional to accept again.", "success")
    except ValueError:
        flash("Check the time and end date.", "error")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.schedule_detail", ref=ref))


# ---------------------------------------------------------------- account
@bp.route("/account", methods=["GET", "POST"])
@role_required("customer", "professional", "admin")
def account():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            name, phone = clean(request.form.get("name"), 120), normalize_phone(request.form.get("phone"))
            from ..models import User
            if len(name) < 2 or phone is None:
                flash("Enter your name and a valid 10-digit mobile number (or leave it empty).", "error")
            elif phone and User.query.filter(User.phone == phone, User.id != current_user.id).first():
                flash("That phone number is used by another account.", "error")
            else:
                current_user.name, current_user.phone = name, phone or None
                db.session.commit()
                flash("Profile saved.", "success")
        elif action == "password":
            if not current_user.check_password(request.form.get("current", "")):
                flash("Your current password is wrong.", "error")
            elif (p := password_problem(request.form.get("new", ""))):
                flash(p, "error")
            else:
                current_user.set_password(request.form["new"])
                db.session.commit()
                flash("Password changed.", "success")
        return redirect(url_for("customer.account"))
    return render_template("account.html", addresses=_addresses(), localities=_localities())


@bp.route("/account/addresses", methods=["POST"])
@customer_only
def add_address():
    try:
        request.form  # noqa
        a = _address_from_form() if request.form.get("address_id") == "new" else None
        db.session.commit()
        flash("Address saved." if a else "Nothing to save.", "success" if a else "error")
    except BookingError as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("customer.account"))


@bp.route("/account/addresses/<int:aid>/<action>", methods=["POST"])
@customer_only
def address_action(aid, action):
    a = db.session.get(Address, aid)
    if not a or a.user_id != current_user.id or a.deleted:
        abort(404)
    if action == "default":
        for x in _addresses():
            x.is_default = x.id == a.id
    elif action == "delete":
        a.deleted, a.is_default = True, False
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for("customer.account"))


# ---------------------------------------------------------------- notifications (all roles)
@bp.route("/notifications")
@role_required("customer", "professional", "admin")
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(100).all()
    return render_template("notifications.html", items=items)


@bp.route("/notifications/read", methods=["POST"])
@role_required("customer", "professional", "admin")
def notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return redirect(request.referrer or url_for("customer.notifications"))


@bp.route("/notifications/<int:nid>/open")
@role_required("customer", "professional", "admin")
def notification_open(nid):
    n = db.session.get(Notification, nid)
    if not n or n.user_id != current_user.id:
        abort(404)
    n.is_read = True
    db.session.commit()
    return redirect(n.link if (n.link or "").startswith("/") else url_for("customer.notifications"))


@bp.route("/api/notifications/count")
@role_required("customer", "professional", "admin")
def notification_count():
    return jsonify(unread=Notification.query.filter_by(user_id=current_user.id, is_read=False).count())
