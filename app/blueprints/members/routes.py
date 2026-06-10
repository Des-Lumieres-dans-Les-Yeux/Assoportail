"""Members blueprint routes — member management and member profile."""

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.members import bp
from app.blueprints.members.forms import (
    CashMembershipForm,
    DeleteMemberForm,
    HelloAssoMembershipForm,
    MemberCreateForm,
    MemberEditForm,
    ProfileEditForm,
)
from app.decorators import bureau_required
from app.extensions import db
from app.models.member import Membership, MembershipSource
from app.models.task import Task, TaskStatus
from app.models.user import BUREAU_DEFAULT_PERMISSIONS, MEMBER_DEFAULT_PERMISSIONS, User, UserRole
from app.services.csv_io import export_members_csv, parse_members_csv
from app.services.mailer import send_welcome_email

# ---------------------------------------------------------------------------
# Member list and detail — bureau only
# ---------------------------------------------------------------------------


def _member_volunteer_hours() -> dict[int, float]:
    """Compute total volunteer hours per member (user_id → hours).

    For each slot where a member declared present/maybe, counts the
    slot duration multiplied by their presence.
    """
    from app.models.event import Event, EventSlot, SlotAvailability

    rows = db.session.execute(
        db.select(
            SlotAvailability.user_id,
            EventSlot.start_time,
            EventSlot.end_time,
        )
        .join(EventSlot, SlotAvailability.slot_id == EventSlot.id)
        .join(Event, EventSlot.event_id == Event.id)
        .where(
            SlotAvailability.status.in_(["present", "maybe"]),
            Event.status == "completed",
        )
    ).all()

    hours: dict[int, float] = {}
    for user_id, start, end in rows:
        if start and end:
            duration_h = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60
            if duration_h > 0:
                hours[user_id] = hours.get(user_id, 0.0) + duration_h
        else:
            # If no times are set, we don't add hours, but the event will still be
            # picked up by _member_event_participations for counting slots.
            pass
    return hours


@bp.route("/")
@bureau_required
def list_members():
    """Render the full member list with current membership status."""
    query = (
        db.select(User)
        .options(selectinload(User.memberships))
        .order_by(User.last_name, User.first_name)
    )

    search = request.args.get("q", "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.where(
            db.or_(
                User.first_name.ilike(pattern),
                User.last_name.ilike(pattern),
                User.email.ilike(pattern),
                User.phone.ilike(pattern),
            )
        )

    users = db.session.scalars(query).all()
    vol_hours = _member_volunteer_hours()

    members = []
    for user in users:
        latest = max(user.memberships, key=lambda m: m.created_at, default=None)
        members.append((user, latest, round(vol_hours.get(user.id, 0.0), 1)))

    return render_template("members/list.html", members=members)


# ---------------------------------------------------------------------------
# Volunteers list — bureau only
# ---------------------------------------------------------------------------


@bp.route("/volunteers")
@bureau_required
def list_volunteers():
    """List external volunteers grouped by email, with total hours and events."""
    from sqlalchemy.orm import joinedload

    from app.models.event import (
        EventVolunteer,
        VolunteerSlotAvailability,
    )

    volunteers = (
        db.session.scalars(
            db.select(EventVolunteer)
            .options(
                joinedload(EventVolunteer.event),
                joinedload(EventVolunteer.slot_availabilities).joinedload(
                    VolunteerSlotAvailability.slot
                ),
            )
            .where(EventVolunteer.confirmed.is_(True))
            .order_by(EventVolunteer.name)
        )
        .unique()
        .all()
    )

    # Group by email
    grouped: dict[str, dict] = {}
    for vol in volunteers:
        email = vol.email.lower()
        if email not in grouped:
            grouped[email] = {
                "name": vol.name,
                "email": email,
                "events": [],
                "hours": 0.0,
                "volunteer_ids": [],
            }
        entry = grouped[email]
        entry["volunteer_ids"].append(vol.id)
        entry["events"].append(vol.event)

        # Compute hours from slot availabilities
        for va in vol.slot_availabilities:
            if va.status.value not in ("present", "maybe"):
                continue
            slot = va.slot
            if not slot.start_time or not slot.end_time:
                continue
            duration_h = (
                (slot.end_time.hour * 60 + slot.end_time.minute)
                - (slot.start_time.hour * 60 + slot.start_time.minute)
            ) / 60
            if duration_h > 0:
                entry["hours"] += duration_h

    # Round hours and sort by name
    for entry in grouped.values():
        entry["hours"] = round(entry["hours"], 1)

    volunteer_list = sorted(grouped.values(), key=lambda v: v["name"].lower())
    return render_template("members/volunteers.html", volunteers=volunteer_list)


@bp.route("/volunteers/<path:email>")
@bureau_required
def volunteer_detail(email: str):
    """Detail page for a volunteer grouped by email."""
    from sqlalchemy.orm import joinedload

    from app.models.event import EventVolunteer, VolunteerSlotAvailability

    volunteers = (
        db.session.scalars(
            db.select(EventVolunteer)
            .options(
                joinedload(EventVolunteer.event),
                joinedload(EventVolunteer.slot_availabilities).joinedload(
                    VolunteerSlotAvailability.slot
                ),
            )
            .where(
                db.func.lower(EventVolunteer.email) == email.lower(),
                EventVolunteer.confirmed.is_(True),
            )
            .order_by(EventVolunteer.registered_at.desc())
        )
        .unique()
        .all()
    )

    if not volunteers:
        abort(404)

    name = volunteers[0].name
    events_data = []
    total_hours = 0.0

    for vol in volunteers:
        hours = 0.0
        for va in vol.slot_availabilities:
            if va.status.value not in ("present", "maybe"):
                continue
            slot = va.slot
            if not slot.start_time or not slot.end_time:
                continue
            duration_h = (
                (slot.end_time.hour * 60 + slot.end_time.minute)
                - (slot.start_time.hour * 60 + slot.start_time.minute)
            ) / 60
            if duration_h > 0:
                hours += duration_h
        hours = round(hours, 1)
        total_hours += hours
        events_data.append(
            {
                "event": vol.event,
                "hours": hours,
                "registered_at": vol.registered_at,
            }
        )

    total_hours = round(total_hours, 1)
    return render_template(
        "members/volunteer_detail.html",
        name=name,
        email=email,
        events=events_data,
        total_hours=total_hours,
    )


@bp.route("/volunteers/<path:email>/certificate.pdf")
@bureau_required
def volunteer_certificate(email: str):
    """Generate a volunteer certificate PDF for an external volunteer."""
    import base64
    from datetime import date

    from sqlalchemy.orm import joinedload

    from app.models.config import AssociationConfig
    from app.models.event import EventVolunteer, VolunteerSlotAvailability

    volunteers = (
        db.session.scalars(
            db.select(EventVolunteer)
            .options(
                joinedload(EventVolunteer.event),
                joinedload(EventVolunteer.slot_availabilities).joinedload(
                    VolunteerSlotAvailability.slot
                ),
            )
            .where(
                db.func.lower(EventVolunteer.email) == email.lower(),
                EventVolunteer.confirmed.is_(True),
            )
            .order_by(EventVolunteer.registered_at.desc())
        )
        .unique()
        .all()
    )

    if not volunteers:
        abort(404)

    name = volunteers[0].name
    participations = []
    total_hours = 0.0

    for vol in volunteers:
        hours = 0.0
        for va in vol.slot_availabilities:
            if va.status.value not in ("present", "maybe"):
                continue
            slot = va.slot
            if not slot.start_time or not slot.end_time:
                continue
            duration_h = (
                (slot.end_time.hour * 60 + slot.end_time.minute)
                - (slot.start_time.hour * 60 + slot.start_time.minute)
            ) / 60
            if duration_h > 0:
                hours += duration_h
        hours = round(hours, 1)
        total_hours += hours
        participations.append(
            {
                "title": vol.event.title,
                "event_date": vol.event.event_date,
                "location": vol.event.location,
                "hours": hours,
            }
        )

    total_hours = round(total_hours, 1)
    if total_hours <= 0:
        flash("Aucune heure de bénévolat enregistrée pour ce bénévole.", "warning")
        return redirect(url_for("members.volunteer_detail", email=email))

    cfg = AssociationConfig.get()
    logo_data = base64.b64encode(cfg.logo).decode("ascii") if cfg.logo else None

    html = render_template(
        "members/certificate.html",
        config=cfg,
        logo_data=logo_data,
        volunteer_name=name,
        participations=participations,
        total_hours=total_hours,
        today=date.today(),
    )

    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html, base_url=".").write_pdf()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("WeasyPrint PDF generation failed")
        return Response(html, mimetype="text/html")

    safe_name = name.replace(" ", "_")
    filename = f"certificat_benevolat_{safe_name}.pdf"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# CSV export / import — bureau only
# ---------------------------------------------------------------------------


@bp.route("/export.csv")
@bureau_required
def export_csv():
    """Export all members as a CSV file."""
    users = db.session.scalars(db.select(User).order_by(User.last_name, User.first_name)).all()
    csv_data = export_members_csv(users)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=membres.csv"},
    )


@bp.route("/import", methods=["POST"])
@bureau_required
def import_csv():
    """Import members from an uploaded CSV file."""
    import secrets

    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("members.list_members"))

    from werkzeug.utils import secure_filename as _secure

    if not _secure(file.filename).lower().endswith(".csv"):
        flash("Seuls les fichiers CSV sont acceptés.", "danger")
        return redirect(url_for("members.list_members"))

    rows, errors = parse_members_csv(file.read())

    if errors:
        for e in errors:
            flash(e, "danger")
        if not rows:
            return redirect(url_for("members.list_members"))

    created = 0
    for row in rows:
        existing = db.session.execute(
            db.select(User).filter_by(email=row["email"])
        ).scalar_one_or_none()
        if existing:
            flash(f"« {row['email']} » ignoré (existe déjà).", "warning")
            continue

        temp_password = secrets.token_urlsafe(12)
        user = User(
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            phone=row["phone"],
            address=row["address"],
            gender=row["gender"],
            role=UserRole(row["role"]),
            is_active=True,
            must_change_password=True,
            permissions=list(MEMBER_DEFAULT_PERMISSIONS)
            if row["role"] == "member"
            else list(BUREAU_DEFAULT_PERMISSIONS),
        )
        user.set_password(temp_password)
        db.session.add(user)
        db.session.flush()
        send_welcome_email(
            user.email,
            temp_password,
            user.full_name,
            url_for("auth.login", _external=True),
        )
        created += 1

    db.session.commit()
    if created:
        flash(
            f"{created} membre(s) importé(s). Un email d'accueil a été envoyé à chacun.", "success"
        )
    return redirect(url_for("members.list_members"))


def _member_event_participations(user_id: int) -> list[dict]:
    """Return event participation details for a member.

    Each dict: {event, slots_count, hours}.
    """
    from app.models.event import Event, EventSlot, SlotAvailability

    rows = db.session.execute(
        db.select(
            Event.id,
            Event.title,
            Event.event_date,
            Event.location,
            EventSlot.start_time,
            EventSlot.end_time,
        )
        .join(EventSlot, EventSlot.event_id == Event.id)
        .join(SlotAvailability, SlotAvailability.slot_id == EventSlot.id)
        .where(
            SlotAvailability.user_id == user_id,
            SlotAvailability.status.in_(["present", "maybe"]),
            Event.status == "completed",
        )
        .order_by(Event.event_date.desc())
    ).all()

    events: dict[int, dict] = {}
    for eid, title, event_date, location, start, end in rows:
        if eid not in events:
            events[eid] = {
                "id": eid,
                "title": title,
                "event_date": event_date,
                "location": location,
                "slots": 0,
                "hours": 0.0,
            }
        events[eid]["slots"] += 1
        if start and end:
            duration_h = ((end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)) / 60
            if duration_h > 0:
                events[eid]["hours"] += duration_h

    for e in events.values():
        e["hours"] = round(e["hours"], 1)
    return list(events.values())


@bp.route("/<int:user_id>")
@bureau_required
def detail(user_id: int):
    """Render the detail page for a single member."""
    from app.models.event import Expense

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    memberships = db.session.scalars(
        db.select(Membership)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at.desc())
    ).all()

    open_assigned_tasks = db.session.scalars(
        db.select(Task).where(
            Task.assigned_to_id == user_id,
            Task.status.in_([TaskStatus.OPEN, TaskStatus.IN_PROGRESS]),
        )
    ).all()

    other_members = db.session.scalars(
        db.select(User)
        .where(User.is_active.is_(True), User.id != user_id)
        .order_by(User.last_name, User.first_name)
    ).all()

    # Event participations and volunteer hours
    participations = _member_event_participations(user_id)
    total_hours = round(sum(p["hours"] for p in participations), 1)

    # Expenses
    expenses = db.session.scalars(
        db.select(Expense)
        .options(selectinload(Expense.event))
        .where(Expense.user_id == user_id)
        .order_by(Expense.submitted_at.desc())
    ).all()

    return render_template(
        "members/detail.html",
        member=user,
        memberships=memberships,
        membership_form=CashMembershipForm(),
        helloasso_form=HelloAssoMembershipForm(),
        delete_form=DeleteMemberForm(),
        open_assigned_tasks=open_assigned_tasks,
        other_members=other_members,
        participations=participations,
        total_hours=total_hours,
        expenses=expenses,
    )


# ---------------------------------------------------------------------------
# Create member — bureau only
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new member account."""
    form = MemberCreateForm()

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        existing = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()
        if existing:
            form.email.errors.append("Cette adresse email est déjà utilisée.")
        else:
            temp_password = form.password.data
            user = User(
                email=email,
                first_name=form.first_name.data.strip(),
                last_name=form.last_name.data.strip(),
                role=form.role.data,
                gender=form.gender.data,
                phone=(form.phone.data or "").strip() or None,
                address=(form.address.data or "").strip() or None,
                is_active=True,
                must_change_password=True,
                permissions=form.permissions.data or [],
            )
            user.set_password(temp_password)
            db.session.add(user)
            db.session.commit()
            send_welcome_email(
                user.email,
                temp_password,
                user.full_name,
                url_for("auth.login", _external=True),
            )
            flash(
                f"Membre {user.full_name} créé. Un email avec les identifiants a été envoyé.",
                "success",
            )
            return redirect(url_for("members.detail", user_id=user.id))

    return render_template("members/form.html", form=form, title="Nouveau membre")


# ---------------------------------------------------------------------------
# Edit member — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(user_id: int):
    """Edit an existing member's profile."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = MemberEditForm(obj=user)

    if form.validate_on_submit():
        new_email = form.email.data.strip().lower()
        if new_email != user.email:
            conflict = db.session.execute(
                db.select(User).filter_by(email=new_email)
            ).scalar_one_or_none()
            if conflict:
                form.email.errors.append("Cette adresse email est déjà utilisée.")
                return render_template(
                    "members/form.html", form=form, title="Modifier le membre", member=user
                )

        user.first_name = form.first_name.data.strip()
        user.last_name = form.last_name.data.strip()
        user.email = new_email
        user.role = form.role.data
        user.gender = form.gender.data
        user.phone = (form.phone.data or "").strip() or None
        user.address = (form.address.data or "").strip() or None
        user.is_active = form.is_active.data
        user.permissions = form.permissions.data or []
        db.session.commit()
        flash("Profil mis à jour.", "success")
        return redirect(url_for("members.detail", user_id=user.id))

    return render_template("members/form.html", form=form, title="Modifier le membre", member=user)


# ---------------------------------------------------------------------------
# Add cash membership — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:user_id>/membership/new", methods=["POST"])
@bureau_required
def add_membership(user_id: int):
    """Record a cash membership payment for a member."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = CashMembershipForm()
    if form.validate_on_submit():
        membership = Membership(
            user_id=user.id,
            source=MembershipSource.CASH,
            amount=form.amount.data,
            started_at=form.started_at.data,
            expires_at=form.expires_at.data,
            notes=(form.notes.data or "").strip() or None,
            is_pending=False,
        )
        db.session.add(membership)
        db.session.commit()
        flash("Cotisation enregistrée.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    return redirect(url_for("members.detail", user_id=user_id))


# ---------------------------------------------------------------------------
# Member certificate — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:user_id>/certificate.pdf")
@bureau_required
def member_certificate(user_id: int):
    """Generate a volunteer certificate PDF for a member."""
    import base64
    from datetime import date

    from app.models.config import AssociationConfig

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    participations = _member_event_participations(user_id)
    total_hours = round(sum(p["hours"] for p in participations), 1)

    if total_hours <= 0:
        flash("Aucune heure de bénévolat enregistrée pour ce membre.", "warning")
        return redirect(url_for("members.detail", user_id=user_id))

    cfg = AssociationConfig.get()
    logo_data = base64.b64encode(cfg.logo).decode("ascii") if cfg.logo else None

    html = render_template(
        "members/certificate.html",
        config=cfg,
        logo_data=logo_data,
        volunteer_name=user.full_name,
        participations=participations,
        total_hours=total_hours,
        today=date.today(),
    )

    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html, base_url=".").write_pdf()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("WeasyPrint PDF generation failed")
        return Response(html, mimetype="text/html")

    filename = f"certificat_benevolat_{user.last_name}_{user.first_name}.pdf"
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Add HelloAsso membership — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:user_id>/membership/helloasso", methods=["POST"])
@bureau_required
def add_helloasso_membership(user_id: int):
    """Declare a HelloAsso membership (payment handled externally)."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = HelloAssoMembershipForm()
    if form.validate_on_submit():
        membership = Membership(
            user_id=user.id,
            source=MembershipSource.HELLOASSO,
            amount=0,
            started_at=form.started_at.data,
            expires_at=form.expires_at.data,
            notes=(form.notes.data or "").strip() or None,
            is_pending=False,
        )
        db.session.add(membership)
        db.session.commit()
        flash("Adhésion HelloAsso enregistrée.", "success")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                flash(error, "danger")

    return redirect(url_for("members.detail", user_id=user_id))


# ---------------------------------------------------------------------------
# Delete member — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:user_id>/delete", methods=["POST"])
@bureau_required
def delete(user_id: int):
    """Delete a member account, optionally transferring their open tasks first."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    if user.id == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for("members.detail", user_id=user_id))

    form = DeleteMemberForm()
    if not form.validate_on_submit():
        abort(400)

    try:
        transfer_to_id = int(request.form.get("transfer_to_id", ""))
        if transfer_to_id <= 0:
            transfer_to_id = None
    except (ValueError, TypeError):
        transfer_to_id = None

    if transfer_to_id:
        transfer_to = db.session.get(User, transfer_to_id)
        if transfer_to and transfer_to.id != user_id and transfer_to.is_active:
            open_tasks = db.session.scalars(
                db.select(Task).where(
                    Task.assigned_to_id == user_id,
                    Task.status.in_([TaskStatus.OPEN, TaskStatus.IN_PROGRESS]),
                )
            ).all()
            for task in open_tasks:
                task.assigned_to_id = transfer_to_id
            db.session.flush()

    name = user.full_name
    db.session.delete(user)
    db.session.commit()
    flash(f"Le compte de {name} a été supprimé définitivement.", "success")
    return redirect(url_for("members.list_members"))


# ---------------------------------------------------------------------------
# Own profile — any authenticated member
# ---------------------------------------------------------------------------


@bp.route("/profile")
@login_required
def profile():
    """Render the currently authenticated user's own profile page."""
    memberships = db.session.scalars(
        db.select(Membership)
        .where(Membership.user_id == current_user.id)
        .order_by(Membership.created_at.desc())
    ).all()

    participations = _member_event_participations(current_user.id)
    total_hours = round(sum(p["hours"] for p in participations), 1)

    return render_template(
        "members/profile.html",
        member=current_user,
        memberships=memberships,
        participations=participations,
        total_hours=total_hours,
    )


@bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    """Allow the authenticated user to edit their own personal information."""
    form = ProfileEditForm(obj=current_user)

    if form.validate_on_submit():
        new_email = form.email.data.strip().lower()
        if new_email != current_user.email:
            conflict = db.session.execute(
                db.select(User).filter_by(email=new_email)
            ).scalar_one_or_none()
            if conflict:
                form.email.errors.append("Cette adresse email est déjà utilisée.")
                return render_template("members/profile_edit.html", form=form)

        current_user.first_name = form.first_name.data.strip()
        current_user.last_name = form.last_name.data.strip()
        current_user.email = new_email
        current_user.gender = form.gender.data
        current_user.phone = form.phone.data.strip() or None
        current_user.address = form.address.data.strip() or None
        db.session.commit()
        flash("Vos informations ont été mises à jour.", "success")
        return redirect(url_for("members.profile"))

    return render_template("members/profile_edit.html", form=form)


# ---------------------------------------------------------------------------
# RGPD purge — bureau only
# ---------------------------------------------------------------------------


@bp.route("/purge-inactive", methods=["GET", "POST"])
@bureau_required
def purge_inactive():
    """Show inactive accounts older than 2 years and allow bulk deletion (RGPD).

    Only accounts with ``is_active=False`` and last-updated more than 730 days ago
    are shown.  The bureau must confirm the purge by submitting the form.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=730)
    candidates = db.session.scalars(
        db.select(User)
        .where(User.is_active.is_(False), User.updated_at < cutoff)
        .order_by(User.updated_at)
    ).all()

    if request.method == "POST":
        # CSRF is validated by Flask-WTF's global after-request hook
        count = len(candidates)
        for user in candidates:
            db.session.delete(user)
        db.session.commit()
        flash(
            f"{count} compte(s) inactif(s) supprimé(s) définitivement.",
            "success" if count else "info",
        )
        return redirect(url_for("members.list_members"))

    return render_template("members/purge_inactive.html", candidates=candidates, cutoff=cutoff)
