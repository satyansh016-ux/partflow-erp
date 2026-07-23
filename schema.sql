-- PartFlow ERP - Reference PostgreSQL / Supabase Schema
-- ---------------------------------------------------------------------------
-- You do NOT need to run this file by hand. When DATABASE_URL points at your
-- Supabase Postgres connection string, `python seed.py` (via SQLAlchemy's
-- db.create_all()) creates every one of these tables automatically, with the
-- exact same structure. This file exists purely as a human-readable reference
-- for DBAs, auditors, or anyone hand-reviewing the data model, and as a
-- starting point if you'd rather manage schema changes with raw SQL/Supabase
-- migrations instead of Flask-Migrate.
-- ---------------------------------------------------------------------------

CREATE TABLE shops (
    id SERIAL PRIMARY KEY,
    name VARCHAR(150) NOT NULL,
    gst_number VARCHAR(20),
    phone VARCHAR(20),
    email VARCHAR(120),
    address VARCHAR(255),
    logo_url VARCHAR(255) DEFAULT '/static/img/logo.svg',
    status VARCHAR(20) NOT NULL DEFAULT 'trial',   -- trial / active / suspended / banned
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE subscriptions (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    plan_name VARCHAR(80) DEFAULT 'Standard',
    start_date DATE DEFAULT CURRENT_DATE,
    end_date DATE,
    amount NUMERIC(10,2) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'trial',
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER REFERENCES shops(id),           -- NULL for super_admin
    name VARCHAR(120) NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    phone VARCHAR(20),
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'employee',    -- super_admin / owner / employee
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now(),
    last_login_at TIMESTAMP
);
CREATE INDEX idx_users_email ON users(email);

CREATE TABLE employee_profiles (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    monthly_salary NUMERIC(10,2) DEFAULT 0,
    joined_on DATE DEFAULT CURRENT_DATE,
    notes VARCHAR(255)
);

CREATE TABLE login_activity (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    shop_id INTEGER REFERENCES shops(id),
    ip_address VARCHAR(64),
    user_agent VARCHAR(255),
    device_fingerprint VARCHAR(128),
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE godowns (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    name VARCHAR(120) NOT NULL,
    address VARCHAR(255),
    is_default BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE part_stock (
    id SERIAL PRIMARY KEY,
    part_id INTEGER NOT NULL REFERENCES parts(id),
    godown_id INTEGER NOT NULL REFERENCES godowns(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    UNIQUE (part_id, godown_id)
);

CREATE TABLE stock_transfers (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    from_godown_id INTEGER NOT NULL REFERENCES godowns(id),
    to_godown_id INTEGER NOT NULL REFERENCES godowns(id),
    quantity INTEGER NOT NULL,
    note VARCHAR(255),
    transferred_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE suppliers (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    name VARCHAR(150) NOT NULL,
    phone VARCHAR(20),
    email VARCHAR(120),
    address VARCHAR(255),
    gst_number VARCHAR(20),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_suppliers_shop ON suppliers(shop_id);

CREATE TABLE parts (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    name VARCHAR(150) NOT NULL,
    oem_number VARCHAR(80),
    alternate_part_number VARCHAR(80),
    vehicle_type VARCHAR(30),          -- bike/car/truck/bus/tractor/jcb/heavy_vehicle
    vehicle_model VARCHAR(100),
    brand VARCHAR(100),
    category VARCHAR(100),
    purchase_price NUMERIC(10,2) DEFAULT 0,
    selling_price NUMERIC(10,2) DEFAULT 0,
    gst_percent NUMERIC(5,2) DEFAULT 18,
    hsn_code VARCHAR(20),
    current_stock INTEGER DEFAULT 0,
    minimum_stock INTEGER DEFAULT 5,
    rack_location VARCHAR(50),
    supplier_id INTEGER REFERENCES suppliers(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_parts_shop ON parts(shop_id);
CREATE INDEX idx_parts_oem ON parts(oem_number);

CREATE TABLE stock_ledger (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    change_qty INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason VARCHAR(30) NOT NULL,        -- purchase/sale/adjustment/return/audit
    reference_type VARCHAR(30),
    reference_id INTEGER,
    godown_id INTEGER REFERENCES godowns(id),
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_stock_ledger_shop ON stock_ledger(shop_id);
CREATE INDEX idx_stock_ledger_part ON stock_ledger(part_id);

CREATE TABLE stock_alerts (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    level VARCHAR(20) NOT NULL,          -- near_min/low/critical
    is_resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT now(),
    resolved_at TIMESTAMP
);
CREATE INDEX idx_stock_alerts_shop ON stock_alerts(shop_id);
CREATE INDEX idx_stock_alerts_part ON stock_alerts(part_id);

CREATE TABLE purchase_requests (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    supplier_id INTEGER REFERENCES suppliers(id),
    quantity INTEGER NOT NULL,
    reason VARCHAR(255),
    status VARCHAR(20) DEFAULT 'pending',  -- pending/approved/rejected/received
    requested_by INTEGER REFERENCES users(id),
    approved_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now(),
    decided_at TIMESTAMP
);
CREATE INDEX idx_purchase_requests_shop ON purchase_requests(shop_id);

CREATE TABLE purchases (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    purchase_request_id INTEGER REFERENCES purchase_requests(id),
    invoice_number VARCHAR(80),
    total_amount NUMERIC(12,2) DEFAULT 0,
    gst_amount NUMERIC(12,2) DEFAULT 0,
    received_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_purchases_shop ON purchases(shop_id);

CREATE TABLE purchase_items (
    id SERIAL PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    quantity INTEGER NOT NULL,
    purchase_price NUMERIC(10,2) NOT NULL,
    gst_percent NUMERIC(5,2) DEFAULT 18
);

CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    name VARCHAR(120) NOT NULL,
    mobile VARCHAR(20),
    vehicle_number VARCHAR(30),
    vehicle_type VARCHAR(30),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_customers_shop ON customers(shop_id);
CREATE INDEX idx_customers_mobile ON customers(mobile);

CREATE TABLE labour_charges (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    name VARCHAR(120) NOT NULL,
    price NUMERIC(10,2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE invoices (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    invoice_number VARCHAR(30) UNIQUE NOT NULL,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    vehicle_number VARCHAR(30),
    vehicle_type VARCHAR(30),
    subtotal NUMERIC(12,2) DEFAULT 0,
    discount NUMERIC(12,2) DEFAULT 0,
    labour_total NUMERIC(12,2) DEFAULT 0,
    gst_total NUMERIC(12,2) DEFAULT 0,
    grand_total NUMERIC(12,2) DEFAULT 0,
    payment_method VARCHAR(20) DEFAULT 'cash',
    payment_status VARCHAR(20) DEFAULT 'paid',   -- paid/partial/credit
    amount_paid NUMERIC(12,2) DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now(),
    is_void BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_invoices_shop ON invoices(shop_id);

CREATE TABLE invoice_items (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    part_id INTEGER NOT NULL REFERENCES parts(id),
    part_name_snapshot VARCHAR(150),
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(10,2) NOT NULL,
    gst_percent NUMERIC(5,2) DEFAULT 18,
    line_total NUMERIC(12,2) NOT NULL
);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    party_type VARCHAR(20) NOT NULL,     -- customer/supplier
    party_id INTEGER NOT NULL,
    invoice_id INTEGER REFERENCES invoices(id),
    purchase_id INTEGER REFERENCES purchases(id),
    amount NUMERIC(12,2) NOT NULL,
    method VARCHAR(20) DEFAULT 'cash',
    direction VARCHAR(10) NOT NULL,       -- in/out
    note VARCHAR(255),
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_payments_shop ON payments(shop_id);

CREATE TABLE expenses (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    employee_id INTEGER REFERENCES users(id),
    expense_type VARCHAR(30) NOT NULL,    -- salary/daily/advance/commission/other
    amount NUMERIC(10,2) NOT NULL,
    expense_date DATE DEFAULT CURRENT_DATE,
    note VARCHAR(255),
    created_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_expenses_shop ON expenses(shop_id);

CREATE TABLE audit_logs (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER REFERENCES shops(id),
    user_id INTEGER REFERENCES users(id),
    action VARCHAR(80) NOT NULL,
    entity_type VARCHAR(50),
    entity_id INTEGER,
    customer_id INTEGER REFERENCES customers(id),   -- used by the Business Activity Timeline's customer filter
    supplier_id INTEGER REFERENCES suppliers(id),   -- used by the Business Activity Timeline's supplier filter
    details TEXT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_audit_logs_shop ON audit_logs(shop_id);
CREATE INDEX idx_audit_logs_customer ON audit_logs(customer_id);
CREATE INDEX idx_audit_logs_supplier ON audit_logs(supplier_id);

-- ---------------------------------------------------------------------------
-- Backup & Recovery
-- ---------------------------------------------------------------------------

CREATE TABLE backup_logs (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER REFERENCES shops(id),          -- NULL only if ever used for a platform-wide log entry
    backup_type VARCHAR(20) NOT NULL,               -- daily/weekly/monthly/manual
    status VARCHAR(20) NOT NULL DEFAULT 'success',   -- success/failed
    file_path VARCHAR(500),
    size_bytes INTEGER,
    checksum_sha256 VARCHAR(64),
    table_counts TEXT,                                -- JSON: {"parts": 5, "invoices": 12, ...}
    error_message TEXT,
    started_at TIMESTAMP DEFAULT now(),
    completed_at TIMESTAMP
);
CREATE INDEX idx_backup_logs_shop ON backup_logs(shop_id);

CREATE TABLE restore_logs (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    backup_log_id INTEGER NOT NULL REFERENCES backup_logs(id),
    restored_by INTEGER REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_restore_logs_shop ON restore_logs(shop_id);

-- ---------------------------------------------------------------------------
-- Help Center
-- ---------------------------------------------------------------------------

CREATE TABLE help_articles (
    id SERIAL PRIMARY KEY,
    category VARCHAR(30) NOT NULL,     -- beginner/owner_guide/employee_guide/faq/whats_new
    title VARCHAR(200) NOT NULL,
    slug VARCHAR(220) UNIQUE NOT NULL,
    body_html TEXT NOT NULL,
    video_url VARCHAR(300),
    order_index INTEGER DEFAULT 0,
    is_published BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Notification Center
-- ---------------------------------------------------------------------------

CREATE TABLE notifications (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER REFERENCES shops(id),     -- NULL = platform-wide
    user_id INTEGER REFERENCES users(id),      -- NULL = whole shop (all owner/employee logins)
    category VARCHAR(30) NOT NULL,              -- inventory/sales/customer/supplier/business/subscription
    event_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    body VARCHAR(500),
    link VARCHAR(300),
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_notifications_shop ON notifications(shop_id);
CREATE INDEX idx_notifications_user ON notifications(user_id);

-- ---------------------------------------------------------------------------
-- Support Center
-- ---------------------------------------------------------------------------

CREATE TABLE support_tickets (
    id SERIAL PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    raised_by INTEGER NOT NULL REFERENCES users(id),
    category VARCHAR(30) NOT NULL,
    subject VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    screenshot_path VARCHAR(500),
    status VARCHAR(20) DEFAULT 'open',     -- open/in_progress/resolved/closed
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);
CREATE INDEX idx_support_tickets_shop ON support_tickets(shop_id);

CREATE TABLE support_ticket_replies (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES support_tickets(id),
    user_id INTEGER REFERENCES users(id),
    message TEXT NOT NULL,
    is_admin_reply BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Recommended: enable Supabase Row Level Security once you connect the API
-- directly to Supabase's PostgREST layer. This Flask app currently enforces
-- shop isolation at the application layer (every query filters by shop_id
-- from the logged-in user's session) rather than via RLS policies, which is
-- the correct model as long as ALL data access goes through this Flask app.
-- If you later expose Supabase's REST/GraphQL API directly to clients, add
-- RLS policies keyed on shop_id to enforce isolation at the database layer too.
-- ---------------------------------------------------------------------------
