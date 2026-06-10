"""Poll blueprint routes."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.polls import bp
from app.blueprints.polls.forms import PollForm, VoteForm
from app.decorators import bureau_required
from app.extensions import db
from app.models.document import Document, DocumentType
from app.models.poll import Poll, PollOption, PollVote

logger = logging.getLogger(__name__)

_PHOTO_MIMES: frozenset[str] = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_IMAGE_EXTENSIONS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_COVER_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _upload_cover(file_storage) -> Document | None:
    """Validate and persist a cover image; return the Document or None on error."""
    if not file_storage or not file_storage.filename:
        return None

    raw_name = file_storage.filename
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in _IMAGE_EXTENSIONS:
        flash("Format d'image non supporté (JPG, PNG, GIF, WEBP uniquement).", "danger")
        return None

    data = file_storage.read()
    if len(data) > _COVER_MAX_BYTES:
        flash("L'image ne doit pas dépasser 5 Mo.", "danger")
        return None

    detected_mime = _detect_image_mime(data)
    if detected_mime != _IMAGE_EXTENSIONS[ext]:
        flash("Le contenu du fichier ne correspond pas à son extension.", "danger")
        return None

    stored_name = f"{datetime.now(UTC).strftime('%Y-%m-%d')}_poll_{uuid.uuid4().hex}{ext}"
    doc = Document(
        original_filename=raw_name,
        stored_filename=stored_name,
        type=DocumentType.PHOTO.value,
        mime_type=detected_mime,
        size_bytes=len(data),
        uploaded_by_id=current_user.id,
    )
    db.session.add(doc)
    db.session.flush()  # get doc.id before potential Drive upload

    drive_uploaded = False
    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
        try:
            from app.services.drive import DriveService

            file_id, web_link = DriveService.from_db().upload_file(
                data,
                raw_name,
                detected_mime,
                DocumentType.PHOTO.value,
                year=datetime.now(UTC).year,
            )
            doc.drive_file_id = file_id
            doc.drive_web_link = web_link
            drive_uploaded = True
        except Exception as exc:
            logger.warning("Drive upload failed for poll cover, falling back to disk: %s", exc)

    if not drive_uploaded:
        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc.subdir)
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, stored_name), "wb") as fh:
            fh.write(data)

    return doc


def _load_poll(poll_id: int) -> Poll:
    poll = db.session.get(
        Poll,
        poll_id,
        options=[
            selectinload(Poll.options).selectinload(PollOption.votes),
            selectinload(Poll.votes),
            selectinload(Poll.created_by),
            selectinload(Poll.cover_document),
        ],
    )
    if poll is None:
        abort(404)
    return poll


def _parse_deadline(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
    except ValueError:
        flash("Format de date limite invalide.", "warning")
        return None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def list_polls():
    stmt = (
        db.select(Poll)
        .options(
            selectinload(Poll.options).selectinload(PollOption.votes),
            selectinload(Poll.votes),
        )
        .order_by(Poll.created_at.desc())
    )
    polls = db.session.execute(stmt).scalars().all()
    now = datetime.now(UTC)
    return render_template("polls/list.html", polls=polls, now=now)


# ---------------------------------------------------------------------------
# Detail + vote
# ---------------------------------------------------------------------------


@bp.route("/<int:poll_id>")
@login_required
def detail(poll_id: int):
    poll = _load_poll(poll_id)
    vote_form = VoteForm()
    my_votes = poll.user_votes(current_user.id)
    my_option_ids = {v.option_id for v in my_votes}
    show_results = bool(my_votes) or current_user.is_bureau
    total_votes = sum(o.vote_count for o in poll.options)
    return render_template(
        "polls/detail.html",
        poll=poll,
        vote_form=vote_form,
        my_option_ids=my_option_ids,
        show_results=show_results,
        total_votes=total_votes,
    )


@bp.route("/<int:poll_id>/vote", methods=["POST"])
@login_required
def vote(poll_id: int):
    poll = _load_poll(poll_id)
    vote_form = VoteForm()
    if not vote_form.validate_on_submit():
        abort(400)

    if not poll.is_active:
        flash("Ce sondage est clôturé ou expiré.", "warning")
        return redirect(url_for("polls.detail", poll_id=poll_id))

    option_ids_raw = request.form.getlist("option_id")
    if not option_ids_raw:
        flash("Veuillez sélectionner au moins une option.", "warning")
        return redirect(url_for("polls.detail", poll_id=poll_id))

    if not poll.allows_multiple and len(option_ids_raw) > 1:
        flash("Ce sondage n'autorise qu'une seule réponse.", "warning")
        return redirect(url_for("polls.detail", poll_id=poll_id))

    valid_option_ids = {o.id for o in poll.options}
    chosen = []
    for raw in option_ids_raw:
        try:
            oid = int(raw)
        except ValueError:
            abort(400)
        if oid not in valid_option_ids:
            abort(400)
        chosen.append(oid)

    # Remove previous votes from this user before saving new ones
    for existing in poll.user_votes(current_user.id):
        db.session.delete(existing)

    for oid in chosen:
        db.session.add(PollVote(poll_id=poll_id, option_id=oid, user_id=current_user.id))

    db.session.commit()
    flash("Vote enregistré.", "success")
    return redirect(url_for("polls.detail", poll_id=poll_id))


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    form = PollForm()
    if form.validate_on_submit():
        option_texts = [t.strip() for t in request.form.getlist("option_text") if t.strip()]
        if len(option_texts) < 2:
            flash("Veuillez saisir au moins deux options.", "danger")
            return render_template("polls/form.html", form=form, poll=None)

        poll = Poll(
            title=form.title.data.strip(),
            description=(form.description.data or "").strip() or None,
            deadline=_parse_deadline(request.form.get("deadline", "")),
            allows_multiple=form.allows_multiple.data,
            created_by_id=current_user.id,
        )
        db.session.add(poll)
        db.session.flush()

        for i, text in enumerate(option_texts):
            db.session.add(PollOption(poll_id=poll.id, text=text, order=i))

        cover_file = request.files.get("cover")
        if cover_file and cover_file.filename:
            doc = _upload_cover(cover_file)
            if doc:
                poll.cover_document_id = doc.id

        db.session.commit()
        flash("Sondage créé.", "success")
        return redirect(url_for("polls.detail", poll_id=poll.id))

    return render_template("polls/form.html", form=form, poll=None)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


@bp.route("/<int:poll_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(poll_id: int):
    poll = _load_poll(poll_id)
    form = PollForm(obj=poll)

    if form.validate_on_submit():
        poll.title = form.title.data.strip()
        poll.description = (form.description.data or "").strip() or None
        poll.deadline = _parse_deadline(request.form.get("deadline", ""))
        poll.allows_multiple = form.allows_multiple.data

        # Only update options if no votes exist yet
        if not poll.votes:
            option_texts = [t.strip() for t in request.form.getlist("option_text") if t.strip()]
            if len(option_texts) < 2:
                flash("Veuillez saisir au moins deux options.", "danger")
                return render_template("polls/form.html", form=form, poll=poll)
            # Replace options entirely
            for opt in list(poll.options):
                db.session.delete(opt)
            db.session.flush()
            for i, text in enumerate(option_texts):
                db.session.add(PollOption(poll_id=poll.id, text=text, order=i))

        cover_file = request.files.get("cover")
        if cover_file and cover_file.filename:
            doc = _upload_cover(cover_file)
            if doc:
                poll.cover_document_id = doc.id

        db.session.commit()
        flash("Sondage modifié.", "success")
        return redirect(url_for("polls.detail", poll_id=poll.id))

    # Pre-fill deadline for GET
    deadline_str = ""
    if poll.deadline:
        deadline_str = poll.deadline.strftime("%Y-%m-%dT%H:%M")

    return render_template("polls/form.html", form=form, poll=poll, deadline_str=deadline_str)


# ---------------------------------------------------------------------------
# Close / reopen
# ---------------------------------------------------------------------------


@bp.route("/<int:poll_id>/close", methods=["POST"])
@bureau_required
def toggle_close(poll_id: int):
    poll = db.session.get(Poll, poll_id)
    if poll is None:
        abort(404)
    form = VoteForm()
    if not form.validate_on_submit():
        abort(400)
    poll.is_closed = not poll.is_closed
    db.session.commit()
    state = "clôturé" if poll.is_closed else "rouvert"
    flash(f"Sondage {state}.", "success")
    return redirect(url_for("polls.detail", poll_id=poll_id))


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@bp.route("/<int:poll_id>/delete", methods=["POST"])
@bureau_required
def delete(poll_id: int):
    poll = db.session.get(Poll, poll_id)
    if poll is None:
        abort(404)
    form = VoteForm()
    if not form.validate_on_submit():
        abort(400)
    db.session.delete(poll)
    db.session.commit()
    flash("Sondage supprimé.", "success")
    return redirect(url_for("polls.list_polls"))
