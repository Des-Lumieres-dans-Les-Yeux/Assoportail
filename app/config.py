"""Flask configuration classes for development, production, and testing."""

import os


class Config:
    """Shared base configuration."""

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    WTF_CSRF_SECRET_KEY: str = os.environ.get("WTF_CSRF_SECRET_KEY", "dev-csrf-key-change-me")

    # Session cookie security
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    PERMANENT_SESSION_LIFETIME: int = 86400  # 24 hours

    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 10)),
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }

    # Rate limiting (Redis backend shared with Celery)
    RATELIMIT_STORAGE_URI: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    RATELIMIT_DEFAULT: str = "2000 per day;500 per hour"

    # File uploads
    UPLOAD_FOLDER: str = os.environ.get("UPLOAD_FOLDER", "/data/uploads")

    # Google OAuth2 / Gmail / Drive integration
    GMAIL_CREDENTIALS_FILE: str = os.environ.get("GMAIL_CREDENTIALS_FILE", "/data/credentials.json")
    GMAIL_POLL_INTERVAL: int = int(os.environ.get("GMAIL_POLL_INTERVAL", 300))
    GOOGLE_SHARED_DRIVE_ID: str = os.environ.get("GOOGLE_SHARED_DRIVE_ID", "")
    # Comma-separated list of base64url-encoded Fernet keys (newest first for key rotation)
    ENCRYPTION_KEYS: list[str] = [k for k in os.environ.get("ENCRYPTION_KEYS", "").split(",") if k]
    MAILING_RATE_LIMIT: int = int(os.environ.get("MAILING_RATE_LIMIT", 100))
    # Emails sent back-to-back per task invocation before rescheduling the next
    # batch. Keeps each task short so it can never hit its time limit mid-send.
    MAILING_BATCH_SIZE: int = int(os.environ.get("MAILING_BATCH_SIZE", 10))
    MAX_UPLOAD_PHOTO: int = int(os.environ.get("MAX_UPLOAD_PHOTO", 10 * 1024 * 1024))
    MAX_UPLOAD_VIDEO: int = int(os.environ.get("MAX_UPLOAD_VIDEO", 50 * 1024 * 1024))
    MAX_UPLOAD_DOCUMENT: int = int(os.environ.get("MAX_UPLOAD_DOCUMENT", 20 * 1024 * 1024))
    # Tombola media videos can be up to 1 GB (cover videos, ceremony recordings, etc.)
    MAX_UPLOAD_TOMBOLA_VIDEO: int = int(
        os.environ.get("MAX_UPLOAD_TOMBOLA_VIDEO", 1024 * 1024 * 1024)
    )
    # Flask uses MAX_CONTENT_LENGTH to reject oversized requests before they reach the app.
    # Set to 1 GB to accommodate tombola media video uploads.
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 1024 * 1024 * 1024))

    # Base URL used by Celery workers to build external URLs (url_for _external=True).
    # Workers run outside any HTTP request, so Flask cannot derive the host from headers.
    # Set to the main portal origin, e.g. "https://portail.deslumieresdanslesyeux.fr".
    TASK_BASE_URL: str = os.environ.get("TASK_BASE_URL", "http://localhost")

    # Base URL for public-facing links sent in emails (feedback, signalement,
    # volunteer confirmation…). These forms may be proxied via a dedicated
    # subdomain (e.g. "https://demande.deslumieresdanslesyeux.fr").
    # Falls back to TASK_BASE_URL when not set.
    LINKS_EXTERNAL_URL: str = os.environ.get("LINKS_EXTERNAL_URL", "")

    # SMTP email delivery
    SMTP_HOST: str = os.environ.get("SMTP_HOST", "localhost")
    SMTP_PORT: int = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USER: str = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.environ.get("SMTP_FROM", "noreply@assoportail.fr")
    SMTP_USE_TLS: bool = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_TIMEOUT: int = int(os.environ.get("SMTP_TIMEOUT", 30))

    # Initial admin account (created on first run)
    ADMIN_EMAIL: str = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD: str = os.environ.get("ADMIN_PASSWORD", "")

    # Social Publishing — Meta (Facebook + Instagram)
    FACEBOOK_APP_ID: str = os.environ.get("FACEBOOK_APP_ID", "")
    FACEBOOK_APP_SECRET: str = os.environ.get("FACEBOOK_APP_SECRET", "")
    # Social Publishing — LinkedIn
    LINKEDIN_CLIENT_ID: str = os.environ.get("LINKEDIN_CLIENT_ID", "")
    LINKEDIN_CLIENT_SECRET: str = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
    SOCIAL_IMAGE_QUALITY: int = int(os.environ.get("SOCIAL_IMAGE_QUALITY", 85))
    SOCIAL_MAX_IMAGES_PER_POST: int = int(os.environ.get("SOCIAL_MAX_IMAGES_PER_POST", 10))

    # Web Push notifications (VAPID)
    # Generate with: flask vapid-keys
    VAPID_PUBLIC_KEY: str = os.environ.get("VAPID_PUBLIC_KEY", "")
    # PEM private key — newlines may be stored as literal \n in the env var
    VAPID_PRIVATE_KEY_PEM: str = os.environ.get("VAPID_PRIVATE_KEY_PEM", "")
    VAPID_CLAIMS_EMAIL: str = os.environ.get("VAPID_CLAIMS_EMAIL", "")

    # Talisman keyword arguments unpacked in create_app()
    TALISMAN_FORCE_HTTPS: bool = False
    WORDPRESS_URL: str = os.environ.get("WORDPRESS_URL", "")
    TALISMAN_CSP: dict = {
        "default-src": "'self'",
        "script-src": [
            "'self'",
            "https://cdn.jsdelivr.net",
            "https://unpkg.com",
            # Script inline d'initialisation de Swagger UI servi par spectree
            # (page /api/docs/swagger/). Hash stable tant que spectree==2.0.1.
            "'sha256-0kSywEu7Zjn7qT57QKszoOA3Vbp9XgKGvPK4mm/gO4E='",
        ],
        "style-src": [
            "'self'",
            "https://cdn.jsdelivr.net",
            "https://unpkg.com",
            "'sha256-bsV5JivYxvGywDAZ22EZJKBFip65Ng9xoJVLbBg7bdo='",
            "'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='",
        ],
        "style-src-attr": "'unsafe-inline'",
        "font-src": ["'self'", "https://cdn.jsdelivr.net", "data:"],
        "img-src": [
            "'self'",
            "data:",
            "blob:",
            "https://drive.google.com",
            "https://*.googleusercontent.com",
            "https://*.usercontent.google.com",
            "https://*.tile.openstreetmap.org",
            "https://unpkg.com",
        ],
        "connect-src": ["'self'", "https://cdn.jsdelivr.net", "https://unpkg.com"],
        "worker-src": "'self'",
        "frame-ancestors": ["'self'"]
        + (
            [
                os.environ.get("WORDPRESS_URL"),
                os.environ.get("WORDPRESS_URL").replace("https://www.", "https://")
                if "https://www." in os.environ.get("WORDPRESS_URL")
                else os.environ.get("WORDPRESS_URL").replace("https://", "https://www."),
            ]
            if os.environ.get("WORDPRESS_URL")
            else []
        ),
    }


class DevelopmentConfig(Config):
    """Development configuration — debug mode, local PostgreSQL."""

    DEBUG: bool = True
    SESSION_COOKIE_SECURE: bool = False  # no HTTPS in dev
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://assoportail:devpassword@localhost:5432/assoportail_dev",
    )
    SQLALCHEMY_ECHO: bool = False  # set True locally to log SQL queries


class ProductionConfig(Config):
    """Production configuration — HTTPS enforced, no debug."""

    DEBUG: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get("DATABASE_URL", "")
    TALISMAN_FORCE_HTTPS: bool = os.environ.get("TALISMAN_FORCE_HTTPS", "true").lower() == "true"

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

    @classmethod
    def _validate(cls) -> None:
        insecure_defaults = {"dev-secret-key-change-me", "dev-csrf-key-change-me", "", None}
        if cls.SECRET_KEY in insecure_defaults:
            raise RuntimeError("SECRET_KEY must be set to a strong random value in production.")
        if cls.WTF_CSRF_SECRET_KEY in insecure_defaults:
            raise RuntimeError(
                "WTF_CSRF_SECRET_KEY must be set to a strong random value in production."
            )
        if not cls.ENCRYPTION_KEYS:
            raise RuntimeError("ENCRYPTION_KEYS must be set in production.")
        if not cls.SQLALCHEMY_DATABASE_URI:
            raise RuntimeError("DATABASE_URL must be set in production.")


class TestingConfig(Config):
    """Testing configuration — CSRF disabled, rate limiting disabled."""

    TESTING: bool = True
    SESSION_COOKIE_SECURE: bool = False
    WTF_CSRF_ENABLED: bool = False
    RATELIMIT_ENABLED: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://assoportail:devpassword@localhost:5432/assoportail_test",
    )
    TALISMAN_FORCE_HTTPS: bool = False
    TALISMAN_CSP: dict = {}  # type: ignore[assignment]
    UPLOAD_FOLDER: str = os.path.join(os.path.dirname(__file__), "..", "tests", "uploads_tmp")
    # Use a static test key (valid base64url-encoded 32-byte Fernet key)
    ENCRYPTION_KEYS: list[str] = ["AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="]


config_by_name: dict[str, type[Config]] = {
    "dev": DevelopmentConfig,
    "development": DevelopmentConfig,
    "prod": ProductionConfig,
    "production": ProductionConfig,
    "test": TestingConfig,
    "testing": TestingConfig,
}
