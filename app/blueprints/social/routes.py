"""Social publishing blueprint routes — post CRUD, images, accounts, publishing."""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import UTC, date, datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.blueprints.social import bp
from app.blueprints.social.forms import (
    ImageUploadForm,
    SocialAccountForm,
    SocialPostForm,
)
from app.decorators import bureau_required, permission_required
from app.extensions import db
from app.models.social import (
    PublishLogStatus,
    SocialAccount,
    SocialPlatform,
    SocialPost,
    SocialPostImage,
    SocialPostStatus,
)
from app.models.user import UserPermission

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_ALLOWED_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _detect_image_mime(data: bytes) -> str | None:
    """Return the image MIME type based on magic bytes, or None if unknown."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------------------------------------------------------------------------
# Post list
# ---------------------------------------------------------------------------


@bp.route("/")
@permission_required(UserPermission.SOCIAL)
def list_posts():
    """List all social posts, newest first."""
    status_filter = request.args.get("status", "").strip()
    page = max(1, request.args.get("page", 1, type=int))

    stmt = (
        db.select(SocialPost)
        .options(selectinload(SocialPost.created_by))
        .order_by(SocialPost.created_at.desc())
    )
    if status_filter and status_filter in {s.value for s in SocialPostStatus}:
        stmt = stmt.where(SocialPost.status == status_filter)

    pagination = db.paginate(stmt, page=page, per_page=20, error_out=False)

    return render_template(
        "social/list.html",
        posts=pagination.items,
        pagination=pagination,
        status=status_filter,
        SocialPostStatus=SocialPostStatus,
    )


# ---------------------------------------------------------------------------
# Create post
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@permission_required(UserPermission.SOCIAL)
def create():
    """Create a new draft social post."""
    form = SocialPostForm()
    form.platforms.choices = _platform_choices()

    if form.validate_on_submit():
        post = SocialPost(
            title=form.title.data.strip(),
            body_html=form.body_html.data,
            body_text=_html_to_text(form.body_html.data),
            platforms=form.platforms.data or [],
            instagram_format=form.instagram_format.data,
            status=SocialPostStatus.DRAFT.value,
            created_by_id=current_user.id,
        )
        if form.scheduled_at.data:
            dt = form.scheduled_at.data
            post.scheduled_at = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
            post.status = SocialPostStatus.SCHEDULED.value

        db.session.add(post)
        db.session.commit()
        flash(f"Post « {post.title} » créé.", "success")
        return redirect(url_for("social.detail", post_id=post.id))

    return render_template("social/form.html", form=form, post=None)


# ---------------------------------------------------------------------------
# Post detail
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>")
@permission_required(UserPermission.SOCIAL)
def detail(post_id: int):
    """Render post detail with images and publish logs."""
    post = db.session.get(
        SocialPost,
        post_id,
        options=[
            selectinload(SocialPost.created_by),
            selectinload(SocialPost.images).selectinload(SocialPostImage.document),
            selectinload(SocialPost.publish_logs),
        ],
    )
    if post is None:
        abort(404)

    upload_form = ImageUploadForm()

    # Load gallery photos for the inline picker (only for editable posts)
    gallery_photos = []
    if post.is_editable:
        from app.models.document import Document, DocumentType

        gallery_photos = db.session.scalars(
            db.select(Document)
            .where(Document.type == DocumentType.PHOTO.value)
            .order_by(Document.uploaded_at.desc())
            .limit(50)
        ).all()

    return render_template(
        "social/detail.html",
        post=post,
        upload_form=upload_form,
        gallery_photos=gallery_photos,
        SocialPostStatus=SocialPostStatus,
        PublishLogStatus=PublishLogStatus,
    )


# ---------------------------------------------------------------------------
# Edit post
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/edit", methods=["GET", "POST"])
@permission_required(UserPermission.SOCIAL)
def edit(post_id: int):
    """Edit a draft social post."""
    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)
    if not post.is_editable:
        flash("Seuls les brouillons peuvent être modifiés.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))

    form = SocialPostForm(obj=post)
    form.platforms.choices = _platform_choices()

    if request.method == "GET":
        form.platforms.data = post.platforms or []
        if post.scheduled_at:
            form.scheduled_at.data = post.scheduled_at.replace(tzinfo=None)

    if form.validate_on_submit():
        post.title = form.title.data.strip()
        post.body_html = form.body_html.data
        post.body_text = _html_to_text(form.body_html.data)
        post.platforms = form.platforms.data or []
        post.instagram_format = form.instagram_format.data

        if form.scheduled_at.data:
            dt = form.scheduled_at.data
            post.scheduled_at = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
            post.status = SocialPostStatus.SCHEDULED.value
        else:
            post.scheduled_at = None
            post.status = SocialPostStatus.DRAFT.value

        db.session.commit()
        flash("Post mis à jour.", "success")
        return redirect(url_for("social.detail", post_id=post.id))

    return render_template("social/form.html", form=form, post=post)


# ---------------------------------------------------------------------------
# Delete post
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/delete", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def delete(post_id: int):
    """Delete a draft social post and its images."""
    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)
    if not post.is_editable:
        flash("Seuls les brouillons peuvent être supprimés.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))

    title = post.title
    db.session.delete(post)
    db.session.commit()
    flash(f"Post « {title} » supprimé.", "success")
    return redirect(url_for("social.list_posts"))


# ---------------------------------------------------------------------------
# Image upload
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/images/upload", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def upload_image(post_id: int):
    """Upload one or more images to a social post (supports multi-file)."""
    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)
    if not post.is_editable:
        flash("Impossible d'ajouter des images à un post publié.", "warning")
        return redirect(url_for("social.detail", post_id=post_id))

    max_images = current_app.config.get("SOCIAL_MAX_IMAGES_PER_POST", 10)
    existing_count = (
        db.session.scalar(
            db.select(db.func.count(SocialPostImage.id)).where(SocialPostImage.post_id == post_id)
        )
        or 0
    )

    files = request.files.getlist("file")
    if not files or not files[0].filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("social.detail", post_id=post_id))

    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "social", "originals")
    os.makedirs(upload_dir, exist_ok=True)

    added = 0
    for file in files:
        if existing_count + added >= max_images:
            flash(f"Maximum {max_images} images par post.", "warning")
            break

        if not file.filename:
            continue
        safe_name = secure_filename(file.filename)
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in _ALLOWED_IMAGE_EXTS:
            flash(
                f"« {safe_name} » ignoré (format non autorisé).",
                "warning",
            )
            continue

        data = file.read()
        detected_mime = _detect_image_mime(data)
        if detected_mime not in _ALLOWED_IMAGE_MIMES:
            flash(f"« {safe_name} » ignoré (contenu non reconnu comme image).", "warning")
            continue

        stored = f"{date.today().isoformat()}_social_{secrets.token_hex(6)}{ext}"
        with open(os.path.join(upload_dir, stored), "wb") as f:
            f.write(data)

        position = existing_count + added
        img = SocialPostImage(
            post_id=post_id,
            original_filename=safe_name,
            stored_filename=stored,
            position=position,
            is_featured=(position == 0 and existing_count == 0),
        )
        db.session.add(img)
        added += 1

    if added:
        db.session.commit()
        flash(
            f"{added} image{'s' if added > 1 else ''} ajoutée{'s' if added > 1 else ''}.", "success"
        )

    return redirect(url_for("social.detail", post_id=post_id))


# ---------------------------------------------------------------------------
# Pick image from gallery
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/images/pick-gallery", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def pick_gallery_image(post_id: int):
    """Attach an existing Document (photo) to the post."""
    from app.models.document import Document, DocumentType

    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)

    doc_id = request.form.get("document_id", type=int)
    if not doc_id:
        flash("Aucune image sélectionnée.", "warning")
        return redirect(url_for("social.detail", post_id=post_id))

    doc = db.session.get(Document, doc_id)
    if doc is None or doc.type != DocumentType.PHOTO.value:
        flash("Document introuvable ou n'est pas une photo.", "danger")
        return redirect(url_for("social.detail", post_id=post_id))

    existing_count = (
        db.session.scalar(
            db.select(db.func.count(SocialPostImage.id)).where(SocialPostImage.post_id == post_id)
        )
        or 0
    )

    img = SocialPostImage(
        post_id=post_id,
        document_id=doc.id,
        original_filename=doc.original_filename,
        stored_filename=doc.stored_filename,
        position=existing_count,
        is_featured=(existing_count == 0),
    )
    db.session.add(img)
    db.session.commit()
    flash(f"Image « {doc.original_filename} » ajoutée depuis la galerie.", "success")
    return redirect(url_for("social.detail", post_id=post_id))


# ---------------------------------------------------------------------------
# Gallery picker (HTMX partial)
# ---------------------------------------------------------------------------


@bp.route("/gallery-picker")
@permission_required(UserPermission.SOCIAL)
def gallery_picker():
    """Return an HTML fragment of gallery photos for HTMX modal."""
    from app.models.document import Document, DocumentType

    photos = db.session.scalars(
        db.select(Document)
        .where(Document.type == DocumentType.PHOTO.value)
        .order_by(Document.uploaded_at.desc())
        .limit(50)
    ).all()
    post_id = request.args.get("post_id", 0, type=int)
    return render_template("social/_image_picker.html", photos=photos, post_id=post_id)


# ---------------------------------------------------------------------------
# Serve uploaded social images
# ---------------------------------------------------------------------------


@bp.route("/images/<int:img_id>/serve")
@permission_required(UserPermission.SOCIAL)
def serve_image(img_id: int):
    """Serve an uploaded social image from disk."""
    from flask import send_from_directory

    img = db.session.get(SocialPostImage, img_id)
    if img is None:
        abort(404)

    if img.document_id:
        return redirect(url_for("documents.download", document_id=img.document_id))

    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "social", "originals")
    return send_from_directory(upload_dir, img.stored_filename)


# ---------------------------------------------------------------------------
# Image management
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/images/<int:img_id>/featured", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def set_featured(post_id: int, img_id: int):
    """Set an image as the featured image for WordPress."""
    post = db.session.get(SocialPost, post_id, options=[selectinload(SocialPost.images)])
    if post is None:
        abort(404)

    for img in post.images:
        img.is_featured = img.id == img_id
    db.session.commit()
    flash("Image mise en avant mise à jour.", "success")
    return redirect(url_for("social.detail", post_id=post_id))


@bp.route("/<int:post_id>/images/<int:img_id>/delete", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def delete_image(post_id: int, img_id: int):
    """Remove an image from the post."""
    img = db.session.get(SocialPostImage, img_id)
    if img is None or img.post_id != post_id:
        abort(404)
    db.session.delete(img)
    db.session.commit()
    flash("Image retirée.", "success")
    return redirect(url_for("social.detail", post_id=post_id))


@bp.route(
    "/<int:post_id>/images/<int:img_id>/crop",
    methods=["POST"],
)
@permission_required(UserPermission.SOCIAL)
def save_crop_data(post_id: int, img_id: int):
    """Save crop coordinates for an image (JSON via HTMX)."""
    img = db.session.get(SocialPostImage, img_id)
    if img is None or img.post_id != post_id:
        abort(404)

    try:
        data = json.loads(request.get_data(as_text=True))
    except (json.JSONDecodeError, ValueError):
        return {"error": "Invalid JSON"}, 400

    img.crop_data = data
    db.session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/publish", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def publish(post_id: int):
    """Trigger async publication via Celery."""
    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)
    if post.status not in (
        SocialPostStatus.DRAFT.value,
        SocialPostStatus.SCHEDULED.value,
    ):
        flash("Ce post a déjà été envoyé ou est en cours d'envoi.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))

    if not post.platforms:
        flash("Sélectionnez au moins une plateforme.", "warning")
        return redirect(url_for("social.detail", post_id=post.id))

    from app.tasks.social import publish_social_post

    publish_social_post.delay(post_id)
    flash(f"Publication de « {post.title} » lancée…", "info")
    return redirect(url_for("social.detail", post_id=post.id))


# ---------------------------------------------------------------------------
# Retry failed platform
# ---------------------------------------------------------------------------


@bp.route("/<int:post_id>/retry/<platform>", methods=["POST"])
@permission_required(UserPermission.SOCIAL)
def retry_platform(post_id: int, platform: str):
    """Retry publishing to a specific platform that previously failed."""
    post = db.session.get(SocialPost, post_id)
    if post is None:
        abort(404)

    if platform not in {p.value for p in SocialPlatform}:
        abort(400)

    from app.tasks.social import publish_to_platform

    publish_to_platform.delay(post_id, platform)
    flash(f"Nouvelle tentative de publication vers {platform}…", "info")
    return redirect(url_for("social.detail", post_id=post.id))


# ---------------------------------------------------------------------------
# Social Accounts CRUD
# ---------------------------------------------------------------------------


@bp.route("/accounts")
@bureau_required
def list_accounts():
    """List all connected social accounts."""
    accounts = db.session.scalars(
        db.select(SocialAccount)
        .options(selectinload(SocialAccount.connected_by))
        .order_by(SocialAccount.platform)
    ).all()
    return render_template("social/accounts.html", accounts=accounts)


@bp.route("/accounts/new", methods=["GET", "POST"])
@bureau_required
def add_account():
    """Add a new social account (credentials form)."""
    form = SocialAccountForm()

    if form.validate_on_submit():
        platform = form.platform.data

        # Check uniqueness
        existing = db.session.execute(
            db.select(SocialAccount).where(SocialAccount.platform == platform)
        ).scalar_one_or_none()
        if existing:
            flash(
                f"Un compte {platform} est déjà connecté. Modifiez-le ou supprimez-le.",
                "warning",
            )
            return redirect(url_for("social.list_accounts"))

        creds = _build_credentials(form)
        from app.services.gmail import encrypt_token

        account = SocialAccount(
            platform=platform,
            display_name=form.display_name.data.strip(),
            credentials_encrypted=encrypt_token(creds),
            connected_by_id=current_user.id,
        )
        db.session.add(account)
        db.session.commit()
        flash(f"Compte {platform} connecté.", "success")
        return redirect(url_for("social.list_accounts"))

    return render_template("social/account_form.html", form=form, account=None)


@bp.route("/accounts/<int:account_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit_account(account_id: int):
    """Edit an existing social account."""
    account = db.session.get(SocialAccount, account_id)
    if account is None:
        abort(404)

    form = SocialAccountForm(obj=account)

    if request.method == "GET":
        form.platform.data = account.platform
        try:
            from app.services.gmail import decrypt_token

            creds = decrypt_token(account.credentials_encrypted)
            form.site_url.data = creds.get("site_url", "")
            form.username.data = creds.get("username", "")
        except Exception:
            logger.debug("Could not decrypt credentials for account %s", account.id, exc_info=True)

    if form.validate_on_submit():
        creds = _build_credentials(form)
        from app.services.gmail import encrypt_token

        account.display_name = form.display_name.data.strip()
        account.credentials_encrypted = encrypt_token(creds)
        db.session.commit()
        flash("Compte mis à jour.", "success")
        return redirect(url_for("social.list_accounts"))

    return render_template("social/account_form.html", form=form, account=account)


@bp.route("/accounts/<int:account_id>/delete", methods=["POST"])
@bureau_required
def delete_account(account_id: int):
    """Delete a social account."""
    account = db.session.get(SocialAccount, account_id)
    if account is None:
        abort(404)
    platform = account.platform
    db.session.delete(account)
    db.session.commit()
    flash(f"Compte {platform} supprimé.", "success")
    return redirect(url_for("social.list_accounts"))


@bp.route("/accounts/<int:account_id>/test", methods=["POST"])
@bureau_required
def test_account(account_id: int):
    """Validate credentials by making a test API call."""
    account = db.session.get(SocialAccount, account_id)
    if account is None:
        abort(404)

    try:
        from app.services.gmail import decrypt_token

        creds = decrypt_token(account.credentials_encrypted)
    except Exception:
        flash("Impossible de déchiffrer les credentials.", "danger")
        return redirect(url_for("social.list_accounts"))

    ok, msg = _test_platform_credentials(account.platform, creds)
    if ok:
        flash(f"Connexion {account.platform} OK : {msg}", "success")
    else:
        flash(f"Échec {account.platform} : {msg}", "danger")

    return redirect(url_for("social.list_accounts"))


# ---------------------------------------------------------------------------
# Facebook OAuth
# ---------------------------------------------------------------------------


@bp.route("/accounts/facebook/oauth/start")
@bureau_required
def fb_oauth_start():
    """Redirect to Facebook OAuth consent page."""
    from flask import session

    app_id = current_app.config.get("FACEBOOK_APP_ID")
    if not app_id:
        flash("FACEBOOK_APP_ID non configuré dans .env.", "danger")
        return redirect(url_for("social.list_accounts"))

    state = secrets.token_urlsafe(32)
    session["fb_oauth_state"] = state

    redirect_uri = url_for("social.fb_oauth_callback", _external=True)
    scopes = "pages_manage_posts,pages_read_engagement,pages_show_list"
    auth_url = (
        f"https://www.facebook.com/v19.0/dialog/oauth"
        f"?client_id={app_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
        f"&scope={scopes}"
    )
    return redirect(auth_url)


@bp.route("/accounts/facebook/oauth/callback")
@bureau_required
def fb_oauth_callback():
    """Handle the Facebook OAuth callback and store the Page Access Token."""
    import httpx
    from flask import session

    state = session.pop("fb_oauth_state", None)
    if not state or state != request.args.get("state"):
        flash("Session OAuth invalide. Recommencez.", "danger")
        return redirect(url_for("social.list_accounts"))

    code = request.args.get("code")
    if not code:
        flash(
            f"Erreur Facebook : {request.args.get('error_description', 'inconnue')}",
            "danger",
        )
        return redirect(url_for("social.list_accounts"))

    app_id = current_app.config["FACEBOOK_APP_ID"]
    app_secret = current_app.config["FACEBOOK_APP_SECRET"]
    redirect_uri = url_for("social.fb_oauth_callback", _external=True)

    try:
        # Exchange code for short-lived token
        r = httpx.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
            timeout=10,
        )
        r.raise_for_status()
        short_token = r.json()["access_token"]

        # Exchange for long-lived token
        r2 = httpx.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=10,
        )
        r2.raise_for_status()
        long_token = r2.json()["access_token"]

        # Get page access token
        r3 = httpx.get(
            "https://graph.facebook.com/v19.0/me/accounts",
            params={"access_token": long_token},
            timeout=10,
        )
        r3.raise_for_status()
        pages = r3.json().get("data", [])
        if not pages:
            flash("Aucune page Facebook trouvée.", "danger")
            return redirect(url_for("social.list_accounts"))

        if len(pages) > 1:
            # Multiple pages — store token in session and let user choose
            session["fb_pages"] = [
                {"id": p["id"], "name": p.get("name", ""), "access_token": p["access_token"]}
                for p in pages
            ]
            return redirect(url_for("social.fb_select_page"))

        page = pages[0]
        _save_facebook_account(
            page_id=page["id"],
            page_token=page["access_token"],
            page_name=page.get("name", "Page Facebook"),
        )

    except Exception as exc:
        logger.exception("Facebook OAuth failed")
        flash(f"Erreur OAuth Facebook : {exc}", "danger")

    return redirect(url_for("social.list_accounts"))


# ---------------------------------------------------------------------------
# Facebook page selection (when multiple pages)
# ---------------------------------------------------------------------------


@bp.route("/accounts/facebook/select-page", methods=["GET", "POST"])
@bureau_required
def fb_select_page():
    """Let the user pick which Facebook page to connect."""
    from flask import session

    pages = session.get("fb_pages", [])
    if not pages:
        flash("Session expirée. Reconnectez Facebook.", "warning")
        return redirect(url_for("social.list_accounts"))

    if request.method == "POST":
        chosen_id = request.form.get("page_id")
        page = next((p for p in pages if p["id"] == chosen_id), None)
        if page is None:
            flash("Page introuvable.", "danger")
            return redirect(url_for("social.fb_select_page"))

        session.pop("fb_pages", None)
        _save_facebook_account(
            page_id=page["id"],
            page_token=page["access_token"],
            page_name=page["name"],
        )
        return redirect(url_for("social.list_accounts"))

    return render_template("social/fb_select_page.html", pages=pages)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_facebook_account(page_id: str, page_token: str, page_name: str) -> None:
    """Persist Facebook (and optionally Instagram) account from OAuth data."""
    import httpx

    from app.services.gmail import encrypt_token

    creds = {
        "page_id": page_id,
        "page_access_token": page_token,
        "page_name": page_name,
    }

    # Upsert Facebook account
    account = db.session.execute(
        db.select(SocialAccount).where(SocialAccount.platform == SocialPlatform.FACEBOOK.value)
    ).scalar_one_or_none()
    if account:
        account.credentials_encrypted = encrypt_token(creds)
        account.display_name = page_name
        account.updated_at = datetime.now(UTC)
    else:
        account = SocialAccount(
            platform=SocialPlatform.FACEBOOK.value,
            display_name=page_name,
            credentials_encrypted=encrypt_token(creds),
            connected_by_id=current_user.id,
        )
        db.session.add(account)
    db.session.commit()

    # Auto-detect linked Instagram Business Account
    ig_name = None
    try:
        r_ig = httpx.get(
            f"https://graph.facebook.com/v19.0/{page_id}",
            params={
                "fields": "instagram_business_account",
                "access_token": page_token,
            },
            timeout=10,
        )
        ig_data = r_ig.json().get("instagram_business_account")
        if ig_data:
            ig_id = ig_data["id"]
            r_ig2 = httpx.get(
                f"https://graph.facebook.com/v19.0/{ig_id}",
                params={"fields": "username", "access_token": page_token},
                timeout=10,
            )
            ig_username = r_ig2.json().get("username", "Instagram")
            ig_name = f"@{ig_username}"

            ig_creds = {
                "page_id": page_id,
                "page_access_token": page_token,
                "ig_user_id": ig_id,
                "ig_username": ig_username,
            }

            ig_account = db.session.execute(
                db.select(SocialAccount).where(
                    SocialAccount.platform == SocialPlatform.INSTAGRAM.value
                )
            ).scalar_one_or_none()
            if ig_account:
                ig_account.credentials_encrypted = encrypt_token(ig_creds)
                ig_account.display_name = ig_name
                ig_account.updated_at = datetime.now(UTC)
            else:
                db.session.add(
                    SocialAccount(
                        platform=SocialPlatform.INSTAGRAM.value,
                        display_name=ig_name,
                        credentials_encrypted=encrypt_token(ig_creds),
                        connected_by_id=current_user.id,
                    )
                )
            db.session.commit()
    except Exception:
        logger.debug("Instagram detection failed", exc_info=True)

    msg = f"Page Facebook « {page_name} » connectée."
    if ig_name:
        msg += f" Instagram {ig_name} détecté et connecté."
    flash(msg, "success")


def _platform_choices() -> list[tuple[str, str]]:
    """Return (value, label) tuples for active social accounts."""
    labels = {
        "wordpress": "WordPress",
        "facebook": "Facebook",
        "instagram": "Instagram",
        "linkedin": "LinkedIn",
    }
    accounts = db.session.scalars(
        db.select(SocialAccount).where(SocialAccount.is_active.is_(True))
    ).all()
    return [
        (a.platform, f"{labels.get(a.platform, a.platform)} — {a.display_name}") for a in accounts
    ]


def _build_credentials(form: SocialAccountForm) -> dict:
    """Extract platform credentials from the form into a dict."""
    if form.platform.data == "wordpress":
        return {
            "site_url": (form.site_url.data or "").strip(),
            "username": (form.username.data or "").strip(),
            "app_password": (form.app_password.data or "").strip(),
        }
    # Facebook/Instagram/LinkedIn use OAuth — credentials set via callback
    return {}


def _is_safe_url(url: str) -> bool:
    """Return True if *url* points to a public (non-private) HTTPS host."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("https",):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        for info in socket.getaddrinfo(hostname, None):
            addr = ipaddress.ip_address(info[4][0])
            if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


def _test_platform_credentials(platform: str, creds: dict) -> tuple[bool, str]:
    """Make a test API call to validate credentials. Returns (ok, message)."""
    import httpx

    if platform == "wordpress":
        site_url = creds.get("site_url", "").rstrip("/")
        username = creds.get("username", "")
        app_password = creds.get("app_password", "")
        if not _is_safe_url(site_url):
            return False, "L'URL doit utiliser HTTPS et pointer vers un hôte public."
        try:
            r = httpx.get(
                f"{site_url}/wp-json/wp/v2/users/me",
                auth=(username, app_password),
                timeout=10,
            )
            if r.status_code == 200:
                name = r.json().get("name", username)
                return True, f"Connecté en tant que {name}"
            return False, f"HTTP {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    elif platform == "facebook":
        token = creds.get("page_access_token", "")
        page_id = creds.get("page_id", "")
        try:
            r = httpx.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"access_token": token, "fields": "name"},
                timeout=10,
            )
            if r.status_code == 200:
                return True, r.json().get("name", "OK")
            return False, f"HTTP {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    elif platform == "instagram":
        token = creds.get("page_access_token", "")
        try:
            r = httpx.get(
                "https://graph.facebook.com/v19.0/me/accounts",
                params={"access_token": token},
                timeout=10,
            )
            if r.status_code == 200:
                return True, "Token valide"
            return False, f"HTTP {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    elif platform == "linkedin":
        token = creds.get("access_token", "")
        try:
            r = httpx.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if r.status_code == 200:
                return True, r.json().get("name", "OK")
            return False, f"HTTP {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    return False, "Plateforme non supportée"


def _html_to_text(html: str) -> str:
    """Crude HTML to plain text conversion for social platforms."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
