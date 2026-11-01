import logging
import os
import secrets
from datetime import datetime

from flask import Flask, jsonify, render_template, request, session
from flask_login import current_user
from sqlalchemy import event

from config import Config

from .extensions import csrf, db, limiter, login_manager, migrate


def _secret_key(app):
    if app.config.get("SECRET_KEY"):
        return
    if app.config["ENV_NAME"] == "production":
        raise RuntimeError("SECRET_KEY must be set in production.")
    path = os.path.join(app.instance_path, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    app.config["SECRET_KEY"] = open(path).read().strip()


def _sqlite_write_lock(app):
    """Every transaction starts with BEGIN IMMEDIATE, so 'check for conflicts, then insert' cannot interleave
    with another writer. This is what makes double-booking checks safe on SQLite."""
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        return
    engine = db.engine

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def create_app(config_object=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object or Config)
    os.makedirs(app.instance_path, exist_ok=True)
    _secret_key(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    with app.app_context():
        _sqlite_write_lock(app)

    from . import models  # noqa: F401
    from .admin.routes import bp as admin_bp
    from .auth.routes import bp as auth_bp
    from .cli import register_cli
    from .customer.routes import bp as customer_bp
    from .main.routes import bp as main_bp
    from .professional.routes import bp as pro_bp

    for bp in (main_bp, auth_bp, customer_bp, pro_bp, admin_bp):
        app.register_blueprint(bp)
    register_cli(app)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @login_manager.user_loader
    def load_user(user_id):
        u = db.session.get(models.User, int(user_id))
        return u if u and u.is_active else None  # suspended users lose their session immediately

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify(error="login_required"), 401
        from flask import flash, redirect, url_for
        flash("Please log in to continue.", "info")
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.template_filter("inr")
    def inr(v):
        return "₹{:,}".format(int(v or 0))

    @app.template_filter("dt")
    def fmt_dt(v):
        return v.strftime("%a %d %b, %I:%M %p").replace(" 0", " ") if v else ""

    @app.template_filter("d")
    def fmt_d(v):
        return v.strftime("%d %b %Y") if v else ""

    @app.template_filter("hours")
    def fmt_hours(minutes):
        h, m = divmod(int(minutes), 60)
        return (f"{h} hr" if h else "") + (f" {m} min" if m else "")

    @app.context_processor
    def inject():
        unread, loc = 0, None
        if current_user.is_authenticated:
            unread = models.Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        localities = models.Locality.query.filter_by(is_active=True).order_by(models.Locality.name).all()
        loc_id = session.get("locality_id")
        loc = next((l for l in localities if l.id == loc_id), None)
        return dict(unread_count=unread, nav_localities=localities, selected_locality=loc,
                    support_email=app.config["SUPPORT_EMAIL"], weekday_names=models.WEEKDAYS)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if current_user.is_authenticated or request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"  # back button after logout must not show a stale header
        return resp

    def error(code, title, text):
        if request.path.startswith("/api/"):
            return jsonify(error=title), code
        return render_template("error.html", code=code, title=title, text=text), code

    @app.errorhandler(400)
    def e400(e):
        return error(400, "That request didn't go through", "Your form may have expired. Go back, refresh the page and try again.")

    @app.errorhandler(403)
    def e403(e):
        return error(403, "You can't open this page", "Your account doesn't have access to this page.")

    @app.errorhandler(404)
    def e404(e):
        return error(404, "We couldn't find that page", "The link may be old, or the page may have moved.")

    @app.errorhandler(429)
    def e429(e):
        return error(429, "Too many attempts", "Please wait a minute and try again.")

    @app.errorhandler(500)
    def e500(e):
        app.logger.exception("Unhandled error")
        return error(500, "Something went wrong on our side", "We've logged the problem. Please try again in a moment.")

    return app
