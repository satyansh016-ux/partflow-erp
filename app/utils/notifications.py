"""
Central place that creates Notification rows. Call `notify(...)` from
anywhere business logic happens (sales, purchases, payments, backups,
subscriptions...) — it just adds to the session; the caller's existing
db.session.commit() picks it up, so no extra commits are introduced into
request flows that already commit at the end.
"""
from app.extensions import db
from app.models import Notification


def notify(shop_id, category, event_type, title, body=None, link=None, user_id=None):
    n = Notification(
        shop_id=shop_id, user_id=user_id, category=category,
        event_type=event_type, title=title, body=body, link=link,
    )
    db.session.add(n)
    return n


def already_notified_today(shop_id, event_type):
    """Used by scheduled reminder jobs (trial/subscription expiry, due
    reminders) so they fire once per day per shop instead of spamming every
    time the job runs."""
    from datetime import datetime, timedelta
    since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return Notification.query.filter(
        Notification.shop_id == shop_id,
        Notification.event_type == event_type,
        Notification.created_at >= since,
    ).first() is not None
