from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Review, ReviewReport
from .bookings import BookingError
from .notify import audit, notify


def create_review(booking, customer, rating, comment):
    if booking.customer_id != customer.id:
        raise BookingError("You can only review your own bookings.")
    if booking.status != "completed" or not booking.professional_id:
        raise BookingError("You can review a job once it is completed.")
    if booking.review is not None:
        raise BookingError("You have already reviewed this booking.")
    if rating not in (1, 2, 3, 4, 5):
        raise BookingError("Choose a rating from 1 to 5 stars.")
    r = Review(booking_id=booking.id, customer_id=customer.id, professional_id=booking.professional_id,
               rating=rating, comment=(comment or "").strip()[:1000])
    db.session.add(r)
    notify(booking.professional.user_id, "New review", f"{booking.reference}: {rating} star review.", f"/pro/jobs/{booking.reference}")
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise BookingError("You have already reviewed this booking.")
    return r


def report_review(review, reporter, reason):
    if not (reason or "").strip():
        raise BookingError("Tell us what is wrong with this review.")
    if reporter.role == "customer" and review.customer_id == reporter.id:
        raise BookingError("You cannot report your own review.")
    db.session.add(ReviewReport(review_id=review.id, reporter_id=reporter.id, reason=reason.strip()[:300]))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise BookingError("You have already reported this review.")


def set_visibility(review, admin, visible):
    review.status = "visible" if visible else "hidden"
    audit(admin, "review.show" if visible else "review.hide", "review", review.id)
    db.session.commit()
