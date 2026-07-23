import os
from flask import Flask, render_template
from config import Config
from app.extensions import db, login_manager, migrate

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app(config_class=Config):
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=os.path.join(PROJECT_ROOT, "templates"),
        static_folder=os.path.join(PROJECT_ROOT, "static"),
    )
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    import json as _json
    app.jinja_env.filters["from_json"] = lambda s: _json.loads(s) if s else {}

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.auth.routes import auth_bp
    from app.superadmin.routes import superadmin_bp
    from app.owner.routes import owner_bp
    from app.employee.routes import employee_bp
    from app.common.routes import common_bp
    from app.internal.routes import internal_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(superadmin_bp, url_prefix="/superadmin")
    app.register_blueprint(owner_bp, url_prefix="/owner")
    app.register_blueprint(employee_bp, url_prefix="/employee")
    app.register_blueprint(common_bp)
    app.register_blueprint(internal_bp)

    from app.auth.routes import auth_bp as root_bp  # noqa: reuse for '/' route registered there

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/error.html", code=403,
                                message=getattr(e, "description", None) or "Access denied."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("errors/error.html", code=500, message="Something went wrong."), 500

    @app.context_processor
    def inject_globals():
        from datetime import datetime
        from flask_login import current_user
        unread_notifications = 0
        if current_user.is_authenticated and not current_user.is_super_admin():
            from app.models import Notification
            unread_notifications = Notification.query.filter(
                Notification.shop_id == current_user.shop_id,
                db.or_(Notification.user_id.is_(None), Notification.user_id == current_user.id),
                Notification.is_read.is_(False),
            ).count()
        return {"current_year": datetime.utcnow().year, "app_name": "PartFlow ERP",
                "unread_notifications": unread_notifications}

    from app.scheduler import init_scheduler
    init_scheduler(app)

    return app
