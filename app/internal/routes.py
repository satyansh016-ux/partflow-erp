"""
Endpoints meant to be called by an external scheduler (cron, your host's
"scheduled jobs" feature, etc.) rather than a logged-in browser session.
Protected by a shared secret, NOT by Flask-Login, since cron has no session.

Example crontab entries (adjust host/port/secret):
    0 2 * * *  curl -s -X POST -H "X-Backup-Secret: $BACKUP_TRIGGER_SECRET" \
               https://yourapp.example.com/internal/run-backup/daily
    30 2 * * 0 curl -s -X POST -H "X-Backup-Secret: $BACKUP_TRIGGER_SECRET" \
               https://yourapp.example.com/internal/run-backup/weekly
    0 3 1 * *  curl -s -X POST -H "X-Backup-Secret: $BACKUP_TRIGGER_SECRET" \
               https://yourapp.example.com/internal/run-backup/monthly

This is the recommended approach for any multi-worker/multi-instance
deployment, where the in-process APScheduler (app/scheduler.py) would
otherwise run once per worker and create duplicate backups.
"""
from flask import Blueprint, request, current_app, jsonify

internal_bp = Blueprint("internal", __name__)


@internal_bp.route("/internal/run-backup/<backup_type>", methods=["POST"])
def run_backup(backup_type):
    if backup_type not in ("daily", "weekly", "monthly", "manual"):
        return jsonify({"error": "invalid backup_type"}), 400

    secret = current_app.config.get("BACKUP_TRIGGER_SECRET", "")
    if not secret:
        return jsonify({"error": "BACKUP_TRIGGER_SECRET is not configured on the server"}), 503
    if request.headers.get("X-Backup-Secret") != secret:
        return jsonify({"error": "unauthorized"}), 401

    from app.utils.backup import backup_all_shops
    logs = backup_all_shops(backup_type)
    return jsonify({
        "triggered": backup_type,
        "shops_backed_up": len(logs),
        "results": [{"shop_id": l.shop_id, "status": l.status} for l in logs],
    })
