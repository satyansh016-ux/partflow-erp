"""
Sends real OS-level push notifications (lock screen / banner, like WhatsApp)
using the Web Push standard - no app install needed on Android; on iPhone the
site must be "Added to Home Screen" first (Apple's rule, not ours).

This is entirely best-effort: if VAPID keys aren't configured yet, or a
specific subscription has expired/been revoked by the user, we skip quietly
rather than ever breaking the request that created the notification. Errors
ARE logged (via print, visible in your host's log viewer) so failures are
diagnosable without ever raising back into the caller.
"""
import json
from flask import current_app
from pywebpush import webpush, WebPushException

from app.extensions import db
from app.models import PushSubscription, User, Notification


def send_push_for_notification(notification: Notification):
    result = {"configured": bool(current_app.config.get("VAPID_PRIVATE_KEY_PEM")),
              "subscriptions_found": 0, "sent": 0, "errors": []}

    if not result["configured"]:
        print("[push] Skipped: VAPID_PRIVATE_KEY_PEM not configured.")
        return result

    if notification.user_id:
        subs = PushSubscription.query.filter_by(user_id=notification.user_id).all()
    elif notification.shop_id:
        user_ids = [u.id for u in User.query.filter_by(shop_id=notification.shop_id).all()]
        subs = PushSubscription.query.filter(PushSubscription.user_id.in_(user_ids)).all() if user_ids else []
    else:
        subs = []

    result["subscriptions_found"] = len(subs)
    print(f"[push] Notification '{notification.title}' -> {len(subs)} subscription(s) found "
          f"(user_id={notification.user_id}, shop_id={notification.shop_id})")

    if not subs:
        return result

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
            print(f"[push] Sent OK to subscription id={sub.id}")
            result["sent"] += 1
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else "?"
            body = e.response.text if e.response is not None else str(e)
            print(f"[push] WebPushException for subscription id={sub.id}: status={status} body={body}")
            result["errors"].append(f"subscription {sub.id}: HTTP {status} - {body[:300]}")
            if e.response is not None and e.response.status_code in (404, 410):
                dead_subscription_ids.append(sub.id)
        except Exception as e:
            print(f"[push] Unexpected error for subscription id={sub.id}: {type(e).__name__}: {e}")
            result["errors"].append(f"subscription {sub.id}: {type(e).__name__}: {e}")

    if dead_subscription_ids:
        PushSubscription.query.filter(PushSubscription.id.in_(dead_subscription_ids)).delete(
            synchronize_session=False
        )
    db.session.commit()
    return result
