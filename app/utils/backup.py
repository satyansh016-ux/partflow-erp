"""
Shop-isolated encrypted backup & restore.

Design notes:
- Backups are per-shop JSON exports of every business table, encrypted with a
  Fernet symmetric key (BACKUP_ENCRYPTION_KEY). This is NOT a raw database
  dump — it's a portable, shop-scoped export, which is what makes
  "shop-specific restore without affecting other shops" possible in a
  multi-tenant database where every shop's rows live in the same tables.
- Restoring deletes only this shop's existing rows (children first, to
  respect foreign keys) then re-inserts the backed-up rows (parents first),
  all inside one transaction — if anything fails, nothing changes.
- A SHA-256 checksum is stored at backup time and re-verified before any
  restore, so a corrupted/tampered backup file is refused rather than
  silently applied.
"""
import os
import json
import hashlib
from datetime import datetime
from decimal import Decimal
from sqlalchemy import inspect as sa_inspect
from cryptography.fernet import Fernet
from flask import current_app

from app.extensions import db
from app.models import (
    Godown, User, EmployeeProfile, Supplier, Part, PartStock, StockLedger, StockAlert,
    Customer, LabourCharge, PurchaseRequest, Purchase, PurchaseItem, Invoice, InvoiceItem,
    Payment, Expense, AuditLog, Notification, SupportTicket, SupportTicketReply,
    StockTransfer, Subscription, BackupLog, RestoreLog, Shop,
)
from app.utils.notifications import notify

# Ordered so parents are created before children — used both when writing the
# backup and when restoring it. Reversed order is used when deleting.
BACKUP_TABLE_ORDER = [
    "godowns", "users", "employee_profiles", "suppliers", "parts", "part_stock",
    "customers", "labour_charges", "purchase_requests", "purchases", "purchase_items",
    "invoices", "invoice_items", "payments", "expenses", "stock_ledger", "stock_alerts",
    "stock_transfers", "subscriptions", "notifications", "support_tickets",
    "support_ticket_replies", "audit_logs",
]

MODEL_BY_TABLE = {
    "godowns": Godown, "users": User, "employee_profiles": EmployeeProfile,
    "suppliers": Supplier, "parts": Part, "part_stock": PartStock,
    "customers": Customer, "labour_charges": LabourCharge,
    "purchase_requests": PurchaseRequest, "purchases": Purchase, "purchase_items": PurchaseItem,
    "invoices": Invoice, "invoice_items": InvoiceItem, "payments": Payment, "expenses": Expense,
    "stock_ledger": StockLedger, "stock_alerts": StockAlert, "stock_transfers": StockTransfer,
    "subscriptions": Subscription, "notifications": Notification, "support_tickets": SupportTicket,
    "support_ticket_replies": SupportTicketReply, "audit_logs": AuditLog,
}


def _rows_for_table(table_name, shop_id):
    model = MODEL_BY_TABLE[table_name]
    if table_name == "employee_profiles":
        user_ids = [u.id for u in User.query.filter_by(shop_id=shop_id).all()]
        return model.query.filter(model.user_id.in_(user_ids)).all() if user_ids else []
    if table_name == "part_stock":
        part_ids = [p.id for p in Part.query.filter_by(shop_id=shop_id).all()]
        return model.query.filter(model.part_id.in_(part_ids)).all() if part_ids else []
    if table_name == "purchase_items":
        purchase_ids = [p.id for p in Purchase.query.filter_by(shop_id=shop_id).all()]
        return model.query.filter(model.purchase_id.in_(purchase_ids)).all() if purchase_ids else []
    if table_name == "invoice_items":
        invoice_ids = [i.id for i in Invoice.query.filter_by(shop_id=shop_id).all()]
        return model.query.filter(model.invoice_id.in_(invoice_ids)).all() if invoice_ids else []
    if table_name == "support_ticket_replies":
        ticket_ids = [t.id for t in SupportTicket.query.filter_by(shop_id=shop_id).all()]
        return model.query.filter(model.ticket_id.in_(ticket_ids)).all() if ticket_ids else []
    return model.query.filter_by(shop_id=shop_id).all()


def _serialize_value(value):
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    import datetime as _dt
    if isinstance(value, _dt.date):
        return {"__type__": "date", "value": value.isoformat()}
    return value


def _deserialize_value(value):
    if isinstance(value, dict) and "__type__" in value:
        if value["__type__"] == "decimal":
            return Decimal(value["value"])
        if value["__type__"] == "datetime":
            return datetime.fromisoformat(value["value"])
        if value["__type__"] == "date":
            import datetime as _dt
            return _dt.date.fromisoformat(value["value"])
    return value


def _row_to_dict(obj):
    """Use mapper.column_attrs (the actual mapped Python attribute names) rather
    than raw column keys — some models (e.g. User.is_active_flag, whose DB
    column is named 'is_active') have a read-only @property shadowing the DB
    column name, which would break restore if we used the DB-side key."""
    mapper = sa_inspect(obj.__class__)
    return {prop.key: _serialize_value(getattr(obj, prop.key)) for prop in mapper.column_attrs}


def _get_fernet():
    key = current_app.config.get("BACKUP_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY is not set. Run `python seed.py` once (it generates "
            "one into .env automatically), or set it yourself before taking backups."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def backup_shop(shop_id: int, backup_type: str = "manual") -> BackupLog:
    """Export every row belonging to this shop, encrypt it, write it to disk,
    log the attempt, and apply the retention policy. Never raises — failures
    are captured in the returned BackupLog (status='failed')."""
    started_at = datetime.utcnow()
    try:
        payload = {"shop_id": shop_id, "created_at": started_at.isoformat(), "tables": {}}
        table_counts = {}
        for table_name in BACKUP_TABLE_ORDER:
            rows = _rows_for_table(table_name, shop_id)
            payload["tables"][table_name] = [_row_to_dict(r) for r in rows]
            table_counts[table_name] = len(rows)

        raw = json.dumps(payload).encode("utf-8")
        encrypted = _get_fernet().encrypt(raw)

        backup_dir = os.path.join(current_app.config["BACKUP_DIR"], str(shop_id))
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(backup_dir, f"{backup_type}_{timestamp}.bak")
        with open(file_path, "wb") as f:
            f.write(encrypted)

        checksum = hashlib.sha256(encrypted).hexdigest()

        log = BackupLog(
            shop_id=shop_id, backup_type=backup_type, status="success",
            file_path=file_path, size_bytes=len(encrypted), checksum_sha256=checksum,
            table_counts=json.dumps(table_counts), started_at=started_at,
            completed_at=datetime.utcnow(),
        )
        db.session.add(log)
        notify(shop_id, "business", "backup_completed",
               f"{backup_type.title()} backup completed",
               body=f"{sum(table_counts.values())} records backed up.")
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        log = BackupLog(
            shop_id=shop_id, backup_type=backup_type, status="failed",
            error_message=str(e), started_at=started_at, completed_at=datetime.utcnow(),
        )
        db.session.add(log)
        notify(shop_id, "business", "backup_failed",
               f"{backup_type.title()} backup failed", body=str(e)[:400])
        db.session.commit()

    apply_retention_policy(shop_id)
    return log


def backup_all_shops(backup_type: str = "daily"):
    return [backup_shop(shop.id, backup_type) for shop in Shop.query.all()]


def apply_retention_policy(shop_id: int):
    """Keep only the most recent N backups per cadence (daily/weekly/monthly),
    deleting older files and their log rows. Manual backups are never
    auto-deleted."""
    retention = {
        "daily": current_app.config["BACKUP_RETENTION_DAILY"],
        "weekly": current_app.config["BACKUP_RETENTION_WEEKLY"],
        "monthly": current_app.config["BACKUP_RETENTION_MONTHLY"],
    }
    for backup_type, keep_count in retention.items():
        logs = (
            BackupLog.query.filter_by(shop_id=shop_id, backup_type=backup_type, status="success")
            .order_by(BackupLog.started_at.desc()).all()
        )
        for old_log in logs[keep_count:]:
            if old_log.file_path and os.path.exists(old_log.file_path):
                try:
                    os.remove(old_log.file_path)
                except OSError:
                    pass
            db.session.delete(old_log)
    db.session.commit()


def verify_backup_integrity(backup_log: BackupLog) -> bool:
    if not backup_log.file_path or not os.path.exists(backup_log.file_path):
        return False
    with open(backup_log.file_path, "rb") as f:
        data = f.read()
    return hashlib.sha256(data).hexdigest() == backup_log.checksum_sha256


def restore_shop_from_backup(shop_id: int, backup_log: BackupLog, restored_by_user_id: int) -> RestoreLog:
    """Wipe this shop's current business data and replace it with the backup's
    contents, inside a single transaction. Raises ValueError for anything
    that should stop the restore before touching data (wrong shop, failed
    backup, missing/corrupted file). Never touches any other shop's rows."""
    if backup_log.shop_id != shop_id:
        raise ValueError("This backup belongs to a different shop — refusing to restore.")
    if backup_log.status != "success":
        raise ValueError("Cannot restore from a failed backup.")
    if not backup_log.file_path or not os.path.exists(backup_log.file_path):
        raise ValueError("Backup file is missing on disk.")

    with open(backup_log.file_path, "rb") as f:
        encrypted = f.read()

    if hashlib.sha256(encrypted).hexdigest() != backup_log.checksum_sha256:
        raise ValueError("Backup file failed integrity check (checksum mismatch) — refusing to restore.")

    raw = _get_fernet().decrypt(encrypted)
    payload = json.loads(raw.decode("utf-8"))

    try:
        for table_name in reversed(BACKUP_TABLE_ORDER):
            for row in _rows_for_table(table_name, shop_id):
                db.session.delete(row)
        db.session.flush()

        for table_name in BACKUP_TABLE_ORDER:
            model = MODEL_BY_TABLE[table_name]
            for row_dict in payload["tables"].get(table_name, []):
                kwargs = {k: _deserialize_value(v) for k, v in row_dict.items()}
                db.session.add(model(**kwargs))
            db.session.flush()

        restore_log = RestoreLog(
            shop_id=shop_id, backup_log_id=backup_log.id,
            restored_by=restored_by_user_id, status="success",
        )
        db.session.add(restore_log)
        notify(shop_id, "business", "data_restored",
               "Shop data restored from backup",
               body=f"Restored from backup taken {backup_log.started_at.strftime('%d %b %Y %H:%M')}.")
        db.session.commit()
        return restore_log

    except Exception as e:
        db.session.rollback()
        restore_log = RestoreLog(
            shop_id=shop_id, backup_log_id=backup_log.id,
            restored_by=restored_by_user_id, status="failed", error_message=str(e),
        )
        db.session.add(restore_log)
        db.session.commit()
        raise
