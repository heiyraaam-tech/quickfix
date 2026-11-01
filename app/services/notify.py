from flask import request

from ..extensions import db
from ..models import AuditLog, Notification


def notify(user_id, title, body="", link=None):
    if user_id:
        db.session.add(Notification(user_id=user_id, title=title[:120], body=(body or "")[:300], link=link))


def audit(actor, action, target_type=None, target_id=None, detail=""):
    db.session.add(AuditLog(actor_id=getattr(actor, "id", None), action=action, target_type=target_type,
                            target_id=target_id, detail=(detail or "")[:400]))
