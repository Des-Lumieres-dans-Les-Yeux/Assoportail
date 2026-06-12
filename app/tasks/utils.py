"""Shared utilities for Celery task modules and mailing routes."""

from __future__ import annotations


def public_url(endpoint: str, **values) -> str:
    """Build a public-facing URL using LINKS_EXTERNAL_URL as the host.

    Use this for links sent to external users (public forms: feedback,
    signalement, tombola, volunteer confirmation…).
    Falls back to TASK_BASE_URL if LINKS_EXTERNAL_URL is not set.

    Unlike url_for(_external=True) which uses TASK_BASE_URL (the portal host),
    this function uses the public subdomain so recipients click the right domain.
    """
    from flask import current_app, url_for

    cfg = current_app.config
    base = (cfg.get("LINKS_EXTERNAL_URL") or cfg.get("TASK_BASE_URL", "http://localhost")).rstrip(
        "/"
    )
    return base + url_for(endpoint, **values)


def make_qr_img_tag(url: str, size_px: int = 150) -> str:
    """Return an inline HTML <img> tag containing a base64-encoded PNG QR code.

    Args:
        url: The URL to encode in the QR code.
        size_px: Rendered width/height in pixels (default 150).
    """
    import base64
    import io

    import qrcode

    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return (
        f'<img src="data:image/png;base64,{b64}"'
        f' width="{size_px}" height="{size_px}"'
        f' style="display:block;" alt="QR code">'
    )
