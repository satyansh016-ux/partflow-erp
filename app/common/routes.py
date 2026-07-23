import os
import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.decorators import roles_required
from app.models import Role, HelpArticle, Notification, SupportTicket, SupportTicketReply
from app.utils.helpers import log_action

common_bp = Blueprint("common", __name__)


@common_bp.before_request
@login_required
@roles_required(Role.OWNER.value, Role.EMPLOYEE.value)
def guard():
    pass


# ---------------------------------------------------------------------------
# Help Center
# ---------------------------------------------------------------------------

@common_bp.route("/help")
def help_center():
    q = request.args.get("q", "").strip()
    guide_category = "owner_guide" if current_user.is_owner() else "employee_guide"

    query = HelpArticle.query.filter_by(is_published=True)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(HelpArticle.title.ilike(like), HelpArticle.body_html.ilike(like)))
        articles = query.order_by(HelpArticle.category, HelpArticle.order_index).all()
        grouped = {}
        for a in articles:
            grouped.setdefault(a.category, []).append(a)
        return render_template("common/help_center.html", grouped=grouped, q=q, search_mode=True)

    articles = query.filter(
        HelpArticle.category.in_(["beginner", guide_category, "faq", "whats_new"])
    ).order_by(HelpArticle.category, HelpArticle.order_index).all()

    grouped = {"beginner": [], guide_category: [], "faq": [], "whats_new": []}
    for a in articles:
        grouped.setdefault(a.category, []).append(a)

    return render_template("common/help_center.html", grouped=grouped, q=q, search_mode=False)


@common_bp.route("/help/<slug>")
def help_article(slug):
    article = HelpArticle.query.filter_by(slug=slug, is_published=True).first_or_404()
    return render_template("common/help_article.html", article=article)


# ---------------------------------------------------------------------------
# Notification Center
# ---------------------------------------------------------------------------

@common_bp.route("/notifications")
def notifications():
    sid = current_user.shop_id
    category = request.args.get("category", "")
    q = request.args.get("q", "").strip()
    date_str = request.args.get("date", "")
    only_unread = request.args.get("unread") == "1"

    query = Notification.query.filter(
        Notification.shop_id == sid,
        db.or_(Notification.user_id.is_(None), Notification.user_id == current_user.id),
    )
    if category:
        query = query.filter_by(category=category)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Notification.title.ilike(like), Notification.body.ilike(like)))
    if date_str:
        query = query.filter(db.func.date(Notification.created_at) == date_str)
    if only_unread:
        query = query.filter_by(is_read=False)

    items = query.order_by(Notification.created_at.desc()).limit(200).all()
    unread_count = Notification.query.filter(
        Notification.shop_id == sid,
        db.or_(Notification.user_id.is_(None), Notification.user_id == current_user.id),
        Notification.is_read.is_(False),
    ).count()

    categories = ["inventory", "sales", "customer", "supplier", "business", "subscription"]
    return render_template("common/notifications.html", items=items, categories=categories,
                            category=category, q=q, date_str=date_str, only_unread=only_unread,
                            unread_count=unread_count)


@common_bp.route("/notifications/<int:notif_id>/read", methods=["POST"])
def mark_read(notif_id):
    sid = current_user.shop_id
    n = Notification.query.filter_by(id=notif_id, shop_id=sid).first_or_404()
    n.is_read = True
    db.session.commit()
    return redirect(request.referrer or url_for("common.notifications"))


@common_bp.route("/notifications/mark-all-read", methods=["POST"])
def mark_all_read():
    sid = current_user.shop_id
    Notification.query.filter(
        Notification.shop_id == sid,
        db.or_(Notification.user_id.is_(None), Notification.user_id == current_user.id),
        Notification.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("common.notifications"))


# ---------------------------------------------------------------------------
# Support Center
# ---------------------------------------------------------------------------

SUPPORT_CATEGORIES = [
    "billing", "inventory", "sales", "purchase", "reports",
    "login_issue", "technical_issue", "bug", "feature_request", "other",
]

ALLOWED_SCREENSHOT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


@common_bp.route("/support")
def support_tickets():
    sid = current_user.shop_id
    tickets = SupportTicket.query.filter_by(shop_id=sid).order_by(SupportTicket.created_at.desc()).all()

    whatsapp_number = current_app.config.get("SUPPORT_WHATSAPP_NUMBER", "")
    support_email = current_app.config.get("SUPPORT_EMAIL", "")
    whatsapp_url = f"https://wa.me/{whatsapp_number}" if whatsapp_number else None

    return render_template("common/support_tickets.html", tickets=tickets,
                            categories=SUPPORT_CATEGORIES, whatsapp_url=whatsapp_url,
                            support_email=support_email)


@common_bp.route("/support/new", methods=["POST"])
def new_support_ticket():
    sid = current_user.shop_id
    category = request.form.get("category", "other")
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()

    if not subject or not description:
        flash("Subject and description are required.", "danger")
        return redirect(url_for("common.support_tickets"))

    screenshot_path = None
    file = request.files.get("screenshot")
    if file and file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if ext in ALLOWED_SCREENSHOT_EXTENSIONS:
            upload_dir = os.path.join(current_app.config["UPLOAD_DIR"], "support")
            os.makedirs(upload_dir, exist_ok=True)
            filename = secure_filename(f"{uuid.uuid4().hex}.{ext}")
            file.save(os.path.join(upload_dir, filename))
            screenshot_path = f"support/{filename}"
        else:
            flash("Screenshot must be an image file (png/jpg/gif/webp).", "warning")

    ticket = SupportTicket(
        shop_id=sid, raised_by=current_user.id, category=category,
        subject=subject, description=description, screenshot_path=screenshot_path,
    )
    db.session.add(ticket)
    log_action("support_ticket_raised", entity_type="support_ticket", details=subject)
    db.session.commit()
    flash(f"Ticket #{ticket.id} raised. Support history is saved permanently for your shop.", "success")
    return redirect(url_for("common.ticket_detail", ticket_id=ticket.id))


@common_bp.route("/support/<int:ticket_id>")
def ticket_detail(ticket_id):
    sid = current_user.shop_id
    ticket = SupportTicket.query.filter_by(id=ticket_id, shop_id=sid).first_or_404()
    return render_template("common/support_ticket_detail.html", ticket=ticket)


@common_bp.route("/support/<int:ticket_id>/reply", methods=["POST"])
def reply_ticket(ticket_id):
    sid = current_user.shop_id
    ticket = SupportTicket.query.filter_by(id=ticket_id, shop_id=sid).first_or_404()
    message = request.form.get("message", "").strip()
    if message:
        db.session.add(SupportTicketReply(ticket_id=ticket.id, user_id=current_user.id, message=message))
        ticket.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Reply added.", "success")
    return redirect(url_for("common.ticket_detail", ticket_id=ticket.id))


@common_bp.route("/support/screenshot/<path:filename>")
def support_screenshot(filename):
    from flask import send_from_directory, abort
    ticket = SupportTicket.query.filter_by(screenshot_path=filename).first()
    if not ticket:
        abort(404)
    if not current_user.is_super_admin() and ticket.shop_id != current_user.shop_id:
        abort(403)
    upload_dir = os.path.join(current_app.config["UPLOAD_DIR"], "support")
    basename = filename.split("/")[-1]
    return send_from_directory(upload_dir, basename)
