"""Server-side image cropping and resizing for social publishing.

Uses Pillow to apply crop coordinates and resize to platform-specific
dimensions.  All processing produces JPEG output at configurable quality.
"""

from __future__ import annotations

import logging
from io import BytesIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

# Target dimensions per platform variant
PLATFORM_SPECS: dict[str, dict[str, int]] = {
    "wordpress_featured": {"width": 1200, "height": 628},
    "wordpress_gallery": {"width": 2048, "height": 0},  # height=0 → proportional
    "facebook": {"width": 1200, "height": 630},
    "instagram_square": {"width": 1080, "height": 1080},
    "instagram_portrait": {"width": 1080, "height": 1350},
    "instagram_landscape": {"width": 1080, "height": 566},
    "linkedin": {"width": 1200, "height": 627},
}


def process_image(
    image_bytes: bytes,
    crop_rect: dict | None,
    spec_name: str,
    quality: int = 85,
) -> tuple[bytes, int, int, str]:
    """Crop and resize an image to a platform specification.

    Args:
        image_bytes: Raw bytes of the source image.
        crop_rect: ``{"x": int, "y": int, "width": int, "height": int}``
            or None for auto-crop.
        spec_name: Key in ``PLATFORM_SPECS`` (e.g. ``"facebook"``).
        quality: JPEG output quality (1-100).

    Returns:
        Tuple of (output_bytes, width, height, mime_type).
    """
    spec = PLATFORM_SPECS.get(spec_name)
    if spec is None:
        raise ValueError(f"Unknown spec: {spec_name}")

    img = Image.open(BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)  # Fix orientation from EXIF
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    target_w = spec["width"]
    target_h = spec["height"]

    # Apply crop
    if crop_rect and all(k in crop_rect for k in ("x", "y", "width", "height")):
        x = max(0, int(crop_rect["x"]))
        y = max(0, int(crop_rect["y"]))
        w = max(1, int(crop_rect["width"]))
        h = max(1, int(crop_rect["height"]))
        # Clamp to image bounds
        x = min(x, img.width - 1)
        y = min(y, img.height - 1)
        w = min(w, img.width - x)
        h = min(h, img.height - y)
        img = img.crop((x, y, x + w, y + h))
    elif target_h > 0:
        # Auto center-crop to target aspect ratio
        img = _auto_crop(img, target_w / target_h)

    # Resize
    if target_h > 0:
        img = img.resize((target_w, target_h), Image.LANCZOS)
    else:
        # Proportional resize (gallery variant)
        if img.width > target_w:
            ratio = target_w / img.width
            new_h = int(img.height * ratio)
            img = img.resize((target_w, new_h), Image.LANCZOS)

    # Output as JPEG
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    output_bytes = buf.getvalue()

    return output_bytes, img.width, img.height, "image/jpeg"


def auto_crop_rect(
    image_bytes: bytes,
    target_ratio: float,
) -> dict[str, int]:
    """Compute a center-crop rectangle for the given aspect ratio.

    Args:
        image_bytes: Raw image bytes.
        target_ratio: Desired width/height ratio (e.g. 1.91 for Facebook).

    Returns:
        ``{"x": int, "y": int, "width": int, "height": int}``
    """
    img = Image.open(BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    return _compute_center_crop(img.width, img.height, target_ratio)


def _auto_crop(img: Image.Image, target_ratio: float) -> Image.Image:
    """Center-crop an image to match a target aspect ratio."""
    rect = _compute_center_crop(img.width, img.height, target_ratio)
    return img.crop(
        (
            rect["x"],
            rect["y"],
            rect["x"] + rect["width"],
            rect["y"] + rect["height"],
        )
    )


def _compute_center_crop(w: int, h: int, target_ratio: float) -> dict[str, int]:
    """Compute a centered crop box for a given aspect ratio."""
    current_ratio = w / h
    if current_ratio > target_ratio:
        # Image is wider → crop sides
        new_w = int(h * target_ratio)
        x = (w - new_w) // 2
        return {"x": x, "y": 0, "width": new_w, "height": h}
    else:
        # Image is taller → crop top/bottom
        new_h = int(w / target_ratio)
        y = (h - new_h) // 2
        return {"x": 0, "y": y, "width": w, "height": new_h}
