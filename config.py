import os
from datetime import timedelta

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config:
    ENV_NAME = os.environ.get("QUICKFIX_ENV", "development")
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + os.path.join(basedir, "instance", "quickfix.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PORT = int(os.environ.get("PORT", "5001"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    REMEMBER_COOKIE_DURATION = timedelta(days=7)
    REMEMBER_COOKIE_HTTPONLY = True

    WTF_CSRF_TIME_LIMIT = 60 * 60 * 8
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = True

    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")
    # Online payments are NOT implemented. Setting these would be the first step
    # for a future gateway integration (see app/services/payments.py).
    PAYMENT_GATEWAY = os.environ.get("PAYMENT_GATEWAY", "")
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key"
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite://"  # overridden per-test with a temp file
