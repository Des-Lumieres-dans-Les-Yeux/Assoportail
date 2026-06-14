"""Assoportail application factory."""

import logging
import os

import click
from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import config_by_name
from app.extensions import csrf, db, limiter, login_manager, migrate, talisman

logger = logging.getLogger(__name__)


def create_app(config_name: str | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_name: One of ``"dev"``, ``"prod"``, or ``"test"``.
            Defaults to the ``APP_CONFIG`` environment variable, then ``"prod"``.

    Returns:
        Configured Flask application instance.
    """
    if config_name is None:
        config_name = os.environ.get("APP_CONFIG", "prod")

    config_obj = config_by_name[config_name]
    if config_name in ("prod", "production") and hasattr(config_obj, "_validate"):
        config_obj._validate()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_obj)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    _init_extensions(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    # Enregistre spectree après tous les blueprints pour que les routes
    # de l'API soient visibles lors de la construction du schéma OpenAPI.
    from app.extensions import spec as _spec

    _spec.register(app)

    @app.route("/sw.js")
    def service_worker():
        """Serve the service worker from root scope with required headers."""
        from flask import make_response, send_from_directory

        resp = make_response(send_from_directory("static", "sw.js"))
        resp.headers["Content-Type"] = "application/javascript"
        resp.headers["Service-Worker-Allowed"] = "/"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    @app.route("/offline")
    def offline():
        """Offline fallback page served by the service worker."""
        from flask import render_template

        return render_template("offline.html")

    @app.after_request
    def set_noindex_header(response):
        """Tell every crawler (search engines and AI) not to index anything."""
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, noai, noimageai"
        return response

    @app.route("/robots.txt")
    @limiter.exempt
    def robots_txt():
        """Disallow the entire site for every crawler, including AI bots.

        Enforcement for non-compliant bots is handled by the global
        ``X-Robots-Tag`` header (below) and Cloudflare's bot rules.
        """
        from flask import Response

        ai_bots = [
            "GPTBot",
            "ChatGPT-User",
            "OAI-SearchBot",
            "ClaudeBot",
            "anthropic-ai",
            "Claude-Web",
            "CCBot",
            "Google-Extended",
            "PerplexityBot",
            "Bytespider",
            "Amazonbot",
            "Applebot-Extended",
            "Meta-ExternalAgent",
            "FacebookBot",
            "Diffbot",
            "ImagesiftBot",
            "Omgili",
            "cohere-ai",
        ]
        lines = ["User-agent: *", "Disallow: /", ""]
        for bot in ai_bots:
            lines += [f"User-agent: {bot}", "Disallow: /", ""]
        return Response("\n".join(lines), mimetype="text/plain")

    @app.route("/health")
    @limiter.exempt
    def health():
        """Liveness / readiness probe for container orchestration."""
        from flask import jsonify

        checks: dict = {"status": "ok"}
        try:
            db.session.execute(db.text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            logger.warning("Health check: database error — %s", exc)
            checks["database"] = "error"
            checks["status"] = "degraded"
        try:
            import redis as _redis

            r = _redis.from_url(app.config.get("RATELIMIT_STORAGE_URI", "redis://localhost:6379/0"))
            r.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            logger.warning("Health check: redis error — %s", exc)
            checks["redis"] = "error"
            checks["status"] = "degraded"

        code = 200 if checks["status"] == "ok" else 503
        return jsonify(checks), code

    @app.cli.command("vapid-keys")
    def vapid_keys():
        """Generate VAPID key pair for Web Push and print the env vars to set."""
        import base64

        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )
        from py_vapid import Vapid

        v = Vapid()
        v.generate_keys()
        pub_bytes = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub_b64url = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
        priv_pem = v.private_pem().decode().replace("\n", "\\n")
        click.echo("Add these to your .env file:\n")
        click.echo(f"VAPID_PUBLIC_KEY={pub_b64url}")
        click.echo(f"VAPID_PRIVATE_KEY_PEM={priv_pem}")
        click.echo("VAPID_CLAIMS_EMAIL=admin@votre-domaine.fr")

    @app.cli.command("api-token")
    @click.option("--email", required=True, help="Email de l'utilisateur propriétaire du token.")
    @click.option(
        "--name", default="API token", show_default=True, help="Libellé lisible du token."
    )
    @click.option(
        "--expires-days",
        default=None,
        type=int,
        help="Durée de validité en jours (optionnel, sans limite par défaut).",
    )
    def api_token(email: str, name: str, expires_days: int | None) -> None:
        """Crée un token Bearer API pour un utilisateur existant.

        Le token est affiché UNE SEULE FOIS en clair — conservez-le précieusement.
        """
        from datetime import UTC, datetime, timedelta

        from app.models.api_token import ApiToken
        from app.models.user import User

        user = db.session.scalars(db.select(User).where(User.email == email)).first()
        if user is None:
            click.echo(f"Erreur : aucun utilisateur trouvé avec l'email « {email} ».", err=True)
            return

        expires_at = None
        if expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_days)

        plaintext, token = ApiToken.generate(name=name, user_id=user.id, expires_at=expires_at)
        db.session.add(token)
        db.session.commit()

        click.echo("")
        click.echo("Token créé avec succès.")
        click.echo(f"  Utilisateur : {user.full_name} <{user.email}>")
        click.echo(f"  Libellé     : {name}")
        click.echo(f"  Préfixe     : {token.token_prefix}")
        if expires_at:
            click.echo(f"  Expiration  : {expires_at.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            click.echo("  Expiration  : aucune")
        click.echo("")
        click.echo("⚠️  Token (affiché UNE SEULE FOIS — ne sera plus jamais visible) :")
        click.echo(f"  {plaintext}")
        click.echo("")

    @app.cli.command("seed-admin")
    def seed_admin():
        """Create the initial admin account from ADMIN_EMAIL/ADMIN_PASSWORD env vars."""
        admin_email = app.config.get("ADMIN_EMAIL")
        admin_password = app.config.get("ADMIN_PASSWORD")
        if not admin_email or not admin_password:
            click.echo("ADMIN_EMAIL and ADMIN_PASSWORD must be set in .env")
            return
        from app.models.user import User, UserRole

        existing = db.session.execute(
            db.select(User).filter_by(email=admin_email)
        ).scalar_one_or_none()
        if existing:
            click.echo(f"Admin {admin_email} already exists.")
            return
        user = User(
            email=admin_email,
            first_name="Admin",
            last_name="Assoportail",
            role=UserRole.BUREAU,
            must_change_password=True,
        )
        user.set_password(admin_password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin account created: {admin_email}")

    return app


def _init_extensions(app: Flask) -> None:
    """Initialize all Flask extensions."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    talisman.init_app(
        app,
        force_https=app.config["TALISMAN_FORCE_HTTPS"],
        frame_options=None,
        content_security_policy=app.config["TALISMAN_CSP"],
        content_security_policy_nonce_in=["script-src", "style-src"],
        x_content_type_options=True,
        x_xss_protection=True,
        referrer_policy="strict-origin-when-cross-origin",
    )

    # Import models so SQLAlchemy registers them and Flask-Login can resolve users.
    with app.app_context():
        from app.audit import register_audit_listeners
        from app.models import api_token as _api_token_module  # noqa: F401
        from app.models import center as _center_module  # noqa: F401
        from app.models import document as _document_module  # noqa: F401
        from app.models import email as _email_module  # noqa: F401
        from app.models import event as _event_module  # noqa: F401
        from app.models import machine as _machine_module  # noqa: F401
        from app.models import mailing as _mailing_module  # noqa: F401
        from app.models import meeting as _meeting_module  # noqa: F401
        from app.models import member as _member_module  # noqa: F401
        from app.models import poll as _poll_module  # noqa: F401
        from app.models import push_subscription as _push_module  # noqa: F401
        from app.models import social as _social_module  # noqa: F401
        from app.models import task as _task_module  # noqa: F401
        from app.models import tombola as _tombola_module  # noqa: F401
        from app.models import treasury as _treasury_module  # noqa: F401
        from app.models import user as _user_module  # noqa: F401

        register_audit_listeners()

        @login_manager.user_loader
        def load_user(user_id: str):  # type: ignore[return]
            from app.models.user import User

            return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    """Register all application blueprints."""
    from app.blueprints.api import bp as api_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.centers import bp as centers_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.documents import bp as documents_bp
    from app.blueprints.events import bp as events_bp
    from app.blueprints.machines import bp as machines_bp
    from app.blueprints.mailbox import bp as mailbox_bp
    from app.blueprints.mailing import bp as mailing_bp
    from app.blueprints.meetings import bp as meetings_bp
    from app.blueprints.members import bp as members_bp
    from app.blueprints.polls import bp as polls_bp
    from app.blueprints.push import bp as push_bp
    from app.blueprints.social import bp as social_bp
    from app.blueprints.tasks import bp as tasks_bp
    from app.blueprints.tombola import bp as tombola_bp
    from app.blueprints.treasury import bp as treasury_bp
    from app.blueprints.vitrine import bp as vitrine_bp

    # L'API REST est stateless (Bearer token) — exempt du cookie CSRF.
    csrf.exempt(api_bp)
    app.register_blueprint(api_bp)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(mailbox_bp)
    app.register_blueprint(mailing_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(machines_bp)
    app.register_blueprint(centers_bp)
    app.register_blueprint(meetings_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(treasury_bp)
    app.register_blueprint(social_bp)
    app.register_blueprint(polls_bp)
    app.register_blueprint(push_bp)
    app.register_blueprint(tombola_bp)
    app.register_blueprint(vitrine_bp)


def _register_error_handlers(app: Flask) -> None:
    """Register HTTP error page handlers."""

    @app.errorhandler(403)
    def forbidden(_e: Exception):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e: Exception):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(_e: Exception):
        logger.exception("Unhandled server error")
        db.session.rollback()
        return render_template("errors/500.html"), 500
