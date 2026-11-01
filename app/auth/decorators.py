from functools import wraps

from flask import abort
from flask_login import current_user

from ..extensions import login_manager


def role_required(*roles):
    """Server-side role gate. The role always comes from the database, never from the browser."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco
