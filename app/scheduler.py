"""
In-process scheduler for automatic backups and recurring notifications.

IMPORTANT PRODUCTION NOTE: this scheduler runs inside the Flask process. That
is fine for a single-process deployment (e.g. `python run.py`, or gunicorn
with `-w 1`). If you run multiple gunicorn workers, each worker would start
its own copy of this scheduler and you'd get duplicate backups. For
multi-worker production deployments, set ENABLE_IN_APP_SCHEDULER=false and
instead point an external cron (or your host's scheduled-jobs feature) at the
/internal/run-backup/<type> endpoint — see README for the exact setup. That
endpoint does the same work and is the more "enterprise-grade" correct choice
for anything beyond a single-process deployment.
"""
import os
from datetime import date, datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None


def init_scheduler(app):
    global _scheduler
    if not app.config.get("ENABLE_IN_APP_SCHEDULER", True):
        return
    # Avoid starting the scheduler twice under Flask's debug reloader (which
    # forks a parent + child process) — only the actual serving process
    # (WERKZEUG_RUN_MAIN=true) or a non-debug run should start it.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    if _scheduler is not None:
        return

    scheduler = BackgroundScheduler(daemon=True, timezone="Asia/Kolkata")

    def run_daily_backup():
        with app.app_context():
            from app.utils.backup import backup_all_shops
            backup_all_shops("daily")

    def run_weekly_backup():
        with app.app_context():
            from app.utils.backup import backup_all_shops
            backup_all_shops("weekly")

    def run_monthly_backup():
        with app.app_context():
            from app.utils.backup import backup_all_shops
            backup_all_shops("monthly")

    def run_daily_reminders():
        with app.app_context():
            _run_daily_reminder_checks(app)

    scheduler.add_job(run_daily_backup, "cron", hour=2, minute=0, id="daily_backup")
    scheduler.add_job(run_weekly_backup, "cron", day_of_week="sun", hour=2, minute=30, id="weekly_backup")
    scheduler.add_job(run_monthly_backup, "cron", day=1, hour=3, minute=0, id="monthly_backup")
    scheduler.add_job(run_daily_reminders, "cron", hour=9, minute=0, id="daily_reminders")

    scheduler.start()
    _scheduler = scheduler


def _run_daily_reminder_checks(app):
    """Subscription/trial expiry reminders, supplier-due summary, and
    daily/monthly report-ready notifications. Runs once a day; each event
    type is deduplicated per shop per day via already_notified_today()."""
    from app.extensions import db
    from app.models import Shop, Subscription, Supplier, User, Role
    from app.utils.notifications import notify, already_notified_today

    today = date.today()

    for shop in Shop.query.all():
        sub = shop.active_subscription()
        if sub and sub.end_date:
            days_left = (sub.end_date - today).days
            if 0 <= days_left <= 3:
                event = "trial_expiry_reminder" if sub.status == "trial" else "subscription_expiry_reminder"
                if not already_notified_today(shop.id, event):
                    notify(shop.id, "subscription", event,
                           f"{'Trial' if sub.status == 'trial' else 'Subscription'} expiring soon",
                           body=f"Expires in {days_left} day(s) on {sub.end_date.strftime('%d %b %Y')}.")

        total_supplier_due = sum(s.total_due() for s in Supplier.query.filter_by(shop_id=shop.id).all())
        if total_supplier_due > 0 and not already_notified_today(shop.id, "pending_supplier_payment"):
            notify(shop.id, "supplier", "pending_supplier_payment",
                   "Pending supplier payments",
                   body=f"Rs. {total_supplier_due:,.2f} total due across suppliers.")

        if not already_notified_today(shop.id, "daily_report_ready"):
            notify(shop.id, "business", "daily_report_ready", "Daily report is ready",
                   link="/owner/reports/daily")

        if today.day == 1 and not already_notified_today(shop.id, "monthly_report_ready"):
            notify(shop.id, "business", "monthly_report_ready", "Monthly report is ready",
                   link="/owner/reports/monthly")

    db.session.commit()
