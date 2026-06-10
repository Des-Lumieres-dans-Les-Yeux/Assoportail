"""Tombola blueprint routes — admin management and public ticket lookup."""

from __future__ import annotations

import logging
import os
import random
import secrets
from datetime import UTC, datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from werkzeug.utils import secure_filename

from app.blueprints.documents.routes import (
    _ALLOWED_EXTENSIONS,
    _PHOTO_MIMES,
    _detect_mime,
    _get_size_limit,
    _make_stored_filename,
)
from app.blueprints.tombola import bp
from app.blueprints.tombola.forms import (
    AssignNumbersForm,
    CreateTombolaForm,
    EditTombolaForm,
    MediaUploadForm,
    PublicLookupForm,
    UploadTicketsForm,
)
from app.blueprints.tombola.upload import parse_file
from app.decorators import bureau_required
from app.extensions import db, limiter
from app.models.document import Document, DocumentType
from app.models.tombola import Tombola, TombolaTicket, tombola_documents

logger = logging.getLogger(__name__)

# ── Admin routes ──────────────────────────────────────────────────────────────


@bp.route("/")
@bureau_required
def list_tombolas():
    tombolas = db.session.scalars(db.select(Tombola).order_by(Tombola.created_at.desc())).all()

    # One aggregate query for all counts (avoids loading every ticket row).
    rows = db.session.execute(
        db.select(
            TombolaTicket.tombola_id,
            func.count().label("total"),
            func.count(TombolaTicket.ticket_number).label("numbered"),
        ).group_by(TombolaTicket.tombola_id)
    ).all()
    stats = {r.tombola_id: (r.total, r.numbered) for r in rows}

    return render_template(
        "tombola/list.html",
        tombolas=tombolas,
        stats=stats,
        form=CreateTombolaForm(),
    )


@bp.route("/new", methods=["POST"])
@bureau_required
def create():
    form = CreateTombolaForm()
    if form.validate_on_submit():
        draw_dt = None
        if form.draw_date.data:
            draw_dt = datetime(
                form.draw_date.data.year,
                form.draw_date.data.month,
                form.draw_date.data.day,
                tzinfo=UTC,
            )
        tombola = Tombola(
            name=form.name.data.strip(),
            draw_date=draw_dt,
            created_by_id=current_user.id,
        )
        db.session.add(tombola)
        db.session.commit()
        flash(f"Tombola « {tombola.name} » créée.", "success")
        return redirect(url_for("tombola.detail", tombola_id=tombola.id))
    flash("Erreur dans le formulaire de création.", "danger")
    return redirect(url_for("tombola.list_tombolas"))


@bp.route("/<int:tombola_id>")
@bureau_required
def detail(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)

    # Load all tickets: filter / sort / pagination are handled client-side
    # (a tombola is bounded — at most a few thousand rows).
    tickets = db.session.scalars(
        db.select(TombolaTicket)
        .where(TombolaTicket.tombola_id == tombola_id)
        .order_by(TombolaTicket.ticket_number, TombolaTicket.id)
    ).all()
    ticket_count = len(tickets)
    numbered_count = sum(1 for t in tickets if t.ticket_number is not None)

    return render_template(
        "tombola/detail.html",
        tombola=tombola,
        upload_form=UploadTicketsForm(),
        assign_form=AssignNumbersForm(obj=tombola),
        tickets=tickets,
        ticket_count=ticket_count,
        numbered_count=numbered_count,
    )


@bp.route("/<int:tombola_id>/anonymize", methods=["POST"])
@bureau_required
def anonymize(tombola_id: int):
    """RGPD: wipe all personal data, keep only ticket numbers. Irreversible."""
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    if tombola.is_anonymized:
        flash("Cette tombola est déjà anonymisée.", "info")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    db.session.execute(
        db.update(TombolaTicket)
        .where(TombolaTicket.tombola_id == tombola_id)
        .values(
            email="[anonymisé]",
            first_name=None,
            last_name=None,
            phone=None,
            order_ref=None,
        )
    )
    # Revoke the public link and mark as archived.
    tombola.slug = None
    tombola.anonymized_at = datetime.now(UTC)
    db.session.commit()

    flash(
        "Données personnelles anonymisées. Seuls les numéros de billets sont conservés.",
        "success",
    )
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


@bp.route("/<int:tombola_id>/generate-link", methods=["POST"])
@bureau_required
def generate_link(tombola_id: int):
    """Create or regenerate the public link. Regenerating revokes the old one."""
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    if tombola.is_anonymized:
        flash("Cette tombola est archivée (anonymisée).", "warning")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    was_existing = tombola.slug is not None
    tombola.slug = secrets.token_urlsafe(16)
    db.session.commit()
    flash(
        "Lien public régénéré — l'ancien lien ne fonctionne plus."
        if was_existing
        else "Lien public généré.",
        "success",
    )
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


@bp.route("/<int:tombola_id>/upload", methods=["POST"])
@bureau_required
def upload(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    if tombola.is_anonymized:
        flash("Cette tombola est archivée (anonymisée) — import impossible.", "warning")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    if tombola.winner_ticket_id is not None:
        flash(
            "Un gagnant est déjà désigné — retirez-le avant de ré-importer.",
            "danger",
        )
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    form = UploadTicketsForm()
    if not form.validate_on_submit():
        flash("Fichier invalide.", "danger")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    f = form.file.data
    data = f.read()
    rows = parse_file(f.filename, data)

    if not rows:
        flash(
            "Aucune ligne valide trouvée. Vérifiez que le fichier contient une colonne email.",
            "warning",
        )
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    db.session.execute(db.delete(TombolaTicket).where(TombolaTicket.tombola_id == tombola_id))
    db.session.bulk_save_objects(
        [
            TombolaTicket(
                tombola_id=tombola_id,
                email=row.email,
                last_name=row.last_name,
                first_name=row.first_name,
                phone=row.phone,
                ticket_number=row.ticket_number,
                order_ref=row.order_ref,
            )
            for row in rows
        ]
    )
    db.session.commit()

    has_numbers = any(r.ticket_number is not None for r in rows)
    flash(
        f"{len(rows)} billets importés."
        + (
            " Les numéros ont été détectés et enregistrés."
            if has_numbers
            else " Aucun numéro détecté — vous pouvez lancer le tirage aléatoire."
        ),
        "success" if has_numbers else "info",
    )
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


@bp.route("/<int:tombola_id>/assign", methods=["POST"])
@bureau_required
def assign_numbers(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    if tombola.is_anonymized:
        flash("Cette tombola est archivée (anonymisée) — tirage impossible.", "warning")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    form = AssignNumbersForm()
    if not form.validate_on_submit():
        flash("Paramètres invalides.", "danger")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    rmin, rmax = form.range_min.data, form.range_max.data
    if rmin >= rmax:
        flash("Le numéro minimum doit être strictement inférieur au maximum.", "danger")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    # Only fill tickets that have no number yet — never re-shuffle issued numbers.
    unnumbered = db.session.scalars(
        db.select(TombolaTicket)
        .where(
            TombolaTicket.tombola_id == tombola_id,
            TombolaTicket.ticket_number.is_(None),
        )
        .order_by(TombolaTicket.id)
    ).all()
    if not unnumbered:
        flash("Tous les billets sont déjà numérotés.", "info")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    used = set(
        db.session.scalars(
            db.select(TombolaTicket.ticket_number).where(
                TombolaTicket.tombola_id == tombola_id,
                TombolaTicket.ticket_number.isnot(None),
            )
        ).all()
    )
    pool = [n for n in range(rmin, rmax + 1) if n not in used]

    if len(unnumbered) > len(pool):
        flash(
            f"La plage {rmin}–{rmax} n'offre que {len(pool)} numéros libres "
            f"pour {len(unnumbered)} billets à numéroter.",
            "danger",
        )
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    random.shuffle(pool)
    for ticket, number in zip(unnumbered, pool, strict=False):
        ticket.ticket_number = number
    tombola.range_min = rmin
    tombola.range_max = rmax
    db.session.commit()

    flash(
        f"Tirage effectué : {len(unnumbered)} numéros attribués (plage {rmin}–{rmax}).",
        "success",
    )
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


@bp.route("/<int:tombola_id>/delete", methods=["POST"])
@bureau_required
def delete(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    name = tombola.name
    db.session.delete(tombola)
    db.session.commit()
    flash(f"Tombola « {name} » supprimée.", "success")
    return redirect(url_for("tombola.list_tombolas"))


@bp.route("/<int:tombola_id>/edit", methods=["POST"])
@bureau_required
def edit(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    if tombola.is_anonymized:
        flash("Cette tombola est archivée et ne peut plus être modifiée.", "warning")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    form = EditTombolaForm()
    if form.validate_on_submit():
        tombola.name = form.name.data.strip()
        if form.draw_date.data:
            tombola.draw_date = datetime(
                form.draw_date.data.year,
                form.draw_date.data.month,
                form.draw_date.data.day,
                tzinfo=UTC,
            )
        else:
            tombola.draw_date = None
        db.session.commit()
        flash("Tombola mise à jour.", "success")
    else:
        flash("Erreur dans le formulaire.", "danger")
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


@bp.route("/<int:tombola_id>/set-winner", methods=["POST"])
@bureau_required
def set_winner(tombola_id: int):
    tombola = db.session.get(Tombola, tombola_id) or abort(404)
    action = request.form.get("action", "draw")

    if action == "clear":
        tombola.winner_ticket_id = None
        db.session.commit()
        flash("Gagnant retiré.", "info")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    if action == "manual":
        try:
            number = int(request.form.get("winner_number", ""))
        except ValueError:
            flash("Numéro invalide.", "danger")
            return redirect(url_for("tombola.detail", tombola_id=tombola_id))
        ticket = db.session.scalar(
            db.select(TombolaTicket).where(
                TombolaTicket.tombola_id == tombola_id,
                TombolaTicket.ticket_number == number,
                TombolaTicket.email != "[anonymisé]",
            )
        )
        if not ticket:
            flash(f"Billet n°{number:03d} introuvable.", "danger")
            return redirect(url_for("tombola.detail", tombola_id=tombola_id))
    else:
        # Random draw — exclude anonymized tickets (unreachable participants).
        numbered = db.session.scalars(
            db.select(TombolaTicket).where(
                TombolaTicket.tombola_id == tombola_id,
                TombolaTicket.ticket_number.isnot(None),
                TombolaTicket.email != "[anonymisé]",
            )
        ).all()
        if not numbered:
            flash("Aucun billet numéroté — lancez le tirage d'abord.", "warning")
            return redirect(url_for("tombola.detail", tombola_id=tombola_id))
        ticket = random.choice(numbered)  # noqa: S311 — non-crypto raffle draw

    tombola.winner_ticket_id = ticket.id
    db.session.commit()
    label = f"n°{ticket.ticket_number:03d}"
    if not tombola.is_anonymized and ticket.first_name:
        label += f" — {ticket.first_name} {ticket.last_name or ''}".rstrip()
    flash(f"Gagnant désigné : {label}", "success")
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


_ALLOWED_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}


@bp.route("/<int:tombola_id>/media", methods=["POST"])
@bureau_required
def upload_media(tombola_id: int):
    if db.session.get(Tombola, tombola_id) is None:
        abort(404)
    # form.validate_on_submit() handles CSRF; FileRequired passes as long as ≥1 file is present.
    form = MediaUploadForm()
    if not form.validate_on_submit():
        flash("Fichier invalide ou CSRF expiré.", "danger")
        return redirect(url_for("tombola.detail", tombola_id=tombola_id))

    back = url_for("tombola.detail", tombola_id=tombola_id)
    files = request.files.getlist("file")
    added = 0
    upload_errors: list[str] = []

    for f in files:
        raw_name = f.filename or ""
        if not raw_name:
            continue

        safe_name = secure_filename(raw_name)
        ext = os.path.splitext(safe_name)[1].lower()

        if ext not in _ALLOWED_MEDIA_EXTS:
            upload_errors.append(f"« {raw_name} » : format non autorisé.")
            continue

        data = f.read()
        detected_mime = _detect_mime(data)
        if detected_mime != _ALLOWED_EXTENSIONS.get(ext):
            upload_errors.append(f"« {raw_name} » : contenu ne correspond pas à l'extension.")
            continue

        size_limit = (
            current_app.config["MAX_UPLOAD_TOMBOLA_VIDEO"]
            if detected_mime and detected_mime.startswith("video/")
            else _get_size_limit(detected_mime)
        )
        if len(data) > size_limit:
            upload_errors.append(
                f"« {raw_name} » : dépasse la limite de {size_limit // (1024 * 1024)} Mo."
            )
            continue

        doc_type = DocumentType.PHOTO if detected_mime in _PHOTO_MIMES else DocumentType.VIDEO
        stored_name = _make_stored_filename(safe_name, doc_type.value, prefix="tombola")

        doc = Document(
            original_filename=raw_name,
            stored_filename=stored_name,
            type=doc_type.value,
            mime_type=detected_mime,
            size_bytes=len(data),
            uploaded_by_id=current_user.id,
        )
        db.session.add(doc)
        try:
            db.session.flush()
            db.session.execute(
                tombola_documents.insert().values(tombola_id=tombola_id, document_id=doc.id)
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            upload_errors.append(f"« {raw_name} » : erreur lors de l'enregistrement.")
            continue

        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc.subdir)
        os.makedirs(subdir, exist_ok=True)
        try:
            with open(os.path.join(subdir, stored_name), "wb") as out:
                out.write(data)
        except OSError as exc:
            logger.error("Failed to write tombola media file %s: %s", stored_name, exc)
            upload_errors.append(
                f"« {raw_name} » : enregistré en base mais écriture disque échouée."
            )  # noqa: E501
            continue

        added += 1

    if added:
        s = "s" if added > 1 else ""
        flash(f"{added} média{s} ajouté{s}.", "success")
    for e in upload_errors:
        flash(e, "danger")
    return redirect(back)


@bp.route("/<int:tombola_id>/media/<int:doc_id>/delete", methods=["POST"])
@bureau_required
def delete_media(tombola_id: int, doc_id: int):
    if db.session.get(Tombola, tombola_id) is None:
        abort(404)
    doc = db.session.get(Document, doc_id) or abort(404)

    # Verify the document is actually linked to this tombola before deleting anything.
    result = db.session.execute(
        tombola_documents.delete().where(
            tombola_documents.c.tombola_id == tombola_id,
            tombola_documents.c.document_id == doc_id,
        )
    )
    if result.rowcount == 0:
        abort(404)  # doc_id not linked to this tombola — prevent cross-resource deletion

    # Capture path info before deleting the row.
    upload_folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    path = os.path.join(upload_folder, doc.subdir, doc.stored_filename)
    real_path = os.path.realpath(path)

    # Commit DB deletion before removing the file so a commit failure never
    # leaves a missing file behind a live Document row.
    db.session.delete(doc)
    db.session.commit()

    # Best-effort file removal after successful commit.
    if real_path.startswith(upload_folder + os.sep) and os.path.isfile(real_path):
        try:
            os.remove(real_path)
        except OSError as exc:
            logger.warning("Could not remove tombola media file %s: %s", real_path, exc)

    flash("Média supprimé.", "success")
    return redirect(url_for("tombola.detail", tombola_id=tombola_id))


# ── Public route ──────────────────────────────────────────────────────────────


@bp.route("/p/<slug>", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def public(slug: str):
    tombola = db.session.scalar(db.select(Tombola).where(Tombola.slug == slug)) or abort(404)

    form = PublicLookupForm()
    tickets: list[TombolaTicket] = []
    searched = False

    if form.validate_on_submit():
        searched = True
        email_norm = form.email.data.strip().lower()
        tickets = db.session.scalars(
            db.select(TombolaTicket)
            .where(
                TombolaTicket.tombola_id == tombola.id,
                TombolaTicket.email == email_norm,
            )
            .order_by(TombolaTicket.ticket_number)
        ).all()

    # Distinguish "found but draw not done yet" from "here are your numbers".
    has_numbers = any(t.ticket_number is not None for t in tickets)

    # The global after_request hook adds X-Robots-Tag: noindex on every response.
    return render_template(
        "tombola/public.html",
        tombola=tombola,
        form=form,
        tickets=tickets,
        searched=searched,
        has_numbers=has_numbers,
    )
