"""API token authentication decorators."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable

from flask import g, jsonify, request

from app.extensions import db
from app.models.api_token import ApiToken

logger = logging.getLogger(__name__)


def _token_error(message: str, status: int = 401):
    """Return a JSON error response."""
    body = {"error": "unauthorized" if status == 401 else "forbidden", "message": message}
    return jsonify(body), status


def api_token_required(f: Callable) -> Callable:
    """Decorator : vérifie le token Bearer et stocke l'utilisateur dans ``g.api_user``.

    Lis l'en-tête ``Authorization: Bearer <token>``. En cas d'absence, de
    format incorrect, de token inconnu, révoqué ou expiré, renvoie un 401
    JSON. Met à jour ``last_used_at`` sur chaque requête valide.

    Args:
        f: La fonction de vue à protéger.

    Returns:
        La vue décorée.
    """

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _token_error("En-tête Authorization manquant ou mal formé.")

        plaintext = auth_header[len("Bearer "):]
        if not plaintext:
            return _token_error("Token vide.")

        token_hash = ApiToken.hash_token(plaintext)
        token = db.session.scalars(
            db.select(ApiToken).where(ApiToken.token_hash == token_hash)
        ).first()

        if token is None:
            return _token_error("Token inconnu.")

        if not token.is_valid:
            if token.revoked:
                return _token_error("Token révoqué.")
            return _token_error("Token expiré.")

        # Mise à jour de last_used_at (commit léger, erreur non bloquante)
        try:
            from datetime import UTC, datetime

            token.last_used_at = datetime.now(UTC)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.warning("Impossible de mettre à jour last_used_at pour le token %s", token.id)

        g.api_user = token.user
        return f(*args, **kwargs)

    return decorated


def api_permission_required(permission: str) -> Callable:
    """Factory de décorateur : vérifie le token puis la permission.

    Args:
        permission: La permission requise (valeur de ``UserPermission``).

    Returns:
        Décorateur qui renvoie 401 si le token est invalide,
        403 si l'utilisateur n'a pas la permission.
    """

    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        @api_token_required
        def decorated(*args, **kwargs):
            user = g.api_user
            if not user.is_active:
                return _token_error("Compte désactivé.", 403)
            if not user.has_permission(permission):
                return _token_error(
                    f"Permission « {permission} » requise.", 403
                )
            return f(*args, **kwargs)

        return decorated

    return decorator


def current_api_user():
    """Retourne l'utilisateur authentifié via token (``g.api_user``).

    Returns:
        L'instance ``User`` associée au token Bearer de la requête courante.
    """
    return g.api_user
