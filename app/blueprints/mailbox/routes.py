"""Mailbox blueprint routes — inbox, email detail, rules CRUD, Gmail OAuth2."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from flask import (
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user
from sqlalchemy.orm import selectinload

from app.blueprints.mailbox import bp
from app.blueprints.mailbox.forms import EmailRuleForm
from app.decorators import bureau_required, permission_required
from app.extensions import db, talisman
from app.models.email import EmailRule, GmailToken, GoogleAppCredentials, InboundEmail
from app.models.user import UserPermission
from app.tasks.email_rules import _evaluate_conditions, _html_to_text

logger = logging.getLogger(__name__)

_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
]
_PER_PAGE = 30


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


@bp.route("/")
@permission_required(UserPermission.MAILBOX)
def inbox():
    """List inbound emails, newest first, with search and filters."""
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()[:200]
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    show_read = request.args.get("show_read", "")
    page = max(1, request.args.get("page", 1, type=int))

    stmt = db.select(InboundEmail).order_by(InboundEmail.received_at.desc())

    if not show_read:
        stmt = stmt.where(InboundEmail.processed.is_(False))

    if category:
        stmt = stmt.where(InboundEmail.category == category)

    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            db.or_(
                InboundEmail.subject.ilike(pattern),
                InboundEmail.sender.ilike(pattern),
                InboundEmail.body_text.ilike(pattern),
            )
        )

    if date_from:
        try:
            from datetime import date as date_type

            d = date_type.fromisoformat(date_from)
            dt_from = datetime(d.year, d.month, d.day, tzinfo=UTC)
            stmt = stmt.where(InboundEmail.received_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            from datetime import date as date_type
            from datetime import timedelta

            d = date_type.fromisoformat(date_to)
            dt_to = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=1)
            stmt = stmt.where(InboundEmail.received_at < dt_to)
        except ValueError:
            pass

    total = db.session.scalar(db.select(db.func.count()).select_from(stmt.subquery())) or 0
    emails = db.session.scalars(stmt.offset((page - 1) * _PER_PAGE).limit(_PER_PAGE)).all()

    # Distinct categories for filter dropdown
    categories = db.session.scalars(
        db.select(InboundEmail.category)
        .where(InboundEmail.category.is_not(None))
        .distinct()
        .order_by(InboundEmail.category)
    ).all()

    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    token_ok = db.session.get(GmailToken, 1) is not None
    credentials_ok = db.session.get(GoogleAppCredentials, 1) is not None

    from app.models.event import Event, EventStatus

    active_events = db.session.scalars(
        db.select(Event)
        .where(Event.status.in_([EventStatus.PLANNED.value, EventStatus.IN_PROGRESS.value]))
        .order_by(Event.event_date.desc())
    ).all()

    return render_template(
        "mailbox/inbox.html",
        emails=emails,
        category=category,
        categories=categories,
        q=q,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total=total,
        total_pages=total_pages,
        token_ok=token_ok,
        credentials_ok=credentials_ok,
        show_read=show_read,
        active_events=active_events,
    )


# ---------------------------------------------------------------------------
# Email detail
# ---------------------------------------------------------------------------


@bp.route("/<int:email_id>")
@permission_required(UserPermission.MAILBOX)
def email_detail(email_id: int):
    """Render a single inbound email with its rule logs."""
    email = db.session.get(
        InboundEmail,
        email_id,
        options=[
            selectinload(InboundEmail.generated_task),
            selectinload(InboundEmail.event),
        ],
    )
    if email is None:
        abort(404)

    from app.models.email import EmailRuleLog
    from app.models.event import Event, EventStatus

    logs = db.session.scalars(
        db.select(EmailRuleLog)
        .options(selectinload(EmailRuleLog.rule))
        .where(EmailRuleLog.email_id == email_id)
        .order_by(EmailRuleLog.applied_at)
    ).all()

    # Fetch active events for linking
    active_events = db.session.scalars(
        db.select(Event)
        .where(Event.status.in_([EventStatus.PLANNED.value, EventStatus.IN_PROGRESS.value]))
        .order_by(Event.event_date.desc())
    ).all()

    email_body_preview = (email.body_text or _html_to_text(email.body_html or ""))[:2000]

    return render_template(
        "mailbox/email_detail.html",
        email=email,
        logs=logs,
        active_events=active_events,
        email_body_preview=email_body_preview,
    )


@bp.route("/<int:email_id>/html-body")
@talisman(
    content_security_policy={
        "default-src": "'none'",
        "style-src": "'unsafe-inline'",
        "font-src": ["https:", "data:"],
        "img-src": ["https:", "data:", "cid:"],
        "frame-ancestors": "'self'",
    },
    content_security_policy_nonce_in=[],
    frame_options="SAMEORIGIN",
)
@permission_required(UserPermission.MAILBOX)
def email_html_body(email_id: int):
    """Serve the raw HTML body for rendering in a sandboxed iframe."""
    from flask import Response

    email = db.session.get(InboundEmail, email_id)
    if email is None or not email.body_html:
        abort(404)

    return Response(email.body_html, content_type="text/html; charset=utf-8")


@bp.route("/<int:email_id>/create-event", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def create_event_from_email(email_id: int):
    """Manually create an event from an inbound email."""
    from app.models.event import Event, EventStatus

    email = db.session.get(InboundEmail, email_id)
    if email is None:
        abort(404)

    title = request.form.get("title", "").strip()
    if not title:
        title = f"Événement : {(email.subject or 'sans sujet')[:180]}"

    date_str = request.form.get("event_date", "")
    try:
        event_date = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        event_date = datetime.now(UTC)

    event = Event(
        title=title,
        description=email.body_text or email.body_html or "",
        status=EventStatus.PLANNED.value,
        event_date=event_date,
        location=request.form.get("location", "").strip() or None,
        created_by_id=current_user.id,
    )
    db.session.add(event)
    db.session.flush()
    email.event_id = event.id
    email.processed = True
    db.session.commit()

    flash(f"Événement « {event.title} » créé.", "success")
    return redirect(url_for("mailbox.email_detail", email_id=email_id))


@bp.route("/<int:email_id>/link-event", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def link_email_to_event(email_id: int):
    """Link an inbound email to an existing event."""
    from app.models.event import Event

    email = db.session.get(InboundEmail, email_id)
    if email is None:
        abort(404)

    event_id = request.form.get("event_id", type=int)
    if event_id:
        event = db.session.get(Event, event_id)
        if event:
            email.event_id = event.id
            email.processed = True
            db.session.commit()
            if request.headers.get("HX-Request") == "true":
                return Response("", status=200)
            flash(f"Email lié à l'événement « {event.title} ».", "success")
        else:
            flash("Événement introuvable.", "danger")
    else:
        email.event_id = None
        db.session.commit()
        flash("Lien avec l'événement supprimé.", "info")

    return redirect(url_for("mailbox.email_detail", email_id=email_id))


# ---------------------------------------------------------------------------
# Rules list
# ---------------------------------------------------------------------------


@bp.route("/rules")
@permission_required(UserPermission.MAILBOX)
def rules():
    """List all email rules ordered by priority."""
    all_rules = db.session.scalars(
        db.select(EmailRule)
        .options(selectinload(EmailRule.created_by))
        .order_by(EmailRule.priority, EmailRule.id)
    ).all()
    return render_template("mailbox/rules.html", rules=all_rules)


# ---------------------------------------------------------------------------
# Create rule
# ---------------------------------------------------------------------------


@bp.route("/rules/new", methods=["GET", "POST"])
@permission_required(UserPermission.MAILBOX)
def create_rule():
    """Create a new email rule."""
    form = EmailRuleForm()

    if form.validate_on_submit():
        if _rule_name_taken(form.name.data.strip()):
            flash(f"Une règle nommée « {form.name.data} » existe déjà.", "warning")
        else:
            rule = EmailRule(
                name=form.name.data.strip(),
                is_active=form.is_active.data,
                priority=form.priority.data,
                match_mode=form.match_mode.data,
                conditions=json.loads(form.conditions.data),
                actions=json.loads(form.actions.data),
                created_by_id=current_user.id,
            )
            db.session.add(rule)
            db.session.commit()
            flash(f"Règle « {rule.name} » créée.", "success")
            return redirect(url_for("mailbox.rules"))

    recent_emails = db.session.scalars(
        db.select(InboundEmail).order_by(InboundEmail.received_at.desc()).limit(20)
    ).all()
    return render_template(
        "mailbox/rule_form.html", form=form, rule=None, recent_emails=recent_emails
    )


# ---------------------------------------------------------------------------
# Edit rule
# ---------------------------------------------------------------------------


@bp.route("/rules/<int:rule_id>/edit", methods=["GET", "POST"])
@permission_required(UserPermission.MAILBOX)
def edit_rule(rule_id: int):
    """Edit an existing email rule."""
    rule = db.session.get(EmailRule, rule_id)
    if rule is None:
        abort(404)

    form = EmailRuleForm(obj=rule)

    if request.method == "GET":
        form.conditions.data = json.dumps(rule.conditions, ensure_ascii=False, indent=2)
        form.actions.data = json.dumps(rule.actions, ensure_ascii=False, indent=2)

    if form.validate_on_submit():
        name = form.name.data.strip()
        if name != rule.name and _rule_name_taken(name):
            flash(f"Une règle nommée « {name} » existe déjà.", "warning")
        else:
            rule.name = name
            rule.is_active = form.is_active.data
            rule.priority = form.priority.data
            rule.match_mode = form.match_mode.data
            rule.conditions = json.loads(form.conditions.data)
            rule.actions = json.loads(form.actions.data)
            db.session.commit()
            flash(f"Règle « {rule.name} » mise à jour.", "success")
            return redirect(url_for("mailbox.rules"))

    recent_emails = db.session.scalars(
        db.select(InboundEmail).order_by(InboundEmail.received_at.desc()).limit(20)
    ).all()
    return render_template(
        "mailbox/rule_form.html", form=form, rule=rule, recent_emails=recent_emails
    )


# ---------------------------------------------------------------------------
# Toggle rule active state
# ---------------------------------------------------------------------------


@bp.route("/rules/<int:rule_id>/toggle", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def toggle_rule(rule_id: int):
    """Toggle a rule's active state."""
    rule = db.session.get(EmailRule, rule_id)
    if rule is None:
        abort(404)
    rule.is_active = not rule.is_active
    db.session.commit()
    state = "activée" if rule.is_active else "désactivée"
    flash(f"Règle « {rule.name} » {state}.", "success")
    return redirect(url_for("mailbox.rules"))


# ---------------------------------------------------------------------------
# Delete rule
# ---------------------------------------------------------------------------


@bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def delete_rule(rule_id: int):
    """Delete an email rule and its associated logs."""
    rule = db.session.get(EmailRule, rule_id)
    if rule is None:
        abort(404)
    name = rule.name
    db.session.delete(rule)
    db.session.commit()
    flash(f"Règle « {name} » supprimée.", "success")
    return redirect(url_for("mailbox.rules"))


# ---------------------------------------------------------------------------
# Dry-run rule test
# ---------------------------------------------------------------------------


@bp.route("/rules/test", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def test_rule_preview():
    """Evaluate conditions (submitted as JSON) against a real inbox email.

    Returns an HTML fragment for HTMX inline display.
    Works for both unsaved (new) and saved rules.
    """
    try:
        conditions = json.loads(request.form.get("conditions", "[]"))
        match_mode = request.form.get("match_mode", "all")
    except (ValueError, TypeError):
        return '<span class="text-danger small">Conditions JSON invalides.</span>'

    email_id = request.form.get("email_id", type=int)
    if not email_id:
        return '<span class="text-warning small">Sélectionnez un email.</span>'

    email = db.session.get(InboundEmail, email_id)
    if email is None:
        return '<span class="text-danger small">Email introuvable.</span>'

    fake_rule = EmailRule(conditions=conditions, match_mode=match_mode)
    matched = _evaluate_conditions(fake_rule, email)

    label = f"« {(email.subject or '(sans sujet)')[:60]} »"
    if matched:
        return f'<span class="text-success fw-semibold">&#10003; Correspondance — {label}</span>'
    return f'<span class="text-muted">&#10007; Pas de correspondance — {label}</span>'


# ---------------------------------------------------------------------------
# Gmail OAuth2 — start
# ---------------------------------------------------------------------------


def _load_credentials_info() -> dict | None:
    """Return credentials dict from DB (preferred) or from the credentials file."""
    creds_row = db.session.get(GoogleAppCredentials, 1)
    if creds_row:
        try:
            return json.loads(creds_row.credentials_json)
        except (json.JSONDecodeError, Exception):
            logger.warning("Failed to parse Google credentials JSON from DB", exc_info=True)

    credentials_file = current_app.config["GMAIL_CREDENTIALS_FILE"]
    if os.path.exists(credentials_file):
        with open(credentials_file) as fh:
            try:
                return json.load(fh)
            except (json.JSONDecodeError, Exception):
                logger.warning(
                    "Failed to parse Google credentials from file %s",
                    credentials_file,
                    exc_info=True,
                )

    return None


# ---------------------------------------------------------------------------
# Upload Google credentials — bureau only
# ---------------------------------------------------------------------------


@bp.route("/credentials/upload", methods=["POST"])
@bureau_required
def upload_credentials():
    """Store uploaded credentials.json content in the database."""
    file = request.files.get("credentials_file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("mailbox.inbox"))

    raw = file.read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        flash("Le fichier n'est pas un JSON valide.", "danger")
        return redirect(url_for("mailbox.inbox"))

    # Basic sanity check: must have the OAuth client_id/secret structure
    client_config = parsed.get("web") or parsed.get("installed")
    if not client_config or "client_id" not in client_config:
        flash(
            "Format invalide. Assurez-vous de télécharger un fichier credentials.json "
            "de type 'Application Web' depuis Google Cloud Console.",
            "danger",
        )
        return redirect(url_for("mailbox.inbox"))

    creds_row = db.session.get(GoogleAppCredentials, 1)
    if creds_row:
        creds_row.credentials_json = raw.decode("utf-8")
        creds_row.updated_at = datetime.now(UTC)
    else:
        db.session.add(GoogleAppCredentials(id=1, credentials_json=raw.decode("utf-8")))
    db.session.commit()

    flash("Credentials Google enregistrés. Vous pouvez maintenant connecter Gmail.", "success")
    return redirect(url_for("mailbox.inbox"))


@bp.route("/<int:email_id>/create-task", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def create_task_from_email(email_id: int):
    """Manually create a task from an inbound email."""
    from app.models.task import Task, TaskPriority, TaskSource, TaskStatus

    email = db.session.get(InboundEmail, email_id, options=[selectinload(InboundEmail.documents)])
    if email is None:
        abort(404)

    title = request.form.get("title", "").strip()
    if not title:
        title = f"Email : {(email.subject or 'sans sujet')[:180]}"

    priority = request.form.get("priority", TaskPriority.NORMAL.value)
    if priority not in {e.value for e in TaskPriority}:
        priority = TaskPriority.NORMAL.value

    task = Task(
        title=title,
        description=request.form.get("description", "").strip() or None,
        source=TaskSource.EMAIL.value,
        source_email_id=email_id,
        priority=priority,
        status=TaskStatus.OPEN.value,
        created_by_id=current_user.id,
    )
    db.session.add(task)
    db.session.flush()
    if email.documents:
        task.documents = list(email.documents)
    email.generated_task_id = task.id
    email.processed = True
    db.session.commit()

    if request.headers.get("HX-Request") == "true":
        return Response("", status=200)

    flash(f"Tâche « {task.title} » créée.", "success")
    return redirect(url_for("mailbox.email_detail", email_id=email_id))


@bp.route("/<int:email_id>/categorize", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def categorize_email(email_id: int):
    """Manually set or clear the category of an inbound email."""
    email = db.session.get(InboundEmail, email_id)
    if email is None:
        abort(404)

    category = request.form.get("category", "").strip() or None
    email.category = category
    email.processed = True
    db.session.commit()

    flash(
        f"Email catégorisé : « {category} »." if category else "Catégorie retirée.",
        "success",
    )
    return redirect(url_for("mailbox.email_detail", email_id=email_id))


@bp.route("/<int:email_id>/mark-read", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def mark_email_read(email_id: int):
    """Mark an inbound email as read in Gmail."""
    from app.services.gmail import GmailClient

    email = db.session.get(InboundEmail, email_id)
    if email is None:
        abort(404)

    if email.gmail_message_id:
        try:
            client = GmailClient.from_db()
            client.mark_as_read(email.gmail_message_id)
        except Exception:
            logger.exception("mark_as_read failed for email %d", email_id)
    email.processed = True
    db.session.commit()

    if request.headers.get("HX-Request") == "true":
        return Response("", status=200)

    flash("Email marqué comme lu.", "success")
    return redirect(url_for("mailbox.email_detail", email_id=email_id))


@bp.route("/<int:email_id>/delete", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def delete_email(email_id: int):
    """Delete an inbound email from the local database and Gmail.

    Refuses deletion if the email is linked to a task or event.
    Also moves the Gmail message to the trash so the next sync does not
    re-import it. If the Gmail call fails (network, revoked token), the local
    record is kept so the user can retry instead of seeing the email reappear.
    """
    from app.services.gmail import GmailClient

    email = db.session.get(InboundEmail, email_id)
    if email is None:
        abort(404)

    is_htmx = request.headers.get("HX-Request") == "true"

    if email.generated_task_id or email.event_id:
        if is_htmx:
            return Response(status=409)
        flash("Impossible de supprimer : email lié à une tâche ou un événement.", "warning")
        return redirect(url_for("mailbox.inbox"))

    # Move the Gmail message to trash before deleting locally — otherwise the
    # next sync will re-import it because it is still UNREAD in Gmail.
    if email.gmail_message_id:
        try:
            client = GmailClient.from_db()
            client.trash_message(email.gmail_message_id)
        except Exception as exc:
            logger.exception("Failed to trash Gmail message for email %d", email_id)
            if is_htmx:
                return Response(status=502)
            flash(
                f"Suppression annulée : impossible de mettre l'email à la corbeille Gmail ({exc}).",
                "danger",
            )
            return redirect(url_for("mailbox.inbox"))

    db.session.delete(email)
    db.session.commit()

    if is_htmx:
        return Response("", status=200)

    flash("Email supprimé.", "success")
    return redirect(url_for("mailbox.inbox"))


@bp.route("/sync", methods=["POST"])
@permission_required(UserPermission.MAILBOX)
def sync_inbox():
    """Synchronize: import new unread emails, mark read ones as processed."""
    from flask import Response

    from app.services.gmail import GmailClient
    from app.tasks.email_polling import _parse_gmail_message

    is_htmx = request.headers.get("HX-Request") == "true"
    silent = is_htmx and request.form.get("auto-sync") == "1"

    try:
        client = GmailClient.from_db()
    except RuntimeError as exc:
        if silent:
            return Response(status=204)
        flash(f"Gmail non connecté : {exc}", "danger")
        return redirect(url_for("mailbox.inbox"))

    unread_ids = client.list_all_unread_ids()

    # Mark local emails that are no longer unread in Gmail
    all_local = db.session.scalars(
        db.select(InboundEmail).where(
            InboundEmail.processed.is_(False),
            InboundEmail.gmail_message_id.is_not(None),
        )
    ).all()
    marked = 0
    for email in all_local:
        if email.gmail_message_id not in unread_ids:
            email.processed = True
            marked += 1

    # Import any unread emails not yet in the DB
    imported = 0
    if unread_ids:
        existing_ids = set(
            db.session.scalars(
                db.select(InboundEmail.gmail_message_id).where(
                    InboundEmail.gmail_message_id.in_(unread_ids)
                )
            ).all()
        )
        for msg_id in unread_ids - existing_ids:
            try:
                raw = client.get_message(msg_id)
            except Exception:
                logger.exception("Failed to fetch Gmail message %s", msg_id)
                continue
            inbound = _parse_gmail_message(raw)
            if inbound:
                db.session.add(inbound)
                imported += 1

    db.session.commit()

    if silent:
        # Only refresh the page if something actually changed; stay quiet otherwise.
        if imported or marked:
            return Response(status=204, headers={"HX-Refresh": "true"})
        return Response(status=204)

    parts = []
    if imported:
        parts.append(f"{imported} nouveau(x) email(s) importé(s)")
    if marked:
        parts.append(f"{marked} email(s) marqué(s) comme lu(s)")
    flash(". ".join(parts) if parts else "Boîte à jour, aucun changement.", "success")
    return redirect(url_for("mailbox.inbox"))


@bp.route("/purge", methods=["POST"])
@bureau_required
def purge_inbox():
    """Delete read emails not linked to any task or event, then re-sync from Gmail.

    Kept emails:
    - processed=False  (still unread / not yet acted on)
    - generated_task_id IS NOT NULL  (linked to a task)
    - event_id IS NOT NULL  (linked to an event)
    """
    from app.services.gmail import GmailClient
    from app.tasks.email_polling import _parse_gmail_message

    # --- 1. Delete purgeable emails -------------------------------------------
    to_delete = db.session.scalars(
        db.select(InboundEmail).where(
            InboundEmail.processed.is_(True),
            InboundEmail.generated_task_id.is_(None),
            InboundEmail.event_id.is_(None),
        )
    ).all()
    deleted = len(to_delete)
    for email in to_delete:
        db.session.delete(email)
    db.session.flush()

    # --- 2. Re-sync from Gmail ------------------------------------------------
    imported = 0
    marked = 0
    try:
        client = GmailClient.from_db()
        unread_ids = client.list_all_unread_ids()

        # Mark local unread emails that are now read in Gmail
        local_unread = db.session.scalars(
            db.select(InboundEmail).where(
                InboundEmail.processed.is_(False),
                InboundEmail.gmail_message_id.is_not(None),
            )
        ).all()
        for email in local_unread:
            if email.gmail_message_id not in unread_ids:
                email.processed = True
                marked += 1

        # Import unread Gmail messages not yet in the DB
        if unread_ids:
            existing_ids = set(
                db.session.scalars(
                    db.select(InboundEmail.gmail_message_id).where(
                        InboundEmail.gmail_message_id.in_(unread_ids)
                    )
                ).all()
            )
            for msg_id in unread_ids - existing_ids:
                try:
                    raw = client.get_message(msg_id)
                except Exception:
                    logger.exception("Failed to fetch Gmail message %s during purge", msg_id)
                    continue
                inbound = _parse_gmail_message(raw)
                if inbound:
                    db.session.add(inbound)
                    imported += 1
    except RuntimeError as exc:
        logger.warning("Gmail sync skipped during purge: %s", exc)

    db.session.commit()

    parts = [f"{deleted} email(s) supprimé(s)"]
    if imported:
        parts.append(f"{imported} nouveau(x) importé(s)")
    if marked:
        parts.append(f"{marked} marqué(s) comme lu(s)")
    flash(". ".join(parts) + ".", "success")
    return redirect(url_for("mailbox.inbox"))


@bp.route("/oauth/start")
@bureau_required
def oauth_start():
    """Initiate the Gmail OAuth2 authorization flow."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash("Bibliothèque Google Auth introuvable.", "danger")
        return redirect(url_for("mailbox.inbox"))

    creds_info = _load_credentials_info()
    if creds_info is None:
        flash(
            "Credentials Google introuvables. "
            "Téléversez votre fichier credentials.json ci-dessous.",
            "danger",
        )
        return redirect(url_for("mailbox.inbox"))

    # google_auth_oauthlib accepts either a file path or a dict
    flow = Flow.from_client_config(
        creds_info,
        scopes=_GMAIL_SCOPES,
        redirect_uri=url_for("mailbox.oauth_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    session["gmail_oauth_state"] = state
    return redirect(auth_url)


# ---------------------------------------------------------------------------
# Gmail OAuth2 — callback
# ---------------------------------------------------------------------------


@bp.route("/oauth/callback")
@bureau_required
def oauth_callback():
    """Handle the OAuth2 redirect from Google and store the token."""
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError:
        flash("Bibliothèque Google Auth introuvable.", "danger")
        return redirect(url_for("mailbox.inbox"))

    state = session.pop("gmail_oauth_state", None)
    if not state:
        flash("Session OAuth expirée ou invalide. Recommencez.", "danger")
        return redirect(url_for("mailbox.inbox"))

    creds_info = _load_credentials_info()
    if creds_info is None:
        flash("Credentials Google introuvables.", "danger")
        return redirect(url_for("mailbox.inbox"))

    flow = Flow.from_client_config(
        creds_info,
        scopes=_GMAIL_SCOPES,
        state=state,
        redirect_uri=url_for("mailbox.oauth_callback", _external=True),
    )

    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:
        logger.exception("OAuth2 token exchange failed")
        flash(f"Erreur lors de l'autorisation Gmail : {exc}", "danger")
        return redirect(url_for("mailbox.inbox"))

    from app.services.gmail import encrypt_token

    token_data = json.loads(flow.credentials.to_json())
    encrypted = encrypt_token(token_data)

    token_row = db.session.get(GmailToken, 1)
    if token_row is None:
        token_row = GmailToken(id=1, token_encrypted=encrypted)
        db.session.add(token_row)
    else:
        token_row.token_encrypted = encrypted
        token_row.updated_at = datetime.now(UTC)

    db.session.commit()
    flash("Compte Gmail autorisé avec succès.", "success")
    return redirect(url_for("mailbox.inbox"))


# ---------------------------------------------------------------------------
# Gmail OAuth2 — revoke
# ---------------------------------------------------------------------------


@bp.route("/oauth/revoke", methods=["POST"])
@bureau_required
def oauth_revoke():
    """Revoke the stored Google OAuth2 token and delete it from the database.

    After revocation the user can re-authorize via /mailbox/oauth/start to
    obtain a fresh token with the correct scopes (Gmail + Drive).
    """
    token_row = db.session.get(GmailToken, 1)
    if token_row is None:
        flash("Aucun compte Google connecté.", "info")
        return redirect(url_for("mailbox.inbox"))

    # Attempt to revoke at Google — best-effort, never blocks the local deletion
    try:
        from app.services.gmail import decrypt_token

        token_data = decrypt_token(token_row.token_encrypted)
        # Prefer the access token; fall back to refresh token
        revoke_token = token_data.get("token") or token_data.get("refresh_token")
        if revoke_token:
            import httpx

            httpx.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": revoke_token},
                timeout=5,
            )
    except Exception:
        logger.warning("Could not revoke Google token remotely — deleting locally anyway.")

    db.session.delete(token_row)
    db.session.commit()
    flash(
        "Compte Google déconnecté. Vous pouvez maintenant reconnecter avec les bons droits.",
        "success",
    )
    return redirect(url_for("mailbox.inbox"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule_name_taken(name: str) -> bool:
    """Return True if an EmailRule with this name already exists."""
    return (
        db.session.scalar(db.select(db.func.count(EmailRule.id)).where(EmailRule.name == name)) or 0
    ) > 0
