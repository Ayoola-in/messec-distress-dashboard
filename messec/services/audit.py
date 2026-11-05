"""Record administrative actions. The caller commits, so the entry and the change land together."""

from flask import has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def record(action, target=None, detail=None):
    actor_id, ip_address = None, None
    if has_request_context():
        ip_address = request.remote_addr
        if current_user.is_authenticated:
            actor_id = current_user.id
    db.session.add(AuditLog(actor_id=actor_id, action=action, target=target, detail=detail, ip_address=ip_address))
