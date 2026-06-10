"""Web Push notification delivery service."""

from __future__ import annotations

import json
import logging

from flask import current_app

logger = logging.getLogger(__name__)


def send_push_notification(
    user_ids: list[int],
    title: str,
    body: str,
    url: str = "/",
) -> None:
    """Send a Web Push notification to all subscribed devices for the given users.

    Silently skips if VAPID keys are not configured.
    Subscriptions that return 410 Gone are automatically removed.

    Args:
        user_ids: List of user PKs to notify.
        title: Notification title.
        body: Notification body text.
        url: URL to open when the user clicks the notification.
    """
    from pywebpush import WebPushException, webpush

    from app.extensions import db
    from app.models.push_subscription import PushSubscription

    private_key_pem = current_app.config.get("VAPID_PRIVATE_KEY_PEM", "")
    claims_email = current_app.config.get("VAPID_CLAIMS_EMAIL", "")

    if not private_key_pem or not claims_email:
        logger.debug("Push notifications disabled: VAPID keys not configured")
        return

    # Restore literal newlines if the PEM was stored with escaped \n in the env var
    private_key_pem = private_key_pem.replace("\\n", "\n")

    subscriptions = db.session.scalars(
        db.select(PushSubscription).where(PushSubscription.user_id.in_(user_ids))
    ).all()

    if not subscriptions:
        return

    payload = json.dumps({"title": title, "body": body, "url": url})
    expired_endpoints: list[str] = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=private_key_pem,
                vapid_claims={"sub": f"mailto:{claims_email}"},
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 410:
                # Subscription expired — queue for removal
                expired_endpoints.append(sub.endpoint)
            else:
                logger.warning(
                    "Push delivery failed for user #%d (status %s): %s",
                    sub.user_id,
                    status,
                    exc,
                )
        except Exception:
            logger.exception("Unexpected push error for user #%d", sub.user_id)

    if expired_endpoints:
        db.session.execute(
            db.delete(PushSubscription).where(PushSubscription.endpoint.in_(expired_endpoints))
        )
        db.session.commit()
