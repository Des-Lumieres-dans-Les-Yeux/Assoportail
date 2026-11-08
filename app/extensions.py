"""Flask extension instances.

All extensions are instantiated here without an app object and initialized
in the application factory (app/__init__.py). This pattern prevents circular
imports between blueprints and the application factory.
"""

import ipaddress

from flask_limiter import Limiter
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


# Plages de documentation (RFC 5737 IPv4, RFC 3849 IPv6) : Python les classe
# ``is_private``, mais ce sont des adresses d'exemple non routables — elles ne
# doivent jamais être considérées comme internes/confiantes.
_DOCUMENTATION_NETS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)


def _is_private_ip(ip: str | None) -> bool:
    """Return True for loopback, link-local and RFC1918 private addresses."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if any(addr in net for net in _DOCUMENTATION_NETS):
        return False
    return addr.is_loopback or addr.is_link_local or addr.is_private or addr.is_reserved


def effective_client_ip() -> str | None:
    """Client IP to trust for security decisions (rate limits, IP gates).

    ``request.remote_addr`` is rewritten by ProxyFix from X-Forwarded-For,
    which a direct (unproxied) caller can forge. Trust the rewritten value
    only when the original socket peer is a private address (our reverse
    proxy); otherwise the caller connects directly and the original peer —
    captured in ``environ["_ORIGINAL_PEER"]`` before ProxyFix — is the only
    honest value.
    """
    from flask import request

    peer = request.environ.get("_ORIGINAL_PEER")
    if peer and not _is_private_ip(peer):
        return peer
    return request.remote_addr


limiter = Limiter(key_func=effective_client_ip, default_limits_exempt_when=_skip_limiter_if_bureau)
csrf = CSRFProtect()

# Flask-Login settings
login_manager.login_view = "auth.login"  # type: ignore[assignment]
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
login_manager.login_message_category = "warning"
