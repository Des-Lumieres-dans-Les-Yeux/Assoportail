"""Web Push subscription management routes."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from app.blueprints.push import bp
from app.extensions import db
from app.models.push_subscription import PushSubscription

logger = logging.getLogger(__name__)

# Endpoints are POSTed to by the server (web workers + Celery), so an
# unvalidated one is SSRF into internal addresses / unbounded hangs.
_MAX_SUBSCRIPTIONS_PER_USER = 5


def _is_public_host(hostname: str) -> bool:
    """True if the hostname resolves to public addresses only."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
            return False
    return True


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

    # Endpoint validation: HTTPS public host only (see _is_public_host).
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or not _is_public_host(
        parsed.hostname
    ):
        return jsonify({"error": "Invalid endpoint host"}), 400

    sub = db.session.execute(
        db.select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).scalar_one_or_none()

    if sub and sub.user_id != current_user.id:
        # Never reassign another user's endpoint (a user who knows the URL
        # must not be able to steal or suppress that subscription).
        return jsonify({"error": "Endpoint already in use"}), 409

    if sub is None:
        own_count = db.session.scalar(
            db.select(db.func.count(PushSubscription.id))
            .where(PushSubscription.user_id == current_user.id)
        )
        if own_count >= _MAX_SUBSCRIPTIONS_PER_USER:
            return jsonify({"error": "Too many push subscriptions"}), 400
        sub = PushSubscription(
            user_id=current_user.id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.session.add(sub)
    else:
        # Refresh keys if the same endpoint re-subscribes
        sub.p256dh = p256dh
        sub.auth = auth

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
