"""Centers blueprint routes — management, breakdown reporting, guestbook."""

import hashlib
import hmac as _hmac
import logging
import os
import re
import secrets
from datetime import UTC, date, datetime

from flask import Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.blueprints.centers import bp
from app.blueprints.centers.forms import (
    BreakdownReportForm,
    CenterContactForm,
    CenterForm,
    FeedbackForm,
    InstallationRequestForm,
    InstallMachineForm,
)
from app.blueprints.documents.routes import _detect_mime
from app.decorators import bureau_required, permission_required
from app.extensions import db, limiter
from app.models.center import Center, CenterContact, CenterFeedback, CenterStatus
from app.models.document import Document, DocumentType, center_documents
from app.models.machine import Machine, MachineInstallation, MachineStatus, MaintenanceRecord
from app.models.task import Task, TaskSource, TaskStatus
from app.models.user import UserPermission
from app.services.csv_io import export_centers_csv, parse_centers_csv
from app.services.geocoding import geocode_address

_CONVENTION_EXTS = {
    ".pdf": "application/pdf",
    ".docx": "application/zip",
    ".odt": "application/zip",
}
_CONVENTION_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# List and detail
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def list_centers():
    """List centers with optional name/city search and status filter."""
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    stmt = (
        db.select(Center)
        .options(
            selectinload(Center.installations).selectinload(MachineInstallation.machine),
            selectinload(Center.contacts),
        )
        .order_by(Center.name)
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(db.or_(Center.name.ilike(pattern), Center.city.ilike(pattern)))
    if status and status in {s.value for s in CenterStatus}:
        stmt = stmt.where(Center.status == status)

    pagination = db.paginate(stmt, page=page, per_page=25, error_out=False)

    # Auto-sync: centres with active installations should be "active"
    dirty = False
    for center in pagination.items:
        if center.active_installations and center.status == CenterStatus.PROSPECT:
            center.status = CenterStatus.ACTIVE
            dirty = True
    if dirty:
        db.session.commit()

    pending_requests_count = 0
    if current_user.has_permission(UserPermission.CENTERS):
        from app.models.center import InstallationRequest

        pending_requests_count = (
            db.session.scalar(
                db.select(db.func.count(InstallationRequest.id)).where(
                    InstallationRequest.status == "pending"
                )
            )
            or 0
        )

    return render_template(
        "centers/list.html",
        centers=pagination.items,
        pagination=pagination,
        q=q,
        status=status,
        CenterStatus=CenterStatus,
        pending_requests_count=pending_requests_count,
    )


# ---------------------------------------------------------------------------
# CSV export — bureau only
# ---------------------------------------------------------------------------


@bp.route("/export.csv")
@bureau_required
def export_csv():
    """Export all centers (with contacts) as a CSV file."""
    centers = db.session.scalars(
        db.select(Center).options(selectinload(Center.contacts)).order_by(Center.name)
    ).all()
    csv_data = export_centers_csv(centers)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=centres.csv"},
    )


# ---------------------------------------------------------------------------
# CSV import — bureau only
# ---------------------------------------------------------------------------


@bp.route("/import", methods=["POST"])
@bureau_required
def import_csv():
    """Import centers (with contacts) from an uploaded CSV file."""
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("centers.list_centers"))

    rows, errors = parse_centers_csv(file.read())

    if errors:
        for e in errors:
            flash(e, "danger")
        if not rows:
            return redirect(url_for("centers.list_centers"))

    created = 0
    for row in rows:
        # Skip if a center with the exact same name already exists
        existing = db.session.scalars(db.select(Center).where(Center.name == row["name"])).first()
        if existing:
            flash(f"Centre « {row['name']} » ignoré (existe déjà).", "warning")
            continue

        try:
            status = CenterStatus(row["status"])
        except ValueError:
            status = CenterStatus.PROSPECT

        center = Center(
            name=row["name"],
            address=row["address"],
            city=row["city"],
            zip_code=row["zip_code"],
            status=status,
            notes=row["notes"],
        )
        db.session.add(center)
        db.session.flush()

        for c in row["contacts"]:
            contact = CenterContact(
                center_id=center.id,
                name=c["name"],
                role=c["role"],
                email=c["email"],
                phone=c["phone"],
            )
            db.session.add(contact)

        created += 1

    db.session.commit()
    if created:
        flash(f"{created} centre(s) importé(s).", "success")
    return redirect(url_for("centers.list_centers"))


@bp.route("/<int:center_id>")
@login_required
def detail(center_id: int):
    """Render the detail page for a center."""
    center = db.session.get(
        Center,
        center_id,
        options=[
            selectinload(Center.contacts),
            selectinload(Center.installations).selectinload(MachineInstallation.machine),
            selectinload(Center.maintenance_records).selectinload(MaintenanceRecord.machine),
            selectinload(Center.feedbacks),
            selectinload(Center.documents),
            selectinload(Center.convention_document),
        ],
    )
    if center is None:
        abort(404)

    breakdown_form = BreakdownReportForm()
    contact_form = CenterContactForm()
    install_form = InstallMachineForm()

    # Machines not currently installed anywhere (status=STOCK)
    available_machines = db.session.scalars(
        db.select(Machine)
        .where(Machine.status == MachineStatus.STOCK)
        .order_by(Machine.manufacturer, Machine.model)
    ).all()
    install_form.machine_id.choices = [
        (m.id, m.display_name + (f" [{m.internal_number}]" if m.internal_number else ""))
        for m in available_machines
    ]

    return render_template(
        "centers/detail.html",
        center=center,
        breakdown_form=breakdown_form,
        contact_form=contact_form,
        install_form=install_form,
        available_machines=available_machines,
    )


# ---------------------------------------------------------------------------
# Convention document — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/convention", methods=["POST"])
@bureau_required
def convention_upload(center_id: int):
    """Upload or replace the convention document for a center."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    file = request.files.get("convention_file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("centers.detail", center_id=center_id))

    raw_name = file.filename
    safe_name = secure_filename(raw_name)
    if not safe_name:
        flash("Nom de fichier invalide.", "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in _CONVENTION_EXTS:
        flash("Seuls les fichiers PDF, DOCX et ODT sont acceptés pour la convention.", "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    data = file.read()
    if data[:4] == b"%PDF":
        detected_mime = "application/pdf"
    elif data[:2] == b"PK":
        detected_mime = "application/zip"
    else:
        flash("Contenu du fichier invalide (vérification MIME).", "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    if detected_mime != _CONVENTION_EXTS[ext]:
        flash("Le contenu du fichier ne correspond pas à son extension.", "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    if len(data) > _CONVENTION_MAX_BYTES:
        flash("Le fichier dépasse la limite de 20 Mo.", "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    name_part = os.path.splitext(safe_name)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", name_part.lower()).strip("-")[:50] or "convention"
    stored_name = f"{date.today().isoformat()}_contract_{slug}{ext}"

    doc = Document(
        original_filename=raw_name,
        stored_filename=stored_name,
        type=DocumentType.CONTRACT.value,
        category="convention",
        mime_type=detected_mime,
        size_bytes=len(data),
        uploaded_by_id=current_user.id,
        description=f"Convention — {center.name}",
    )
    db.session.add(doc)
    db.session.flush()

    drive_uploaded = False
    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
        try:
            from app.services.drive import DriveService

            file_id, web_link = DriveService.from_db().upload_file(
                data, raw_name, detected_mime, DocumentType.CONTRACT.value
            )
            doc.drive_file_id = file_id
            doc.drive_web_link = web_link
            drive_uploaded = True
        except Exception as exc:
            logger.warning("Drive upload failed for convention: %s", exc)

    if not drive_uploaded:
        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "contracts")
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, stored_name), "wb") as fh:
            fh.write(data)

    center.convention_document_id = doc.id
    db.session.commit()
    flash("Convention téléversée.", "success")
    return redirect(url_for("centers.detail", center_id=center_id))


@bp.route("/<int:center_id>/convention/detach", methods=["POST"])
@bureau_required
def convention_detach(center_id: int):
    """Detach the convention document from a center (keeps the file)."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)
    center.convention_document_id = None
    db.session.commit()
    flash("Convention détachée.", "info")
    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Center photos — any authenticated user
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/photos", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def upload_photo(center_id: int):
    """Upload a photo and attach it to a center."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    back = url_for("centers.detail", center_id=center_id)
    files = request.files.getlist("file")
    if not files or all(not f.filename for f in files):
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(back)

    description = request.form.get("description", "").strip() or None
    photo_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    added = 0
    upload_errors: list[str] = []

    for file in files:
        if not file.filename:
            continue

        safe_name = secure_filename(file.filename)
        if not safe_name:
            upload_errors.append(f"« {file.filename} » : nom de fichier invalide.")
            continue

        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in photo_exts:
            upload_errors.append(f"« {file.filename} » : extension non autorisée.")
            continue

        data = file.read()
        if len(data) > 10 * 1024 * 1024:
            upload_errors.append(f"« {file.filename} » : dépasse la limite de 10 Mo.")
            continue

        mime_type = _detect_mime(data)
        if mime_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            upload_errors.append(f"« {file.filename} » : type de fichier non autorisé.")
            continue

        slug = (
            re.sub(r"[^a-z0-9]+", "-", os.path.splitext(safe_name)[0].lower()).strip("-")[:40]
            or "photo"
        )
        stored_name = f"{date.today().isoformat()}_center_{center_id}_{slug}{ext}"

        doc = Document(
            original_filename=file.filename,
            stored_filename=stored_name,
            type=DocumentType.PHOTO.value,
            category="center",
            mime_type=mime_type,
            size_bytes=len(data),
            uploaded_by_id=current_user.id,
            description=description,
        )
        db.session.add(doc)
        db.session.flush()
        db.session.execute(
            center_documents.insert().values(center_id=center_id, document_id=doc.id)
        )

        drive_uploaded = False
        if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
            try:
                from app.services.drive import DriveService

                file_id, web_link = DriveService.from_db().upload_file(
                    data, file.filename, doc.mime_type, DocumentType.PHOTO.value
                )
                doc.drive_file_id = file_id
                doc.drive_web_link = web_link
                drive_uploaded = True
            except Exception as exc:
                logger.warning("Drive upload failed for center photo: %s", exc)

        if not drive_uploaded:
            subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "photos")
            os.makedirs(subdir, exist_ok=True)
            with open(os.path.join(subdir, stored_name), "wb") as fh:
                fh.write(data)

        added += 1

    db.session.commit()
    if added:
        msg = f"{added} photo{'s' if added > 1 else ''} ajoutée{'s' if added > 1 else ''}."
        flash(msg, "success")
    for e in upload_errors:
        flash(e, "danger")
    return redirect(back)


@bp.route("/<int:center_id>/photos/<int:document_id>/detach", methods=["POST"])
@bureau_required
def detach_photo(center_id: int, document_id: int):
    """Remove the link between a photo and a center (keeps the file)."""
    db.session.execute(
        center_documents.delete().where(
            center_documents.c.center_id == center_id,
            center_documents.c.document_id == document_id,
        )
    )
    db.session.commit()
    flash("Photo détachée.", "info")
    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Machine installation — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/install", methods=["POST"])
@permission_required(UserPermission.CENTERS)
def install_machine(center_id: int):
    """Install an available machine into a center and activate the center."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    # Rebuild choices so validation works
    available_machines = db.session.scalars(
        db.select(Machine)
        .where(Machine.status == MachineStatus.STOCK)
        .order_by(Machine.manufacturer, Machine.model)
    ).all()
    install_form = InstallMachineForm()
    install_form.machine_id.choices = [(m.id, m.display_name) for m in available_machines]

    if install_form.validate_on_submit():
        machine = db.session.get(Machine, install_form.machine_id.data)
        if machine is None or machine.status != MachineStatus.STOCK:
            flash("Machine introuvable ou déjà installée.", "danger")
            return redirect(url_for("centers.detail", center_id=center_id))

        installation = MachineInstallation(
            machine_id=machine.id,
            center_id=center_id,
            installed_at=install_form.installed_at.data,
        )
        machine.status = MachineStatus.INSTALLED
        center.status = CenterStatus.ACTIVE
        db.session.add(installation)
        db.session.commit()
        flash(
            f"{machine.display_name} installée. Le centre est maintenant actif.",
            "success",
        )
    else:
        for field_errors in install_form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Create and edit — bureau only
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@permission_required(UserPermission.CENTERS)
def create():
    """Create a new center record."""
    form = CenterForm()
    if form.validate_on_submit():
        center = Center(
            name=form.name.data.strip(),
            address=(form.address.data or "").strip() or None,
            city=form.city.data.strip(),
            zip_code=form.zip_code.data.strip(),
            status=form.status.data,
            pathology=form.pathology.data or None,
            target_audience=form.target_audience.data or None,
            latitude=form.latitude.data,
            longitude=form.longitude.data,
            notes=(form.notes.data or "").strip() or None,
        )

        if not center.latitude and not center.longitude and center.city and center.zip_code:
            lat, lng = geocode_address(center.address or "", center.city, center.zip_code)
            center.latitude = lat
            center.longitude = lng

        db.session.add(center)
        db.session.commit()
        flash(f"Centre « {center.name} » créé.", "success")
        return redirect(url_for("centers.detail", center_id=center.id))
    return render_template("centers/form.html", form=form, title="Nouveau centre")


@bp.route("/<int:center_id>/edit", methods=["GET", "POST"])
@permission_required(UserPermission.CENTERS)
def edit(center_id: int):
    """Edit an existing center record."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    form = CenterForm(obj=center)
    if form.validate_on_submit():
        # Compare against the current values BEFORE overwriting them.
        new_address = (form.address.data or "").strip() or None
        new_city = form.city.data.strip()
        new_zip = form.zip_code.data.strip()
        addr_changed = (
            center.address != new_address or center.city != new_city or center.zip_code != new_zip
        )

        center.name = form.name.data.strip()
        center.address = new_address
        center.city = new_city
        center.zip_code = new_zip
        center.status = form.status.data
        center.pathology = form.pathology.data or None
        center.target_audience = form.target_audience.data or None
        center.notes = (form.notes.data or "").strip() or None

        # Keep the submitted coordinates unless the address changed.
        center.latitude = form.latitude.data if form.latitude.data is not None else None
        center.longitude = form.longitude.data if form.longitude.data is not None else None

        # Re-geocode when the address changed, or when coordinates are missing.
        needs_geocode = addr_changed or (center.latitude is None and center.longitude is None)
        if needs_geocode and center.city and center.zip_code:
            lat, lng = geocode_address(center.address or "", center.city, center.zip_code)
            center.latitude = lat
            center.longitude = lng

        db.session.commit()
        flash("Centre mis à jour.", "success")
        return redirect(url_for("centers.detail", center_id=center.id))
    return render_template(
        "centers/form.html", form=form, title="Modifier le centre", center=center
    )


# ---------------------------------------------------------------------------
# Contact management — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/contacts", methods=["POST"])
@permission_required(UserPermission.CENTERS)
def add_contact(center_id: int):
    """Add a contact person to the center."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    form = CenterContactForm()
    if form.validate_on_submit():
        contact = CenterContact(
            center_id=center_id,
            name=form.name.data.strip(),
            role=(form.role.data or "").strip() or None,
            email=(form.email.data or "").strip().lower() or None,
            phone=(form.phone.data or "").strip() or None,
        )
        db.session.add(contact)
        db.session.commit()
        flash(f"Contact « {contact.name} » ajouté.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
    return redirect(url_for("centers.detail", center_id=center_id))


@bp.route("/<int:center_id>/contacts/<int:contact_id>/delete", methods=["POST"])
@permission_required(UserPermission.CENTERS)
def delete_contact(center_id: int, contact_id: int):
    """Delete a contact person from the center."""
    contact = db.session.get(CenterContact, contact_id)
    if contact is None or contact.center_id != center_id:
        abort(404)
    db.session.delete(contact)
    db.session.commit()
    flash(f"Contact « {contact.name} » supprimé.", "success")
    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Breakdown reporting — any logged-in member
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/breakdown", methods=["POST"])
@login_required
@limiter.limit("5 per hour")
def report_breakdown(center_id: int):
    """Report a machine breakdown at a center — creates an open Task."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    form = BreakdownReportForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
        return redirect(url_for("centers.detail", center_id=center_id))

    task = Task(
        title=f"Panne signalée — {center.name}",
        description=form.description.data.strip(),
        status=TaskStatus.OPEN,
        priority=form.priority.data,
        created_by_id=current_user.id,
        source=TaskSource.CENTER_BREAKDOWN,
        source_center_id=center_id,
    )
    db.session.add(task)
    db.session.commit()
    flash(
        f"Panne signalée (tâche #{task.id} créée). Le bureau a été notifié.",
        "success",
    )
    _alert_bureau_breakdown(task, center)
    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Feedback token generation — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/feedback-link", methods=["POST"])
@bureau_required
def generate_feedback_link(center_id: int):
    """Generate (or regenerate) a permanent token-based URL for public feedback submission.

    Overwrites any existing token for this center, invalidating the previous link.
    """
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    center.feedback_token = secrets.token_urlsafe(32)
    db.session.commit()

    link = url_for("centers.submit_feedback", token=center.feedback_token, _external=True)
    flash(f"Lien de témoignage (permanent) : {link}", "info")
    return redirect(url_for("centers.detail", center_id=center_id))


@bp.route("/<int:center_id>/breakdown-link", methods=["POST"])
@bureau_required
def generate_breakdown_link(center_id: int):
    """Generate (or regenerate) a permanent token-based URL for public breakdown reporting."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    center.breakdown_token = secrets.token_urlsafe(32)
    db.session.commit()

    link = url_for("machines.public_breakdown", token=center.breakdown_token, _external=True)
    flash(f"Lien de signalement de panne (permanent) : {link}", "info")
    return redirect(url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Public feedback submission — no auth, DB token + rate limit
# ---------------------------------------------------------------------------


@bp.route("/feedback/<token>", methods=["GET", "POST"])
@limiter.limit(
    "5 per hour", key_func=lambda: f"{request.remote_addr}:{request.view_args.get('token', '')}"
)
def submit_feedback(token: str):
    """Public feedback form, identified by a permanent DB-stored token.

    The token must match a center's feedback_token.
    A honeypot field silently discards bot submissions.
    """
    center = db.session.scalars(db.select(Center).where(Center.feedback_token == token)).first()
    if center is None:
        abort(403)

    form = FeedbackForm()

    if request.method == "POST":
        # Honeypot check — silently accept but discard if filled
        if form.website.data:
            logger.warning("Honeypot triggered on feedback form center=%d", center.id)
            flash("Merci pour votre témoignage !", "success")
            return redirect(url_for("centers.feedback_thanks"))

        if form.validate_on_submit():
            rating_raw = form.rating.data
            rating = int(rating_raw) if rating_raw else None
            feedback = CenterFeedback(
                center_id=center.id,
                submitted_by=form.submitted_by.data.strip(),
                content=form.content.data.strip(),
                rating=rating,
                submitted_at=datetime.now(UTC),
            )
            db.session.add(feedback)
            db.session.commit()
            flash("Merci pour votre témoignage ! Il sera publié après modération.", "success")
            return redirect(url_for("centers.feedback_thanks"))

    return render_template("centers/feedback_form.html", center=center, form=form, token=token)


@bp.route("/feedback/merci")
def feedback_thanks():
    """Thank-you page after submitting feedback."""
    return render_template("centers/feedback_thanks.html")


# ---------------------------------------------------------------------------
# Guestbook — logged-in users
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/guestbook")
@login_required
def guestbook(center_id: int):
    """Show published feedbacks for a center."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)

    published = db.session.scalars(
        db.select(CenterFeedback)
        .where(
            CenterFeedback.center_id == center_id,
            CenterFeedback.is_published.is_(True),
        )
        .order_by(CenterFeedback.published_at.desc())
    ).all()

    return render_template("centers/guestbook.html", center=center, feedbacks=published)


# ---------------------------------------------------------------------------
# Feedback moderation — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/feedbacks/<int:fb_id>/publish", methods=["POST"])
@bureau_required
def publish_feedback(center_id: int, fb_id: int):
    """Publish a pending feedback entry."""
    fb = db.session.get(CenterFeedback, fb_id)
    if fb is None or fb.center_id != center_id:
        abort(404)
    fb.is_published = True
    fb.published_by_id = current_user.id
    fb.published_at = datetime.now(UTC)
    db.session.commit()
    flash("Témoignage publié.", "success")
    return redirect(request.referrer or url_for("centers.detail", center_id=center_id))


@bp.route("/<int:center_id>/feedbacks/<int:fb_id>/unpublish", methods=["POST"])
@bureau_required
def unpublish_feedback(center_id: int, fb_id: int):
    """Unpublish a feedback entry."""
    fb = db.session.get(CenterFeedback, fb_id)
    if fb is None or fb.center_id != center_id:
        abort(404)
    fb.is_published = False
    fb.published_by_id = None
    fb.published_at = None
    db.session.commit()
    flash("Témoignage retiré de la publication.", "success")
    return redirect(request.referrer or url_for("centers.detail", center_id=center_id))


# ---------------------------------------------------------------------------
# Delete center — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/qr.svg")
@login_required
def qr_svg(center_id: int):
    """Return a QR code SVG pointing to the center's public feedback form."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)
    if not center.feedback_token:
        flash("Générez d'abord un lien de témoignage.", "warning")
        return redirect(url_for("centers.detail", center_id=center_id))
    feedback_url = url_for("centers.submit_feedback", token=center.feedback_token, _external=True)
    return Response(_make_qr_svg(feedback_url), mimetype="image/svg+xml")


@bp.route("/<int:center_id>/breakdown-qr.svg")
@login_required
def breakdown_qr_svg(center_id: int):
    """Return a QR code SVG pointing to the center's public breakdown form."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)
    if not center.breakdown_token:
        flash("Générez d'abord un lien de signalement de panne.", "warning")
        return redirect(url_for("centers.detail", center_id=center_id))
    breakdown_url = url_for(
        "machines.public_breakdown", token=center.breakdown_token, _external=True
    )
    return Response(_make_qr_svg(breakdown_url), mimetype="image/svg+xml")


# ---------------------------------------------------------------------------
# Delete center — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:center_id>/delete", methods=["POST"])
@bureau_required
def delete(center_id: int):
    """Delete a center and all its related records."""
    center = db.session.get(Center, center_id)
    if center is None:
        abort(404)
    name = center.name
    db.session.delete(center)
    db.session.commit()
    flash(f"Centre « {name} » supprimé.", "success")
    return redirect(url_for("centers.list_centers"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_captcha() -> str | None:
    """Validate the math captcha submitted in the installation request form.

    Returns an error message string on failure, or None on success.
    Checks HMAC integrity and rejects tokens older than _CAPTCHA_TTL seconds.
    """
    import time

    a_str = request.form.get("captcha_a", "")
    b_str = request.form.get("captcha_b", "")
    ts_str = request.form.get("captcha_ts", "")
    token = request.form.get("captcha_token", "")
    answer = request.form.get("captcha_answer", "").strip()

    _invalid = "Données de vérification invalides. Veuillez recharger la page."

    try:
        a, b, ts = int(a_str), int(b_str), int(ts_str)
    except ValueError:
        return _invalid

    if time.time() - ts > _CAPTCHA_TTL:
        return "Le formulaire a expiré. Veuillez recharger la page."

    expected_token = _captcha_token(a, b, ts)
    if not _hmac.compare_digest(token, expected_token):
        return _invalid

    try:
        if int(answer) != a + b:
            return "Réponse incorrecte à la question de vérification."
    except ValueError:
        return "Réponse incorrecte à la question de vérification."

    return None


def _alert_bureau_installation_request(req) -> None:  # type: ignore[annotation-unchecked]
    """Email all bureau members about a new public installation request (best-effort)."""
    from app.models.user import User, UserRole
    from app.services.mailer import send_installation_request_email

    bureau_users = db.session.scalars(db.select(User).where(User.role == UserRole.BUREAU)).all()
    portal_url = url_for("centers.list_requests", _external=True)
    for bu in bureau_users:
        if not bu.email:
            continue
        try:
            send_installation_request_email(
                to_email=bu.email,
                full_name=bu.full_name,
                center_name=req.center_name,
                contact_name=req.contact_name,
                contact_email=req.contact_email,
                city=req.city,
                portal_url=portal_url,
            )
        except Exception:
            logger.exception("Failed to send installation request alert to %s", bu.email)


def _alert_bureau_breakdown(task: "Task", center: "Center") -> None:  # type: ignore[name-defined]
    """Email all bureau members about a new breakdown task (best-effort)."""
    from app.models.user import User, UserRole
    from app.services.mailer import send_breakdown_alert_email

    bureau_users = db.session.scalars(db.select(User).where(User.role == UserRole.BUREAU)).all()
    portal_url = url_for("tasks.detail", task_id=task.id, _external=True)
    for bu in bureau_users:
        if not bu.email:
            continue
        try:
            send_breakdown_alert_email(
                to_email=bu.email,
                full_name=bu.full_name,
                center_name=center.name,
                description=task.description or "",
                reporter_name=current_user.full_name,
                portal_url=portal_url,
            )
        except Exception:
            logger.exception("Failed to send breakdown alert to %s", bu.email)


def _make_qr_svg(url: str) -> bytes:
    """Return a QR code as SVG bytes for the given URL."""
    import qrcode
    import qrcode.image.svg as qrsvg

    factory = qrsvg.SvgPathImage
    img = qrcode.make(url, image_factory=factory, box_size=8, border=2)
    buf = __import__("io").BytesIO()
    img.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public installation request — no auth, rate limited
# ---------------------------------------------------------------------------


_CAPTCHA_TTL = 30 * 60  # seconds


def _captcha_token(a: int, b: int, ts: int) -> str:
    """HMAC-sign the captcha challenge including a timestamp to prevent replay attacks."""
    key = current_app.secret_key
    if isinstance(key, str):
        key = key.encode()
    msg = f"{a},{b},{ts}".encode()
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


@bp.route("/request-installation", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def request_installation():
    """Public page for centers to request machine installation.

    After a successful submission the user is redirected back to this same URL
    with ?merci=1 so the confirmation is shown without leaving the domain.
    """
    import time
    from datetime import timedelta

    from app.models.center import InstallationRequest

    # Show the confirmation card when the user just submitted
    if request.args.get("merci"):
        return render_template("centers/request_installation.html", submitted=True)

    form = InstallationRequestForm()

    if request.method == "POST":
        # Honeypot check — silently redirect to confirm
        if form.website.data:
            logger.warning("Honeypot triggered on installation request form")
            return redirect(url_for("centers.request_installation", merci=1))

        # Math captcha verification
        captcha_error = _verify_captcha()
        if captcha_error:
            flash(captcha_error, "danger")
            ts = int(time.time())
            captcha_a = secrets.randbelow(9) + 1
            captcha_b = secrets.randbelow(9) + 1
            return render_template(
                "centers/request_installation.html",
                form=form,
                captcha_a=captcha_a,
                captcha_b=captcha_b,
                captcha_ts=ts,
                captcha_token=_captcha_token(captcha_a, captcha_b, ts),
            )

        if form.validate_on_submit():
            email = form.contact_email.data.strip().lower()
            center_name = form.center_name.data.strip()

            # Deduplication: reject duplicate pending request within the last hour
            cutoff = datetime.now(UTC) - timedelta(hours=1)
            duplicate = db.session.scalars(
                db.select(InstallationRequest).where(
                    InstallationRequest.contact_email == email,
                    InstallationRequest.center_name == center_name,
                    InstallationRequest.status == "pending",
                    InstallationRequest.created_at >= cutoff,
                )
            ).first()
            if duplicate:
                # Silent redirect — avoids leaking info about existing submissions
                return redirect(url_for("centers.request_installation", merci=1))

            req = InstallationRequest(
                center_name=center_name,
                address=(form.address.data or "").strip() or None,
                city=form.city.data.strip(),
                zip_code=form.zip_code.data.strip(),
                contact_name=form.contact_name.data.strip(),
                contact_role=(form.contact_role.data or "").strip() or None,
                contact_email=email,
                contact_phone=(form.contact_phone.data or "").strip() or None,
                motivation=form.motivation.data.strip(),
                status="pending",
                created_at=datetime.now(UTC),
            )
            db.session.add(req)
            db.session.commit()
            _alert_bureau_installation_request(req)
            return redirect(url_for("centers.request_installation", merci=1))

    ts = int(time.time())
    captcha_a = secrets.randbelow(9) + 1
    captcha_b = secrets.randbelow(9) + 1
    return render_template(
        "centers/request_installation.html",
        form=form,
        captcha_a=captcha_a,
        captcha_b=captcha_b,
        captcha_ts=ts,
        captcha_token=_captcha_token(captcha_a, captcha_b, ts),
    )


# ---------------------------------------------------------------------------
# Installation requests management — centers permission
# ---------------------------------------------------------------------------


@bp.route("/requests")
@permission_required(UserPermission.CENTERS)
def list_requests():
    """List all installation requests."""
    from app.models.center import InstallationRequest

    status_filter = request.args.get("status", "pending").strip()

    stmt = db.select(InstallationRequest).order_by(InstallationRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(InstallationRequest.status == status_filter)

    requests_list = db.session.scalars(stmt).all()

    return render_template(
        "centers/requests_list.html", requests=requests_list, status=status_filter
    )


@bp.route("/requests/<int:request_id>/approve", methods=["GET", "POST"])
@permission_required(UserPermission.CENTERS)
def approve_request(request_id: int):
    """Approve installation request, create center + contact."""
    from app.models.center import InstallationRequest

    req = db.session.get(InstallationRequest, request_id)
    if req is None:
        abort(404)
    if req.status != "pending":
        flash("Cette demande a déjà été traitée.", "warning")
        return redirect(url_for("centers.list_requests"))

    form = CenterForm()

    if request.method == "GET":
        # Pre-fill form with request data
        form.name.data = req.center_name
        form.address.data = req.address
        form.city.data = req.city
        form.zip_code.data = req.zip_code
        form.status.data = CenterStatus.PROSPECT.value

    if form.validate_on_submit():
        # Create Center
        center = Center(
            name=form.name.data.strip(),
            address=(form.address.data or "").strip() or None,
            city=form.city.data.strip(),
            zip_code=form.zip_code.data.strip(),
            status=form.status.data,
            notes=(form.notes.data or "").strip() or None,
        )

        if center.city and center.zip_code:
            lat, lng = geocode_address(center.address or "", center.city, center.zip_code)
            center.latitude = lat
            center.longitude = lng

        db.session.add(center)
        db.session.flush()

        # Create Contact
        contact = CenterContact(
            center_id=center.id,
            name=req.contact_name,
            role=req.contact_role,
            email=req.contact_email,
            phone=req.contact_phone,
        )
        db.session.add(contact)

        # Update Request
        req.status = "approved"
        req.processed_at = datetime.now(UTC)
        req.processed_by_id = current_user.id
        req.created_center_id = center.id

        db.session.commit()
        flash(f"Le centre « {center.name} » et son contact ont été créés.", "success")
        return redirect(url_for("centers.detail", center_id=center.id))

    return render_template("centers/approve_request.html", form=form, req=req)


@bp.route("/requests/<int:request_id>/reject", methods=["POST"])
@permission_required(UserPermission.CENTERS)
def reject_request(request_id: int):
    """Reject/ignore installation request."""
    from app.models.center import InstallationRequest

    req = db.session.get(InstallationRequest, request_id)
    if req is None:
        abort(404)
    if req.status != "pending":
        flash("Cette demande a déjà été traitée.", "warning")
        return redirect(url_for("centers.list_requests"))

    req.status = "rejected"
    req.processed_at = datetime.now(UTC)
    req.processed_by_id = current_user.id
    db.session.commit()

    flash("La demande a été rejetée.", "info")
    return redirect(url_for("centers.list_requests"))


# ---------------------------------------------------------------------------
# Global guestbook — members read-only, bureau moderates
# ---------------------------------------------------------------------------


@bp.route("/guestbook")
@login_required
def global_guestbook():
    """Show guestbook entries across all centers, with moderation for bureau."""
    stmt = db.select(CenterFeedback).join(CenterFeedback.center)

    if not current_user.is_bureau:
        stmt = stmt.where(CenterFeedback.is_published.is_(True))

    feedbacks = db.session.scalars(stmt.order_by(CenterFeedback.submitted_at.desc())).all()

    return render_template("centers/global_guestbook.html", feedbacks=feedbacks)


@bp.route("/feedbacks/<int:fb_id>/delete", methods=["POST"])
@bureau_required
def delete_feedback(fb_id: int):
    """Delete a feedback entry directly."""
    fb = db.session.get(CenterFeedback, fb_id)
    if fb is None:
        abort(404)
    db.session.delete(fb)
    db.session.commit()
    flash("Témoignage supprimé.", "success")
    return redirect(request.referrer or url_for("centers.global_guestbook"))
