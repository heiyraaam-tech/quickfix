import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def clean(value, max_len=200):
    return (value or "").strip()[:max_len]


def valid_email(email):
    return bool(email) and len(email) <= 255 and bool(EMAIL_RE.match(email))


def normalize_phone(raw):
    """Return a 10-digit Indian mobile number, or None if invalid. Empty input -> ''."""
    digits = re.sub(r"[\s\-()+]", "", raw or "")
    if not digits:
        return ""
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def password_problem(password):
    if len(password or "") < 8:
        return "Use at least 8 characters."
    if len(password) > 128:
        return "Password is too long."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Include at least one letter and one number."
    return None


def valid_pincode(p):
    return bool(re.fullmatch(r"[1-9]\d{5}", p or ""))
