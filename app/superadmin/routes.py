from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.decorators import roles_required
from app.models import (
    Shop, Subscription, User, Role, ShopStatus, Invoice, LoginActivity, AuditLog,
    BackupLog, RestoreLog, HelpArticle, SupportTicket, SupportTicketReply
)
from app.utils.helpers import log_action
from app.utils.notifications import notify
from app.utils.backup import (
    backup_shop, backup_all_shops, restore_shop_from_backup, verify_backup_integrity
)

superadmin_bp = Blueprint("superadmin", __name__)


@superadmin_bp.before_request
@login_required
@roles_required(Role.SUPER_ADMIN.value)
def guard():
    pass


@superadmin_bp.route("/dashboard")
def dashboard():
    shops = Shop.query.all()
    total_shops = len(shops)
    active_shops = sum(1 for s in shops if s.status == ShopStatus.ACTIVE.value)
    trial_shops = sum(1 for s in shops if s.status == ShopStatus.TRIAL.value)
    suspended_shops = sum(1 for s in shops if s.status in ("suspended", "banned"))

    # Simple platform revenue = sum of subscription amounts marked active
    revenue = db.session.query(db.func.coalesce(db.func.sum(Subscription.amount), 0)).filter(
        Subscription.status == "active"
    ).scalar()

    misuse_alerts = (
        AuditLog.query.filter_by(action="license_misuse_suspected")
        .order_by(AuditLog.created_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "superadmin/dashboard.html",
        total_shops=total_shops, active_shops=active_shops,
        trial_shops=trial_shops, suspended_shops=suspended_shops,
        revenue=revenue, misuse_alerts=misuse_alerts,
    )


@superadmin_bp.route("/shops")
def shops():
    all_shops = Shop.query.order_by(Shop.created_at.desc()).all()
    return render_template("superadmin/shops.html", shops=all_shops)


@superadmin_bp.route("/shops/new", methods=["GET", "POST"])
def new_shop():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        owner_email = request.form.get("owner_email", "").strip().lower()
        owner_password = request.form.get("owner_password", "")
        gst_number = request.form.get("gst_number", "").strip()
        phone = request.form.get("phone", "").strip()
        trial_days = int(request.form.get("trial_days", 14))

        if not all([name, owner_name, owner_email, owner_password]):
            flash("Shop name, owner name, email and password are required.", "danger")
            return render_template("superadmin/shop_form.html")

        if User.query.filter_by(email=owner_email).first():
            flash("A user with this email already exists.", "danger")
            return render_template("superadmin/shop_form.html")

        shop = Shop(name=name, gst_number=gst_number, phone=phone, status=ShopStatus.TRIAL.value)
        db.session.add(shop)
        db.session.flush()  # get shop.id

        owner = User(
            shop_id=shop.id, name=owner_name, email=owner_email,
            role=Role.OWNER.value,
        )
        owner.set_password(owner_password)
        db.session.add(owner)

        sub = Subscription(
            shop_id=shop.id, plan_name="Trial",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=trial_days),
            amount=0, status="trial",
        )
        db.session.add(sub)

        log_action("shop_created", entity_type="shop", entity_id=shop.id, details=name)
        db.session.commit()

        flash(f"Shop '{name}' created with a {trial_days}-day trial. Owner login: {owner_email}", "success")
        return redirect(url_for("superadmin.shops"))

    return render_template("superadmin/shop_form.html")


@superadmin_bp.route("/shops/<int:shop_id>")
def shop_detail(shop_id):
    shop = Shop.query.get_or_404(shop_id)
    users = User.query.filter_by(shop_id=shop.id).all()
    subs = Subscription.query.filter_by(shop_id=shop.id).order_by(Subscription.start_date.desc()).all()
    invoice_count = Invoice.query.filter_by(shop_id=shop.id).count()
    recent_logins = (
        LoginActivity.query.filter_by(shop_id=shop.id)
        .order_by(LoginActivity.created_at.desc())
        .limit(30)
        .all()
    )
    distinct_devices = {a.device_fingerprint for a in recent_logins}
    return render_template(
        "superadmin/shop_detail.html", shop=shop, users=users, subs=subs,
        invoice_count=invoice_count, recent_logins=recent_logins,
        distinct_device_count=len(distinct_devices),
    )


@superadmin_bp.route("/shops/<int:shop_id>/status", methods=["POST"])
def change_shop_status(shop_id):
    shop = Shop.query.get_or_404(shop_id)
    new_status = request.form.get("status")
    if new_status not in [s.value for s in ShopStatus]:
        flash("Invalid status.", "danger")
        return redirect(url_for("superadmin.shop_detail", shop_id=shop_id))

    old_status = shop.status
    shop.status = new_status
    log_action("shop_status_changed", entity_type="shop", entity_id=shop.id,
               details=f"{old_status} -> {new_status}")
    db.session.commit()
    flash(f"Shop status updated to {new_status}.", "success")
    return redirect(url_for("superadmin.shop_detail", shop_id=shop_id))


@superadmin_bp.route("/shops/<int:shop_id>/subscription", methods=["POST"])
def update_subscription(shop_id):
    shop = Shop.query.get_or_404(shop_id)
    plan_name = request.form.get("plan_name", "Standard")
    amount = request.form.get("amount", 0)
    duration_days = int(request.form.get("duration_days", 30))

    sub = Subscription(
        shop_id=shop.id, plan_name=plan_name, amount=amount,
        start_date=date.today(), end_date=date.today() + timedelta(days=duration_days),
        status="active",
    )
    db.session.add(sub)
    if shop.status == ShopStatus.TRIAL.value:
        shop.status = ShopStatus.ACTIVE.value

    log_action("subscription_added", entity_type="shop", entity_id=shop.id,
               details=f"{plan_name} / {duration_days} days / {amount}")
    notify(shop.id, "subscription", "payment_success", "Subscription activated",
           body=f"{plan_name} plan active for {duration_days} days.")
    db.session.commit()
    flash("Subscription activated.", "success")
    return redirect(url_for("superadmin.shop_detail", shop_id=shop_id))


@superadmin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
def reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        flash("Password must be at least 8 characters.", "danger")
        return redirect(url_for("superadmin.shop_detail", shop_id=user.shop_id))

    user.set_password(new_password)
    log_action("password_reset_by_admin", entity_type="user", entity_id=user.id)
    db.session.commit()
    flash(f"Password reset for {user.email}.", "success")
    return redirect(url_for("superadmin.shop_detail", shop_id=user.shop_id))


@superadmin_bp.route("/activity-logs")
def activity_logs():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return render_template("superadmin/activity_logs.html", logs=logs)


# ---------------------------------------------------------------------------
# Backup & Recovery (Super Admin only)
# ---------------------------------------------------------------------------

@superadmin_bp.route("/backups")
def backups():
    shops = Shop.query.order_by(Shop.name).all()
    recent_logs = BackupLog.query.order_by(BackupLog.started_at.desc()).limit(100).all()
    failed_count = BackupLog.query.filter_by(status="failed").count()
    return render_template("superadmin/backups.html", shops=shops, logs=recent_logs,
                            failed_count=failed_count)


@superadmin_bp.route("/backups/run/<backup_type>", methods=["POST"])
def run_backup_now(backup_type):
    shop_id = request.form.get("shop_id")
    if backup_type not in ("daily", "weekly", "monthly", "manual"):
        flash("Invalid backup type.", "danger")
        return redirect(url_for("superadmin.backups"))

    if shop_id:
        log = backup_shop(int(shop_id), "manual")
        flash(f"Backup {'completed' if log.status == 'success' else 'FAILED'} for shop #{shop_id}.",
              "success" if log.status == "success" else "danger")
    else:
        logs = backup_all_shops(backup_type)
        failed = sum(1 for l in logs if l.status == "failed")
        flash(f"Backup run complete: {len(logs) - failed} succeeded, {failed} failed.",
              "success" if failed == 0 else "warning")

    log_action("backup_triggered_manually", details=f"type={backup_type} shop_id={shop_id or 'all'}")
    db.session.commit()
    return redirect(url_for("superadmin.backups"))


@superadmin_bp.route("/backups/<int:backup_id>/verify", methods=["POST"])
def verify_backup(backup_id):
    log = BackupLog.query.get_or_404(backup_id)
    ok = verify_backup_integrity(log)
    flash(f"Backup #{log.id}: integrity check {'PASSED' if ok else 'FAILED'}.",
          "success" if ok else "danger")
    return redirect(url_for("superadmin.shop_backups", shop_id=log.shop_id) if log.shop_id
                     else url_for("superadmin.backups"))


@superadmin_bp.route("/shops/<int:shop_id>/backups")
def shop_backups(shop_id):
    shop = Shop.query.get_or_404(shop_id)
    logs = BackupLog.query.filter_by(shop_id=shop_id).order_by(BackupLog.started_at.desc()).all()
    restores = RestoreLog.query.filter_by(shop_id=shop_id).order_by(RestoreLog.created_at.desc()).all()
    return render_template("superadmin/shop_backups.html", shop=shop, logs=logs, restores=restores)


@superadmin_bp.route("/shops/<int:shop_id>/backups/<int:backup_id>/restore", methods=["POST"])
def restore_backup(shop_id, backup_id):
    shop = Shop.query.get_or_404(shop_id)
    backup_log = BackupLog.query.get_or_404(backup_id)
    confirm_text = request.form.get("confirm_text", "")

    if confirm_text != shop.name:
        flash("Confirmation text didn't match the shop name exactly. Restore cancelled for safety.", "danger")
        return redirect(url_for("superadmin.shop_backups", shop_id=shop_id))

    try:
        restore_shop_from_backup(shop_id, backup_log, current_user.id)
        log_action("shop_restored", entity_type="shop", entity_id=shop_id,
                   details=f"from backup #{backup_id}")
        db.session.commit()
        flash(f"Shop '{shop.name}' restored successfully from backup #{backup_id}.", "success")
    except ValueError as e:
        flash(f"Restore refused: {e}", "danger")
    except Exception as e:
        flash(f"Restore failed: {e}", "danger")

    return redirect(url_for("superadmin.shop_backups", shop_id=shop_id))


# ---------------------------------------------------------------------------
# Support Inbox (Super Admin only)
# ---------------------------------------------------------------------------

@superadmin_bp.route("/support")
def support_inbox():
    status_filter = request.args.get("status", "open")
    query = SupportTicket.query
    if status_filter != "all":
        query = query.filter_by(status=status_filter)
    tickets = query.order_by(SupportTicket.created_at.desc()).all()
    return render_template("superadmin/support_inbox.html", tickets=tickets, status_filter=status_filter)


@superadmin_bp.route("/support/<int:ticket_id>")
def support_ticket_detail(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    return render_template("superadmin/support_ticket_detail.html", ticket=ticket)


@superadmin_bp.route("/support/<int:ticket_id>/reply", methods=["POST"])
def support_ticket_reply(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    message = request.form.get("message", "").strip()
    new_status = request.form.get("status")

    if message:
        db.session.add(SupportTicketReply(
            ticket_id=ticket.id, user_id=current_user.id, message=message, is_admin_reply=True
        ))
        from app.utils.notifications import notify as _notify
        _notify(ticket.shop_id, "business", "support_ticket_reply",
                f"Support replied on ticket #{ticket.id}", body=ticket.subject,
                link=f"/support/{ticket.id}")
    if new_status in ("open", "in_progress", "resolved", "closed"):
        ticket.status = new_status
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Ticket updated.", "success")
    return redirect(url_for("superadmin.support_ticket_detail", ticket_id=ticket.id))


@superadmin_bp.route("/support/screenshot/<path:filename>")
def support_screenshot(filename):
    from flask import send_from_directory, abort, current_app as capp
    import os as _os
    ticket = SupportTicket.query.filter_by(screenshot_path=filename).first()
    if not ticket:
        abort(404)
    upload_dir = _os.path.join(capp.config["UPLOAD_DIR"], "support")
    basename = filename.split("/")[-1]
    return send_from_directory(upload_dir, basename)


# ---------------------------------------------------------------------------
# Help Center — publish a "What's New" update (also notifies every shop)
# ---------------------------------------------------------------------------

@superadmin_bp.route("/help/publish-update", methods=["GET", "POST"])
def publish_update():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body_html = request.form.get("body_html", "").strip()
        if not title or not body_html:
            flash("Title and content are required.", "danger")
            return render_template("superadmin/publish_update.html")

        import re as _re
        slug_base = _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        slug = f"{slug_base}-{int(datetime.utcnow().timestamp())}"

        article = HelpArticle(category="whats_new", title=title, slug=slug,
                               body_html=body_html, order_index=0)
        db.session.add(article)

        for shop in Shop.query.all():
            notify(shop.id, "business", "software_update", title, body="See What's New in Help Center.",
                   link="/help")

        log_action("update_published", entity_type="help_article", details=title)
        db.session.commit()
        flash("Update published and all shops notified.", "success")
        return redirect(url_for("superadmin.dashboard"))

    return render_template("superadmin/publish_update.html")
