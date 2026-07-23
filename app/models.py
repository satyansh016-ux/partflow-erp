import enum
from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    OWNER = "owner"
    EMPLOYEE = "employee"


class ShopStatus(str, enum.Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BANNED = "banned"


class VehicleType(str, enum.Enum):
    BIKE = "bike"
    CAR = "car"
    TRUCK = "truck"
    BUS = "bus"
    TRACTOR = "tractor"
    JCB = "jcb"
    HEAVY_VEHICLE = "heavy_vehicle"


class PurchaseRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECEIVED = "received"


class PaymentMethod(str, enum.Enum):
    CASH = "cash"
    CARD = "card"
    UPI = "upi"
    BANK_TRANSFER = "bank_transfer"
    CREDIT = "credit"


class PaymentStatus(str, enum.Enum):
    PAID = "paid"
    PARTIAL = "partial"
    CREDIT = "credit"


class StockAlertLevel(str, enum.Enum):
    NEAR_MIN = "near_min"      # YELLOW
    LOW = "low"                # ORANGE
    CRITICAL = "critical"      # RED


# ---------------------------------------------------------------------------
# Core: Shops, Subscriptions, Users
# ---------------------------------------------------------------------------

class Shop(db.Model):
    __tablename__ = "shops"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    gst_number = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    logo_url = db.Column(db.String(255), default="/static/img/logo.svg")
    status = db.Column(db.String(20), default=ShopStatus.TRIAL.value, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="shop", lazy="dynamic")
    subscriptions = db.relationship("Subscription", backref="shop", lazy="dynamic")

    def active_subscription(self):
        return (
            self.subscriptions.filter(Subscription.status.in_(["active", "trial"]))
            .order_by(Subscription.end_date.desc())
            .first()
        )

    def __repr__(self):
        return f"<Shop {self.name}>"


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False)
    plan_name = db.Column(db.String(80), default="Standard")
    start_date = db.Column(db.Date, default=date.today)
    end_date = db.Column(db.Date)
    amount = db.Column(db.Numeric(10, 2), default=0)
    status = db.Column(db.String(20), default="trial")  # trial/active/expired/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_valid(self):
        if self.status not in ("trial", "active"):
            return False
        if self.end_date and self.end_date < date.today():
            return False
        return True


class LoginActivity(db.Model):
    """Used to detect license misuse: same subscription/shop logged in from
    multiple distinct device fingerprints in a short window."""
    __tablename__ = "login_activity"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"))
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    device_fingerprint = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True)  # null for super admin
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=Role.EMPLOYEE.value)
    is_active_flag = db.Column("is_active", db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime)

    employee_profile = db.relationship(
        "EmployeeProfile", backref="user", uselist=False, cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_active(self):
        return self.is_active_flag

    def is_super_admin(self):
        return self.role == Role.SUPER_ADMIN.value

    def is_owner(self):
        return self.role == Role.OWNER.value

    def is_employee(self):
        return self.role == Role.EMPLOYEE.value

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


class EmployeeProfile(db.Model):
    __tablename__ = "employee_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    monthly_salary = db.Column(db.Numeric(10, 2), default=0)
    joined_on = db.Column(db.Date, default=date.today)
    notes = db.Column(db.String(255))


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Godowns (warehouses / branches) & per-location stock
# ---------------------------------------------------------------------------

class Godown(db.Model):
    """A physical stock location. Every shop gets an auto-created 'Main Godown'
    the first time stock is touched, so single-location shops need no setup."""
    __tablename__ = "godowns"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255))
    is_default = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Godown {self.name}>"


class PartStock(db.Model):
    """Per-godown quantity for a part. Sum across a part's rows always equals
    Part.current_stock — both are updated together inside app/utils/stock.py."""
    __tablename__ = "part_stock"
    __table_args__ = (db.UniqueConstraint("part_id", "godown_id", name="uq_part_godown"),)

    id = db.Column(db.Integer, primary_key=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    godown_id = db.Column(db.Integer, db.ForeignKey("godowns.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)

    part = db.relationship("Part", backref=db.backref("godown_stock", lazy="dynamic"))
    godown = db.relationship("Godown", backref=db.backref("part_stock", lazy="dynamic"))


class StockTransfer(db.Model):
    """Audited record of moving stock of one part from one godown to another."""
    __tablename__ = "stock_transfers"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    from_godown_id = db.Column(db.Integer, db.ForeignKey("godowns.id"), nullable=False)
    to_godown_id = db.Column(db.Integer, db.ForeignKey("godowns.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255))
    transferred_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    part = db.relationship("Part")
    from_godown = db.relationship("Godown", foreign_keys=[from_godown_id])
    to_godown = db.relationship("Godown", foreign_keys=[to_godown_id])


class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.String(255))
    gst_number = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    parts = db.relationship("Part", backref="supplier", lazy="dynamic")

    def total_due(self):
        purchases_total = sum(p.total_amount for p in self.purchases)
        paid_total = sum(
            pay.amount for pay in Payment.query.filter_by(
                party_type="supplier", party_id=self.id
            ).all()
        )
        return purchases_total - paid_total


class Part(db.Model):
    __tablename__ = "parts"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)

    name = db.Column(db.String(150), nullable=False)
    oem_number = db.Column(db.String(80), index=True)
    alternate_part_number = db.Column(db.String(80))
    vehicle_type = db.Column(db.String(30))
    vehicle_model = db.Column(db.String(100))
    brand = db.Column(db.String(100))
    category = db.Column(db.String(100))

    purchase_price = db.Column(db.Numeric(10, 2), default=0)
    selling_price = db.Column(db.Numeric(10, 2), default=0)
    gst_percent = db.Column(db.Numeric(5, 2), default=18)
    hsn_code = db.Column(db.String(20))

    current_stock = db.Column(db.Integer, default=0)
    minimum_stock = db.Column(db.Integer, default=5)
    rack_location = db.Column(db.String(50))

    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    stock_ledger = db.relationship("StockLedger", backref="part", lazy="dynamic")

    def stock_level(self):
        """Returns one of: 'critical', 'low', 'near_min', 'ok' for dashboard color-coding."""
        if self.minimum_stock <= 0:
            return "ok"
        ratio = self.current_stock / self.minimum_stock
        if ratio <= 0.5:
            return "critical"
        if ratio <= 1.0:
            return "low"
        if ratio <= 1.25:
            return "near_min"
        return "ok"


class StockLedger(db.Model):
    """Immutable audit trail of every stock movement (purchase, sale, adjustment, return)."""
    __tablename__ = "stock_ledger"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)

    change_qty = db.Column(db.Integer, nullable=False)   # positive = stock in, negative = stock out
    balance_after = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(30), nullable=False)    # purchase / sale / adjustment / return / audit / transfer_in / transfer_out
    reference_type = db.Column(db.String(30))             # 'invoice' / 'purchase' / 'manual' / 'transfer'
    reference_id = db.Column(db.Integer)
    godown_id = db.Column(db.Integer, db.ForeignKey("godowns.id"), nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    godown = db.relationship("Godown")


class StockAlert(db.Model):
    """Tracks whether a low-stock alert has already fired for a part, so we send
    exactly one notification per dip below a threshold (no repeat spam)."""
    __tablename__ = "stock_alerts"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False, index=True)
    level = db.Column(db.String(20), nullable=False)
    is_resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)

    part = db.relationship("Part")


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------

class PurchaseRequest(db.Model):
    """Employee-initiated request. Approval alone never changes stock."""
    __tablename__ = "purchase_requests"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(255))
    status = db.Column(db.String(20), default=PurchaseRequestStatus.PENDING.value)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)

    part = db.relationship("Part")
    requester = db.relationship("User", foreign_keys=[requested_by])


class Purchase(db.Model):
    """A material-received event. This is the ONLY place stock increases from purchasing."""
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_request_id = db.Column(db.Integer, db.ForeignKey("purchase_requests.id"), nullable=True)

    invoice_number = db.Column(db.String(80))
    total_amount = db.Column(db.Numeric(12, 2), default=0)
    gst_amount = db.Column(db.Numeric(12, 2), default=0)

    received_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    supplier = db.relationship("Supplier", backref="purchases")
    items = db.relationship("PurchaseItem", backref="purchase", cascade="all, delete-orphan")


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    purchase_price = db.Column(db.Numeric(10, 2), nullable=False)
    gst_percent = db.Column(db.Numeric(5, 2), default=18)

    part = db.relationship("Part")


# ---------------------------------------------------------------------------
# Customers, Sales / Invoices
# ---------------------------------------------------------------------------

class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    mobile = db.Column(db.String(20), index=True)
    vehicle_number = db.Column(db.String(30))
    vehicle_type = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship("Invoice", backref="customer", lazy="dynamic")

    def total_due(self):
        return sum(inv.balance_due() for inv in self.invoices)


class LabourCharge(db.Model):
    """Owner-defined master list of fitting/repair/service charges."""
    __tablename__ = "labour_charges"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    invoice_number = db.Column(db.String(30), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)

    vehicle_number = db.Column(db.String(30))
    vehicle_type = db.Column(db.String(30))

    subtotal = db.Column(db.Numeric(12, 2), default=0)
    discount = db.Column(db.Numeric(12, 2), default=0)
    labour_total = db.Column(db.Numeric(12, 2), default=0)
    gst_total = db.Column(db.Numeric(12, 2), default=0)
    grand_total = db.Column(db.Numeric(12, 2), default=0)

    payment_method = db.Column(db.String(20), default=PaymentMethod.CASH.value)
    payment_status = db.Column(db.String(20), default=PaymentStatus.PAID.value)
    amount_paid = db.Column(db.Numeric(12, 2), default=0)

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_void = db.Column(db.Boolean, default=False)  # employees cannot set this; owner-only

    items = db.relationship("InvoiceItem", backref="invoice", cascade="all, delete-orphan")
    payments = db.relationship("Payment", backref="invoice", lazy="dynamic")
    creator = db.relationship("User")

    def balance_due(self):
        return float(self.grand_total) - float(self.amount_paid)


class InvoiceItem(db.Model):
    __tablename__ = "invoice_items"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    part_id = db.Column(db.Integer, db.ForeignKey("parts.id"), nullable=False)
    part_name_snapshot = db.Column(db.String(150))  # preserved even if part is edited/deactivated later
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    gst_percent = db.Column(db.Numeric(5, 2), default=18)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)

    part = db.relationship("Part")


class Payment(db.Model):
    """Generic ledger entry for money in (from customers) or out (to suppliers/employees)."""
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    party_type = db.Column(db.String(20), nullable=False)  # customer / supplier
    party_id = db.Column(db.Integer, nullable=False)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    method = db.Column(db.String(20), default=PaymentMethod.CASH.value)
    direction = db.Column(db.String(10), nullable=False)  # 'in' or 'out'
    note = db.Column(db.String(255))

    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Employees & Expenses
# ---------------------------------------------------------------------------

class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    expense_type = db.Column(db.String(30), nullable=False)  # salary/daily/advance/commission/other
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    expense_date = db.Column(db.Date, default=date.today)
    note = db.Column(db.String(255))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship("User", foreign_keys=[employee_id])


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.Integer)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True, index=True)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
    customer = db.relationship("Customer")
    supplier = db.relationship("Supplier")


# ---------------------------------------------------------------------------
# Backup & Recovery
# ---------------------------------------------------------------------------

class BackupLog(db.Model):
    """One row per backup attempt. shop_id is NULL for a platform-wide run
    that backed up every shop in one pass (each shop still gets its own
    encrypted file — see app/utils/backup.py)."""
    __tablename__ = "backup_logs"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)
    backup_type = db.Column(db.String(20), nullable=False)   # daily/weekly/monthly/manual
    status = db.Column(db.String(20), nullable=False, default="success")  # success/failed
    file_path = db.Column(db.String(500))
    size_bytes = db.Column(db.Integer)
    checksum_sha256 = db.Column(db.String(64))
    table_counts = db.Column(db.Text)          # JSON string: {"parts": 5, "invoices": 12, ...}
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    shop = db.relationship("Shop")


class RestoreLog(db.Model):
    """Audit trail of restores — who restored what shop from which backup, and when."""
    __tablename__ = "restore_logs"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    backup_log_id = db.Column(db.Integer, db.ForeignKey("backup_logs.id"), nullable=False)
    restored_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(20), default="success")  # success/failed
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Help Center
# ---------------------------------------------------------------------------

class HelpArticle(db.Model):
    __tablename__ = "help_articles"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(30), nullable=False)
    # beginner / owner_guide / employee_guide / faq / whats_new
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(220), unique=True, nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    video_url = db.Column(db.String(300))
    order_index = db.Column(db.Integer, default=0)
    is_published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Notification Center
# ---------------------------------------------------------------------------

class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=True, index=True)  # NULL = platform-wide
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)  # NULL = whole shop (all owner/employee logins)
    category = db.Column(db.String(30), nullable=False)
    # inventory / sales / customer / supplier / business / subscription
    event_type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.String(500))
    link = db.Column(db.String(300))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Support Center
# ---------------------------------------------------------------------------

class SupportTicket(db.Model):
    __tablename__ = "support_tickets"

    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.Integer, db.ForeignKey("shops.id"), nullable=False, index=True)
    raised_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    category = db.Column(db.String(30), nullable=False)
    # billing / inventory / sales / purchase / reports / login_issue / technical_issue / bug / feature_request / other
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    screenshot_path = db.Column(db.String(500))
    status = db.Column(db.String(20), default="open")  # open / in_progress / resolved / closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shop = db.relationship("Shop")
    raiser = db.relationship("User", foreign_keys=[raised_by])
    replies = db.relationship("SupportTicketReply", backref="ticket",
                               cascade="all, delete-orphan", order_by="SupportTicketReply.created_at")


class SupportTicketReply(db.Model):
    __tablename__ = "support_ticket_replies"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("support_tickets.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    message = db.Column(db.Text, nullable=False)
    is_admin_reply = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")
