from functools import wraps
from flask import abort
from flask_login import current_user


def roles_required(*roles):
    """Restrict a route to one or more roles, e.g. @roles_required('owner', 'employee')."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def active_shop_required(view_func):
    """Blocks access for owners/employees whose shop has been suspended/banned,
    or whose subscription has lapsed. Super admin is exempt (no shop attached)."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if current_user.is_authenticated and not current_user.is_super_admin():
            shop = current_user.shop
            if shop is None or shop.status in ("suspended", "banned"):
                abort(403, description="This shop's access has been suspended. Contact support.")
            sub = shop.active_subscription()
            if sub is None or not sub.is_valid():
                abort(403, description="Subscription expired. Please renew to continue.")
        return view_func(*args, **kwargs)
    return wrapped
