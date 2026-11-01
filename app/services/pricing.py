"""Server-side price calculation. The browser never supplies a price."""


class PricingError(ValueError):
    pass


def minutes_allowed(service, minutes):
    if service.pricing_model in ("hourly", "duration"):
        return minutes in service.durations
    return minutes == service.default_minutes


def rate_bounds(service):
    """A professional may set their own rate between 50% and 150% of the catalogue rate."""
    base = service.hourly_rate if service.pricing_model == "hourly" else service.base_price
    if not base:
        return None
    return (round(base * 0.5), round(base * 1.5))


def calculate(service, minutes=None, rate_override=None):
    minutes = minutes or service.default_minutes
    if not minutes_allowed(service, minutes):
        raise PricingError("That duration is not available for this service.")
    model = service.pricing_model
    visit, svc, dur = service.visit_fee or 0, 0, 0
    estimate = quote = False
    note = ""
    if model == "fixed":
        svc = rate_override or service.base_price
    elif model == "visit_estimate":
        svc = rate_override or service.base_price
        estimate = True
        note = "Visit fee is fixed. The service charge is an estimate; the professional confirms the final amount after inspecting the work."
    elif model == "hourly":
        rate = rate_override or service.hourly_rate
        dur = round(rate * minutes / 60)
    elif model == "duration":
        svc = rate_override or service.base_price
        dur = round(service.hourly_rate * max(0, minutes - service.min_minutes) / 60)
    elif model == "quote":
        estimate = quote = True
        note = "Custom quote: only the visit fee is charged up front. The professional quotes the work after inspection."
    total = visit + svc + dur
    return {"visit_fee": visit, "service_charge": svc, "duration_charge": dur, "total": total,
            "is_estimate": estimate, "is_quote": quote, "note": note, "minutes": minutes}
