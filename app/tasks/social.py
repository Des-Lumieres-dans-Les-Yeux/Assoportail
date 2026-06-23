"""Celery tasks — social publishing (WordPress, Facebook, Instagram, LinkedIn).

Image processing runs first, then each platform is published independently.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime

from celery import shared_task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@shared_task(
    name="tasks.publish_social_post",
    bind=True,
    max_retries=0,
    time_limit=600,
    soft_time_limit=570,
)
def publish_social_post(self, post_id: int) -> dict:
    """Process images and publish a social post to all selected platforms."""
    from app.extensions import db
    from app.models.social import (
        PublishLogStatus,
        SocialPost,
        SocialPostStatus,
        SocialPublishLog,
    )

    post = db.session.get(SocialPost, post_id)
    if post is None:
        logger.error("Social post %d not found", post_id)
        return {"error": "not_found"}

    if post.status not in (
        SocialPostStatus.DRAFT.value,
        SocialPostStatus.SCHEDULED.value,
    ):
        logger.info("Post %d already in status %r", post_id, post.status)
        return {"skipped": True}

    post.status = SocialPostStatus.PUBLISHING.value
    db.session.commit()

    # 1. Process images
    try:
        _process_post_images(post_id)
    except Exception:
        logger.exception("Image processing failed for post %d", post_id)

    # 2. Publish to each platform
    results: dict[str, str] = {}
    for platform in post.platforms:
        log = SocialPublishLog(
            post_id=post_id,
            platform=platform,
            status=PublishLogStatus.PENDING.value,
        )
        db.session.add(log)
        db.session.flush()

        try:
            result = _dispatch_publish(post_id, platform)
            log.status = PublishLogStatus.SUCCESS.value
            log.remote_id = result.get("remote_id")
            log.remote_url = result.get("remote_url")
            results[platform] = "success"
        except Exception as exc:
            logger.exception("Publish to %s failed for post %d", platform, post_id)
            log.status = PublishLogStatus.FAILED.value
            log.error_message = str(exc)[:1000]
            results[platform] = "failed"

        db.session.commit()

    # 3. Determine final status
    post = db.session.get(SocialPost, post_id)
    statuses = set(results.values())
    if statuses == {"success"}:
        post.status = SocialPostStatus.PUBLISHED.value
    elif statuses == {"failed"}:
        post.status = SocialPostStatus.FAILED.value
    else:
        post.status = SocialPostStatus.PARTIAL.value
    db.session.commit()

    return results


# ---------------------------------------------------------------------------
# Per-platform retry task
# ---------------------------------------------------------------------------


@shared_task(
    name="tasks.publish_to_platform",
    bind=True,
    max_retries=3,
    time_limit=120,
    soft_time_limit=100,
)
def publish_to_platform(self, post_id: int, platform: str) -> dict:
    """Publish (or retry) a single platform for a post."""
    from app.extensions import db
    from app.models.social import (
        PublishLogStatus,
        SocialPost,
        SocialPublishLog,
    )

    post = db.session.get(SocialPost, post_id)
    if post is None:
        return {"error": "not_found"}

    log = SocialPublishLog(
        post_id=post_id,
        platform=platform,
        status=PublishLogStatus.PENDING.value,
    )
    db.session.add(log)
    db.session.flush()

    try:
        result = _dispatch_publish(post_id, platform)
        log.status = PublishLogStatus.SUCCESS.value
        log.remote_id = result.get("remote_id")
        log.remote_url = result.get("remote_url")
        db.session.commit()

        # Update post status if it was partial/failed
        _update_post_status(post_id)
        return result
    except Exception as exc:
        log.status = PublishLogStatus.FAILED.value
        log.error_message = str(exc)[:1000]
        log.retry_count = self.request.retries
        db.session.commit()
        raise self.retry(exc=exc) from exc


# ---------------------------------------------------------------------------
# Scheduled posts checker (celery-beat)
# ---------------------------------------------------------------------------


@shared_task(
    name="tasks.publish_scheduled_social_posts",
    time_limit=60,
    soft_time_limit=50,
)
def publish_scheduled_social_posts() -> dict:
    """Check for posts due for scheduled publishing and dispatch them."""
    from app.extensions import db
    from app.models.social import SocialPost, SocialPostStatus

    now = datetime.now(UTC)
    posts = db.session.scalars(
        db.select(SocialPost).where(
            SocialPost.status == SocialPostStatus.SCHEDULED.value,
            SocialPost.scheduled_at <= now,
        )
    ).all()

    dispatched = 0
    for post in posts:
        publish_social_post.delay(post.id)
        dispatched += 1

    return {"dispatched": dispatched}


# ---------------------------------------------------------------------------
# Image processing
# ---------------------------------------------------------------------------


def _process_post_images(post_id: int) -> int:
    """Crop and resize all images for a post. Returns count of processed files."""
    from flask import current_app
    from sqlalchemy.orm import selectinload

    from app.extensions import db
    from app.models.social import SocialPost, SocialPostProcessedImage
    from app.services.image_processor import process_image

    post = db.session.get(
        SocialPost,
        post_id,
        options=[selectinload(SocialPost.images)],
    )
    if not post:
        return 0

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    quality = current_app.config.get("SOCIAL_IMAGE_QUALITY", 85)
    out_dir = os.path.join(upload_folder, "social", "processed", str(post_id))
    os.makedirs(out_dir, exist_ok=True)

    count = 0
    for img in post.images:
        # Load original bytes
        original_bytes = _load_image_bytes(img, upload_folder)
        if not original_bytes:
            logger.warning("Could not load image %d for post %d", img.id, post_id)
            continue

        crop_data = img.crop_data or {}

        # Determine which specs to generate
        specs_to_generate = _specs_for_platforms(
            post.platforms, img.is_featured, post.instagram_format
        )

        for spec_name in specs_to_generate:
            crop_rect = crop_data.get(spec_name)
            try:
                out_bytes, w, h, mime = process_image(original_bytes, crop_rect, spec_name, quality)
            except Exception:
                logger.exception(
                    "Failed to process image %d for spec %s",
                    img.id,
                    spec_name,
                )
                continue

            fname = f"{spec_name}_{secrets.token_hex(6)}.jpg"
            fpath = os.path.join(out_dir, fname)
            with open(fpath, "wb") as f:
                f.write(out_bytes)

            processed = SocialPostProcessedImage(
                post_image_id=img.id,
                platform=spec_name,
                stored_filename=fname,
                width=w,
                height=h,
                size_bytes=len(out_bytes),
                mime_type=mime,
            )
            db.session.add(processed)
            count += 1

    db.session.commit()
    return count


def _load_image_bytes(img, upload_folder: str) -> bytes | None:
    """Load original image bytes from disk or Document."""
    if img.document_id:
        # Gallery image — load from document storage
        from app.extensions import db
        from app.models.document import Document

        doc = db.session.get(Document, img.document_id)
        if doc and doc.stored_filename:
            path = os.path.join(upload_folder, doc.subdir, doc.stored_filename)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    return f.read()
    else:
        # Direct upload
        path = os.path.join(upload_folder, "social", "originals", img.stored_filename)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
    return None


def _specs_for_platforms(
    platforms: list[str],
    is_featured: bool,
    ig_format: str,
) -> list[str]:
    """Return the list of spec names needed for the given platforms."""
    specs: list[str] = []
    for p in platforms:
        if p == "wordpress":
            specs.append("wordpress_featured" if is_featured else "wordpress_gallery")
        elif p == "facebook":
            specs.append("facebook")
        elif p == "instagram":
            specs.append(f"instagram_{ig_format}")
        elif p == "linkedin":
            specs.append("linkedin")
    return specs


# ---------------------------------------------------------------------------
# Platform dispatchers
# ---------------------------------------------------------------------------


def _dispatch_publish(post_id: int, platform: str) -> dict:
    """Call the appropriate platform publisher. Returns {remote_id, remote_url}."""
    if platform == "wordpress":
        return _publish_wordpress(post_id)
    elif platform == "facebook":
        return _publish_facebook(post_id)
    elif platform == "instagram":
        return _publish_instagram(post_id)
    elif platform == "linkedin":
        return _publish_linkedin(post_id)
    raise ValueError(f"Unsupported platform: {platform}")


def _get_account_creds(platform: str) -> dict:
    """Load and decrypt credentials for a platform."""
    from app.extensions import db
    from app.models.social import SocialAccount
    from app.services.gmail import decrypt_token

    account = db.session.execute(
        db.select(SocialAccount).where(
            SocialAccount.platform == platform,
            SocialAccount.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if account is None:
        raise RuntimeError(f"No active {platform} account configured")
    return decrypt_token(account.credentials_encrypted)


# ---------------------------------------------------------------------------
# WordPress
# ---------------------------------------------------------------------------


def _publish_wordpress(post_id: int) -> dict:
    """Publish to WordPress via REST API v2.

    Uploads all images to WP Media Library, sets the featured image,
    and appends a gallery of all images at the end of the post content.
    """
    import httpx
    from sqlalchemy.orm import selectinload

    from app.extensions import db
    from app.models.social import SocialPost, SocialPostProcessedImage

    creds = _get_account_creds("wordpress")
    site_url = creds["site_url"].rstrip("/")
    auth = (creds["username"], creds["app_password"])

    post = db.session.get(SocialPost, post_id, options=[selectinload(SocialPost.images)])

    # Upload ALL images to WordPress Media Library
    featured_media_id = None
    gallery_urls: list[str] = []

    for img in post.images:
        processed = db.session.execute(
            db.select(SocialPostProcessedImage).where(
                SocialPostProcessedImage.post_image_id == img.id,
                SocialPostProcessedImage.platform.like("wordpress%"),
            )
        ).scalar_one_or_none()
        if not processed:
            continue

        img_bytes = _load_processed_bytes(post_id, processed.stored_filename)
        if not img_bytes:
            continue

        try:
            r = httpx.post(
                f"{site_url}/wp-json/wp/v2/media",
                auth=auth,
                content=img_bytes,
                headers={
                    "Content-Type": "image/jpeg",
                    "Content-Disposition": (f'attachment; filename="{processed.stored_filename}"'),
                },
                timeout=30,
            )
            r.raise_for_status()
            media_data = r.json()
            media_id = media_data["id"]
            media_url = media_data.get("source_url", "")

            if img.is_featured:
                featured_media_id = media_id

            if media_url:
                alt = img.original_filename
                gallery_urls.append(
                    f'<figure class="wp-block-image"><img src="{media_url}" alt="{alt}"/></figure>'
                )
        except Exception:
            logger.exception("Failed to upload image %s to WordPress", img.original_filename)

    # Build content with gallery appended
    content = post.body_html
    if gallery_urls:
        content += "\n\n" + "\n".join(gallery_urls)

    # Create post
    payload: dict = {
        "title": post.title,
        "content": content,
        "status": "publish",
    }
    if featured_media_id:
        payload["featured_media"] = featured_media_id

    r = httpx.post(
        f"{site_url}/wp-json/wp/v2/posts",
        auth=auth,
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return {
        "remote_id": str(data["id"]),
        "remote_url": data.get("link", ""),
    }


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


def _publish_facebook(post_id: int) -> dict:
    """Publish to Facebook Page via Graph API."""
    import httpx
    from sqlalchemy.orm import selectinload

    from app.extensions import db
    from app.models.social import SocialPost

    creds = _get_account_creds("facebook")
    page_id = creds["page_id"]
    token = creds["page_access_token"]

    post = db.session.get(SocialPost, post_id, options=[selectinload(SocialPost.images)])
    text = post.body_text or post.title

    # Upload images as unpublished photos
    photo_ids = []
    for img in post.images:
        from app.models.social import SocialPostProcessedImage

        processed = db.session.execute(
            db.select(SocialPostProcessedImage).where(
                SocialPostProcessedImage.post_image_id == img.id,
                SocialPostProcessedImage.platform == "facebook",
            )
        ).scalar_one_or_none()
        if not processed:
            continue
        img_bytes = _load_processed_bytes(post_id, processed.stored_filename)
        if not img_bytes:
            continue

        r = httpx.post(
            f"https://graph.facebook.com/v19.0/{page_id}/photos",
            data={"access_token": token, "published": "false"},
            files={"source": (processed.stored_filename, img_bytes, "image/jpeg")},
            timeout=30,
        )
        r.raise_for_status()
        photo_ids.append(r.json()["id"])

    if photo_ids:
        # Multi-photo post
        data: dict = {
            "message": text,
            "access_token": token,
        }
        for i, pid in enumerate(photo_ids):
            data[f"attached_media[{i}]"] = f'{{"media_fbid":"{pid}"}}'

        r = httpx.post(
            f"https://graph.facebook.com/v19.0/{page_id}/feed",
            data=data,
            timeout=30,
        )
        r.raise_for_status()
        fb_id = r.json().get("id", "")
        return {
            "remote_id": fb_id,
            "remote_url": f"https://www.facebook.com/{fb_id}" if fb_id else "",
        }
    else:
        # Text-only post
        r = httpx.post(
            f"https://graph.facebook.com/v19.0/{page_id}/feed",
            data={"message": text, "access_token": token},
            timeout=30,
        )
        r.raise_for_status()
        fb_id = r.json().get("id", "")
        return {
            "remote_id": fb_id,
            "remote_url": f"https://www.facebook.com/{fb_id}" if fb_id else "",
        }


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------


def _publish_instagram(post_id: int) -> dict:
    """Publish to Instagram via Graph API (requires public image URLs)."""
    import httpx
    from sqlalchemy.orm import selectinload

    from app.extensions import db
    from app.models.social import SocialPost, SocialPostProcessedImage

    creds = _get_account_creds("instagram")
    token = creds["page_access_token"]
    ig_user_id = creds.get("ig_user_id")

    if not ig_user_id:
        raise RuntimeError("Instagram non configuré. Reconnectez Facebook via /social/accounts.")

    post = db.session.get(SocialPost, post_id, options=[selectinload(SocialPost.images)])
    caption = post.body_text or post.title

    # For Instagram, we need publicly accessible image URLs.
    # We generate temporary signed URLs via the portal's document serve endpoint.

    container_ids = []
    for img in post.images:
        ig_spec = f"instagram_{post.instagram_format}"
        processed = db.session.execute(
            db.select(SocialPostProcessedImage).where(
                SocialPostProcessedImage.post_image_id == img.id,
                SocialPostProcessedImage.platform == ig_spec,
            )
        ).scalar_one_or_none()
        if not processed:
            continue

        # Upload via Facebook photo endpoint and use the URL
        img_bytes = _load_processed_bytes(post_id, processed.stored_filename)
        if not img_bytes:
            continue

        # Upload to Facebook as unpublished to get a URL
        r = httpx.post(
            f"https://graph.facebook.com/v19.0/{creds['page_id']}/photos",
            data={"access_token": token, "published": "false"},
            files={"source": (processed.stored_filename, img_bytes, "image/jpeg")},
            timeout=30,
        )
        r.raise_for_status()
        fb_photo_id = r.json()["id"]

        # Get the image URL from the photo
        r2 = httpx.get(
            f"https://graph.facebook.com/v19.0/{fb_photo_id}",
            params={"fields": "images", "access_token": token},
            timeout=10,
        )
        r2.raise_for_status()
        images = r2.json().get("images", [])
        if not images:
            continue
        image_url = images[0]["source"]  # Largest version

        # Create Instagram media container
        r3 = httpx.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media",
            data={
                "image_url": image_url,
                "caption": caption if not container_ids else "",
                "access_token": token,
            },
            timeout=30,
        )
        r3.raise_for_status()
        container_ids.append(r3.json()["id"])

    if not container_ids:
        raise RuntimeError("No images could be uploaded for Instagram")

    if len(container_ids) == 1:
        creation_id = container_ids[0]
    else:
        # Carousel
        r = httpx.post(
            f"https://graph.facebook.com/v19.0/{ig_user_id}/media",
            data={
                "media_type": "CAROUSEL",
                "children": ",".join(container_ids),
                "caption": caption,
                "access_token": token,
            },
            timeout=30,
        )
        r.raise_for_status()
        creation_id = r.json()["id"]

    # Wait for container to be ready (Instagram processes asynchronously)
    import time

    for attempt in range(30):
        r_status = httpx.get(
            f"https://graph.facebook.com/v19.0/{creation_id}",
            params={
                "fields": "status_code",
                "access_token": token,
            },
            timeout=10,
        )
        if r_status.status_code == 200:
            status_code = r_status.json().get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                raise RuntimeError(f"Instagram container error: {r_status.json()}")
        logger.debug(
            "Waiting for IG container %s (attempt %d, status=%s)",
            creation_id,
            attempt + 1,
            r_status.json().get("status_code", "unknown"),
        )
        time.sleep(2)
    else:
        raise RuntimeError(f"Instagram container {creation_id} not ready after 60s")

    # Publish
    r = httpx.post(
        f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish",
        data={
            "creation_id": creation_id,
            "access_token": token,
        },
        timeout=30,
    )
    r.raise_for_status()
    ig_media_id = r.json().get("id", "")

    # Get permalink
    remote_url = ""
    try:
        r2 = httpx.get(
            f"https://graph.facebook.com/v19.0/{ig_media_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=10,
        )
        if r2.status_code == 200:
            remote_url = r2.json().get("permalink", "")
    except Exception:
        logger.debug("Failed to fetch Instagram permalink for media %s", ig_media_id, exc_info=True)

    return {"remote_id": ig_media_id, "remote_url": remote_url}


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------


def _publish_linkedin(post_id: int) -> dict:
    """Publish to LinkedIn Organization page via Marketing API."""
    import httpx
    from flask import current_app
    from sqlalchemy.orm import selectinload

    from app.extensions import db
    from app.models.social import SocialPost, SocialPostProcessedImage

    creds = _get_account_creds("linkedin")
    token = creds["access_token"]
    org_id = creds.get("organization_id", "")
    api_version = current_app.config["LINKEDIN_API_VERSION"]

    post = db.session.get(SocialPost, post_id, options=[selectinload(SocialPost.images)])
    text = post.body_text or post.title

    # Upload images to LinkedIn
    image_urns = []
    for img in post.images:
        processed = db.session.execute(
            db.select(SocialPostProcessedImage).where(
                SocialPostProcessedImage.post_image_id == img.id,
                SocialPostProcessedImage.platform == "linkedin",
            )
        ).scalar_one_or_none()
        if not processed:
            continue
        img_bytes = _load_processed_bytes(post_id, processed.stored_filename)
        if not img_bytes:
            continue

        # Initialize upload
        r = httpx.post(
            "https://api.linkedin.com/rest/images",
            headers={
                "Authorization": f"Bearer {token}",
                "LinkedIn-Version": api_version,
                "Content-Type": "application/json",
            },
            json={
                "initializeUploadRequest": {
                    "owner": f"urn:li:organization:{org_id}",
                }
            },
            params={"action": "initializeUpload"},
            timeout=15,
        )
        r.raise_for_status()
        upload_data = r.json().get("value", {})
        upload_url = upload_data.get("uploadUrl", "")
        image_urn = upload_data.get("image", "")

        if upload_url and image_urn:
            # Upload binary
            r2 = httpx.put(
                upload_url,
                content=img_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "image/jpeg",
                },
                timeout=30,
            )
            r2.raise_for_status()
            image_urns.append(image_urn)

    # Create post
    payload: dict = {
        "author": f"urn:li:organization:{org_id}",
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
    }

    if image_urns:
        payload["content"] = {
            "multiImage": {
                "images": [{"id": urn} for urn in image_urns],
            }
        }

    r = httpx.post(
        "https://api.linkedin.com/rest/posts",
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": api_version,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    r.raise_for_status()

    # LinkedIn returns the post URN in the x-restli-id header
    post_urn = r.headers.get("x-restli-id", "")
    return {
        "remote_id": post_urn,
        "remote_url": "",  # LinkedIn doesn't return a direct URL
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_processed_bytes(post_id: int, filename: str) -> bytes | None:
    """Load processed image bytes from disk."""
    from flask import current_app

    path = os.path.join(
        current_app.config["UPLOAD_FOLDER"],
        "social",
        "processed",
        str(post_id),
        filename,
    )
    if os.path.isfile(path):
        with open(path, "rb") as f:
            return f.read()
    return None


def _update_post_status(post_id: int) -> None:
    """Recalculate post status from its publish logs."""
    from app.extensions import db
    from app.models.social import (
        PublishLogStatus,
        SocialPost,
        SocialPostStatus,
        SocialPublishLog,
    )

    post = db.session.get(SocialPost, post_id)
    if not post:
        return

    # Get latest log per platform
    latest_logs: dict[str, str] = {}
    for p in post.platforms:
        log = db.session.execute(
            db.select(SocialPublishLog)
            .where(
                SocialPublishLog.post_id == post_id,
                SocialPublishLog.platform == p,
            )
            .order_by(SocialPublishLog.attempted_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if log:
            latest_logs[p] = log.status

    statuses = set(latest_logs.values())
    if not statuses:
        return
    if statuses == {PublishLogStatus.SUCCESS.value}:
        post.status = SocialPostStatus.PUBLISHED.value
    elif PublishLogStatus.SUCCESS.value in statuses:
        post.status = SocialPostStatus.PARTIAL.value
    else:
        post.status = SocialPostStatus.FAILED.value
    db.session.commit()
