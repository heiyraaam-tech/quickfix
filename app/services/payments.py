"""Payment status tracking. Only the manual/cash workflow exists today.

To add a real gateway later (Razorpay, Cashfree, ...): implement PaymentProvider,
register it in PROVIDERS, store its order id in Payment.provider_ref, and confirm
payments from the gateway's signed webhook - never from the browser.
No online payment is simulated anywhere in QuickFix.
"""
from ..extensions import db
from ..models import now_ist
from .notify import audit, notify


class PaymentProvider:
    name = "base"
    online = False


class ManualCashProvider(PaymentProvider):
    name = "manual"


PROVIDERS = {"manual": ManualCashProvider()}


def online_payments_enabled(app_config):
    return bool(app_config.get("PAYMENT_GATEWAY")) and app_config.get("PAYMENT_GATEWAY") in PROVIDERS and \
        PROVIDERS[app_config["PAYMENT_GATEWAY"]].online


class PaymentError(Exception):
    pass


def mark_paid(booking, actor):
    p = booking.payment
    if booking.status != "completed":
        raise PaymentError("Payment can be recorded only after the job is completed.")
    is_owner = actor.role == "professional" and actor.professional and booking.professional_id == actor.professional.id
    if not (is_owner or actor.role == "admin"):
        raise PaymentError("Not allowed.")
    if p.status == "paid":
        raise PaymentError("Already marked as paid.")
    p.amount = booking.amount_due
    p.status, p.paid_at = "paid", now_ist()
    notify(booking.customer_id, "Payment recorded", f"{booking.reference}: Rs {p.amount} received in cash.", f"/bookings/{booking.reference}")
    audit(actor, "payment.mark_paid", "booking", booking.id, f"Rs {p.amount}") if actor.role == "admin" else None
    db.session.commit()


def refund(booking, actor):
    p = booking.payment
    if actor.role != "admin":
        raise PaymentError("Only an administrator can record a refund.")
    if p.status != "paid":
        raise PaymentError("Only paid bookings can be refunded.")
    p.status = "refunded"
    audit(actor, "payment.refund", "booking", booking.id, f"Rs {p.amount} (recorded manually; no money moved by QuickFix)")
    notify(booking.customer_id, "Refund recorded", f"{booking.reference}: a refund was recorded by QuickFix support.", f"/bookings/{booking.reference}")
    db.session.commit()
