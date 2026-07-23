from datetime import date, datetime, timedelta
from decimal import Decimal
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.decorators import roles_required, active_shop_required
from app.models import (
    Role, Part, Supplier, StockLedger, StockAlert, PurchaseRequest, Purchase, PurchaseItem,
    Customer, Invoice, InvoiceItem, Payment, LabourCharge, User, EmployeeProfile, Expense,
    PurchaseRequestStatus, PaymentStatus, PaymentMethod, Godown, PartStock, StockTransfer, AuditLog
)
from app.utils.stock import apply_stock_change, get_active_alerts, get_default_godown, \
    transfer_stock, get_godown_breakdown
from app.utils.notifications import notify
from app.utils.helpers import generate_invoice_number, log_action, build_whatsapp_link, \
    invoice_whatsapp_message, customer_statement_whatsapp_message
from app.utils.invoice_pdf import build_invoice_pdf

owner_bp = Blueprint("owner", __name__)


@owner_bp.before_request
@login_required
@roles_required(Role.OWNER.value)
@active_shop_required
def guard():
    pass


def shop_id():
    return current_user.shop_id


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@owner_bp.route("/dashboard")
def dashboard():
    today = date.today()
    sid = shop_id()

    todays_invoices = Invoice.query.filter(
        Invoice.shop_id == sid, func.date(Invoice.created_at) == today, Invoice.is_void.is_(False)
    ).all()
    today_sales = sum(float(i.grand_total) for i in todays_invoices)
    today_bills = len(todays_invoices)

    todays_purchases = Purchase.query.filter(
        Purchase.shop_id == sid, func.date(Purchase.created_at) == today
    ).all()
    today_purchase_total = sum(float(p.total_amount) for p in todays_purchases)

    today_profit = today_sales - today_purchase_total  # simplified cash-flow style profit

    total_bills = Invoice.query.filter_by(shop_id=sid, is_void=False).count()

    low_stock_parts = [p for p in Part.query.filter_by(shop_id=sid, is_active=True).all()
                        if p.stock_level() in ("low", "near_min")]
    critical_stock_parts = [p for p in Part.query.filter_by(shop_id=sid, is_active=True).all()
                             if p.stock_level() == "critical"]

    customer_due = sum(c.total_due() for c in Customer.query.filter_by(shop_id=sid).all())
    supplier_due = sum(s.total_due() for s in Supplier.query.filter_by(shop_id=sid).all())

    month_start = today.replace(day=1)
    employee_expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.shop_id == sid, Expense.expense_date >= month_start
    ).scalar()

    active_alerts = get_active_alerts(sid)

    return render_template(
        "owner/dashboard.html",
        today_sales=today_sales, today_purchase_total=today_purchase_total,
        today_profit=today_profit, today_bills=today_bills, total_bills=total_bills,
        low_stock_count=len(low_stock_parts), critical_stock_count=len(critical_stock_parts),
        customer_due=customer_due, supplier_due=supplier_due,
        employee_expenses=float(employee_expenses), active_alerts=active_alerts,
    )


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

@owner_bp.route("/inventory")
def inventory():
    sid = shop_id()
    q = request.args.get("q", "").strip()
    vehicle_type = request.args.get("vehicle_type", "")

    query = Part.query.filter_by(shop_id=sid, is_active=True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Part.name.ilike(like), Part.oem_number.ilike(like),
            Part.alternate_part_number.ilike(like), Part.vehicle_model.ilike(like),
            Part.brand.ilike(like), Part.category.ilike(like)
        ))
    if vehicle_type:
        query = query.filter_by(vehicle_type=vehicle_type)

    parts = query.order_by(Part.name).all()
    suppliers = Supplier.query.filter_by(shop_id=sid).all()
    return render_template("owner/inventory.html", parts=parts, suppliers=suppliers, q=q,
                            vehicle_type=vehicle_type)


@owner_bp.route("/inventory/new", methods=["GET", "POST"])
def new_part():
    sid = shop_id()
    suppliers = Supplier.query.filter_by(shop_id=sid).all()
    if request.method == "POST":
        part = Part(
            shop_id=sid,
            name=request.form["name"].strip(),
            oem_number=request.form.get("oem_number", "").strip(),
            alternate_part_number=request.form.get("alternate_part_number", "").strip(),
            vehicle_type=request.form.get("vehicle_type"),
            vehicle_model=request.form.get("vehicle_model", "").strip(),
            brand=request.form.get("brand", "").strip(),
            category=request.form.get("category", "").strip(),
            purchase_price=Decimal(request.form.get("purchase_price") or 0),
            selling_price=Decimal(request.form.get("selling_price") or 0),
            gst_percent=Decimal(request.form.get("gst_percent") or 18),
            hsn_code=request.form.get("hsn_code", "").strip(),
            minimum_stock=int(request.form.get("minimum_stock") or 5),
            rack_location=request.form.get("rack_location", "").strip(),
            supplier_id=request.form.get("supplier_id") or None,
        )
        db.session.add(part)
        db.session.flush()

        opening_stock = int(request.form.get("opening_stock") or 0)
        if opening_stock:
            apply_stock_change(part, opening_stock, "adjustment", current_user.id,
                                reference_type="opening_stock")

        log_action("part_created", entity_type="part", entity_id=part.id, details=part.name)
        db.session.commit()
        flash(f"Part '{part.name}' added.", "success")
        return redirect(url_for("owner.inventory"))

    return render_template("owner/part_form.html", part=None, suppliers=suppliers)


@owner_bp.route("/inventory/<int:part_id>/edit", methods=["GET", "POST"])
def edit_part(part_id):
    sid = shop_id()
    part = Part.query.filter_by(id=part_id, shop_id=sid).first_or_404()
    suppliers = Supplier.query.filter_by(shop_id=sid).all()

    if request.method == "POST":
        part.name = request.form["name"].strip()
        part.oem_number = request.form.get("oem_number", "").strip()
        part.alternate_part_number = request.form.get("alternate_part_number", "").strip()
        part.vehicle_type = request.form.get("vehicle_type")
        part.vehicle_model = request.form.get("vehicle_model", "").strip()
        part.brand = request.form.get("brand", "").strip()
        part.category = request.form.get("category", "").strip()
        part.purchase_price = Decimal(request.form.get("purchase_price") or 0)
        part.selling_price = Decimal(request.form.get("selling_price") or 0)
        part.gst_percent = Decimal(request.form.get("gst_percent") or 18)
        part.hsn_code = request.form.get("hsn_code", "").strip()
        part.minimum_stock = int(request.form.get("minimum_stock") or 5)
        part.rack_location = request.form.get("rack_location", "").strip()
        part.supplier_id = request.form.get("supplier_id") or None

        log_action("part_updated", entity_type="part", entity_id=part.id, details=part.name)
        db.session.commit()
        flash("Part updated.", "success")
        return redirect(url_for("owner.inventory"))

    return render_template("owner/part_form.html", part=part, suppliers=suppliers)


@owner_bp.route("/inventory/<int:part_id>/adjust", methods=["POST"])
def adjust_stock(part_id):
    """Manual stock audit adjustment - owner only, always logged."""
    sid = shop_id()
    part = Part.query.filter_by(id=part_id, shop_id=sid).first_or_404()
    new_qty = int(request.form.get("new_qty"))
    diff = new_qty - part.current_stock
    reason_note = request.form.get("note", "Manual stock audit")

    apply_stock_change(part, diff, "audit", current_user.id, reference_type="manual")
    log_action("stock_adjusted", entity_type="part", entity_id=part.id,
               details=f"{reason_note} ({diff:+d})")
    db.session.commit()
    flash(f"Stock updated to {new_qty}.", "success")
    return redirect(url_for("owner.inventory"))


@owner_bp.route("/inventory/<int:part_id>/history")
def part_history(part_id):
    sid = shop_id()
    part = Part.query.filter_by(id=part_id, shop_id=sid).first_or_404()
    ledger = StockLedger.query.filter_by(part_id=part.id).order_by(StockLedger.created_at.desc()).all()
    breakdown = get_godown_breakdown(part)
    return render_template("owner/part_history.html", part=part, ledger=ledger, breakdown=breakdown)


@owner_bp.route("/inventory/<int:part_id>/deactivate", methods=["POST"])
def deactivate_part(part_id):
    sid = shop_id()
    part = Part.query.filter_by(id=part_id, shop_id=sid).first_or_404()
    part.is_active = False
    log_action("part_deactivated", entity_type="part", entity_id=part.id, details=part.name)
    db.session.commit()
    flash(f"'{part.name}' removed from active inventory.", "info")
    return redirect(url_for("owner.inventory"))


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

@owner_bp.route("/suppliers")
def suppliers():
    sid = shop_id()
    all_suppliers = Supplier.query.filter_by(shop_id=sid).order_by(Supplier.name).all()
    return render_template("owner/suppliers.html", suppliers=all_suppliers)


@owner_bp.route("/suppliers/new", methods=["GET", "POST"])
def new_supplier():
    if request.method == "POST":
        s = Supplier(
            shop_id=shop_id(),
            name=request.form["name"].strip(),
            phone=request.form.get("phone", "").strip(),
            email=request.form.get("email", "").strip(),
            address=request.form.get("address", "").strip(),
            gst_number=request.form.get("gst_number", "").strip(),
        )
        db.session.add(s)
        log_action("supplier_created", entity_type="supplier", entity_id=s.id, details=s.name,
                   supplier_id=s.id)
        db.session.commit()
        flash(f"Supplier '{s.name}' added.", "success")
        return redirect(url_for("owner.suppliers"))
    return render_template("owner/supplier_form.html")


@owner_bp.route("/suppliers/<int:supplier_id>")
def supplier_detail(supplier_id):
    sid = shop_id()
    supplier = Supplier.query.filter_by(id=supplier_id, shop_id=sid).first_or_404()
    purchases = Purchase.query.filter_by(supplier_id=supplier.id).order_by(Purchase.created_at.desc()).all()
    payments = Payment.query.filter_by(shop_id=sid, party_type="supplier", party_id=supplier.id)\
        .order_by(Payment.created_at.desc()).all()
    return render_template("owner/supplier_detail.html", supplier=supplier, purchases=purchases,
                            payments=payments)


@owner_bp.route("/suppliers/<int:supplier_id>/pay", methods=["POST"])
def pay_supplier(supplier_id):
    sid = shop_id()
    supplier = Supplier.query.filter_by(id=supplier_id, shop_id=sid).first_or_404()
    amount = Decimal(request.form.get("amount") or 0)
    method = request.form.get("method", PaymentMethod.CASH.value)
    payment = Payment(
        shop_id=sid, party_type="supplier", party_id=supplier.id,
        amount=amount, method=method, direction="out",
        note=request.form.get("note", ""), created_by=current_user.id,
    )
    db.session.add(payment)
    log_action("supplier_payment", entity_type="supplier", entity_id=supplier.id,
               details=f"Rs. {amount:,.2f} paid to {supplier.name}", supplier_id=supplier.id)
    db.session.commit()
    flash("Payment recorded.", "success")
    return redirect(url_for("owner.supplier_detail", supplier_id=supplier.id))


# ---------------------------------------------------------------------------
# Purchase Requests & Receiving
# ---------------------------------------------------------------------------

@owner_bp.route("/purchase-requests")
def purchase_requests():
    sid = shop_id()
    status_filter = request.args.get("status", "pending")
    query = PurchaseRequest.query.filter_by(shop_id=sid)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    requests_list = query.order_by(PurchaseRequest.created_at.desc()).all()
    return render_template("owner/purchase_requests.html", requests=requests_list, status_filter=status_filter)


@owner_bp.route("/purchase-requests/<int:req_id>/decide", methods=["POST"])
def decide_purchase_request(req_id):
    sid = shop_id()
    pr = PurchaseRequest.query.filter_by(id=req_id, shop_id=sid).first_or_404()
    decision = request.form.get("decision")  # 'approved' or 'rejected'
    if decision not in (PurchaseRequestStatus.APPROVED.value, PurchaseRequestStatus.REJECTED.value):
        abort(400)

    pr.status = decision
    pr.approved_by = current_user.id
    pr.decided_at = datetime.utcnow()
    # NOTE: approval intentionally does NOT change stock. Stock only changes on receiving.
    log_action("purchase_request_decided", entity_type="purchase_request", entity_id=pr.id,
               details=f"{pr.part.name} x{pr.quantity} — {decision}")
    if decision == PurchaseRequestStatus.APPROVED.value:
        notify(sid, "inventory", "purchase_approved", f"Purchase request approved: {pr.part.name}",
               body=f"Qty {pr.quantity} approved.", link=url_for("owner.receive_purchase"))
    db.session.commit()
    flash(f"Purchase request {decision}.", "success")
    return redirect(url_for("owner.purchase_requests"))


@owner_bp.route("/purchases/receive", methods=["GET", "POST"])
def receive_purchase():
    sid = shop_id()
    parts = Part.query.filter_by(shop_id=sid, is_active=True).order_by(Part.name).all()
    suppliers = Supplier.query.filter_by(shop_id=sid).order_by(Supplier.name).all()
    approved_requests = PurchaseRequest.query.filter_by(
        shop_id=sid, status=PurchaseRequestStatus.APPROVED.value
    ).all()

    if request.method == "POST":
        supplier_id = request.form.get("supplier_id")
        invoice_number = request.form.get("invoice_number", "").strip()
        part_ids = request.form.getlist("part_id[]")
        quantities = request.form.getlist("quantity[]")
        prices = request.form.getlist("purchase_price[]")
        gsts = request.form.getlist("gst_percent[]")
        pr_id = request.form.get("purchase_request_id") or None

        if not part_ids:
            flash("Add at least one part to receive.", "danger")
            return redirect(url_for("owner.receive_purchase"))

        purchase = Purchase(
            shop_id=sid, supplier_id=supplier_id, invoice_number=invoice_number,
            purchase_request_id=pr_id, received_by=current_user.id,
        )
        db.session.add(purchase)
        db.session.flush()

        total = Decimal("0")
        gst_total = Decimal("0")
        for pid, qty, price, gst in zip(part_ids, quantities, prices, gsts):
            part = Part.query.filter_by(id=pid, shop_id=sid).first()
            if not part:
                continue
            qty = int(qty)
            price = Decimal(price or 0)
            gst_pct = Decimal(gst or 18)

            item = PurchaseItem(purchase_id=purchase.id, part_id=part.id, quantity=qty,
                                 purchase_price=price, gst_percent=gst_pct)
            db.session.add(item)

            line_total = qty * price
            total += line_total
            gst_total += line_total * gst_pct / 100

            # Stock increases ONLY here, at receiving time.
            apply_stock_change(part, qty, "purchase", current_user.id,
                                reference_type="purchase", reference_id=purchase.id)
            # Keep purchase price current for future costing.
            part.purchase_price = price

        purchase.total_amount = total + gst_total
        purchase.gst_amount = gst_total

        if pr_id:
            pr = PurchaseRequest.query.get(pr_id)
            if pr:
                pr.status = PurchaseRequestStatus.RECEIVED.value

        log_action("purchase_received", entity_type="purchase", entity_id=purchase.id,
                   details=f"Received from {purchase.supplier.name} — Rs. {purchase.total_amount:,.2f}",
                   supplier_id=purchase.supplier_id)
        notify(sid, "inventory", "purchase_received", f"Purchase received from {purchase.supplier.name}",
               body=f"Rs. {purchase.total_amount:,.2f} — stock updated.",
               link=url_for("owner.inventory"))
        notify(sid, "supplier", "purchase_arrival", f"Purchase arrival: {purchase.supplier.name}",
               body=f"Invoice {purchase.invoice_number or '-'} received.")
        db.session.commit()
        flash("Purchase received and stock updated.", "success")
        return redirect(url_for("owner.inventory"))

    return render_template("owner/receive_purchase.html", parts=parts, suppliers=suppliers,
                            approved_requests=approved_requests)


# ---------------------------------------------------------------------------
# Sales / Invoicing (also used by employee blueprint via shared helpers below)
# ---------------------------------------------------------------------------

@owner_bp.route("/sales/new", methods=["GET", "POST"])
def new_sale():
    return _create_sale_view("owner")


@owner_bp.route("/invoices")
def invoices():
    sid = shop_id()
    all_invoices = Invoice.query.filter_by(shop_id=sid).order_by(Invoice.created_at.desc()).limit(200).all()
    return render_template("owner/invoices.html", invoices=all_invoices)


@owner_bp.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    sid = shop_id()
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    whatsapp_url = build_whatsapp_link(
        invoice.customer.mobile, invoice_whatsapp_message(invoice, current_user.shop)
    )
    return render_template("owner/invoice_detail.html", invoice=invoice, whatsapp_url=whatsapp_url)


@owner_bp.route("/invoices/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    sid = shop_id()
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    buffer = build_invoice_pdf(invoice, current_user.shop, invoice.customer, invoice.items)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                      download_name=f"{invoice.invoice_number}.pdf")


@owner_bp.route("/invoices/<int:invoice_id>/void", methods=["POST"])
def void_invoice(invoice_id):
    """Only owners can void an invoice. When voided, stock is restored."""
    sid = shop_id()
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    if invoice.is_void:
        flash("Invoice already voided.", "info")
        return redirect(url_for("owner.invoice_detail", invoice_id=invoice.id))

    for item in invoice.items:
        if item.part:
            apply_stock_change(item.part, item.quantity, "return", current_user.id,
                                reference_type="invoice_void", reference_id=invoice.id)
    invoice.is_void = True
    log_action("invoice_voided", entity_type="invoice", entity_id=invoice.id,
               details=invoice.invoice_number)
    db.session.commit()
    flash("Invoice voided and stock restored.", "success")
    return redirect(url_for("owner.invoice_detail", invoice_id=invoice.id))


@owner_bp.route("/invoices/<int:invoice_id>/pay", methods=["POST"])
def record_invoice_payment(invoice_id):
    sid = shop_id()
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    amount = Decimal(request.form.get("amount") or 0)
    method = request.form.get("method", PaymentMethod.CASH.value)

    payment = Payment(
        shop_id=sid, party_type="customer", party_id=invoice.customer_id,
        invoice_id=invoice.id, amount=amount, method=method, direction="in",
        created_by=current_user.id,
    )
    db.session.add(payment)
    invoice.amount_paid = Decimal(invoice.amount_paid) + amount
    if invoice.amount_paid >= invoice.grand_total:
        invoice.payment_status = PaymentStatus.PAID.value
        notify(sid, "customer", "full_payment_received", f"Full payment received: {invoice.invoice_number}",
               body=f"Rs. {amount:,.2f} from {invoice.customer.name}.",
               link=url_for("owner.invoice_detail", invoice_id=invoice.id))
    elif invoice.amount_paid > 0:
        invoice.payment_status = PaymentStatus.PARTIAL.value
        notify(sid, "customer", "partial_payment_received",
               f"Partial payment received: {invoice.invoice_number}",
               body=f"Rs. {amount:,.2f} from {invoice.customer.name}. "
                    f"Balance Rs. {invoice.balance_due():,.2f}.",
               link=url_for("owner.invoice_detail", invoice_id=invoice.id))

    log_action("payment_recorded", entity_type="invoice", entity_id=invoice.id,
               details=f"Rs. {amount:,.2f} from {invoice.customer.name}",
               customer_id=invoice.customer_id)
    db.session.commit()
    flash("Payment recorded.", "success")
    return redirect(url_for("owner.invoice_detail", invoice_id=invoice.id))


def _create_sale_view(role_prefix):
    """Shared sale-creation logic used by both owner.new_sale and employee.new_sale
    so behavior (stock deduction, invoice numbering, GST calc) is identical for both roles."""
    sid = current_user.shop_id
    parts = Part.query.filter_by(shop_id=sid, is_active=True).order_by(Part.name).all()
    labour_options = LabourCharge.query.filter_by(shop_id=sid, is_active=True).all()

    if request.method == "POST":
        customer_name = request.form.get("customer_name", "").strip()
        mobile = request.form.get("mobile", "").strip()
        vehicle_number = request.form.get("vehicle_number", "").strip()
        vehicle_type = request.form.get("vehicle_type", "")

        customer = None
        if mobile:
            customer = Customer.query.filter_by(shop_id=sid, mobile=mobile).first()
        if not customer:
            customer = Customer(shop_id=sid, name=customer_name, mobile=mobile,
                                 vehicle_number=vehicle_number, vehicle_type=vehicle_type)
            db.session.add(customer)
            db.session.flush()

        part_ids = request.form.getlist("part_id[]")
        quantities = request.form.getlist("quantity[]")
        discount = Decimal(request.form.get("discount") or 0)
        payment_method = request.form.get("payment_method", PaymentMethod.CASH.value)
        amount_paid_input = request.form.get("amount_paid")

        labour_names = request.form.getlist("labour_name[]")
        labour_prices = request.form.getlist("labour_price[]")

        if not part_ids:
            flash("Add at least one part to the sale.", "danger")
            return redirect(request.url)

        invoice = Invoice(
            shop_id=sid,
            invoice_number=generate_invoice_number(sid),
            customer_id=customer.id,
            vehicle_number=vehicle_number,
            vehicle_type=vehicle_type,
            discount=discount,
            payment_method=payment_method,
            created_by=current_user.id,
        )
        db.session.add(invoice)
        db.session.flush()

        subtotal = Decimal("0")
        gst_total = Decimal("0")
        for pid, qty in zip(part_ids, quantities):
            part = Part.query.filter_by(id=pid, shop_id=sid).first()
            if not part:
                continue
            qty = int(qty)
            if qty <= 0:
                continue
            if qty > part.current_stock:
                flash(f"Not enough stock for {part.name} (have {part.current_stock}).", "danger")
                db.session.rollback()
                return redirect(request.url)

            line_total = qty * part.selling_price
            item = InvoiceItem(
                invoice_id=invoice.id, part_id=part.id, part_name_snapshot=part.name,
                quantity=qty, unit_price=part.selling_price, gst_percent=part.gst_percent,
                line_total=line_total,
            )
            db.session.add(item)
            subtotal += line_total
            gst_total += line_total * part.gst_percent / (100 + part.gst_percent)

            # Stock reduces immediately on sale.
            apply_stock_change(part, -qty, "sale", current_user.id,
                                reference_type="invoice", reference_id=invoice.id)

        labour_total = Decimal("0")
        for name, price in zip(labour_names, labour_prices):
            if name and price:
                labour_total += Decimal(price)

        grand_total = subtotal + labour_total - discount

        invoice.subtotal = subtotal
        invoice.labour_total = labour_total
        invoice.gst_total = gst_total
        invoice.grand_total = grand_total

        if payment_method == PaymentMethod.CREDIT.value:
            invoice.payment_status = PaymentStatus.CREDIT.value
            invoice.amount_paid = Decimal("0")
        else:
            amount_paid = Decimal(amount_paid_input) if amount_paid_input else grand_total
            invoice.amount_paid = amount_paid
            invoice.payment_status = (
                PaymentStatus.PAID.value if amount_paid >= grand_total else PaymentStatus.PARTIAL.value
            )
            if amount_paid > 0:
                db.session.add(Payment(
                    shop_id=sid, party_type="customer", party_id=customer.id,
                    invoice_id=invoice.id, amount=amount_paid, method=payment_method,
                    direction="in", created_by=current_user.id,
                ))

        log_action("invoice_created", entity_type="invoice", entity_id=invoice.id,
                   details=f"{current_user.name} sold to {customer.name} — Rs. {grand_total:,.2f}",
                   customer_id=customer.id)
        notify(sid, "sales", "new_invoice_generated", f"New invoice: {invoice.invoice_number}",
               body=f"Rs. {grand_total:,.2f} to {customer.name} by {current_user.name}.",
               link=url_for(f"{role_prefix}.invoice_detail", invoice_id=invoice.id))
        if payment_method == PaymentMethod.CREDIT.value:
            notify(sid, "sales", "credit_sale", f"Credit sale: {invoice.invoice_number}",
                   body=f"Rs. {grand_total:,.2f} on credit to {customer.name}.",
                   link=url_for(f"{role_prefix}.invoice_detail", invoice_id=invoice.id))
        db.session.commit()
        flash(f"Invoice {invoice.invoice_number} created.", "success")
        return redirect(url_for(f"{role_prefix}.invoice_detail", invoice_id=invoice.id))

    return render_template(f"{role_prefix}/new_sale.html", parts=parts, labour_options=labour_options)


# ---------------------------------------------------------------------------
# Customers / Customer Ledger
# ---------------------------------------------------------------------------

@owner_bp.route("/customers")
def customers():
    sid = shop_id()
    q = request.args.get("q", "").strip()
    query = Customer.query.filter_by(shop_id=sid)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Customer.name.ilike(like), Customer.mobile.ilike(like),
                                  Customer.vehicle_number.ilike(like)))
    all_customers = query.order_by(Customer.name).all()
    return render_template("owner/customers.html", customers=all_customers, q=q)


@owner_bp.route("/customers/<int:customer_id>")
def customer_detail(customer_id):
    sid = shop_id()
    customer = Customer.query.filter_by(id=customer_id, shop_id=sid).first_or_404()
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    query = Invoice.query.filter_by(customer_id=customer.id, shop_id=sid)
    if date_from:
        query = query.filter(func.date(Invoice.created_at) >= date_from)
    if date_to:
        query = query.filter(func.date(Invoice.created_at) <= date_to)
    invoices_list = query.order_by(Invoice.created_at.desc()).all()

    payments = Payment.query.filter_by(shop_id=sid, party_type="customer", party_id=customer.id)\
        .order_by(Payment.created_at.desc()).all()

    whatsapp_url = build_whatsapp_link(
        customer.mobile, customer_statement_whatsapp_message(customer, current_user.shop)
    )

    return render_template("owner/customer_detail.html", customer=customer, invoices=invoices_list,
                            payments=payments, date_from=date_from or "", date_to=date_to or "",
                            whatsapp_url=whatsapp_url)


# ---------------------------------------------------------------------------
# Labour Charges Master
# ---------------------------------------------------------------------------

@owner_bp.route("/labour-charges", methods=["GET", "POST"])
def labour_charges():
    sid = shop_id()
    if request.method == "POST":
        lc = LabourCharge(shop_id=sid, name=request.form["name"].strip(),
                           price=Decimal(request.form.get("price") or 0))
        db.session.add(lc)
        db.session.commit()
        flash("Labour charge added.", "success")
        return redirect(url_for("owner.labour_charges"))

    charges = LabourCharge.query.filter_by(shop_id=sid, is_active=True).order_by(LabourCharge.name).all()
    return render_template("owner/labour_charges.html", charges=charges)


@owner_bp.route("/labour-charges/<int:charge_id>/delete", methods=["POST"])
def delete_labour_charge(charge_id):
    sid = shop_id()
    lc = LabourCharge.query.filter_by(id=charge_id, shop_id=sid).first_or_404()
    lc.is_active = False
    db.session.commit()
    flash("Labour charge removed.", "info")
    return redirect(url_for("owner.labour_charges"))


# ---------------------------------------------------------------------------
# Employees & Payments
# ---------------------------------------------------------------------------

@owner_bp.route("/employees")
def employees():
    sid = shop_id()
    staff = User.query.filter_by(shop_id=sid, role=Role.EMPLOYEE.value).all()
    return render_template("owner/employees.html", staff=staff)


@owner_bp.route("/employees/new", methods=["GET", "POST"])
def new_employee():
    sid = shop_id()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("A user with this email already exists.", "danger")
            return render_template("owner/employee_form.html")

        emp = User(shop_id=sid, name=request.form["name"].strip(), email=email,
                   phone=request.form.get("phone", "").strip(), role=Role.EMPLOYEE.value)
        emp.set_password(request.form.get("password"))
        db.session.add(emp)
        db.session.flush()

        profile = EmployeeProfile(
            user_id=emp.id,
            monthly_salary=Decimal(request.form.get("monthly_salary") or 0),
        )
        db.session.add(profile)
        log_action("employee_created", entity_type="user", entity_id=emp.id, details=emp.name)
        db.session.commit()
        flash(f"Employee '{emp.name}' added. Login: {emp.email}", "success")
        return redirect(url_for("owner.employees"))

    return render_template("owner/employee_form.html")


@owner_bp.route("/employees/<int:user_id>/toggle-active", methods=["POST"])
def toggle_employee_active(user_id):
    sid = shop_id()
    emp = User.query.filter_by(id=user_id, shop_id=sid, role=Role.EMPLOYEE.value).first_or_404()
    emp.is_active_flag = not emp.is_active_flag
    log_action("employee_status_toggled", entity_type="user", entity_id=emp.id,
               details=str(emp.is_active_flag))
    db.session.commit()
    flash(f"{emp.name} is now {'active' if emp.is_active_flag else 'disabled'}.", "info")
    return redirect(url_for("owner.employees"))


@owner_bp.route("/employees/<int:user_id>/activity")
def employee_activity(user_id):
    sid = shop_id()
    emp = User.query.filter_by(id=user_id, shop_id=sid, role=Role.EMPLOYEE.value).first_or_404()
    invoices_made = Invoice.query.filter_by(shop_id=sid, created_by=emp.id)\
        .order_by(Invoice.created_at.desc()).limit(100).all()
    expenses = Expense.query.filter_by(shop_id=sid, employee_id=emp.id)\
        .order_by(Expense.expense_date.desc()).all()
    return render_template("owner/employee_activity.html", employee=emp, invoices=invoices_made,
                            expenses=expenses)


@owner_bp.route("/expenses", methods=["GET", "POST"])
def expenses():
    sid = shop_id()
    staff = User.query.filter_by(shop_id=sid, role=Role.EMPLOYEE.value).all()

    if request.method == "POST":
        exp = Expense(
            shop_id=sid,
            employee_id=request.form.get("employee_id") or None,
            expense_type=request.form.get("expense_type"),
            amount=Decimal(request.form.get("amount") or 0),
            expense_date=datetime.strptime(request.form.get("expense_date"), "%Y-%m-%d").date()
            if request.form.get("expense_date") else date.today(),
            note=request.form.get("note", "").strip(),
            created_by=current_user.id,
        )
        db.session.add(exp)
        log_action("expense_recorded", entity_type="expense", details=f"{exp.expense_type} {exp.amount}")
        db.session.commit()
        flash("Expense recorded.", "success")
        return redirect(url_for("owner.expenses"))

    month_start = date.today().replace(day=1)
    this_month_expenses = Expense.query.filter(
        Expense.shop_id == sid, Expense.expense_date >= month_start
    ).order_by(Expense.expense_date.desc()).all()
    return render_template("owner/expenses.html", staff=staff, expenses=this_month_expenses)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@owner_bp.route("/reports")
def reports():
    return render_template("owner/reports.html")


@owner_bp.route("/reports/daily")
def daily_report():
    sid = shop_id()
    report_date = request.args.get("date", date.today().isoformat())
    d = datetime.strptime(report_date, "%Y-%m-%d").date()

    invoices_today = Invoice.query.filter(
        Invoice.shop_id == sid, func.date(Invoice.created_at) == d, Invoice.is_void.is_(False)
    ).all()
    purchases_today = Purchase.query.filter(
        Purchase.shop_id == sid, func.date(Purchase.created_at) == d
    ).all()
    payments_today = Payment.query.filter(
        Payment.shop_id == sid, func.date(Payment.created_at) == d
    ).all()

    total_sales = sum(float(i.grand_total) for i in invoices_today)
    total_gst = sum(float(i.gst_total) for i in invoices_today)
    total_purchases = sum(float(p.total_amount) for p in purchases_today)

    stock_movements = StockLedger.query.filter(
        StockLedger.shop_id == sid, func.date(StockLedger.created_at) == d
    ).order_by(StockLedger.created_at.desc()).all()

    return render_template(
        "owner/daily_report.html", report_date=d, invoices=invoices_today,
        purchases=purchases_today, payments=payments_today, total_sales=total_sales,
        total_gst=total_gst, total_purchases=total_purchases,
        profit=total_sales - total_purchases, stock_movements=stock_movements,
    )


@owner_bp.route("/reports/monthly")
def monthly_report():
    sid = shop_id()
    month_str = request.args.get("month", date.today().strftime("%Y-%m"))
    year, month = map(int, month_str.split("-"))

    invoices_month = Invoice.query.filter(
        Invoice.shop_id == sid,
        func.strftime("%Y-%m", Invoice.created_at) == month_str,
        Invoice.is_void.is_(False),
    ).all() if db.engine.url.get_backend_name() == "sqlite" else Invoice.query.filter(
        Invoice.shop_id == sid,
        func.extract("year", Invoice.created_at) == year,
        func.extract("month", Invoice.created_at) == month,
        Invoice.is_void.is_(False),
    ).all()

    total_sales = sum(float(i.grand_total) for i in invoices_month)
    total_gst = sum(float(i.gst_total) for i in invoices_month)

    # HSN-wise summary for GST filing
    hsn_summary = {}
    for inv in invoices_month:
        for item in inv.items:
            hsn = (item.part.hsn_code if item.part else None) or "N/A"
            bucket = hsn_summary.setdefault(hsn, {"qty": 0, "taxable": 0.0, "gst": 0.0})
            bucket["qty"] += item.quantity
            taxable = float(item.line_total) - (
                float(item.line_total) * float(item.gst_percent) / (100 + float(item.gst_percent))
            )
            bucket["taxable"] += taxable
            bucket["gst"] += float(item.line_total) - taxable

    parts = Part.query.filter_by(shop_id=sid, is_active=True).all()
    stock_valuation = sum(float(p.current_stock) * float(p.purchase_price) for p in parts)

    return render_template(
        "owner/monthly_report.html", month_str=month_str, total_sales=total_sales,
        total_gst=total_gst, hsn_summary=hsn_summary, stock_valuation=stock_valuation,
        invoice_count=len(invoices_month),
    )


@owner_bp.route("/reports/stock-movement")
def stock_movement_report():
    """Fast-moving vs slow-moving vs dead-stock parts, based on sales in the last 90 days."""
    sid = shop_id()
    since = datetime.utcnow() - timedelta(days=90)
    parts = Part.query.filter_by(shop_id=sid, is_active=True).all()

    sold_qty_by_part = dict(
        db.session.query(StockLedger.part_id, func.sum(func.abs(StockLedger.change_qty)))
        .filter(StockLedger.shop_id == sid, StockLedger.reason == "sale",
                StockLedger.created_at >= since)
        .group_by(StockLedger.part_id).all()
    )

    fast_moving, slow_moving, dead_stock = [], [], []
    for p in parts:
        sold = sold_qty_by_part.get(p.id, 0)
        if sold >= 10:
            fast_moving.append((p, sold))
        elif sold > 0:
            slow_moving.append((p, sold))
        else:
            dead_stock.append(p)

    fast_moving.sort(key=lambda x: -x[1])
    slow_moving.sort(key=lambda x: -x[1])

    return render_template("owner/stock_movement_report.html", fast_moving=fast_moving,
                            slow_moving=slow_moving, dead_stock=dead_stock)


@owner_bp.route("/reports/purchase-suggestions")
def purchase_suggestions():
    """Smart suggestion: parts at/below minimum stock, ranked by recent sale velocity."""
    sid = shop_id()
    since = datetime.utcnow() - timedelta(days=30)
    parts = Part.query.filter_by(shop_id=sid, is_active=True).all()

    sold_qty_30d = dict(
        db.session.query(StockLedger.part_id, func.sum(func.abs(StockLedger.change_qty)))
        .filter(StockLedger.shop_id == sid, StockLedger.reason == "sale",
                StockLedger.created_at >= since)
        .group_by(StockLedger.part_id).all()
    )

    suggestions = []
    for p in parts:
        if p.stock_level() in ("critical", "low", "near_min"):
            velocity = sold_qty_30d.get(p.id, 0)
            suggested_qty = max(p.minimum_stock * 2 - p.current_stock, velocity)
            suggestions.append((p, velocity, suggested_qty))

    suggestions.sort(key=lambda x: -x[1])
    return render_template("owner/purchase_suggestions.html", suggestions=suggestions)


# ---------------------------------------------------------------------------
# Godowns (warehouses / branches) & Stock Transfer
# ---------------------------------------------------------------------------

@owner_bp.route("/godowns")
def godowns():
    sid = shop_id()
    # Ensure at least the default godown exists before listing.
    get_default_godown(sid)
    db.session.commit()
    all_godowns = Godown.query.filter_by(shop_id=sid, is_active=True).order_by(
        Godown.is_default.desc(), Godown.name
    ).all()

    # Total units currently sitting in each godown, for a quick overview.
    totals = {}
    for g in all_godowns:
        totals[g.id] = db.session.query(func.coalesce(func.sum(PartStock.quantity), 0)).filter(
            PartStock.godown_id == g.id
        ).scalar()

    return render_template("owner/godowns.html", godowns=all_godowns, totals=totals)


@owner_bp.route("/godowns/new", methods=["GET", "POST"])
def new_godown():
    sid = shop_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Godown name is required.", "danger")
            return render_template("owner/godown_form.html")

        godown = Godown(shop_id=sid, name=name, address=request.form.get("address", "").strip())
        db.session.add(godown)
        log_action("godown_created", entity_type="godown", details=name)
        db.session.commit()
        flash(f"Godown '{name}' added.", "success")
        return redirect(url_for("owner.godowns"))

    return render_template("owner/godown_form.html")


@owner_bp.route("/godowns/<int:godown_id>")
def godown_detail(godown_id):
    sid = shop_id()
    godown = Godown.query.filter_by(id=godown_id, shop_id=sid).first_or_404()
    stock_rows = (
        PartStock.query.filter_by(godown_id=godown.id)
        .filter(PartStock.quantity > 0)
        .join(Part).order_by(Part.name).all()
    )
    return render_template("owner/godown_detail.html", godown=godown, stock_rows=stock_rows)


@owner_bp.route("/stock-transfer", methods=["GET", "POST"])
def stock_transfer():
    sid = shop_id()
    get_default_godown(sid)
    db.session.commit()

    all_godowns = Godown.query.filter_by(shop_id=sid, is_active=True).order_by(Godown.name).all()
    parts = Part.query.filter_by(shop_id=sid, is_active=True).order_by(Part.name).all()

    if len(all_godowns) < 2:
        flash("Add a second godown before transferring stock between locations.", "info")

    if request.method == "POST":
        part = Part.query.filter_by(id=request.form.get("part_id"), shop_id=sid).first()
        from_godown_id = int(request.form.get("from_godown_id"))
        to_godown_id = int(request.form.get("to_godown_id"))
        quantity = int(request.form.get("quantity") or 0)
        note = request.form.get("note", "").strip()

        if not part:
            flash("Select a valid part.", "danger")
            return redirect(url_for("owner.stock_transfer"))

        try:
            transfer_stock(part, from_godown_id, to_godown_id, quantity, current_user.id, note=note)
            log_action("stock_transferred", entity_type="part", entity_id=part.id,
                       details=f"{quantity} units: godown {from_godown_id} -> {to_godown_id}")
            db.session.commit()
            flash(f"Transferred {quantity} x {part.name}.", "success")
        except ValueError as e:
            db.session.rollback()
            flash(str(e), "danger")

        return redirect(url_for("owner.stock_transfer"))

    recent_transfers = (
        StockTransfer.query.filter_by(shop_id=sid).order_by(StockTransfer.created_at.desc()).limit(30).all()
    )
    return render_template("owner/stock_transfer.html", godowns=all_godowns, parts=parts,
                            recent_transfers=recent_transfers)


@owner_bp.route("/api/part-stock/<int:part_id>/<int:godown_id>")
def api_part_stock_at_godown(part_id, godown_id):
    """Small JSON helper used by the stock-transfer form to show available
    quantity at the selected source godown before submitting."""
    sid = shop_id()
    part = Part.query.filter_by(id=part_id, shop_id=sid).first_or_404()
    row = PartStock.query.filter_by(part_id=part.id, godown_id=godown_id).first()
    return {"available": row.quantity if row else 0}


# ---------------------------------------------------------------------------
# Business Activity Timeline
# ---------------------------------------------------------------------------

ACTION_LABELS = {
    "invoice_created": "Sale",
    "invoice_voided": "Invoice Voided",
    "payment_recorded": "Payment Received",
    "purchase_received": "Purchase Received",
    "purchase_request_created": "Purchase Requested",
    "purchase_request_decided": "Purchase Request Decision",
    "supplier_created": "Supplier Added",
    "supplier_payment": "Supplier Payment",
    "part_created": "Part Added",
    "part_updated": "Part Updated",
    "stock_adjusted": "Stock Adjusted",
    "stock_transferred": "Stock Transferred",
    "godown_created": "Godown Added",
    "employee_created": "Employee Added",
    "expense_recorded": "Expense Recorded",
    "support_ticket_raised": "Support Ticket Raised",
    "login": "Logged In",
    "logout": "Logged Out",
}


def _timeline_query(sid, date_from=None, date_to=None, employee_id=None, customer_id=None,
                     supplier_id=None, q=None):
    query = AuditLog.query.filter_by(shop_id=sid)
    if date_from:
        query = query.filter(func.date(AuditLog.created_at) >= date_from)
    if date_to:
        query = query.filter(func.date(AuditLog.created_at) <= date_to)
    if employee_id:
        query = query.filter(AuditLog.user_id == employee_id)
    if customer_id:
        query = query.filter(AuditLog.customer_id == customer_id)
    if supplier_id:
        query = query.filter(AuditLog.supplier_id == supplier_id)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(AuditLog.action.ilike(like), AuditLog.details.ilike(like)))
    return query.order_by(AuditLog.created_at.desc())


@owner_bp.route("/timeline")
def timeline():
    sid = shop_id()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    employee_id = request.args.get("employee_id", "")
    customer_id = request.args.get("customer_id", "")
    supplier_id = request.args.get("supplier_id", "")
    q = request.args.get("q", "").strip()

    entries = _timeline_query(sid, date_from, date_to, employee_id, customer_id, supplier_id, q).limit(300).all()

    staff = User.query.filter_by(shop_id=sid).all()
    customers_list = Customer.query.filter_by(shop_id=sid).order_by(Customer.name).all()
    suppliers_list = Supplier.query.filter_by(shop_id=sid).order_by(Supplier.name).all()

    return render_template(
        "owner/timeline.html", entries=entries, action_labels=ACTION_LABELS,
        staff=staff, customers=customers_list, suppliers=suppliers_list,
        date_from=date_from, date_to=date_to, employee_id=employee_id,
        customer_id=customer_id, supplier_id=supplier_id, q=q,
    )


@owner_bp.route("/timeline/export")
def timeline_export():
    import csv
    import io as _io
    from flask import Response

    sid = shop_id()
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")
    employee_id = request.args.get("employee_id", "")
    customer_id = request.args.get("customer_id", "")
    supplier_id = request.args.get("supplier_id", "")
    q = request.args.get("q", "").strip()

    entries = _timeline_query(sid, date_from, date_to, employee_id, customer_id, supplier_id, q).all()

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Date", "Time", "Activity", "Details", "By User"])
    for e in entries:
        writer.writerow([
            e.created_at.strftime("%Y-%m-%d"), e.created_at.strftime("%H:%M:%S"),
            ACTION_LABELS.get(e.action, e.action.replace("_", " ").title()),
            e.details or "", e.user.name if e.user else "-",
        ])

    return Response(buf.getvalue(), mimetype="text/csv",
                     headers={"Content-Disposition": "attachment; filename=activity_timeline.csv"})
