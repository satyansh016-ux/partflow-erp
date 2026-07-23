"""
Sends real OS-level push notifications (lock screen / banner, like WhatsApp)
using the Web Push standard - no app install needed on Android; on iPhone the
site must be "Added to Home Screen" first (Apple's rule, not ours).

This is entirely best-effort: if VAPID keys aren't configured yet, or a
specific subscription has expired/been revoked by the user, we skip quietly
rather than ever breaking the request that created the notification.
"""
import json
from flask import current_app
from pywebpush import webpush, WebPushException

from app.extensions import db
from app.models import PushSubscription, User, Notification


def send_push_for_notification(notification: Notification):
    if not current_app.config.get("VAPID_PRIVATE_KEY_PEM"):
        return  # push not configured yet - nothing to do

    if notification.user_id:
        subs = PushSubscription.query.filter_by(user_id=notification.user_id).all()
    elif notification.shop_id:
        user_ids = [u.id for u in User.query.filter_by(shop_id=notification.shop_id).all()]
        subs = PushSubscription.query.filter(PushSubscription.user_id.in_(user_ids)).all() if user_ids else []
    else:
        subs = []

    if not subs:
        return

    payload = json.dumps({
        "title": notification.title,
        "body": notification.body or "",
        "link": notification.link or "/notifications",
    })

    dead_subscription_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=current_app.config["VAPID_PRIVATE_KEY_PEM"],
                vapid_claims={"sub": f"mailto:{current_app.config['VAPID_CLAIM_EMAIL']}"},
            )
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):
                dead_subscription_ids.append(sub.id)
        except Exception:
            pass  # push is best-effort - never let it break the caller

    if dead_subscription_ids:
        PushSubscription.query.filter(PushSubscription.id.in_(dead_subscription_ids)).delete(
            synchronize_session=False
        )
    db.session.commit()
