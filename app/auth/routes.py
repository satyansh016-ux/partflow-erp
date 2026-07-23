from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import User, LoginActivity, Role
from app.utils.helpers import log_action, client_fingerprint

auth_bp = Blueprint("auth", __name__)

MAX_DISTINCT_DEVICES_PER_SUBSCRIPTION = 3  # heuristic threshold before flagging misuse


@auth_bp.route("/")
def root():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))
    return redirect(url_for("auth.login"))


def _home_for(user):
    if user.is_super_admin():
        return url_for("superadmin.dashboard")
    if user.is_owner():
        return url_for("owner.dashboard")
    return url_for("employee.dashboard")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_home_for(current_user))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html")

        if not user.is_active:
            flash("This account has been disabled. Contact your administrator.", "danger")
            return render_template("auth/login.html")

        if not user.is_super_admin():
            shop = user.shop
            if shop is None or shop.status in ("suspended", "banned"):
                flash("This shop's access has been suspended. Contact support.", "danger")
                return render_template("auth/login.html")

        login_user(user)
        session.permanent = True
        user.last_login_at = datetime.utcnow()

        fingerprint = client_fingerprint()
        activity = LoginActivity(
            user_id=user.id,
            shop_id=user.shop_id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent", "")[:255],
            device_fingerprint=fingerprint,
        )
        db.session.add(activity)

        # --- License misuse heuristic -------------------------------------------
        # If this shop's owner login has come from many distinct device
        # fingerprints in the last 24h, flag it for super admin review.
        if user.is_owner():
            window_start = datetime.utcnow() - timedelta(hours=24)
            recent = (
                LoginActivity.query.filter(
                    LoginActivity.shop_id == user.shop_id,
                    LoginActivity.created_at >= window_start,
                ).all()
            )
            distinct_devices = {a.device_fingerprint for a in recent}
            if len(distinct_devices) > MAX_DISTINCT_DEVICES_PER_SUBSCRIPTION:
                log_action(
                    "license_misuse_suspected",
                    entity_type="shop",
                    entity_id=user.shop_id,
                    details=f"{len(distinct_devices)} distinct devices in 24h",
                )

        log_action("login", entity_type="user", entity_id=user.id)
        db.session.commit()

        next_url = request.args.get("next")
        return redirect(next_url or _home_for(user))

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    log_action("logout", entity_type="user", entity_id=current_user.id)
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
