"""Documents blueprint routes — gallery, upload, download, delete."""

import logging
import os
import re
from datetime import date

from flask import (
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.blueprints.documents import bp
from app.blueprints.documents.forms import DocumentUploadForm
from app.decorators import bureau_required
from app.extensions import db, limiter
from app.models.document import (
    Document,
    DocumentType,
    center_documents,
    center_feedback_documents,
    event_documents,
    expense_documents,
    machine_documents,
    maintenance_documents,
    meeting_documents,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Upload validation constants
# ---------------------------------------------------------------------------

# Allowed file extensions mapped to expected MIME type(s)
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".docx": "application/zip",
    ".xlsx": "application/zip",
    ".odt": "application/zip",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}

_PHOTO_MIMES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_VIDEO_MIMES = frozenset({"video/mp4", "video/webm"})

# Entity type → (junction table, FK column name)
_ENTITY_MAP = {
    "event": (event_documents, "event_id"),
    "machine": (machine_documents, "machine_id"),
    "center": (center_documents, "center_id"),
    "meeting": (meeting_documents, "meeting_id"),
    "expense": (expense_documents, "expense_id"),
    "maintenance": (maintenance_documents, "maintenance_record_id"),
    "center_feedback": (center_feedback_documents, "center_feedback_id"),
}


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def gallery():
    """Render the document gallery."""
    from app.models.event import Event

    type_filter = request.args.get("type", "")
    stmt = (
        db.select(Document)
        .options(db.orm.selectinload(Document.uploaded_by))
        .order_by(Document.uploaded_at.desc())
    )
    if type_filter in {e.value for e in DocumentType}:
        stmt = stmt.where(Document.type == type_filter)
    documents = db.session.scalars(stmt).all()
    events = db.session.scalars(db.select(Event).order_by(Event.event_date.desc()).limit(100)).all()
    return render_template(
        "documents/gallery.html",
        documents=documents,
        type_filter=type_filter,
        DocumentType=DocumentType,
        events=events,
    )


# ---------------------------------------------------------------------------
# Upload — bureau only
# ---------------------------------------------------------------------------


@bp.route("/upload", methods=["GET", "POST"])
@bureau_required
def upload():
    """Upload a new document."""
    form = DocumentUploadForm()

    if form.validate_on_submit():
        file = form.file.data
        # Keep the raw filename for display; secure_filename is used only for
        # path generation, not for the value stored in original_filename.
        raw_name = file.filename or ""
        safe_name = secure_filename(raw_name)
        if not safe_name:
            flash("Nom de fichier invalide.", "danger")
            return render_template("documents/upload.html", form=form)

        original_name = raw_name or safe_name
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            flash(
                f"Extension « {ext} » non autorisée. "
                f"Extensions acceptées : {', '.join(sorted(_ALLOWED_EXTENSIONS))}.",
                "danger",
            )
            return render_template("documents/upload.html", form=form)

        data = file.read()
        detected_mime = _detect_mime(data)
        expected_mime = _ALLOWED_EXTENSIONS[ext]

        if detected_mime != expected_mime:
            flash(
                "Le contenu du fichier ne correspond pas à son extension (vérification MIME).",
                "danger",
            )
            return render_template("documents/upload.html", form=form)

        size_limit = _get_size_limit(detected_mime)
        if len(data) > size_limit:
            limit_mb = size_limit // (1024 * 1024)
            flash(f"Le fichier dépasse la limite de {limit_mb} Mo pour ce type.", "danger")
            return render_template("documents/upload.html", form=form)

        doc_type = form.type.data
        stored_name = _make_stored_filename(safe_name, doc_type)
        doc_record = Document(
            original_filename=original_name,
            stored_filename=stored_name,
            type=doc_type,
            category=(form.category.data or "").strip() or None,
            mime_type=detected_mime,
            size_bytes=len(data),
            uploaded_by_id=current_user.id,
            description=(form.description.data or "").strip() or None,
        )
        db.session.add(doc_record)
        db.session.flush()  # obtain doc_record.id before junction insert

        # Optionally link to an entity
        entity_type = form.entity_type.data or ""
        entity_id_str = form.entity_id.data or ""
        if entity_type in _ENTITY_MAP and entity_id_str.isdigit():
            junction_table, fk_col = _ENTITY_MAP[entity_type]
            db.session.execute(
                junction_table.insert().values(
                    {fk_col: int(entity_id_str), "document_id": doc_record.id}
                )
            )

        # Try to upload to Google Drive if configured
        drive_uploaded = False
        if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
            try:
                from app.services.drive import DriveService

                drive_svc = DriveService.from_db()
                file_id, web_link = drive_svc.upload_file(
                    data,
                    original_name,
                    detected_mime,
                    doc_type,
                    year=date.today().year,
                )
                doc_record.drive_file_id = file_id
                doc_record.drive_web_link = web_link
                drive_uploaded = True
            except Exception as exc:
                logger.warning("Drive upload failed, falling back to disk: %s", exc)

        if not drive_uploaded:
            subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc_record.subdir)
            os.makedirs(subdir, exist_ok=True)
            save_path = os.path.join(subdir, stored_name)
            with open(save_path, "wb") as fh:
                fh.write(data)

        db.session.commit()
        flash(f"Fichier « {original_name} » téléversé.", "success")
        return redirect(url_for("documents.gallery"))

    return render_template("documents/upload.html", form=form)


# ---------------------------------------------------------------------------
# Media upload — any member, photos/videos only, must link to an event
# ---------------------------------------------------------------------------


def _process_media_upload(
    file,
    entity_id: int | None,
    description: str | None,
) -> tuple[bool, str | None]:
    """Valide et enregistre un fichier média (photo ou vidéo) uploadé par un membre.

    Effectue toutes les validations (nom, extension, MIME, type photo/vidéo, taille),
    crée le ``Document``, le lie à l'événement si ``entity_id`` est fourni, et gère
    l'upload sur Google Drive (avec fallback disque).  Le commit DB est laissé au
    caller.

    Args:
        file: Objet ``FileStorage`` werkzeug avec un ``filename`` non vide.
        entity_id: Identifiant de l'événement auquel lier le document, ou ``None``.
        description: Description libre du document, ou ``None``.

    Returns:
        ``(True, None)`` si le document a été ajouté, ``(False, message)`` sinon.
    """
    raw_name = file.filename
    safe_name = secure_filename(raw_name)
    if not safe_name:
        return False, f"« {raw_name} » : nom invalide."

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return False, f"« {raw_name} » : extension non autorisée."

    data = file.read()
    detected_mime = _detect_mime(data)
    if detected_mime != _ALLOWED_EXTENSIONS[ext]:
        return False, f"« {raw_name} » : contenu invalide (vérification MIME)."

    if detected_mime not in (_PHOTO_MIMES | _VIDEO_MIMES):
        return False, f"« {raw_name} » : seuls les photos et vidéos sont autorisés."

    size_limit = _get_size_limit(detected_mime)
    if len(data) > size_limit:
        limit_mb = size_limit // (1024 * 1024)
        return False, f"« {raw_name} » : dépasse la limite de {limit_mb} Mo."

    doc_type = (
        DocumentType.VIDEO.value if detected_mime in _VIDEO_MIMES else DocumentType.PHOTO.value
    )
    prefix = f"evt{entity_id}" if entity_id is not None else ""
    stored_name = _make_stored_filename(safe_name, doc_type, prefix=prefix)

    doc_record = Document(
        original_filename=raw_name,
        stored_filename=stored_name,
        type=doc_type,
        mime_type=detected_mime,
        size_bytes=len(data),
        uploaded_by_id=current_user.id,
        description=description,
    )
    db.session.add(doc_record)
    db.session.flush()

    if entity_id is not None:
        db.session.execute(
            event_documents.insert().values(event_id=entity_id, document_id=doc_record.id)
        )

    drive_uploaded = False
    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
        try:
            from app.services.drive import DriveService

            file_id, web_link = DriveService.from_db().upload_file(
                data, raw_name, detected_mime, doc_type, year=date.today().year
            )
            doc_record.drive_file_id = file_id
            doc_record.drive_web_link = web_link
            drive_uploaded = True
        except Exception as exc:
            logger.warning(
                "Drive upload failed for media « %s », falling back to disk: %s",
                raw_name,
                exc,
            )

    if not drive_uploaded:
        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc_record.subdir)
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, stored_name), "wb") as fh:
            fh.write(data)

    return True, None


@bp.route("/upload-media", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def upload_media():
    """Permet à tout membre d'uploader une photo ou vidéo attachée à un événement."""
    entity_id_str = request.form.get("entity_id", "")
    if not entity_id_str.isdigit():
        abort(400)
    entity_id = int(entity_id_str)

    from app.models.event import Event

    if db.session.get(Event, entity_id) is None:
        abort(404)

    back_url = url_for("events.detail", event_id=entity_id)

    files = request.files.getlist("file")
    if not files or all(not f.filename for f in files):
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(back_url)

    description = request.form.get("description", "").strip() or None
    added = 0
    upload_errors: list[str] = []

    for file in files:
        if not file.filename:
            continue
        ok, err = _process_media_upload(file, entity_id, description)
        if ok:
            added += 1
        else:
            upload_errors.append(err)

    db.session.commit()
    if added:
        msg = f"{added} média{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''}."
        flash(msg, "success")
    for e in upload_errors:
        flash(e, "danger")
    return redirect(back_url)


# ---------------------------------------------------------------------------
# Gallery media upload — any member, entity_id optionnel
# ---------------------------------------------------------------------------


@bp.route("/upload-gallery-media", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def upload_gallery_media():
    """Permet à tout membre d'uploader des photos/vidéos depuis la galerie.

    L'association à un événement est optionnelle : si ``entity_id`` est fourni et
    correspond à un événement existant, le document y est lié ; sinon il est créé
    sans rattachement.
    """
    from app.models.event import Event

    entity_id: int | None = None
    entity_id_str = request.form.get("entity_id", "").strip()
    if entity_id_str.isdigit():
        candidate = int(entity_id_str)
        if db.session.get(Event, candidate) is not None:
            entity_id = candidate

    files = request.files.getlist("file")
    if not files or all(not f.filename for f in files):
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("documents.gallery"))

    description = request.form.get("description", "").strip() or None
    added = 0
    upload_errors: list[str] = []

    for file in files:
        if not file.filename:
            continue
        ok, err = _process_media_upload(file, entity_id, description)
        if ok:
            added += 1
        else:
            upload_errors.append(err)

    db.session.commit()
    if added:
        msg = f"{added} média{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''}."
        flash(msg, "success")
    for e in upload_errors:
        flash(e, "danger")
    return redirect(url_for("documents.gallery"))


# ---------------------------------------------------------------------------
# Download / serve
# ---------------------------------------------------------------------------


@bp.route("/<int:document_id>")
@login_required
def download(document_id: int):
    """Serve a document file to authenticated users."""
    doc = db.session.get(Document, document_id)
    if doc is None:
        abort(404)

    # Proxy from Drive if the file was stored there
    if doc.drive_file_id:
        try:
            from app.services.drive import DriveService

            data = DriveService.from_db().download_file(doc.drive_file_id)
            return Response(
                data,
                mimetype=doc.mime_type or "application/octet-stream",
                headers={"Cache-Control": "private, max-age=3600"},
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to proxy Drive file %s", doc.drive_file_id
            )
            abort(502)

    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], doc.subdir, doc.stored_filename)
    real_path = os.path.realpath(file_path)
    upload_folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    if not real_path.startswith(upload_folder + os.sep):
        abort(403)
    if not os.path.isfile(file_path):
        abort(404)

    return send_file(file_path, download_name=doc.original_filename, as_attachment=False)


# ---------------------------------------------------------------------------
# Delete — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:document_id>/delete", methods=["POST"])
@bureau_required
def delete(document_id: int):
    """Delete a document record and its file from disk."""
    doc = db.session.get(Document, document_id)
    if doc is None:
        abort(404)

    drive_file_id = doc.drive_file_id
    stored_filename = doc.stored_filename
    subdir = doc.subdir
    original_name = doc.original_filename

    db.session.delete(doc)
    db.session.commit()

    # Delete from Drive if stored there
    if drive_file_id:
        try:
            from app.services.drive import DriveService

            DriveService.from_db().delete_file(drive_file_id)
        except Exception as exc:
            logger.warning("Could not delete Drive file %s: %s", drive_file_id, exc)
    else:
        file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], subdir, stored_filename)
        real_path = os.path.realpath(file_path)
        upload_folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
        if real_path.startswith(upload_folder + os.sep) and os.path.isfile(file_path):
            os.remove(file_path)

    flash(f"Document « {original_name} » supprimé.", "success")
    return redirect(url_for("documents.gallery"))


# ---------------------------------------------------------------------------
# Event file upload — bureau only, PDFs / office docs
# ---------------------------------------------------------------------------

_ATTACHMENT_EXTS = frozenset({".pdf", ".docx", ".xlsx", ".odt"})


@bp.route("/upload-event-file", methods=["POST"])
@bureau_required
@limiter.limit("20 per hour")
def upload_event_file():
    """Bureau only: upload a PDF or document attached to an event."""
    event_id_str = request.form.get("event_id", "")
    if not event_id_str.isdigit():
        abort(400)
    event_id = int(event_id_str)

    from app.models.event import Event

    if db.session.get(Event, event_id) is None:
        abort(404)

    back_url = url_for("events.detail", event_id=event_id)

    file = request.files.get("file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(back_url)

    raw_name = file.filename
    safe_name = secure_filename(raw_name)
    if not safe_name:
        flash("Nom de fichier invalide.", "danger")
        return redirect(back_url)

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _ATTACHMENT_EXTS:
        flash("Seuls les fichiers PDF, DOCX, XLSX et ODT sont autorisés.", "danger")
        return redirect(back_url)

    data = file.read()
    detected_mime = _detect_mime(data)
    expected_mime = _ALLOWED_EXTENSIONS.get(ext)
    if detected_mime != expected_mime:
        msg = "Le contenu du fichier ne correspond pas à son extension (vérif MIME)."
        flash(msg, "danger")
        return redirect(back_url)

    size_limit = _get_size_limit(detected_mime)
    if len(data) > size_limit:
        size_mb = size_limit // (1024 * 1024)
        flash(f"Le fichier dépasse la limite de {size_mb} Mo.", "danger")
        return redirect(back_url)

    description = request.form.get("description", "").strip() or None
    stored_name = _make_stored_filename(
        safe_name, DocumentType.OTHER.value, prefix=f"evt{event_id}"
    )
    doc_record = Document(
        original_filename=raw_name,
        stored_filename=stored_name,
        type=DocumentType.OTHER.value,
        mime_type=detected_mime,
        size_bytes=len(data),
        uploaded_by_id=current_user.id,
        description=description,
    )
    db.session.add(doc_record)
    db.session.flush()

    db.session.execute(
        event_documents.insert().values(event_id=event_id, document_id=doc_record.id)
    )

    drive_uploaded = False
    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
        try:
            from app.services.drive import DriveService

            file_id, web_link = DriveService.from_db().upload_file(
                data,
                raw_name,
                detected_mime,
                DocumentType.OTHER.value,
                year=date.today().year,
            )
            doc_record.drive_file_id = file_id
            doc_record.drive_web_link = web_link
            drive_uploaded = True
        except Exception as exc:
            logger.warning(
                "Drive upload failed for event attachment, falling back to disk: %s", exc
            )

    if not drive_uploaded:
        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc_record.subdir)
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, stored_name), "wb") as fh:
            fh.write(data)

    db.session.commit()
    flash(f"Fichier « {raw_name} » ajouté à l'événement.", "success")
    return redirect(back_url)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_mime(data: bytes) -> str | None:
    """Return the MIME type based on file magic bytes, or None if unknown."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] == b"PK":  # ZIP archive: covers .docx, .xlsx, .odt
        return "application/zip"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def _get_size_limit(mime_type: str | None) -> int:
    """Return the upload size limit in bytes for the given MIME type."""
    cfg = current_app.config
    if mime_type in _PHOTO_MIMES:
        return cfg["MAX_UPLOAD_PHOTO"]
    if mime_type in _VIDEO_MIMES:
        return cfg["MAX_UPLOAD_VIDEO"]
    return cfg["MAX_UPLOAD_DOCUMENT"]


def _make_stored_filename(original: str, doc_type: str, prefix: str = "") -> str:
    """Generate a sanitised, unique stored filename.

    Format: ``YYYY-MM-DD_<type>_[<prefix>_]<slug>.<ext>``

    Uniqueness is checked against both the filesystem and the DB, so the same
    file can be attached to multiple entities without collision.
    """
    name, ext = os.path.splitext(original)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "file"
    today = date.today().isoformat()
    prefix_part = f"{prefix}_" if prefix else ""
    base = f"{today}_{doc_type}_{prefix_part}{slug}{ext.lower()}"

    subdir = {
        DocumentType.PHOTO.value: "photos",
        DocumentType.VIDEO.value: "videos",
        DocumentType.INVOICE.value: "invoices",
        DocumentType.REPORT.value: "reports",
        DocumentType.CONTRACT.value: "contracts",
        DocumentType.OTHER.value: "documents",
    }.get(doc_type, "documents")
    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], subdir)

    candidate = base
    counter = 1
    while (
        os.path.exists(os.path.join(upload_dir, candidate))
        or db.session.scalar(db.select(Document.id).where(Document.stored_filename == candidate))
        is not None
    ):
        name_part, ext_part = os.path.splitext(base)
        candidate = f"{name_part}_{counter}{ext_part}"
        counter += 1

    return candidate
