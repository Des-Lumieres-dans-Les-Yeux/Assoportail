"""Authentication blueprint routes — login, logout, user creation (admin), password change, 2FA."""

import logging
import secrets
from datetime import UTC, datetime

import redis
from flask import abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth import bp
from app.blueprints.auth.forms import ChangePasswordForm, CreateUserForm, LoginForm, TotpCodeForm
from app.decorators import bureau_required
from app.extensions import db, limiter
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes


def _get_redis():
    """Return a Redis client from the rate-limiter storage URI."""
    uri = current_app.config.get("RATELIMIT_STORAGE_URI", "redis://localhost:6379/0")
    return redis.from_url(uri)


def _is_locked_out(email: str) -> bool:
    """Check if an account is locked due to too many failed login attempts."""
    key = f"login_lockout:{email.lower().strip()}"
    try:
        r = _get_redis()
        attempts = r.get(key)
        return attempts is not None and int(attempts) >= _LOCKOUT_MAX_ATTEMPTS
    except Exception:
        return False


def _record_failed_attempt(email: str) -> None:
    """Increment the failed login counter for an email in Redis."""
    key = f"login_lockout:{email.lower().strip()}"
    try:
        r = _get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _LOCKOUT_WINDOW_SECONDS)
        pipe.execute()
    except Exception:
        logger.warning("Could not record failed login attempt in Redis")


def _clear_lockout(email: str) -> None:
    """Clear the lockout counter after a successful login."""
    key = f"login_lockout:{email.lower().strip()}"
    try:
        r = _get_redis()
        r.delete(key)
    except Exception:
        logger.debug("Could not clear login lockout from Redis")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    """Display and process the login form.

    Redirects authenticated users directly to the dashboard.
    Rate-limited to 10 attempts per minute per IP to prevent brute force.
    After login, users with must_change_password=True are redirected to
    the change-password page instead of the dashboard.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        email_input = form.email.data.lower().strip()

        if _is_locked_out(email_input):
            flash(
                "Trop de tentatives. Compte temporairement verrouillé (15 min).",
                "danger",
            )
            return render_template("auth/login.html", form=form)

        user = db.session.execute(db.select(User).filter_by(email=email_input)).scalar_one_or_none()

        if user is None or not user.check_password(form.password.data):
            _record_failed_attempt(email_input)
            flash("Adresse email ou mot de passe incorrect.", "danger")
            return render_template("auth/login.html", form=form)

        if not user.is_active:
            flash("Votre compte est désactivé. Contactez l'administrateur.", "warning")
            return render_template("auth/login.html", form=form)

        # If the user has 2FA enabled, go through the TOTP verification step
        # before calling login_user().
        if user.totp_secret:
            session["pending_2fa_user_id"] = user.id
            session["pending_2fa_remember"] = form.remember_me.data
            return redirect(url_for("auth.verify_2fa"))

        _clear_lockout(email_input)
        user.last_login_at = datetime.now(UTC)
        db.session.commit()
        login_user(user, remember=form.remember_me.data)

        if user.must_change_password:
            return redirect(url_for("auth.change_password"))

        next_page = request.args.get("next")
        if next_page and (not next_page.startswith("/") or next_page.startswith("//")):
            next_page = None

        return redirect(next_page or url_for("dashboard.index"))

    return render_template("auth/login.html", form=form)


@bp.route("/2fa", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def verify_2fa():
    """Second-factor verification step — entered after a valid password."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    user_id = session.get("pending_2fa_user_id")
    if not user_id:
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if user is None or not user.totp_secret:
        session.pop("pending_2fa_user_id", None)
        session.pop("pending_2fa_remember", None)
        return redirect(url_for("auth.login"))

    form = TotpCodeForm()
    if form.validate_on_submit():
        import pyotp

        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(form.code.data.strip(), valid_window=1):
            session.pop("pending_2fa_user_id", None)
            remember = session.pop("pending_2fa_remember", False)
            _clear_lockout(user.email)
            user.last_login_at = datetime.now(UTC)
            db.session.commit()
            login_user(user, remember=remember)
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            return redirect(url_for("dashboard.index"))
        flash("Code invalide. Vérifiez l'heure de votre appareil.", "danger")

    return render_template("auth/2fa.html", form=form)


@bp.route("/totp-setup", methods=["GET", "POST"])
@login_required
def setup_totp():
    """Set up TOTP 2FA for the current user.

    On GET: generates a new secret and shows the QR code.
    On POST: verifies the code and saves the secret.
    """
    import pyotp

    form = TotpCodeForm()

    if request.method == "GET":
        # Generate a fresh secret and store it in session until confirmed
        secret = pyotp.random_base32()
        session["totp_pending_secret"] = secret
    else:
        secret = session.get("totp_pending_secret")
        if not secret:
            flash("Session expirée. Recommencez.", "warning")
            return redirect(url_for("auth.setup_totp"))

    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
        name=current_user.email,
        issuer_name="Assoportail",
    )

    # Generate QR code as a base64-encoded SVG data URI
    qr_data_uri = _totp_qr_data_uri(provisioning_uri)

    if form.validate_on_submit():
        totp = pyotp.TOTP(secret)
        if totp.verify(form.code.data.strip(), valid_window=1):
            current_user.totp_secret = secret
            db.session.commit()
            session.pop("totp_pending_secret", None)
            flash("Authentification à deux facteurs activée.", "success")
            return redirect(url_for("dashboard.index"))
        flash("Code invalide. Scannez à nouveau et réessayez.", "danger")

    return render_template(
        "auth/totp_setup.html",
        form=form,
        secret=secret,
        qr_data_uri=qr_data_uri,
    )


@bp.route("/totp-disable", methods=["POST"])
@login_required
def disable_totp():
    """Disable TOTP 2FA for the current user."""
    current_user.totp_secret = None
    db.session.commit()
    flash("Authentification à deux facteurs désactivée.", "info")
    return redirect(url_for("dashboard.index"))


@bp.route("/logout")
@login_required
def logout():
    """Log the current user out and redirect to the login page."""
    logout_user()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
@bureau_required
def create_user():
    """Create a new user account (bureau admins only).

    Generates a temporary password, creates the account with
    must_change_password=True, and sends the credentials by email.
    """
    form = CreateUserForm()
    if form.validate_on_submit():
        existing = db.session.execute(
            db.select(User).filter_by(email=form.email.data.lower().strip())
        ).scalar_one_or_none()

        if existing:
            flash("Cette adresse email est déjà utilisée.", "danger")
            return render_template("auth/register.html", form=form)

        role = UserRole.BUREAU if form.role.data == "bureau" else UserRole.MEMBER
        temp_password = secrets.token_urlsafe(9)

        user = User(
            email=form.email.data.lower().strip(),
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            role=role,
            must_change_password=True,
        )
        user.set_password(temp_password)
        db.session.add(user)
        db.session.commit()

        from app.tasks.notifications import send_welcome_email_task
        send_welcome_email_task.delay(user.id, temp_password)

        flash(
            f"Compte créé pour {user.full_name}."
            " Un email avec le mot de passe temporaire a été envoyé.",
            "success",
        )
        return redirect(url_for("members.list_members"))

    return render_template("auth/register.html", form=form)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute")
def change_password():
    """Allow a logged-in user to change their password.

    Forced for all users whose must_change_password flag is True
    (typically after account creation by an admin).
    """
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Le mot de passe actuel est incorrect.", "danger")
            return render_template("auth/change_password.html", form=form)

        current_user.set_password(form.new_password.data)
        current_user.must_change_password = False
        db.session.commit()

        flash("Votre mot de passe a été modifié avec succès.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("auth/change_password.html", form=form)


# ---------------------------------------------------------------------------
# Forgot / Reset password
# ---------------------------------------------------------------------------


@bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    """Request a password reset email.

    Always shows a success message regardless of whether the email exists
    to prevent user enumeration.
    """
    from app.blueprints.auth.forms import ForgotPasswordForm

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email_input = form.email.data.lower().strip()
        user = db.session.execute(db.select(User).filter_by(email=email_input)).scalar_one_or_none()

        if user and user.is_active:
            token = _generate_reset_token(user.id)
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            from app.tasks.notifications import send_password_reset_task
            send_password_reset_task.delay(user.id, reset_url)

        # Always show the same message to prevent user enumeration
        flash(
            "Si cette adresse est associée à un compte, un email de réinitialisation a été envoyé.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def reset_password(token: str):
    """Reset password using a signed token.

    The token is valid for 30 minutes and contains the user ID signed with
    the application's SECRET_KEY via itsdangerous.
    """
    from app.blueprints.auth.forms import ResetPasswordForm

    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    user_id = _verify_reset_token(token)
    if user_id is None:
        flash("Lien de réinitialisation invalide ou expiré.", "danger")
        return redirect(url_for("auth.forgot_password"))

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        flash("Lien de réinitialisation invalide ou expiré.", "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        user.must_change_password = False
        db.session.commit()
        _clear_lockout(user.email)
        flash("Votre mot de passe a été réinitialisé. Vous pouvez vous connecter.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


def _generate_reset_token(user_id: int) -> str:
    """Generate a time-limited, signed password reset token."""
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(user_id, salt="password-reset")


def _verify_reset_token(token: str, max_age: int = 1800) -> int | None:
    """Verify a password reset token. Returns user_id or None.

    Args:
        token: The signed token from the URL.
        max_age: Maximum token age in seconds (default 30 minutes).
    """
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        return s.loads(token, salt="password-reset", max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


@bp.route("/reset-password-admin/<int:user_id>", methods=["POST"])
@bureau_required
def admin_reset_password(user_id: int):
    """Reset a user's password (bureau admins only).

    Generates a new temporary password, sets must_change_password=True,
    and sends the new credentials by email.
    """
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    temp_password = secrets.token_urlsafe(12)
    user.set_password(temp_password)
    user.must_change_password = True
    db.session.commit()

    from app.tasks.notifications import send_admin_reset_task
    send_admin_reset_task.delay(user.id, temp_password)
    flash(
        f"Mot de passe réinitialisé pour {user.full_name}. "
        "Un email avec le nouveau mot de passe temporaire a été envoyé.",
        "success",
    )
    return redirect(url_for("members.detail", user_id=user.id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _totp_qr_data_uri(provisioning_uri: str) -> str:
    """Return a base64-encoded SVG data URI for a TOTP provisioning URI QR code."""
    import base64
    import io

    import qrcode
    import qrcode.image.svg as qrsvg

    factory = qrsvg.SvgPathImage
    img = qrcode.make(provisioning_uri, image_factory=factory, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{b64}"
