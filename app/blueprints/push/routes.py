"""Web Push subscription management routes."""

from __future__ import annotations

import logging

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.blueprints.push import bp
from app.extensions import db
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)


@bp.route("/vapid-key")
@login_required
def vapid_key():
    """Return the VAPID public key for the frontend to use when subscribing."""
    key = current_app.config.get("VAPID_PUBLIC_KEY", "")
    return key, 200, {"Content-Type": "text/plain"}


@bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    """Create or refresh a push subscription for the current user."""
    data = request.get_json(silent=True)
    if not data or "endpoint" not in data:
        return jsonify({"error": "Invalid subscription data"}), 400

    endpoint = data["endpoint"]
    keys = data.get("keys", {})
    p256dh = keys.get("p256dh", "")
    auth = keys.get("auth", "")

    if not p256dh or not auth:
        return jsonify({"error": "Missing encryption keys"}), 400

    sub = db.session.execute(
        db.select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).scalar_one_or_none()

    if sub:
        # Refresh keys if the same endpoint re-subscribes
        sub.user_id = current_user.id
        sub.p256dh = p256dh
        sub.auth = auth
    else:
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.session.add(sub)

    db.session.commit()
    logger.info("Push subscription saved for user #%d", current_user.id)
    return "", 204


@bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    """Remove the push subscription matching the given endpoint."""
    data = request.get_json(silent=True)
    endpoint = (data or {}).get("endpoint", "")
    if endpoint:
        db.session.execute(
            db.delete(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.user_id == current_user.id,
            )
        )
        db.session.commit()
    return "", 204
