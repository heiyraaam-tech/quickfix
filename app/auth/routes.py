from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from ..extensions import db, limiter
from ..models import Locality, ProfessionalProfile, User, now_ist
from ..validators import clean, normalize_phone, password_problem, valid_email

bp = Blueprint("auth", __name__)


def home_for(user):
    return {"admin": "admin.dashboard", "professional": "professional.dashboard"}.get(user.role, "customer.my_bookings")


def safe_next(target):
    if not target:
        return None
    p = urlparse(target)
    return target if not p.netloc and not p.scheme and target.startswith("/") and not target.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(home_for(current_user)))
    if request.method == "POST":
        ident = clean(request.form.get("identifier"), 255).lower()
        password = request.form.get("password", "")
        user = None
        if ident:
            phone = normalize_phone(ident) if "@" not in ident else None
            user = User.query.filter_by(email=ident).first() if "@" in ident else (User.query.filter_by(phone=phone).first() if phone else None)
        if user and user.check_password(password):
            if not user.is_active:
                flash("This account has been suspended. Contact support.", "error")
                return render_template("auth/login.html"), 403
            session.clear()
            login_user(user)
            session.permanent = True
            user.last_login_at = now_ist()
            db.session.commit()
            return redirect(safe_next(request.args.get("next")) or url_for(home_for(user)))
        flash("That email/phone and password don't match.", "error")
        return render_template("auth/login.html", identifier=ident), 401
    return render_template("auth/login.html")


def _register(role):
    form = request.form
    name, email = clean(form.get("name"), 120), clean(form.get("email"), 255).lower()
    phone_raw, password = form.get("phone", ""), form.get("password", "")
    errors = []
    if len(name) < 2:
        errors.append("Enter your full name.")
    if not valid_email(email):
        errors.append("Enter a valid email address.")
    phone = normalize_phone(phone_raw)
    if phone is None:
        errors.append("Enter a valid 10-digit Indian mobile number, or leave it empty.")
    if role == "professional" and not phone:
        errors.append("A mobile number is required for professionals.")
    if (p := password_problem(password)):
        errors.append(p)
    if password != form.get("confirm", password):
        errors.append("The two passwords don't match.")
    if not errors:
        if User.query.filter_by(email=email).first():
            errors.append("An account with this email already exists. Try logging in.")
        if phone and User.query.filter_by(phone=phone).first():
            errors.append("An account with this phone number already exists.")
    return name, email, phone or None, password, errors


def _do_register(role):
    if current_user.is_authenticated:
        return redirect(url_for(home_for(current_user)))
    tpl = "auth/register.html"
    if request.method == "POST":
        name, email, phone, password, errors = _register(role)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(tpl, role=role, form=request.form), 400
        u = User(name=name, email=email, phone=phone, role=role)  # role is fixed by the route, never by the form
        u.set_password(password)
        db.session.add(u)
        try:
            db.session.flush()
            if role == "professional":
                db.session.add(ProfessionalProfile(user_id=u.id))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account with this email or phone already exists.", "error")
            return render_template(tpl, role=role, form=request.form), 400
        session.clear()
        login_user(u)
        session.permanent = True
        flash("Welcome to QuickFix!" if role == "customer" else "Account created. Complete your profile so we can review your application.", "success")
        return redirect(url_for("professional.profile") if role == "professional" else (safe_next(request.args.get("next")) or url_for("main.home")))
    return render_template(tpl, role=role, form={})


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    return _do_register("customer")


@bp.route("/register/professional", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register_professional():
    return _do_register("professional")


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    session.clear()
    flash("You're logged out.", "success")
    return redirect(url_for("main.home"))
