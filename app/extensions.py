"""Flask extension instances.

All extensions are instantiated here without an app object and initialized
in the application factory (app/__init__.py). This pattern prevents circular
imports between blueprints and the application factory.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
from spectree import SecurityScheme, SecuritySchemeData, SpecTree

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
talisman = Talisman()

# OpenAPI / Swagger UI — servi sur /api/docs/swagger/ (CDN jsdelivr.net déjà autorisé par la CSP)
# mode="strict" : seules les routes décorées @spec.validate sont incluses dans la spec,
# ce qui empêche la fuite des 252 routes non-API vers /api/docs/openapi.json.
spec = SpecTree(
    "flask",
    title="DLDLY Portal API",
    version="v1",
    path="api/docs",
    mode="strict",
    annotations=False,
    security_schemes=[
        SecurityScheme(
            name="BearerAuth",
            data=SecuritySchemeData(
                type="http",
                scheme="bearer",
                bearerFormat="opaque",
                description="Token Bearer généré via `flask api-token`.",
            ),
        )
    ],
    security={"BearerAuth": []},
)


def _skip_limiter_if_bureau():
    """Skip rate limiting for bureau members."""
    from flask_login import current_user

    return (
        current_user.is_authenticated
        and hasattr(current_user, "is_bureau")
        and current_user.is_bureau
    )


limiter = Limiter(key_func=get_remote_address, default_limits_exempt_when=_skip_limiter_if_bureau)
csrf = CSRFProtect()

# Flask-Login settings
login_manager.login_view = "auth.login"  # type: ignore[assignment]
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"
