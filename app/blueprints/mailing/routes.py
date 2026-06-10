"""Mailing blueprint routes — campaign CRUD and send trigger."""

from __future__ import annotations

from datetime import UTC

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import selectinload

from app.blueprints.mailing import bp
from app.blueprints.mailing.forms import CampaignForm
from app.decorators import bureau_required
from app.extensions import db
from app.models.mailing import CampaignStatus, MailingCampaign, MailingRecipient, RecipientStatus
from app.models.tombola import Tombola

# ---------------------------------------------------------------------------
# Campaign list
# ---------------------------------------------------------------------------


@bp.route("/")
@bureau_required
def list_campaigns():
    """List all mailing campaigns, newest first."""
    campaigns = db.session.scalars(
        db.select(MailingCampaign)
        .options(selectinload(MailingCampaign.created_by))
        .order_by(MailingCampaign.created_at.desc())
    ).all()
    return render_template("mailing/list.html", campaigns=campaigns, CampaignStatus=CampaignStatus)


# ---------------------------------------------------------------------------
# Campaign detail
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>")
@bureau_required
def detail(campaign_id: int):
    """Render campaign detail with recipient list."""
    campaign = db.session.get(
        MailingCampaign,
        campaign_id,
        options=[
            selectinload(MailingCampaign.created_by),
            selectinload(MailingCampaign.recipients).selectinload(MailingRecipient.user),
        ],
    )
    if campaign is None:
        abort(404)
    return render_template(
        "mailing/detail.html",
        campaign=campaign,
        CampaignStatus=CampaignStatus,
        RecipientStatus=RecipientStatus,
    )


# ---------------------------------------------------------------------------
# Campaign preview
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>/preview")
@bureau_required
def preview(campaign_id: int):
    """Render a campaign preview personalised for a concrete recipient."""
    import secrets as _secrets

    from app.models.center import Center, CenterStatus
    from app.models.machine import MachineInstallation
    from app.models.tombola import TombolaTicket
    from app.models.user import User

    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        abort(404)

    rf = campaign.recipients_filter or {}
    audience = rf.get("audience", "members")

    # For "both", let the user toggle between member and center preview via ?mode=
    if audience == "both":
        preview_mode = request.args.get("mode", "members")
    else:
        preview_mode = audience

    # ── Load recipient candidates ──────────────────────────────────────────────

    members: list[User] = []
    centers: list = []
    tombola_participants: list[TombolaTicket] = []  # one row per unique email

    if preview_mode in ("members", "both"):
        members = db.session.scalars(
            db.select(User)
            .where(User.is_active.is_(True))
            .order_by(User.last_name, User.first_name)
            .limit(50)
        ).all()

    if preview_mode in ("center_contacts", "both"):
        center_ids = db.session.scalars(
            db.select(MachineInstallation.center_id)
            .where(MachineInstallation.removed_at.is_(None))
            .where(MachineInstallation.center_id.is_not(None))
            .distinct()
        ).all()
        centers = db.session.scalars(
            db.select(Center)
            .where(Center.status == CenterStatus.ACTIVE.value)
            .where(Center.id.in_(center_ids))
            .order_by(Center.name)
        ).all()

    if preview_mode == "tombola":
        tombola_id = rf.get("tombola_id")
        if tombola_id:
            # One row per unique email (first ticket per email)
            tombola_participants = db.session.scalars(
                db.select(TombolaTicket)
                .where(
                    TombolaTicket.tombola_id == tombola_id,
                    TombolaTicket.email != "[anonymisé]",
                )
                .distinct(TombolaTicket.email)
                .order_by(TombolaTicket.email)
                .limit(50)
            ).all()

    # ── Resolve selected recipient ─────────────────────────────────────────────

    selected_member: User | None = None
    selected_center = None
    selected_participant: TombolaTicket | None = None

    if preview_mode in ("members", "both") and members:
        sel_id = request.args.get("member_id", type=int)
        selected_member = next((m for m in members if m.id == sel_id), members[0])

    if preview_mode in ("center_contacts", "both") and centers:
        sel_id = request.args.get("center_id", type=int)
        selected_center = next((c for c in centers if c.id == sel_id), centers[0])

    if preview_mode == "tombola" and tombola_participants:
        sel_email = request.args.get("participant_email", "")
        selected_participant = next(
            (p for p in tombola_participants if p.email == sel_email),
            tombola_participants[0],
        )

    # ── Build personalised preview values ─────────────────────────────────────

    if preview_mode in ("members", "both") and selected_member:
        preview_name = selected_member.full_name
        preview_email = selected_member.email
    elif preview_mode == "tombola" and selected_participant:
        name_parts = [selected_participant.first_name or "", selected_participant.last_name or ""]
        preview_name = " ".join(p for p in name_parts if p) or selected_participant.email
        preview_email = selected_participant.email
    elif preview_mode in ("center_contacts", "both") and selected_center:
        preview_name = selected_center.name
        preview_email = f"contact@{selected_center.name.lower().replace(' ', '-')}.fr"
    else:
        preview_name = "Destinataire Exemple"
        preview_email = "destinataire-exemple@exemple.com"

    # ── Center-specific URLs (empty when audience has no center recipients) ─────

    if selected_center:
        dirty = False
        if not selected_center.feedback_token:
            selected_center.feedback_token = _secrets.token_urlsafe(32)
            dirty = True
        if not selected_center.breakdown_token:
            selected_center.breakdown_token = _secrets.token_urlsafe(32)
            dirty = True
        if dirty:
            db.session.commit()
        breakdown_url = url_for(
            "machines.public_breakdown", token=selected_center.breakdown_token, _external=True
        )
        feedback_url = url_for(
            "centers.submit_feedback", token=selected_center.feedback_token, _external=True
        )
    else:
        # Tags that don't apply to this audience are replaced with empty string.
        breakdown_url = ""
        feedback_url = ""

    # ── Tombola ticket numbers (empty when audience is not tombola) ────────────

    if preview_mode == "tombola" and selected_participant:
        all_tickets = db.session.scalars(
            db.select(TombolaTicket)
            .where(
                TombolaTicket.tombola_id == rf.get("tombola_id"),
                TombolaTicket.email == selected_participant.email,
                TombolaTicket.ticket_number.isnot(None),
            )
            .order_by(TombolaTicket.ticket_number)
        ).all()
        numeros = ", ".join(f"{t.ticket_number:03d}" for t in all_tickets)
    else:
        numeros = ""

    # ── Substitute all tags ────────────────────────────────────────────────────

    body = campaign.body_html
    subject = campaign.subject

    for tag in ["{lien_panne}", "{{lien_panne}}", "[[lien_panne]]"]:
        body = body.replace(tag, breakdown_url)
        subject = subject.replace(tag, breakdown_url)
    for tag in ["{lien_livre_or}", "{{lien_livre_or}}", "[[lien_livre_or]]"]:
        body = body.replace(tag, feedback_url)
        subject = subject.replace(tag, feedback_url)
    for tag in ["{numeros_tombola}", "{{numeros_tombola}}", "[[numeros_tombola]]"]:
        body = body.replace(tag, numeros)
        subject = subject.replace(tag, numeros)

    return render_template(
        "mailing/preview.html",
        campaign=campaign,
        subject=subject,
        body=body,
        audience=audience,
        preview_mode=preview_mode,
        # Recipient lists
        members=members,
        centers=centers,
        tombola_participants=tombola_participants,
        # Selected
        selected_member=selected_member,
        selected_center=selected_center,
        selected_participant=selected_participant,
        # Display
        preview_name=preview_name,
        preview_email=preview_email,
    )


# ---------------------------------------------------------------------------
# Create campaign
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new draft campaign."""
    form = CampaignForm()

    tombolas = db.session.scalars(
        db.select(Tombola)
        .where(Tombola.anonymized_at.is_(None))
        .order_by(Tombola.created_at.desc())
    ).all()

    # Pre-select tombola audience when arriving from a tombola detail page.
    preselect_tombola_id: int | None = None
    if request.method == "GET":
        preselect_tombola_id = request.args.get("tombola_id", type=int)
        if preselect_tombola_id:
            form.audience.data = "tombola"

    if form.validate_on_submit():
        rfilter: dict = {
            "audience": form.audience.data,
            "membership_status": form.membership_status.data,
            "role": form.role.data,
        }
        if form.audience.data == "tombola":
            tombola_id = request.form.get("tombola_id", type=int)
            if not tombola_id:
                flash("Veuillez sélectionner une tombola.", "danger")
                return render_template(
                    "mailing/form.html", form=form, campaign=None, tombolas=tombolas
                )
            rfilter["tombola_id"] = tombola_id

        campaign = MailingCampaign(
            name=form.name.data.strip(),
            subject=form.subject.data.strip(),
            body_html=form.body_html.data,
            status=CampaignStatus.DRAFT.value,
            created_by_id=current_user.id,
            recipients_filter=rfilter,
        )
        if form.scheduled_at.data:
            dt = form.scheduled_at.data
            campaign.scheduled_at = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
            campaign.status = CampaignStatus.SCHEDULED.value

        db.session.add(campaign)
        db.session.commit()
        flash(f"Campagne « {campaign.name} » créée.", "success")
        return redirect(url_for("mailing.detail", campaign_id=campaign.id))

    return render_template(
        "mailing/form.html",
        form=form,
        campaign=None,
        tombolas=tombolas,
        preselect_tombola_id=preselect_tombola_id,
    )


# ---------------------------------------------------------------------------
# Edit campaign (draft only)
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(campaign_id: int):
    """Edit a draft campaign."""
    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        abort(404)
    if not campaign.is_editable:
        flash("Seules les campagnes en brouillon peuvent être modifiées.", "warning")
        return redirect(url_for("mailing.detail", campaign_id=campaign.id))

    tombolas = db.session.scalars(
        db.select(Tombola)
        .where(Tombola.anonymized_at.is_(None))
        .order_by(Tombola.created_at.desc())
    ).all()

    form = CampaignForm(obj=campaign)
    if request.method == "GET":
        f = campaign.recipients_filter or {}
        form.audience.data = f.get("audience", "members")
        form.membership_status.data = f.get("membership_status", "active")
        form.role.data = f.get("role", "all")
        if campaign.scheduled_at:
            form.scheduled_at.data = campaign.scheduled_at.replace(tzinfo=None)

    if form.validate_on_submit():
        rfilter: dict = {
            "audience": form.audience.data,
            "membership_status": form.membership_status.data,
            "role": form.role.data,
        }
        if form.audience.data == "tombola":
            tombola_id = request.form.get("tombola_id", type=int)
            if not tombola_id:
                flash("Veuillez sélectionner une tombola.", "danger")
                return render_template(
                    "mailing/form.html", form=form, campaign=campaign, tombolas=tombolas
                )
            rfilter["tombola_id"] = tombola_id

        campaign.name = form.name.data.strip()
        campaign.subject = form.subject.data.strip()
        campaign.body_html = form.body_html.data
        campaign.recipients_filter = rfilter
        if form.scheduled_at.data:
            dt = form.scheduled_at.data
            campaign.scheduled_at = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
            campaign.status = CampaignStatus.SCHEDULED.value
        else:
            campaign.scheduled_at = None
            campaign.status = CampaignStatus.DRAFT.value

        db.session.commit()
        flash(f"Campagne « {campaign.name} » mise à jour.", "success")
        return redirect(url_for("mailing.detail", campaign_id=campaign.id))

    return render_template("mailing/form.html", form=form, campaign=campaign, tombolas=tombolas)


# ---------------------------------------------------------------------------
# Send campaign
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>/send", methods=["POST"])
@bureau_required
def send(campaign_id: int):
    """Trigger sending a campaign via Celery."""
    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        abort(404)
    if campaign.status not in (CampaignStatus.DRAFT.value, CampaignStatus.SCHEDULED.value):
        flash("Cette campagne a déjà été envoyée ou est en cours d'envoi.", "warning")
        return redirect(url_for("mailing.detail", campaign_id=campaign.id))

    from app.tasks.mailing import send_campaign

    send_campaign.delay(campaign_id)
    flash(f"Envoi de la campagne « {campaign.name} » en cours…", "info")
    return redirect(url_for("mailing.detail", campaign_id=campaign.id))


# ---------------------------------------------------------------------------
# Delete campaign (draft only)
# ---------------------------------------------------------------------------


@bp.route("/<int:campaign_id>/delete", methods=["POST"])
@bureau_required
def delete(campaign_id: int):
    """Delete a draft campaign."""
    campaign = db.session.get(MailingCampaign, campaign_id)
    if campaign is None:
        abort(404)
    if not campaign.is_editable:
        flash("Seules les campagnes en brouillon peuvent être supprimées.", "warning")
        return redirect(url_for("mailing.detail", campaign_id=campaign.id))

    name = campaign.name
    db.session.delete(campaign)
    db.session.commit()
    flash(f"Campagne « {name} » supprimée.", "success")
    return redirect(url_for("mailing.list_campaigns"))
