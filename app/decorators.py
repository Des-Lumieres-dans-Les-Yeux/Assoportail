"""Route access-control decorators.

Usage::

    from app.decorators import bureau_required, member_required

    @bp.route("/admin")
    @bureau_required
    def admin_page():
        ...
"""

from collections.abc import Callable
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from app.models.user import UserPermission


def bureau_required(f: Callable) -> Callable:
    """Restrict a view to users with the ``bureau`` role.

    Redirects unauthenticated users to the login page (via ``@login_required``).
    Returns HTTP 403 for authenticated members without bureau privileges.
    """

    @wraps(f)
    @login_required
    def decorated(*args: object, **kwargs: object) -> object:
        if not current_user.is_bureau:
            abort(403)
        return f(*args, **kwargs)

    return decorated


def permission_required(permission: UserPermission) -> Callable:
    """Restrict a view to users with a specific granular permission.

    Bureau members always have access.
    Members must have the required permission in their ``permissions`` list.
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @login_required
        def decorated(*args: object, **kwargs: object) -> object:
            if not current_user.is_active or not current_user.has_permission(permission):
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


def any_permission_required(*permissions: UserPermission) -> Callable:
    """Restrict a view to users holding at least one of the given permissions.

    Bureau members always have access. The view is allowed for any member who
    has at least one of the requested granular permissions.
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        @login_required
        def decorated(*args: object, **kwargs: object) -> object:
            if not current_user.is_active:
                abort(403)
            if not (
                current_user.is_bureau or any(current_user.has_permission(p) for p in permissions)
            ):
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


def member_required(f: Callable) -> Callable:
    """Restrict a view to active, authenticated members.

    Redirects unauthenticated users to the login page.
    Returns HTTP 403 for authenticated but inactive accounts.
    """

    @wraps(f)
    @login_required
    def decorated(*args: object, **kwargs: object) -> object:
        if not current_user.is_active:
            abort(403)
        return f(*args, **kwargs)

    return decorated
