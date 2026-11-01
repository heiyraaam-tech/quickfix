"""Recurring house help: a RecurringSchedule rule plus explicit visit (Booking) rows.

There is NO background scheduler. Visits are generated explicitly: when a schedule is created,
whenever its page is opened, and by `flask generate-visits` (run it daily with cron if wanted).
Generation is idempotent - (schedule_id, original_start) is unique in the database.
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import RecurringSchedule, now_ist
from . import matching
from .bookings import BookingError, LATE_CANCEL_HOURS, build_booking, change_status, validate_slot
from .notify import notify

HORIZON_DAYS = 28
MAX_SPAN_DAYS = 90


def create_schedule(customer, service, extra_services, address, weekdays, start_time, minutes, start_date, end_date,
                    tasks, instructions="", preferred_professional=None):
    days = sorted({d for d in weekdays if 0 <= d <= 6})
    if not days:
        raise BookingError("Choose at least one weekday.")
    if end_date < start_date:
        raise BookingError("The end date must be after the start date.")
    if (end_date - start_date).days > MAX_SPAN_DAYS:
        raise BookingError(f"A schedule can cover at most {MAX_SPAN_DAYS} days. You can renew it afterwards.")
    if start_date < now_ist().date():
        raise BookingError("The start date cannot be in the past.")
    if address is None or address.user_id != customer.id or address.deleted:
        raise BookingError("Choose one of your saved addresses.")
    sched = RecurringSchedule(
        customer_id=customer.id, service_id=service.id, address_id=address.id,
        preferred_professional_id=preferred_professional.id if preferred_professional else None,
        weekdays=",".join(map(str, days)), start_time=start_time, duration_minutes=minutes,
        start_date=start_date, end_date=end_date, tasks_json=json.dumps(tasks),
        service_ids=",".join(str(s.id) for s in [service, *extra_services]), instructions=(instructions or "")[:1000])
    db.session.add(sched)
    db.session.flush()
    created = generate_visits(sched, commit=False, _extra=extra_services)
    if created == 0:
        db.session.rollback()
        raise BookingError("No visits fall inside that date range. Check the weekdays and dates.")
    notify(customer.id, "Recurring schedule created",
           f"{sched.reference}: {created} upcoming visits were created. They are confirmed only once a professional accepts each one.",
           f"/schedules/{sched.reference}")
    db.session.commit()
    return sched


def generate_visits(sched, today=None, horizon_days=HORIZON_DAYS, commit=True, _extra=None):
    """Create any missing visits in the horizon. Returns how many were created."""
    if sched.status != "active":
        return 0
    now = now_ist()
    today = today or now.date()
    if today > sched.end_date:
        sched.status = "ended"
        if commit:
            db.session.commit()
        return 0
    from ..models import Service
    extra = _extra if _extra is not None else [
        s for s in Service.query.filter(Service.id.in_(matching.parse_ids(sched.service_ids))).all() if s.id != sched.service_id]
    last = min(sched.end_date, today + timedelta(days=horizon_days))
    d, created = max(sched.start_date, today), 0
    while d <= last:
        if d.weekday() in sched.weekday_list:
            start = datetime.combine(d, sched.start_time)
            if start >= now + timedelta(minutes=60):
                exists = _booking().query.filter_by(schedule_id=sched.id, original_start=start).first() is not None
                if not exists:
                    pro = sched.preferred_professional
                    if pro and not matching.is_eligible(pro, matching.parse_ids(sched.service_ids), sched.address.locality_id,
                                                        start, sched.duration_minutes)[0]:
                        pro = None
                    try:
                        with db.session.begin_nested():
                            b = build_booking(sched.customer, sched.service, sched.address, start, sched.duration_minutes,
                                              professional=pro, tasks=sched.tasks, extra_services=extra,
                                              instructions=sched.instructions, schedule=sched, original_start=start)
                        created += 1
                        if b.professional_id:
                            notify(b.professional.user_id, "Recurring visit request",
                                   f"{b.service.name} on {start:%a %d %b, %I:%M %p}.", f"/pro/jobs/{b.reference}")
                    except IntegrityError:
                        pass  # another request generated it first - exactly what the constraint is for
        d += timedelta(days=1)
    if commit:
        db.session.commit()
    return created


def _booking():
    from ..models import Booking
    return Booking


def generate_all(commit=True):
    total = 0
    for s in RecurringSchedule.query.filter_by(status="active").all():
        total += generate_visits(s, commit=False)
    if commit:
        db.session.commit()
    return total


def cancel_schedule(sched, actor, reason):
    if actor.role != "admin" and sched.customer_id != actor.id:
        raise BookingError("Not allowed.")
    if sched.status != "active":
        raise BookingError("This schedule is already closed.")
    sched.status, sched.cancel_reason = "cancelled", (reason or "Cancelled by customer")[:300]
    n = _cancel_future_visits(sched, actor, "Schedule cancelled")
    notify(sched.customer_id, "Schedule cancelled", f"{sched.reference}: {n} upcoming visits were cancelled.", f"/schedules/{sched.reference}")
    db.session.commit()
    return n


def _cancel_future_visits(sched, actor, reason):
    n = 0
    for v in list(sched.visits):
        if v.status in ("pending", "accepted") and v.scheduled_start > now_ist():
            change_status_force_cancel(v, actor, reason)
            n += 1
    return n


def change_status_force_cancel(visit, actor, reason):
    """Bulk cancel without the 'give a reason' prompt; uses the same state machine and history."""
    change_status(visit, "cancelled", actor, note=reason)


def update_schedule(sched, actor, weekdays, start_time, end_date):
    """Change the pattern: cancel future untouched visits and regenerate under the new rule."""
    if sched.customer_id != actor.id:
        raise BookingError("Not allowed.")
    if sched.status != "active":
        raise BookingError("Only active schedules can be changed.")
    days = sorted({d for d in weekdays if 0 <= d <= 6})
    if not days:
        raise BookingError("Choose at least one weekday.")
    if end_date < max(sched.start_date, now_ist().date()):
        raise BookingError("The end date must be in the future.")
    if (end_date - sched.start_date).days > MAX_SPAN_DAYS:
        raise BookingError(f"A schedule can cover at most {MAX_SPAN_DAYS} days.")
    for v in sched.visits:
        if v.status in ("accepted", "on_the_way", "in_progress") and v.scheduled_start - now_ist() < timedelta(hours=LATE_CANCEL_HOURS) and v.scheduled_start > now_ist():
            raise BookingError("A visit starting within 2 hours is already confirmed; cancel it separately first.")
    _cancel_future_visits(sched, actor, "Schedule changed by customer")
    sched.weekdays, sched.start_time, sched.end_date = ",".join(map(str, days)), start_time, end_date
    db.session.flush()
    # cancelled originals keep their original_start, so they are never regenerated
    created = generate_visits(sched, commit=False)
    notify(actor.id, "Schedule updated", f"{sched.reference}: {created} new visits created.", f"/schedules/{sched.reference}")
    db.session.commit()
    return created


def accept_series(sched, actor):
    """A professional accepts every pending visit of a schedule that does not conflict."""
    ok = skipped = 0
    for v in list(sched.visits):
        if v.status == "pending" and v.scheduled_start > now_ist() and v.professional_id in (None, actor.professional.id):
            try:
                change_status(v, "accepted", actor)
                ok += 1
            except BookingError:
                skipped += 1
    return ok, skipped
