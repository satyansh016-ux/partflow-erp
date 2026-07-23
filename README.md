# PartFlow ERP

A multi-tenant SaaS ERP for automobile spare parts businesses (bike, car,
truck, bus, tractor, JCB, heavy commercial vehicle). Built with Flask +
Bootstrap 5 + SQLAlchemy, ready to run on SQLite locally or PostgreSQL/Supabase
in production.

## What's built (Phase 1 — core foundation)

- **Auth & roles**: Super Admin / Shop Owner / Employee, session-based login,
  password hashing, per-shop data isolation enforced on every query.
- **Super Admin**: create/manage shops, activate/suspend/ban, subscription
  management, password resets, login-activity monitoring, and a heuristic
  license-misuse detector (flags a shop when its owner logs in from many
  distinct devices in 24h).
- **Inventory**: full part CRUD (OEM number, alternate part number, vehicle
  type/model, brand, category, GST%, HSN, rack location, supplier), search,
  stock ledger (full audit trail of every movement), manual stock audit
  adjustments.
- **Smart stock alerts**: fires exactly once when a part first dips at/below
  minimum stock, never spams on further drops, and auto-resolves the moment
  stock is replenished above minimum — implemented and covered by an
  automated test.
- **Purchases**: employee purchase requests → owner approval → separate
  "receive" step that is the *only* place stock increases (approval alone
  never touches stock, per spec).
- **Sales & GST invoicing**: multi-part + labour-charge billing, automatic
  stock deduction, GST invoice PDF generation (navy/orange branded, HSN-wise
  line items), cash/card/UPI/bank/credit payment handling, partial payments,
  invoice voiding (owner-only, restores stock).
- **Customer & supplier ledgers**: due tracking, payment history, date-range
  search.
- **Employee management**: activity tracking, salary/advance/commission
  expense logging, permission boundaries (employees cannot void invoices,
  edit past transactions, or manually adjust stock — those actions simply
  aren't exposed in their blueprint).
- **Reports**: daily report, monthly CA report (HSN-wise GST summary, stock
  valuation), fast/slow/dead-stock movement report, smart reorder
  suggestions ranked by 30-day sale velocity.
- **Multi-godown stock transfer**: shops can have multiple godowns
  (warehouses/branches); every part's stock is tracked per-godown as well as
  a shop-wide total, and stock can be transferred between godowns with full
  audit history (who, when, how much, ledger entries on both sides). A
  single-location shop needs zero setup — a "Main Godown" is auto-created
  behind the scenes the first time stock is touched.
- **WhatsApp sharing (free, no API/fees)**: invoices and customer statements
  have a "Share on WhatsApp" button that opens a `wa.me` click-to-chat link
  with the message pre-filled to the customer's saved mobile number. No
  WhatsApp Business API, no Meta approval, no per-message cost — the person
  sending just taps Send (and can attach the downloaded PDF in the same
  chat). This is a deliberate choice over the paid Business API: see the
  note under "What's intentionally out of scope" below for when the paid
  API would actually be worth it.
- **Automatic backup & recovery**: every shop is backed up daily, weekly, and
  monthly — encrypted (Fernet), checksummed, and completely isolated per
  shop (a multi-tenant-safe export/import, not a raw DB dump). Super Admin
  gets a Backups dashboard to trigger manual backups, verify integrity, and
  do a one-click restore of a single shop without touching any other shop's
  data (restore requires typing the shop's name to confirm). Retention:
  last 7 daily / 4 weekly / 12 monthly backups kept per shop automatically.
  **Read the "Backup scheduling in production" section below** — this
  matters if you deploy with more than one worker process.
- **Help Center**: searchable, role-aware (owners see the Owner Guide,
  employees see the Employee Guide, everyone sees Beginner/FAQ/What's New).
  Seeded with real written guides covering the actual features in this app
  — not placeholder text. Super Admin can publish a new "What's New" article
  that also notifies every shop.
- **Notification Center**: bell icon with unread count in the sidebar.
  Real triggers wired into the app — new invoices, credit sales,
  partial/full payments, purchase received/approved, stock transfers,
  low/critical stock, backup completed/failed, subscription/trial expiry,
  pending supplier dues, daily/monthly report ready — filterable by
  category, searchable, mark-as-read / mark-all-read.
- **Business Activity Timeline**: chronological, day-grouped feed of what
  happened in the shop (sales, purchases, payments, stock changes, employee
  actions), filterable by date/employee/customer/supplier, searchable, and
  exportable to CSV.
- **Support Center**: raise a ticket (with category, screenshot upload),
  track status, reply in-thread; WhatsApp and email support buttons; Super
  Admin gets a cross-shop inbox to respond and change ticket status. Ticket
  history is never deleted.
- **Security**: bcrypt-strength password hashing (Werkzeug), role decorators,
  shop-scoped queries everywhere, audit log on every sensitive action,
  subscription-expiry gate.

Every route above was smoke-tested end-to-end (see "Testing" below), and the
core stock-alert / oversell-prevention / purchase-replenishment logic was
verified with an automated scenario test before delivery.

## What's intentionally out of scope for Phase 1

These were in the original spec but would have diluted the core build if
attempted in the same pass. The architecture leaves clean extension points
for each:

- Warranty & return management (a `reason='return'` stock-ledger reason
  already exists to build on)
- Barcode/QR scanning (stock ledger + part lookup already support it — needs
  a scanner-input UI layer)
- Automated/bulk WhatsApp sending (current sharing uses free `wa.me` links,
  which need a manual tap per message and can't send to many customers at
  once — automating that requires the paid WhatsApp Business API plus a BSP
  like Gupshup/AiSensy/Interakt, Meta business verification, and approved
  message templates)
- PDF/Excel export of the monthly report table (the PDF invoice generator
  and `openpyxl` dependency are already in place — the report page just
  needs an export button wired to them)
- A real payment gateway for subscription billing (subscriptions are
  currently activated manually by the Super Admin)

## Tech stack

- **Backend**: Python 3.12, Flask 3, SQLAlchemy, Flask-Login, Flask-Migrate
- **Frontend**: Bootstrap 5, vanilla JS, Bootstrap Icons
- **Database**: SQLite by default (zero setup) — swap to Supabase/PostgreSQL
  by changing one environment variable, no code changes needed
- **PDF**: ReportLab (GST invoices)

## Getting started

```bash
cd partflow-erp
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # edit values as needed
python seed.py                  # creates tables + super admin + demo shop
python run.py                   # http://localhost:5000
```

### Backup scheduling in production (please read)

The daily/weekly/monthly backup schedule runs **inside the Flask process**
via APScheduler (`app/scheduler.py`). This is correct and sufficient for:
- `python run.py` (single process)
- `gunicorn -w 1 run:app` (single worker)

It is **not** correct for multi-worker deployments (`gunicorn -w 4 run:app`,
or multiple container replicas) — each worker would start its own scheduler
and you'd get duplicate backups running at the same time. For that case:

1. Set `ENABLE_IN_APP_SCHEDULER=false` in `.env`.
2. Point an external cron (or your host's scheduled-jobs feature) at the
   protected internal endpoint instead:
   ```bash
   curl -s -X POST -H "X-Backup-Secret: $BACKUP_TRIGGER_SECRET" \
        https://yourapp.example.com/internal/run-backup/daily
   ```
   Use `/daily`, `/weekly`, or `/monthly` on the matching cron schedule.
   `BACKUP_TRIGGER_SECRET` is auto-generated into `.env` by `seed.py`.

Either way, **`BACKUP_ENCRYPTION_KEY` is the single most important secret in
this app** — back it up somewhere separate from the database itself (a
password manager, a secrets vault, wherever you keep credentials). Without
it, every backup file is permanently undecryptable, including on the same
server.

### Demo logins (created by `seed.py`)

| Role         | Email                     | Password      |
|--------------|---------------------------|---------------|
| Super Admin  | admin@partflow.com        | ChangeMe123!  |
| Shop Owner   | owner@sharmaauto.com      | Owner@123     |
| Employee     | employee@sharmaauto.com   | Employee@123  |

**Change these passwords before any real deployment.** `SUPERADMIN_EMAIL` /
`SUPERADMIN_PASSWORD` in `.env` control the super admin account seeded on
first run.

### Switching to Supabase / production Postgres

1. In Supabase: Project Settings → Database → Connection string (URI).
2. Set `DATABASE_URL` in `.env` to that string.
3. Run `python seed.py` again — it will create all tables in Supabase (see
   `schema.sql` for the equivalent raw SQL if you prefer to review/apply it
   by hand or via Supabase's SQL editor).
4. Set `SESSION_COOKIE_SECURE=true` once you're serving over HTTPS.

### Deploying

`gunicorn` is already in `requirements.txt`. A typical production start
command:

```bash
gunicorn -w 4 -b 0.0.0.0:8000 run:app
```

Put this behind Nginx/Caddy with TLS, or deploy directly to Railway,
Render, Fly.io, or a VPS — any host that can run a standard Flask/gunicorn
app and reach your Supabase Postgres instance will work.

## Project structure

```
partflow-erp/
  app/
    __init__.py          # app factory
    models.py             # all database tables
    decorators.py          # role-based access control
    extensions.py           # db/login_manager/migrate instances
    scheduler.py             # in-process APScheduler jobs (backups, reminders)
    utils/
      stock.py              # single source of truth for all stock changes
      helpers.py              # invoice numbering, audit logging, WhatsApp links
      invoice_pdf.py           # GST invoice PDF generator
      backup.py                # encrypted per-shop backup/restore engine
      notifications.py          # Notification-center helper
    auth/routes.py         # login/logout
    superadmin/routes.py    # platform management, backups, support inbox
    owner/routes.py          # full shop management (largest module)
    employee/routes.py        # restricted subset of owner's capabilities
    common/routes.py          # Help Center, Notifications, Support (owner+employee)
    internal/routes.py        # secret-protected endpoint for external cron
  templates/               # Jinja2 + Bootstrap 5, one folder per role + common/
  static/css/theme.css     # navy/orange PartFlow ERP brand theme
  static/img/logo.svg      # brand mark
  seed.py                  # bootstraps super admin + demo shop/data + help articles
  run.py                   # entry point
  schema.sql               # human-readable Postgres schema reference
```

## Testing

There's no bundled test suite in the deliverable (kept the codebase lean per
your priority on the core foundation), but every route was verified via
Flask's test client before delivery, plus a scenario test covering:
stock deduction on sale → alert fires once → alert does not repeat on
further depletion → alert auto-resolves on purchase receipt → oversell is
blocked and stock never goes negative. If you want, I can leave a proper
`pytest` suite in the repo for regression protection going forward — just ask.
