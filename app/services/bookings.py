"""Booking creation, the status state machine, cancellation and rescheduling."""
import json
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (ACTIVE_STATUSES, Booking, BookingStatusHistory, Payment, Service, now_ist)
from . import matching, pricing
from .notify import audit, notify

LEAD_MINUTES = 60
MAX_DAYS_AHEAD = 60
LATE_CANCEL_HOURS = 2


class BookingError(Exception):
    pass


TRANSITIONS = {
    "pending": {"accepted", "rejected", "cancelled"},
    "accepted": {"on_the_way", "in_progress", "cancelled"},
    "on_the_way": {"in_progress", "cancelled"},
    "in_progress": {"completed"},
}
ACTOR_STATUSES = {
    "customer": {"cancelled"},
    "professional": {"accepted", "rejected", "on_the_way", "in_progress", "completed", "cancelled"},
    "admin": {"accepted", "rejected", "on_the_way", "in_progress", "completed", "cancelled"},
}


def parse_dt(date_str, time_str):
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        raise BookingError("Choose a valid date and time.")


def validate_slot(start, enforce_lead=True):
    now = now_ist()
    if enforce_lead and start < now + timedelta(minutes=LEAD_MINUTES):
        raise BookingError("Pick a start time at least one hour from now.")
    if start > now + timedelta(days=MAX_DAYS_AHEAD):
        raise BookingError(f"Bookings can be made up to {MAX_DAYS_AHEAD} days ahead.")


def services_of(booking):
    ids = matching.parse_ids(booking.service_ids)
    return Service.query.filter(Service.id.in_(ids)).all()


def _history(booking, from_status, to_status, actor, note=None):
    db.session.add(BookingStatusHistory(booking_id=booking.id, from_status=from_status, to_status=to_status,
                                        actor_id=getattr(actor, "id", None), note=(note or "")[:300]))


def build_booking(customer, service, address, start, minutes, professional=None, tasks=None, extra_services=(),
                  details="", instructions="", schedule=None, original_start=None, enforce_lead=True):
    """Validate and add a Booking to the session (no commit). Prices are always computed here."""
    all_services = [service, *extra_services]
    if not all(s.is_active and s.category.is_active for s in all_services):
        raise BookingError("That service is not available right now.")
    if address is None or address.user_id != customer.id or address.deleted:
        raise BookingError("Choose one of your saved addresses.")
    if not address.locality.is_active:
        raise BookingError("We do not serve that locality yet.")
    validate_slot(start, enforce_lead)
    override = None
    if professional is not None:
        ok, why = matching.is_eligible(professional, [s.id for s in all_services], address.locality_id, start, minutes)
        if not ok:
            raise BookingError(why)
        off = professional.offering_for(service.id)
        override = off.rate_override if off else None
    try:
        price = pricing.calculate(service, minutes, override)
    except pricing.PricingError as exc:
        raise BookingError(str(exc))
    b = Booking(customer_id=customer.id, professional_id=professional.id if professional else None,
                service_id=service.id, schedule_id=schedule.id if schedule else None, address_id=address.id,
                locality_id=address.locality_id, address_text=address.full,
                service_ids=",".join(str(s.id) for s in all_services), tasks_json=json.dumps(tasks or []),
                scheduled_start=start, scheduled_end=start + timedelta(minutes=minutes),
                original_start=original_start or start, duration_minutes=minutes,
                details=(details or "")[:1000], instructions=(instructions or "")[:1000], status="pending",
                visit_fee=price["visit_fee"], service_charge=price["service_charge"],
                duration_charge=price["duration_charge"], total_amount=price["total"],
                is_estimate=price["is_estimate"], is_quote=price["is_quote"], price_note=price["note"])
    db.session.add(b)
    db.session.flush()
    db.session.add(Payment(booking_id=b.id, amount=b.total_amount))
    _history(b, None, "pending", customer, "Booking created")
    return b


def create_booking(customer, service, address, start, minutes, **kw):
    b = build_booking(customer, service, address, start, minutes, **kw)
    link_c, link_p = f"/bookings/{b.reference}", f"/pro/jobs/{b.reference}"
    notify(customer.id, "Booking created", f"{b.reference} is waiting for a professional to accept.", link_c)
    if b.professional_id:
        notify(b.professional.user_id, "New booking request", f"{b.service.name} on {b.scheduled_start:%d %b, %I:%M %p}.", link_p)
    else:
        for pro in matching.eligible_professionals(matching.parse_ids(b.service_ids), b.locality_id, start, minutes)[:20]:
            notify(pro.user_id, "New open request", f"{b.service.name} in {b.locality.name} on {b.scheduled_start:%d %b, %I:%M %p}.", link_p)
    db.session.commit()
    return b


def can_view(user, booking):
    if user.role == "admin":
        return True
    if user.role == "customer":
        return booking.customer_id == user.id
    if user.role == "professional" and user.professional:
        if booking.professional_id == user.professional.id:
            return True
        # unassigned open requests are visible to professionals who could actually take them
        return booking.professional_id is None and booking.status == "pending" and matching.is_eligible(
            user.professional, matching.parse_ids(booking.service_ids), booking.locality_id, booking.scheduled_start,
            booking.duration_minutes, exclude_booking_id=booking.id)[0]
    return False


def change_status(booking, new_status, actor, note=None, final_amount=None):
    old = booking.status
    if new_status not in TRANSITIONS.get(old, set()):
        raise BookingError(f"A {old.replace('_', ' ')} booking cannot become {new_status.replace('_', ' ')}.")
    if new_status not in ACTOR_STATUSES.get(actor.role, set()):
        raise BookingError("You are not allowed to make that change.")
    now = now_ist()
    claim = None

    if actor.role == "customer":
        if booking.customer_id != actor.id:
            raise BookingError("Not allowed.")
        if old not in ("pending", "accepted"):
            raise BookingError("This booking can no longer be cancelled online. Contact support.")
    if actor.role == "professional":
        pro = actor.professional
        if pro is None or not pro.is_approved:
            raise BookingError("Your professional account is not approved.")
        if booking.professional_id is None:
            if new_status != "accepted":
                raise BookingError("Accept the request first.")
            ok, why = matching.is_eligible(pro, matching.parse_ids(booking.service_ids), booking.locality_id,
                                           booking.scheduled_start, booking.duration_minutes,
                                           exclude_booking_id=booking.id, check_status=False)
            if not ok:
                raise BookingError(why)
            claim = pro
        elif booking.professional_id != pro.id:
            raise BookingError("Not allowed.")

    if new_status == "accepted":
        # double-booking guard: re-check inside this (write-locked) transaction
        pro_id = claim.id if claim else booking.professional_id
        if pro_id is None:
            raise BookingError("Assign a professional first.")
        if matching.has_conflict(pro_id, booking.scheduled_start, booking.scheduled_end, booking.id):
            raise BookingError("That professional already has a job at this time.")
        booking.professional_id = pro_id
        booking.accepted_at = now
    if new_status == "in_progress":
        booking.started_at = now
    if new_status == "completed":
        booking.completed_at = now
        if final_amount is not None:
            if not (booking.is_estimate or actor.role == "admin"):
                raise BookingError("The price for this booking is fixed.")
            if not (0 < final_amount <= 200000):
                raise BookingError("Enter a final amount between 1 and 200000.")
            booking.final_amount = final_amount
        elif booking.is_quote:
            raise BookingError("Enter the final amount for this custom-quote job.")
        else:
            booking.final_amount = booking.total_amount
        booking.payment.amount = booking.final_amount
    if new_status == "cancelled":
        if actor.role != "admin" and not (note or "").strip():
            raise BookingError("Please give a short reason for cancelling.")
        booking.cancel_reason = (note or "")[:300]
        booking.cancelled_by_id = actor.id
        booking.cancelled_at = now
        if old == "accepted" and booking.scheduled_start - now < timedelta(hours=LATE_CANCEL_HOURS):
            booking.late_cancellation = True
        booking.payment.status = "void"
    if new_status == "rejected":
        booking.cancel_reason = (note or "")[:300]

    booking.status = new_status
    try:
        _history(booking, old, new_status, actor, note)
        _notify_status(booking, new_status, actor)
        if actor.role == "admin":
            audit(actor, f"booking.{new_status}", "booking", booking.id, note)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise BookingError("That professional already has a job at this time.")
    return booking


def _notify_status(b, new_status, actor):
    link_c, link_p = f"/bookings/{b.reference}", f"/pro/jobs/{b.reference}"
    msgs = {
        "accepted": ("Booking accepted", f"{b.reference} is confirmed for {b.scheduled_start:%d %b, %I:%M %p}."),
        "rejected": ("Booking declined", f"{b.reference} was declined. You can book another professional."),
        "on_the_way": ("Professional is on the way", f"{b.reference}: your professional marked themselves on the way."),
        "in_progress": ("Job started", f"{b.reference} is in progress."),
        "completed": ("Job completed", f"{b.reference} is complete. You can now leave a review."),
        "cancelled": ("Booking cancelled", f"{b.reference} was cancelled."),
    }
    title, body = msgs[new_status]
    if actor.id != b.customer_id:
        notify(b.customer_id, title, body, link_c)
    if b.professional_id and b.professional.user_id != actor.id:
        notify(b.professional.user_id, title, body.replace("your professional", "the customer"), link_p)


def reschedule(booking, actor, new_start):
    if actor.role != "customer" or booking.customer_id != actor.id:
        raise BookingError("Not allowed.")
    if booking.status not in ("pending", "accepted"):
        raise BookingError("Only pending or accepted bookings can be rescheduled.")
    now = now_ist()
    if booking.scheduled_start - now < timedelta(hours=LATE_CANCEL_HOURS):
        raise BookingError("Bookings starting within 2 hours cannot be rescheduled online.")
    validate_slot(new_start)
    old_status = booking.status
    if booking.professional_id:
        ok, why = matching.is_eligible(booking.professional, matching.parse_ids(booking.service_ids), booking.locality_id,
                                       new_start, booking.duration_minutes, exclude_booking_id=booking.id)
        if not ok:
            raise BookingError("Your professional is not available then: " + why)
    old = booking.scheduled_start
    booking.scheduled_start = new_start
    booking.scheduled_end = new_start + timedelta(minutes=booking.duration_minutes)
    booking.status, booking.accepted_at = "pending", None  # the professional must re-confirm
    _history(booking, old_status, "pending", actor, f"Rescheduled from {old:%d %b %I:%M %p} to {new_start:%d %b %I:%M %p}")
    if booking.professional_id:
        notify(booking.professional.user_id, "Booking rescheduled", f"{booking.reference} moved to {new_start:%d %b, %I:%M %p}. Please confirm.", f"/pro/jobs/{booking.reference}")
    notify(actor.id, "Booking rescheduled", f"{booking.reference} is now {new_start:%d %b, %I:%M %p} and needs confirmation.", f"/bookings/{booking.reference}")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise BookingError("Another visit in this schedule already uses that time.")
    return booking
