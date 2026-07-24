import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-key-change-me")

    # Default to a local SQLite file so the app runs with zero external setup.
    # Point DATABASE_URL at your Supabase Postgres connection string in production.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(basedir, "instance", "partflow.db")
    )
    # Supabase/Heroku-style URLs sometimes start with postgres:// ; SQLAlchemy needs postgresql://
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql://", 1
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 hours

    SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "admin@partflow.com")
    SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "ChangeMe123!")

    # --- Backup & Recovery -------------------------------------------------
    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(basedir, "instance", "backups"))
    # Fernet key for encrypting backup files. Auto-generated into .env on first
    # run by seed.py if not set — back this up separately from the database
    # itself, since backups are useless without it.
    BACKUP_ENCRYPTION_KEY = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
    BACKUP_RETENTION_DAILY = int(os.environ.get("BACKUP_RETENTION_DAILY", 7))
    BACKUP_RETENTION_WEEKLY = int(os.environ.get("BACKUP_RETENTION_WEEKLY", 4))
    BACKUP_RETENTION_MONTHLY = int(os.environ.get("BACKUP_RETENTION_MONTHLY", 12))
    # Shared secret for the /internal/run-backup endpoint, so an external cron
    # (recommended for real production reliability — see README) can trigger
    # backups without needing a logged-in session.
    BACKUP_TRIGGER_SECRET = os.environ.get("BACKUP_TRIGGER_SECRET", "")
    ENABLE_IN_APP_SCHEDULER = os.environ.get("ENABLE_IN_APP_SCHEDULER", "true").lower() == "true"

    # --- Support Center ------------------------------------------------------
    SUPPORT_WHATSAPP_NUMBER = os.environ.get("SUPPORT_WHATSAPP_NUMBER", "")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@partflow.com")
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(basedir, "instance", "uploads"))

    # --- Web Push Notifications (real OS-level push, like WhatsApp) --------
    # py_vapid (used internally by pywebpush) expects the private key as a
    # base64url-encoded DER blob - NOT a PEM string. This one clean string,
    # single line, no special characters - safe to copy/paste anywhere.
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "support@partflow.com")

    # Low-stock alert thresholds are per-part (minimum_stock field), these are dashboard color bands
    STOCK_CRITICAL_RATIO = 0.5   # stock <= 50% of minimum => RED
    STOCK_LOW_RATIO = 1.0        # stock <= 100% of minimum => ORANGE
    STOCK_NEAR_MIN_RATIO = 1.25  # stock <= 125% of minimum => YELLOW
