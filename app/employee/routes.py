from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user

from app.extensions import db
from app.decorators import roles_required, active_shop_required
from app.models import Role, Part, Supplier, Invoice, PurchaseRequest, Customer
from app.utils.helpers import log_action, build_whatsapp_link, invoice_whatsapp_message
from app.utils.invoice_pdf import build_invoice_pdf
from app.owner.routes import _create_sale_view  # shared sale-creation logic (identical rules for both roles)

employee_bp = Blueprint("employee", __name__)


@employee_bp.before_request
@login_required
@roles_required(Role.EMPLOYEE.value)
@active_shop_required
def guard():
    pass


def shop_id():
    return current_user.shop_id


@employee_bp.route("/dashboard")
def dashboard():
    sid = shop_id()
    today = date.today()
    my_invoices_today = Invoice.query.filter(
        Invoice.shop_id == sid, Invoice.created_by == current_user.id,
        db.func.date(Invoice.created_at) == today,
    ).all()
    low_stock_count = len([p for p in Part.query.filter_by(shop_id=sid, is_active=True).all()
                            if p.stock_level() in ("low", "critical")])
    return render_template("employee/dashboard.html", my_invoices_today=my_invoices_today,
                            low_stock_count=low_stock_count)


@employee_bp.route("/sales/new", methods=["GET", "POST"])
def new_sale():
    return _create_sale_view("employee")


@employee_bp.route("/invoices")
def invoices():
    """Employees only see invoices they personally created."""
    sid = shop_id()
    my_invoices = Invoice.query.filter_by(shop_id=sid, created_by=current_user.id)\
        .order_by(Invoice.created_at.desc()).limit(100).all()
    return render_template("employee/invoices.html", invoices=my_invoices)


@employee_bp.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    sid = shop_id()
    # Employees may view any invoice from their shop (e.g. to answer a customer query)
    # but cannot edit or void it - those actions simply aren't exposed in this blueprint.
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    whatsapp_url = build_whatsapp_link(
        invoice.customer.mobile, invoice_whatsapp_message(invoice, current_user.shop)
    )
    return render_template("employee/invoice_detail.html", invoice=invoice, whatsapp_url=whatsapp_url)


@employee_bp.route("/invoices/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    sid = shop_id()
    invoice = Invoice.query.filter_by(id=invoice_id, shop_id=sid).first_or_404()
    buffer = build_invoice_pdf(invoice, current_user.shop, invoice.customer, invoice.items)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True,
                      download_name=f"{invoice.invoice_number}.pdf")


@employee_bp.route("/inventory")
def inventory():
    """Read-only stock lookup. No add/edit/delete/adjust actions for employees."""
    sid = shop_id()
    q = request.args.get("q", "").strip()
    query = Part.query.filter_by(shop_id=sid, is_active=True)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Part.name.ilike(like), Part.oem_number.ilike(like), Part.vehicle_model.ilike(like)
        ))
    parts = query.order_by(Part.name).all()
    return render_template("employee/inventory.html", parts=parts, q=q)


@employee_bp.route("/purchase-requests", methods=["GET", "POST"])
def purchase_requests():
    sid = shop_id()
    parts = Part.query.filter_by(shop_id=sid, is_active=True).order_by(Part.name).all()
    suppliers = Supplier.query.filter_by(shop_id=sid).order_by(Supplier.name).all()

    if request.method == "POST":
        pr = PurchaseRequest(
            shop_id=sid, part_id=request.form.get("part_id"),
            supplier_id=request.form.get("supplier_id") or None,
            quantity=int(request.form.get("quantity")),
            reason=request.form.get("reason", "").strip(),
            requested_by=current_user.id,
        )
        db.session.add(pr)
        log_action("purchase_request_created", entity_type="purchase_request", details=pr.reason)
        db.session.commit()
        flash("Purchase request sent to the owner for approval.", "success")
        return redirect(url_for("employee.purchase_requests"))

    my_requests = PurchaseRequest.query.filter_by(shop_id=sid, requested_by=current_user.id)\
        .order_by(PurchaseRequest.created_at.desc()).all()
    return render_template("employee/purchase_requests.html", parts=parts, suppliers=suppliers,
                            requests=my_requests)


@employee_bp.route("/customers")
def customers():
    sid = shop_id()
    q = request.args.get("q", "").strip()
    query = Customer.query.filter_by(shop_id=sid)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Customer.name.ilike(like), Customer.mobile.ilike(like),
                                     Customer.vehicle_number.ilike(like)))
    all_customers = query.order_by(Customer.name).limit(100).all()
    return render_template("employee/customers.html", customers=all_customers, q=q)
