import re
from datetime import date
from urllib.parse import quote
from flask import request
from flask_login import current_user
from app.extensions import db
from app.models import Invoice, AuditLog


def generate_invoice_number(shop_id: int) -> str:
    """Format: INV-<shopid>-<YYYYMMDD>-<sequence>. Sequence resets daily per shop."""
    today_str = date.today().strftime("%Y%m%d")
    prefix = f"INV-{shop_id}-{today_str}"
    count_today = Invoice.query.filter(
        Invoice.shop_id == shop_id,
        Invoice.invoice_number.like(f"{prefix}%")
    ).count()
    return f"{prefix}-{count_today + 1:04d}"


def log_action(action: str, entity_type: str = None, entity_id: int = None, details: str = None,
               customer_id: int = None, supplier_id: int = None):
    """Write an audit log entry. Call this for every create/update/delete of
    sensitive records (invoices, stock, purchases, users, shop status changes).
    customer_id/supplier_id are optional links used by the Business Activity
    Timeline's customer/supplier filters."""
    entry = AuditLog(
        shop_id=getattr(current_user, "shop_id", None) if current_user.is_authenticated else None,
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        customer_id=customer_id,
        supplier_id=supplier_id,
        details=details,
    )
    db.session.add(entry)


def client_fingerprint():
    """Very lightweight device fingerprint for license-misuse detection.
    Not cryptographically strong — good enough to flag 'same login, many devices'."""
    ua = request.headers.get("User-Agent", "")
    ip = request.remote_addr or ""
    return f"{ip}|{ua}"[:128]


def build_whatsapp_link(phone: str, message: str) -> str | None:
    """Build a free wa.me click-to-chat link — no WhatsApp Business API, no
    per-message fees, no Meta approval needed. Opens WhatsApp (app or web)
    with the message pre-filled; the person on this side still taps Send
    (and can attach the invoice PDF manually in the same chat).

    Returns None if there's no usable phone number, so templates can hide
    the button instead of showing a dead link.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    # Assume Indian numbers when a bare 10-digit mobile is stored (the common
    # case here); leave already-prefixed / other-country numbers untouched.
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(message)}"


def invoice_whatsapp_message(invoice, shop) -> str:
    lines = [
        f"*{shop.name}*",
        f"Invoice: {invoice.invoice_number}",
        f"Date: {invoice.created_at.strftime('%d %b %Y')}",
        f"Amount: Rs. {float(invoice.grand_total):,.2f}",
    ]
    balance = invoice.balance_due()
    if balance > 0:
        lines.append(f"Balance Due: Rs. {balance:,.2f}")
    else:
        lines.append("Status: Paid in full")
    lines.append("")
    lines.append("Thank you for your business! (Invoice PDF attached separately)")
    return "\n".join(lines)


def customer_statement_whatsapp_message(customer, shop) -> str:
    lines = [
        f"*{shop.name}*",
        f"Statement for {customer.name}",
        f"Total Due: Rs. {customer.total_due():,.2f}",
        "",
        "Please clear the pending amount at your earliest convenience. Thank you!",
    ]
    return "\n".join(lines)
