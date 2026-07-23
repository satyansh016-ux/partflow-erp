"""
Single source of truth for stock changes. ALL stock movements — sales, purchase
receiving, manual adjustments, returns, transfers — must go through the
functions here so that:
  1. Every movement is written to StockLedger (audit trail / stock history).
  2. Part.current_stock (the shop-wide total) and PartStock (the per-godown
     breakdown) always stay in sync — one function updates both together.
  3. Smart alerts fire exactly once per dip below a threshold, and auto-resolve
     when stock is replenished, per the "no repeated alerts" rule.
"""
from datetime import datetime
from app.extensions import db
from app.models import Part, StockLedger, StockAlert, Godown, PartStock
from app.utils.notifications import notify


def get_default_godown(shop_id: int) -> Godown:
    """Every shop needs at least one godown. Auto-create 'Main Godown' the
    first time it's needed so single-location shops require zero setup."""
    godown = Godown.query.filter_by(shop_id=shop_id, is_default=True).first()
    if godown:
        return godown
    godown = Godown.query.filter_by(shop_id=shop_id).first()
    if godown:
        return godown
    godown = Godown(shop_id=shop_id, name="Main Godown", is_default=True)
    db.session.add(godown)
    db.session.flush()
    return godown


def _get_or_create_part_stock(part_id: int, godown_id: int) -> PartStock:
    row = PartStock.query.filter_by(part_id=part_id, godown_id=godown_id).first()
    if row is None:
        row = PartStock(part_id=part_id, godown_id=godown_id, quantity=0)
        db.session.add(row)
        db.session.flush()
    return row


def apply_stock_change(part: Part, change_qty: int, reason: str, user_id: int,
                        reference_type: str = None, reference_id: int = None,
                        godown_id: int = None):
    """Adjust a part's stock, write a ledger entry, and evaluate stock alerts.

    change_qty: positive to add stock (purchase/return), negative to remove (sale).
    godown_id: which location the movement happened at. Defaults to the shop's
    default godown, so existing single-location flows (sales, purchase
    receiving) don't need to know godowns exist.
    """
    if godown_id is None:
        godown_id = get_default_godown(part.shop_id).id

    part.current_stock = (part.current_stock or 0) + change_qty
    if part.current_stock < 0:
        part.current_stock = 0  # never allow negative stock

    stock_row = _get_or_create_part_stock(part.id, godown_id)
    stock_row.quantity = max(0, (stock_row.quantity or 0) + change_qty)

    ledger = StockLedger(
        shop_id=part.shop_id,
        part_id=part.id,
        godown_id=godown_id,
        change_qty=change_qty,
        balance_after=part.current_stock,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        created_by=user_id,
    )
    db.session.add(ledger)

    _evaluate_alerts(part)
    return part


def transfer_stock(part: Part, from_godown_id: int, to_godown_id: int, quantity: int,
                    user_id: int, note: str = None):
    """Move `quantity` units of a part from one godown to another. Does NOT
    change Part.current_stock (the shop-wide total is unaffected by an
    internal transfer) — only the per-godown split changes. Raises ValueError
    if the source godown doesn't have enough stock."""
    if from_godown_id == to_godown_id:
        raise ValueError("Source and destination godown must be different.")
    if quantity <= 0:
        raise ValueError("Transfer quantity must be positive.")

    source_row = _get_or_create_part_stock(part.id, from_godown_id)
    if source_row.quantity < quantity:
        raise ValueError(
            f"Only {source_row.quantity} units of '{part.name}' available at the source godown."
        )

    dest_row = _get_or_create_part_stock(part.id, to_godown_id)

    source_row.quantity -= quantity
    dest_row.quantity += quantity

    db.session.add(StockLedger(
        shop_id=part.shop_id, part_id=part.id, godown_id=from_godown_id,
        change_qty=-quantity, balance_after=part.current_stock,
        reason="transfer_out", reference_type="transfer", created_by=user_id,
    ))
    db.session.add(StockLedger(
        shop_id=part.shop_id, part_id=part.id, godown_id=to_godown_id,
        change_qty=quantity, balance_after=part.current_stock,
        reason="transfer_in", reference_type="transfer", created_by=user_id,
    ))

    from app.models import StockTransfer
    record = StockTransfer(
        shop_id=part.shop_id, part_id=part.id,
        from_godown_id=from_godown_id, to_godown_id=to_godown_id,
        quantity=quantity, note=note, transferred_by=user_id,
    )
    db.session.add(record)

    from_g = Godown.query.get(from_godown_id)
    to_g = Godown.query.get(to_godown_id)
    notify(part.shop_id, "inventory", "stock_transfer",
           f"Stock transferred: {part.name}",
           body=f"{quantity} units moved from {from_g.name if from_g else '?'} to "
                f"{to_g.name if to_g else '?'}.",
           link=f"/owner/inventory/{part.id}/history")

    return record


def get_godown_breakdown(part: Part):
    """Returns [(Godown, quantity), ...] for every godown holding this part,
    including zero-stock godowns for the shop so the UI can show a full picture."""
    godowns = Godown.query.filter_by(shop_id=part.shop_id, is_active=True).order_by(Godown.name).all()
    rows = {ps.godown_id: ps.quantity for ps in PartStock.query.filter_by(part_id=part.id).all()}
    return [(g, rows.get(g.id, 0)) for g in godowns]


def _evaluate_alerts(part: Part):
    """Fire a new alert only on first crossing below the minimum-stock threshold.
    If stock is already below and an unresolved alert exists, do nothing (no spam).
    If stock recovers above minimum, auto-resolve any open alert."""
    open_alert = (
        StockAlert.query.filter_by(part_id=part.id, is_resolved=False)
        .order_by(StockAlert.created_at.desc())
        .first()
    )

    is_below_minimum = part.minimum_stock > 0 and part.current_stock <= (part.minimum_stock - 1)

    if is_below_minimum and open_alert is None:
        level = part.stock_level()  # 'critical' / 'low' / 'near_min'
        alert = StockAlert(
            shop_id=part.shop_id,
            part_id=part.id,
            level=level if level != "ok" else "low",
        )
        db.session.add(alert)
        event = "critical_stock" if level == "critical" else "low_stock"
        notify(part.shop_id, "inventory", event,
               f"{'Critical' if level == 'critical' else 'Low'} stock: {part.name}",
               body=f"Only {part.current_stock} left (minimum {part.minimum_stock}).",
               link=f"/owner/inventory/{part.id}/history")
    elif not is_below_minimum and open_alert is not None:
        open_alert.is_resolved = True
        open_alert.resolved_at = datetime.utcnow()
        notify(part.shop_id, "inventory", "stock_updated",
               f"Stock replenished: {part.name}",
               body=f"Now at {part.current_stock} units.",
               link=f"/owner/inventory/{part.id}/history")


def get_active_alerts(shop_id):
    return (
        StockAlert.query.filter_by(shop_id=shop_id, is_resolved=False)
        .order_by(StockAlert.created_at.desc())
        .all()
    )
