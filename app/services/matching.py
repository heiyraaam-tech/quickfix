"""Availability + service-area matching (no GPS, no maps API)."""
from datetime import datetime, timedelta

from ..models import (ACTIVE_STATUSES, Booking, ProfessionalArea, ProfessionalProfile, User, now_ist)


def parse_ids(csv):
    return [int(x) for x in (csv or "").split(",") if x.strip().isdigit()]


def has_conflict(pro_id, start, end, exclude_booking_id=None):
    q = Booking.query.filter(Booking.professional_id == pro_id, Booking.status.in_(ACTIVE_STATUSES),
                             Booking.scheduled_start < end, Booking.scheduled_end > start)
    if exclude_booking_id:
        q = q.filter(Booking.id != exclude_booking_id)
    return db_exists(q)


def db_exists(q):
    return q.first() is not None


def is_eligible(pro, service_ids, locality_id, start, minutes, exclude_booking_id=None, check_status=True):
    """Return (ok, reason)."""
    if not pro.is_approved:
        return False, "Professional is not approved."
    if check_status and pro.availability_status != "available":
        return False, "Professional is not accepting jobs right now."
    if not set(service_ids) <= {o.service_id for o in pro.offerings}:
        return False, "Professional does not offer every requested service."
    if not pro.serves_locality(locality_id):
        return False, "Professional does not serve this locality."
    end = start + timedelta(minutes=minutes)
    wh = pro.hours_for(start.weekday())
    if not wh or end.date() != start.date() or start.time() < wh.start_time or end.time() > wh.end_time:
        return False, "Outside the professional's working hours."
    if any(t.start_at < end and t.end_at > start for t in pro.time_off):
        return False, "Professional has time off then."
    if has_conflict(pro.id, start, end, exclude_booking_id):
        return False, "Professional already has a job at that time."
    return True, ""


def eligible_professionals(service_ids, locality_id, start, minutes):
    pros = (ProfessionalProfile.query.join(User, User.id == ProfessionalProfile.user_id)
            .join(ProfessionalArea, ProfessionalArea.professional_id == ProfessionalProfile.id)
            .filter(ProfessionalProfile.verification_status == "approved", User.active.is_(True),
                    ProfessionalProfile.availability_status == "available",
                    ProfessionalArea.locality_id == locality_id).all())
    ok = [p for p in pros if is_eligible(p, service_ids, locality_id, start, minutes)[0]]
    ok.sort(key=lambda p: (-(p.rating_avg or 0), -(p.experience_years or 0), p.id))
    return ok


def alternative_slots(service_ids, locality_id, start, minutes, limit=6):
    """Nearby start times (same day and the next two) that have at least one eligible professional."""
    found, now = [], now_ist() + timedelta(minutes=60)
    for day in range(3):
        base = datetime.combine(start.date() + timedelta(days=day), datetime.min.time())
        for hour in range(7, 21):
            cand = base.replace(hour=hour)
            if cand < now or cand == start:
                continue
            if eligible_professionals(service_ids, locality_id, cand, minutes):
                found.append(cand)
                if len(found) >= limit:
                    return found
    return found


def open_requests_for(pro):
    """Unassigned pending bookings this professional could take right now."""
    if not pro.is_approved or not pro.areas:
        return []
    area_ids = [a.locality_id for a in pro.areas]
    cands = (Booking.query.filter(Booking.status == "pending", Booking.professional_id.is_(None),
                                  Booking.locality_id.in_(area_ids), Booking.scheduled_start > now_ist())
             .order_by(Booking.scheduled_start).limit(60).all())
    return [b for b in cands if is_eligible(pro, parse_ids(b.service_ids), b.locality_id, b.scheduled_start,
                                            b.duration_minutes, exclude_booking_id=b.id)[0]]
