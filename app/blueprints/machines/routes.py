"""Machines blueprint routes — inventory, installations, maintenance."""

import base64
import logging
import secrets
from datetime import date

from flask import Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.blueprints.centers.forms import FeedbackForm
from app.blueprints.machines import bp
from app.blueprints.machines.forms import (
    InstallMachineForm,
    MachineForm,
    MaintenanceRecordForm,
    PublicBreakdownForm,
    PublicMachineBreakdownForm,
    RemoveInstallationForm,
    ResolveMaintenanceForm,
)
from app.decorators import bureau_required
from app.extensions import db, limiter
from app.models.center import Center, CenterFeedback, CenterStatus
from app.models.document import Document, DocumentType, machine_documents
from app.models.event import EventMachine
from app.models.machine import (
    Machine,
    MachineInstallation,
    MachineStatus,
    MaintenanceRecord,
    MaintenanceStatus,
)
from app.models.task import Task
from app.models.user import User
from app.services.csv_io import export_machines_csv, parse_machines_csv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# List and detail
# ---------------------------------------------------------------------------


_SORT_COLUMNS = {
    "name": (Machine.manufacturer, Machine.model),
    "status": (Machine.status,),
}


@bp.route("/")
@login_required
def list_machines():
    """List machines with optional full-text search, status filter, and column sort."""
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)
    sort = request.args.get("sort", "name")
    dir_ = request.args.get("dir", "asc")
    if sort not in _SORT_COLUMNS:
        sort = "name"
    if dir_ not in ("asc", "desc"):
        dir_ = "asc"

    order_cols = _SORT_COLUMNS[sort]
    if dir_ == "desc":
        order_cols = tuple(c.desc() for c in order_cols)

    stmt = (
        db.select(Machine)
        .options(
            selectinload(Machine.installations).selectinload(MachineInstallation.center),
            selectinload(Machine.installations).selectinload(MachineInstallation.hosted_by),
            selectinload(Machine.maintenance_records),
        )
        .order_by(*order_cols)
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            db.or_(
                Machine.manufacturer.ilike(pattern),
                Machine.model.ilike(pattern),
                Machine.serial_number.ilike(pattern),
                Machine.internal_number.ilike(pattern),
            )
        )
    if status and status in {s.value for s in MachineStatus}:
        stmt = stmt.where(Machine.status == status)

    pagination = db.paginate(stmt, page=page, per_page=25, error_out=False)
    return render_template(
        "machines/list.html",
        machines=pagination.items,
        pagination=pagination,
        q=q,
        status=status,
        sort=sort,
        dir_=dir_,
        MachineStatus=MachineStatus,
        centers=_active_center_choices(),
        members=_member_choices(),
        today=date.today().isoformat(),
    )


# ---------------------------------------------------------------------------
# CSV export — bureau only
# ---------------------------------------------------------------------------


@bp.route("/export.csv")
@bureau_required
def export_csv():
    """Export all machines as a CSV file."""
    machines = db.session.scalars(
        db.select(Machine).order_by(Machine.manufacturer, Machine.model)
    ).all()
    csv_data = export_machines_csv(machines)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=machines.csv"},
    )


# ---------------------------------------------------------------------------
# CSV import — bureau only
# ---------------------------------------------------------------------------


@bp.route("/import", methods=["POST"])
@bureau_required
def import_csv():
    """Import machines from an uploaded CSV file."""
    file = request.files.get("csv_file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(url_for("machines.list_machines"))

    rows, errors = parse_machines_csv(file.read())

    if errors:
        for e in errors:
            flash(e, "danger")
        if not rows:
            return redirect(url_for("machines.list_machines"))

    created = 0
    skipped = 0
    for row in rows:
        # Skip if serial number already exists
        if row["serial_number"]:
            existing = db.session.scalars(
                db.select(Machine).where(Machine.serial_number == row["serial_number"])
            ).first()
            if existing:
                skipped += 1
                continue

        status_val = row["status"]
        try:
            status = MachineStatus(status_val)
        except ValueError:
            status = MachineStatus.STOCK

        machine = Machine(
            internal_number=row["internal_number"],
            model=row["model"],
            manufacturer=row["manufacturer"],
            serial_number=row["serial_number"],
            year=row["year"],
            status=status,
            notes=row["notes"],
        )
        db.session.add(machine)
        created += 1

    db.session.commit()
    if created:
        flash(f"{created} machine(s) importée(s).", "success")
    if skipped:
        flash(f"{skipped} machine(s) ignorée(s) (numéro de série déjà présent).", "warning")
    return redirect(url_for("machines.list_machines"))


@bp.route("/<int:machine_id>")
@login_required
def detail(machine_id: int):
    """Render the detail page for a single machine."""
    machine = db.session.get(
        Machine,
        machine_id,
        options=[
            selectinload(Machine.installations).selectinload(MachineInstallation.center),
            selectinload(Machine.installations).selectinload(MachineInstallation.hosted_by),
            selectinload(Machine.maintenance_records).selectinload(MaintenanceRecord.resolved_by),
            selectinload(Machine.maintenance_records).selectinload(MaintenanceRecord.source_task),
            selectinload(Machine.tasks),
            selectinload(Machine.documents).selectinload(Document.uploaded_by),
            selectinload(Machine.event_machines).selectinload(EventMachine.event),
        ],
    )
    if machine is None:
        abort(404)

    install_form = InstallMachineForm()
    install_form.center_id.choices = [("", "— choisir —")] + _active_center_choices()
    install_form.hosted_by_id.choices = [("", "— choisir —")] + _member_choices()

    remove_form = RemoveInstallationForm()
    remove_form.move_to_member_id.choices = [("", "— aucun —")] + _member_choices()

    maint_form = MaintenanceRecordForm()
    maint_form.maintainer_name.data = maint_form.maintainer_name.data or current_user.full_name
    resolve_form = ResolveMaintenanceForm()

    pending_install = session.pop(f"pending_install_{machine_id}", None)

    return render_template(
        "machines/detail.html",
        machine=machine,
        install_form=install_form,
        remove_form=remove_form,
        maint_form=maint_form,
        resolve_form=resolve_form,
        MachineStatus=MachineStatus,
        MaintenanceStatus=MaintenanceStatus,
        today=date.today().isoformat(),
        pending_install=pending_install,
    )


# ---------------------------------------------------------------------------
# Create and edit
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new machine record."""
    form = MachineForm()
    if form.validate_on_submit():
        serial = (form.serial_number.data or "").strip() or None
        if serial:
            exists = db.session.execute(
                db.select(Machine).filter_by(serial_number=serial)
            ).scalar_one_or_none()
            if exists:
                form.serial_number.errors.append("Ce numéro de série est déjà enregistré.")
                return render_template("machines/form.html", form=form, title="Nouvelle machine")

        internal_number = (form.internal_number.data or "").strip() or None
        if internal_number:
            exists = db.session.execute(
                db.select(Machine).filter_by(internal_number=internal_number)
            ).scalar_one_or_none()
            if exists:
                form.internal_number.errors.append("Ce numéro est déjà utilisé.")
                return render_template("machines/form.html", form=form, title="Nouvelle machine")

        machine = Machine(
            internal_number=internal_number,
            model=form.model.data.strip(),
            manufacturer=form.manufacturer.data.strip(),
            serial_number=serial,
            year=form.year.data,
            status=form.status.data,
            notes=(form.notes.data or "").strip() or None,
            purchase_date=form.purchase_date.data,
            purchase_price=form.purchase_price.data,
            estimated_value=form.estimated_value.data,
        )
        db.session.add(machine)
        db.session.commit()
        flash(f"Machine « {machine.display_name} » créée.", "success")
        return redirect(url_for("machines.detail", machine_id=machine.id))

    return render_template("machines/form.html", form=form, title="Nouvelle machine")


@bp.route("/<int:machine_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(machine_id: int):
    """Edit an existing machine record."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)

    form = MachineForm(obj=machine)
    if form.validate_on_submit():
        serial = (form.serial_number.data or "").strip() or None
        if serial and serial != machine.serial_number:
            exists = db.session.execute(
                db.select(Machine).filter_by(serial_number=serial)
            ).scalar_one_or_none()
            if exists:
                form.serial_number.errors.append("Ce numéro de série est déjà enregistré.")
                return render_template(
                    "machines/form.html", form=form, title="Modifier la machine", machine=machine
                )

        internal_number = (form.internal_number.data or "").strip() or None
        if internal_number and internal_number != machine.internal_number:
            exists = db.session.execute(
                db.select(Machine).filter_by(internal_number=internal_number)
            ).scalar_one_or_none()
            if exists:
                form.internal_number.errors.append("Ce numéro est déjà utilisé.")
                return render_template(
                    "machines/form.html", form=form, title="Modifier la machine", machine=machine
                )

        machine.internal_number = internal_number
        machine.model = form.model.data.strip()
        machine.manufacturer = form.manufacturer.data.strip()
        machine.serial_number = serial
        machine.year = form.year.data
        # Block manual status change while machine is in MAINTENANCE — only a
        # resolved maintenance can bring it back to INSTALLED/STOCK.
        if machine.status != MachineStatus.MAINTENANCE:
            machine.status = form.status.data
        machine.notes = (form.notes.data or "").strip() or None
        db.session.commit()
        flash("Machine mise à jour.", "success")
        return redirect(url_for("machines.detail", machine_id=machine.id))

    return render_template(
        "machines/form.html", form=form, title="Modifier la machine", machine=machine
    )


# ---------------------------------------------------------------------------
# Installations
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/install", methods=["POST"])
@bureau_required
def install(machine_id: int):
    """Install a machine at a center or a member's home (or confirm a move)."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)

    # ── Confirmed move: use data saved in session ─────────────────────────────
    if request.form.get("confirmed_move") == "1":
        pending = session.pop(f"pending_install_{machine_id}", None)
        if not pending:
            flash("Session expirée. Veuillez réessayer l'installation.", "warning")
            return redirect(url_for("machines.detail", machine_id=machine_id))
        active = db.session.execute(
            db.select(MachineInstallation).where(
                MachineInstallation.machine_id == machine_id,
                MachineInstallation.removed_at.is_(None),
            )
        ).scalar_one_or_none()
        if active:
            active.removed_at = date.today()
        installation = MachineInstallation(
            machine_id=machine_id,
            center_id=pending["center_id"],
            hosted_by_id=pending["hosted_by_id"],
            installed_at=date.fromisoformat(pending["installed_at"]),
            notes=pending["notes"],
        )
        machine.status = MachineStatus.INSTALLED
        db.session.add(installation)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Impossible de déplacer la machine.", "danger")
        else:
            flash(f"Machine déplacée {pending['label']}.", "success")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    # ── Standard install flow ─────────────────────────────────────────────────
    form = InstallMachineForm()
    form.center_id.choices = [("", "—")] + _active_center_choices()
    form.hosted_by_id.choices = [("", "—")] + _member_choices()

    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    loc_type = form.location_type.data
    center_id = None
    hosted_by_id = None
    label = ""

    if loc_type == "member":
        hosted_by_id = form.hosted_by_id.data
        if not hosted_by_id:
            flash("Veuillez sélectionner un membre.", "danger")
            return redirect(url_for("machines.detail", machine_id=machine_id))
        member = db.session.get(User, hosted_by_id)
        if member is None:
            flash("Membre introuvable.", "danger")
            return redirect(url_for("machines.detail", machine_id=machine_id))
        label = f"chez {member.full_name}"
    else:
        center_id = form.center_id.data
        if not center_id:
            flash("Veuillez sélectionner un centre.", "danger")
            return redirect(url_for("machines.detail", machine_id=machine_id))
        center = db.session.get(Center, center_id)
        if center is None:
            flash("Centre introuvable.", "danger")
            return redirect(url_for("machines.detail", machine_id=machine_id))
        label = f"au centre « {center.name} »"

    # Check for existing active installation
    active = db.session.execute(
        db.select(MachineInstallation)
        .options(
            selectinload(MachineInstallation.center),
            selectinload(MachineInstallation.hosted_by),
        )
        .where(
            MachineInstallation.machine_id == machine_id,
            MachineInstallation.removed_at.is_(None),
        )
    ).scalar_one_or_none()

    if active:
        current_loc = (
            active.center.name
            if active.center
            else (active.hosted_by.full_name if active.hosted_by else "lieu inconnu")
        )
        session[f"pending_install_{machine_id}"] = {
            "location_type": loc_type,
            "center_id": center_id,
            "hosted_by_id": hosted_by_id,
            "installed_at": str(form.installed_at.data),
            "notes": (form.notes.data or "").strip() or None,
            "label": label,
            "current_loc": current_loc,
            "current_since": active.installed_at.strftime("%d/%m/%Y"),
        }
        return redirect(url_for("machines.detail", machine_id=machine_id))

    installation = MachineInstallation(
        machine_id=machine_id,
        center_id=center_id,
        hosted_by_id=hosted_by_id,
        installed_at=form.installed_at.data,
        notes=(form.notes.data or "").strip() or None,
    )
    machine.status = MachineStatus.INSTALLED
    db.session.add(installation)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Cette machine est déjà installée.", "danger")
        return redirect(url_for("machines.detail", machine_id=machine_id))
    flash(f"Machine installée {label}.", "success")
    next_url = request.form.get("_next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("machines.detail", machine_id=machine_id))


@bp.route("/<int:machine_id>/installations/<int:inst_id>/remove", methods=["POST"])
@bureau_required
def remove_installation(machine_id: int, inst_id: int):
    """Mark an active installation as removed.

    If the machine was at a center, the center is set to INACTIVE.
    Optionally moves the machine directly to a member's home.
    """
    installation = db.session.get(MachineInstallation, inst_id)
    if installation is None or installation.machine_id != machine_id:
        abort(404)
    if not installation.is_active:
        flash("Cette installation est déjà terminée.", "warning")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    form = RemoveInstallationForm()
    form.move_to_member_id.choices = [("", "—")] + _member_choices()

    if form.validate_on_submit():
        installation.removed_at = form.removed_at.data

        # If retrieved from a center, set center to INACTIVE
        if installation.center_id:
            center = db.session.get(Center, installation.center_id)
            if center and center.status != CenterStatus.INACTIVE:
                center.status = CenterStatus.INACTIVE

        move_to = form.move_to_member_id.data
        if move_to:
            member = db.session.get(User, move_to)
            if member:
                new_inst = MachineInstallation(
                    machine_id=machine_id,
                    hosted_by_id=member.id,
                    installed_at=form.removed_at.data,
                    notes=f"Récupérée et déplacée chez {member.full_name}",
                )
                db.session.add(new_inst)
                installation.machine.status = MachineStatus.INSTALLED
                flash(f"Machine récupérée et déplacée chez {member.full_name}.", "success")
            else:
                installation.machine.status = MachineStatus.STOCK
                flash("Machine récupérée et remise en stock.", "success")
        else:
            installation.machine.status = MachineStatus.STOCK
            flash("Machine récupérée et remise en stock.", "success")

        db.session.commit()
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    next_url = request.form.get("_next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("machines.detail", machine_id=machine_id))


# ---------------------------------------------------------------------------
# Maintenance records
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/maintenance", methods=["POST"])
@bureau_required
def add_maintenance(machine_id: int):
    """Add an open maintenance record for a machine.

    Sets the machine to MAINTENANCE status regardless of its current state
    (unless RETIRED). Only resolving the maintenance restores the previous status.
    """
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)

    form = MaintenanceRecordForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    # Validate optional task link
    task_id = form.source_task_id.data
    if task_id:
        task = db.session.get(Task, task_id)
        if task is None:
            flash(f"Tâche #{task_id} introuvable.", "danger")
            return redirect(url_for("machines.detail", machine_id=machine_id))

    # Capture the center where the machine currently is (if any)
    active_install = db.session.execute(
        db.select(MachineInstallation).where(
            MachineInstallation.machine_id == machine_id,
            MachineInstallation.removed_at.is_(None),
        )
    ).scalar_one_or_none()

    record = MaintenanceRecord(
        machine_id=machine_id,
        center_id=active_install.center_id if active_install else None,
        date=form.date.data,
        description=form.description.data.strip(),
        cost=form.cost.data,
        maintainer_name=form.maintainer_name.data.strip(),
        maintainer_user_id=current_user.id,
        source_task_id=task_id,
        status=MaintenanceStatus.OPEN,
    )
    # Any non-retired machine goes into MAINTENANCE status on opening a record.
    if machine.status != MachineStatus.RETIRED:
        machine.status = MachineStatus.MAINTENANCE
    db.session.add(record)
    db.session.commit()
    flash("Fiche de maintenance ouverte.", "success")
    next_url = request.form.get("_next", "")
    # Reject protocol-relative URLs (//attacker.com) — only accept path-relative redirects.
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("machines.detail", machine_id=machine_id))


@bp.route("/<int:machine_id>/maintenance/<int:record_id>/resolve", methods=["POST"])
@bureau_required
def resolve_maintenance(machine_id: int, record_id: int):
    """Resolve an open maintenance record and restore the machine status.

    If no other open maintenance records remain for this machine, the status
    is restored to INSTALLED (if an active installation exists) or STOCK.
    """
    machine = db.session.get(
        Machine,
        machine_id,
        options=[selectinload(Machine.maintenance_records)],
    )
    if machine is None:
        abort(404)

    record = db.session.get(MaintenanceRecord, record_id)
    if record is None or record.machine_id != machine_id:
        abort(404)

    if record.status == MaintenanceStatus.RESOLVED:
        flash("Cette fiche est déjà résolue.", "warning")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    form = ResolveMaintenanceForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
        return redirect(url_for("machines.detail", machine_id=machine_id))

    record.status = MaintenanceStatus.RESOLVED
    record.resolved_at = form.resolved_at.data
    record.resolved_by_id = current_user.id
    record.resolution_comment = (form.resolution_comment.data or "").strip() or None

    # Restore machine status only when no other open maintenance records remain.
    still_open = [
        r
        for r in machine.maintenance_records
        if r.id != record_id and r.status == MaintenanceStatus.OPEN
    ]
    if not still_open:
        active_install = db.session.execute(
            db.select(MachineInstallation).where(
                MachineInstallation.machine_id == machine_id,
                MachineInstallation.removed_at.is_(None),
            )
        ).scalar_one_or_none()
        machine.status = MachineStatus.INSTALLED if active_install else MachineStatus.STOCK
        # Reflect the repair date as the last known activity on the machine.
        machine.last_checked_at = record.resolved_at

    db.session.commit()
    flash("Maintenance résolue, statut machine restauré.", "success")
    return redirect(url_for("machines.detail", machine_id=machine_id))


@bp.route("/<int:machine_id>/maintenance/<int:record_id>/delete", methods=["POST"])
@bureau_required
def delete_maintenance(machine_id: int, record_id: int):
    """Delete a maintenance record.

    If the deleted record was open and no other open records remain,
    the machine status is restored.
    """
    machine = db.session.get(
        Machine,
        machine_id,
        options=[selectinload(Machine.maintenance_records)],
    )
    if machine is None:
        abort(404)

    record = db.session.get(MaintenanceRecord, record_id)
    if record is None or record.machine_id != machine_id:
        abort(404)

    was_open = record.status == MaintenanceStatus.OPEN
    db.session.delete(record)

    if was_open:
        still_open = [
            r
            for r in machine.maintenance_records
            if r.id != record_id and r.status == MaintenanceStatus.OPEN
        ]
        if not still_open:
            active_install = db.session.execute(
                db.select(MachineInstallation).where(
                    MachineInstallation.machine_id == machine_id,
                    MachineInstallation.removed_at.is_(None),
                )
            ).scalar_one_or_none()
            machine.status = MachineStatus.INSTALLED if active_install else MachineStatus.STOCK

    db.session.commit()
    flash("Fiche de maintenance supprimée.", "success")
    return redirect(url_for("machines.detail", machine_id=machine_id))


# ---------------------------------------------------------------------------
# Operational check — any authenticated user
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/check", methods=["POST"])
@login_required
def check_operational(machine_id: int):
    """Mark a machine as operational, resetting the days-since-last-activity counter."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    machine.last_checked_at = date.today()
    db.session.commit()
    flash("Machine signalée opérationnelle.", "success")
    next_url = request.form.get("_next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("machines.detail", machine_id=machine_id))


# ---------------------------------------------------------------------------
# Public breakdown report — centers (no auth, token-protected)
# ---------------------------------------------------------------------------


@bp.route("/breakdown/<token>", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def public_breakdown(token: str):
    """Public page for a center to report a machine breakdown.

    Accessible via a signed URL containing the center's breakdown_token.
    No authentication required — the token acts as proof of identity.
    """
    center = db.session.execute(
        db.select(Center).where(Center.breakdown_token == token)
    ).scalar_one_or_none()
    if center is None:
        abort(404)

    # Find machines currently installed at this center
    active_installations = db.session.scalars(
        db.select(MachineInstallation)
        .options(selectinload(MachineInstallation.machine))
        .where(
            MachineInstallation.center_id == center.id,
            MachineInstallation.removed_at.is_(None),
        )
    ).all()

    if not active_installations:
        return render_template(
            "machines/public_breakdown.html",
            center=center,
            form=None,
            no_machines=True,
        )

    form = PublicBreakdownForm()
    form.machine_id.choices = [
        (inst.machine.id, inst.machine.display_name) for inst in active_installations
    ]

    if form.validate_on_submit():
        machine = db.session.get(Machine, form.machine_id.data)
        if machine is None:
            abort(404)

        record = MaintenanceRecord(
            machine_id=machine.id,
            center_id=center.id,
            date=date.today(),
            description=form.description.data.strip(),
            maintainer_name=form.reporter_name.data.strip(),
            status=MaintenanceStatus.OPEN,
        )
        if machine.status != MachineStatus.RETIRED:
            machine.status = MachineStatus.MAINTENANCE
        db.session.add(record)
        db.session.commit()

        return render_template(
            "machines/public_breakdown.html",
            center=center,
            form=None,
            submitted=True,
            machine=machine,
        )

    return render_template(
        "machines/public_breakdown.html",
        center=center,
        form=form,
    )


# ---------------------------------------------------------------------------
# Delete machine — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/delete", methods=["POST"])
@bureau_required
def delete(machine_id: int):
    """Delete a machine and all its related records."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    name = machine.display_name
    db.session.delete(machine)
    db.session.commit()
    flash(f"Machine « {name} » supprimée.", "success")
    return redirect(url_for("machines.list_machines"))


# ---------------------------------------------------------------------------
# Machine documents — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/documents", methods=["POST"])
@bureau_required
def upload_document(machine_id: int):
    """Upload a document and attach it to a machine."""
    import logging
    import os
    import re

    from flask import current_app
    from werkzeug.utils import secure_filename

    logger = logging.getLogger(__name__)

    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)

    back = url_for("machines.detail", machine_id=machine_id)
    files = request.files.getlist("file")
    if not files or all(not f.filename for f in files):
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(back)

    from app.blueprints.documents.routes import _detect_mime

    description = request.form.get("description", "").strip() or None
    allowed_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".odt"}
    expected_mimes: dict[str, set[str]] = {
        ".jpg": {"image/jpeg"},
        ".jpeg": {"image/jpeg"},
        ".png": {"image/png"},
        ".gif": {"image/gif"},
        ".webp": {"image/webp"},
        ".pdf": {"application/pdf"},
        ".docx": {"application/zip"},
        ".xlsx": {"application/zip"},
        ".odt": {"application/zip"},
    }
    added = 0
    upload_errors: list[str] = []

    for file in files:
        if not file.filename:
            continue

        safe_name = secure_filename(file.filename)
        if not safe_name:
            upload_errors.append(f"« {file.filename} » : nom invalide.")
            continue

        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in allowed_exts:
            upload_errors.append(f"« {file.filename} » : extension non autorisée.")
            continue

        data = file.read()
        if len(data) > 20 * 1024 * 1024:
            upload_errors.append(f"« {file.filename} » : dépasse la limite de 20 Mo.")
            continue

        detected_mime = _detect_mime(data)
        if detected_mime not in expected_mimes.get(ext, set()):
            upload_errors.append(f"« {file.filename} » : contenu ne correspond pas à l'extension.")
            continue

        slug = (
            re.sub(r"[^a-z0-9]+", "-", os.path.splitext(safe_name)[0].lower()).strip("-")[:40]
            or "doc"
        )
        stored_name = f"{date.today().isoformat()}_machine_{machine_id}_{slug}{ext}"

        doc = Document(
            original_filename=file.filename,
            stored_filename=stored_name,
            type=DocumentType.MACHINE.value,
            category="machine",
            mime_type=detected_mime or "application/octet-stream",
            size_bytes=len(data),
            uploaded_by_id=current_user.id,
            description=description,
        )
        db.session.add(doc)
        db.session.flush()
        db.session.execute(
            machine_documents.insert().values(machine_id=machine_id, document_id=doc.id)
        )

        drive_uploaded = False
        if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
            try:
                from app.services.drive import DriveService

                file_id, web_link = DriveService.from_db().upload_file(
                    data, file.filename, doc.mime_type, DocumentType.MACHINE.value
                )
                doc.drive_file_id = file_id
                doc.drive_web_link = web_link
                drive_uploaded = True
            except Exception as exc:
                logger.warning("Drive upload failed for machine doc: %s", exc)

        if not drive_uploaded:
            subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "machines")
            os.makedirs(subdir, exist_ok=True)
            with open(os.path.join(subdir, stored_name), "wb") as fh:
                fh.write(data)

        added += 1

    db.session.commit()
    if added:
        msg = f"{added} document{'s' if added > 1 else ''} ajouté{'s' if added > 1 else ''}."
        flash(msg, "success")
    for e in upload_errors:
        flash(e, "danger")
    return redirect(back)


@bp.route("/<int:machine_id>/documents/<int:document_id>/detach", methods=["POST"])
@bureau_required
def detach_document(machine_id: int, document_id: int):
    """Remove the link between a document and a machine (keeps the file)."""
    db.session.execute(
        machine_documents.delete().where(
            machine_documents.c.machine_id == machine_id,
            machine_documents.c.document_id == document_id,
        )
    )
    db.session.commit()
    flash("Document détaché.", "info")
    return redirect(url_for("machines.detail", machine_id=machine_id))


# ---------------------------------------------------------------------------
# PDF exports — fiche technique + convention de prêt
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/fiche.pdf")
@login_required
def fiche_pdf(machine_id: int):
    """Generate and serve the machine technical sheet as PDF."""
    machine = db.session.get(
        Machine,
        machine_id,
        options=[
            selectinload(Machine.installations).selectinload(MachineInstallation.center),
            selectinload(Machine.maintenance_records),
        ],
    )
    if machine is None:
        abort(404)
    html = render_template("machines/fiche.html", machine=machine, today=date.today())
    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html).write_pdf()
    except Exception:
        return Response(html, mimetype="text/html")
    safe = _make_safe_filename(machine.display_name)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="fiche_{safe}.pdf"'},
    )


@bp.route("/<int:machine_id>/convention.pdf")
@bureau_required
def convention_pdf(machine_id: int):
    """Generate and serve the machine loan agreement as PDF."""
    from app.models.config import AssociationConfig

    machine = db.session.get(
        Machine,
        machine_id,
        options=[
            selectinload(Machine.installations)
            .selectinload(MachineInstallation.center)
            .selectinload(Center.contacts)
        ],
    )
    if machine is None:
        abort(404)
    cfg = AssociationConfig.get()
    active_inst = machine.current_installation
    center = active_inst.center if active_inst else None
    html = render_template(
        "machines/convention_pret.html",
        machine=machine,
        cfg=cfg,
        active_inst=active_inst,
        center=center,
        today=date.today(),
    )
    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html).write_pdf()
    except Exception:
        return Response(html, mimetype="text/html")
    safe = _make_safe_filename(machine.display_name)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="convention_{safe}.pdf"'},
    )


# ---------------------------------------------------------------------------
# QR code — authenticated users
# ---------------------------------------------------------------------------


@bp.route("/<int:machine_id>/qr.svg")
@login_required
def qr_svg(machine_id: int):
    """Return a QR code SVG pointing to the machine's public page."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    public_url = url_for("machines.machine_public", machine_id=machine_id, _external=True)
    return Response(_make_qr_svg(public_url), mimetype="image/svg+xml")


# ---------------------------------------------------------------------------
# Public machine page — no authentication required
# ---------------------------------------------------------------------------


@bp.route("/p/<int:machine_id>")
def machine_public(machine_id: int):
    """Public-facing machine page — accessible without login (e.g. via QR code).

    Shows the machine's basic info and the center where it is currently
    installed.  No sensitive data is exposed.
    """
    machine = db.session.get(
        Machine,
        machine_id,
        options=[
            selectinload(Machine.installations).selectinload(MachineInstallation.center),
        ],
    )
    if machine is None:
        abort(404)
    # Link to the machine guestbook (resolves the current center, or "during an
    # event" when the machine is not installed anywhere).
    feedback_url = None
    if machine.public_token:
        feedback_url = url_for(
            "machines.public_machine_feedback",
            token=machine.public_token,
            _external=True,
        )
    return render_template(
        "machines/public.html",
        machine=machine,
        feedback_url=feedback_url,
    )


# ---------------------------------------------------------------------------
# Machine QR codes — breakdown + guestbook stuck physically on the machine
# ---------------------------------------------------------------------------


def _ensure_public_token(machine: Machine) -> str:
    """Return the machine's permanent public token, generating it once if absent.

    The token is immutable: it encodes only the machine (never the center), so
    the printed QR sticker stays valid when the machine moves between centers.
    """
    if not machine.public_token:
        machine.public_token = secrets.token_urlsafe(32)
        db.session.commit()
    return machine.public_token


def _machine_by_public_token(token: str) -> Machine:
    """Look up a machine by its public token or abort 404."""
    machine = db.session.execute(
        db.select(Machine).where(Machine.public_token == token)
    ).scalar_one_or_none()
    if machine is None:
        abort(404)
    return machine


@bp.route("/<int:machine_id>/public-links", methods=["POST"])
@bureau_required
def generate_public_links(machine_id: int):
    """Create the permanent public token for a machine's QR codes (idempotent)."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    _ensure_public_token(machine)
    flash("QR codes de la machine générés.", "success")
    return redirect(url_for("machines.detail", machine_id=machine_id))


@bp.route("/<int:machine_id>/breakdown-qr.svg")
@login_required
def breakdown_qr_svg(machine_id: int):
    """QR code pointing to the machine's public breakdown form."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    token = _ensure_public_token(machine)
    url = url_for("machines.public_machine_breakdown", token=token, _external=True)
    return Response(_make_qr_svg(url), mimetype="image/svg+xml")


@bp.route("/<int:machine_id>/feedback-qr.svg")
@login_required
def feedback_qr_svg(machine_id: int):
    """QR code pointing to the machine's public guestbook form."""
    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    token = _ensure_public_token(machine)
    url = url_for("machines.public_machine_feedback", token=token, _external=True)
    return Response(_make_qr_svg(url), mimetype="image/svg+xml")


_DEFAULT_CARD_MESSAGE = (
    "Un souci avec ce flipper ? Une expérience à partager ? Scannez le QR code correspondant."
)


@bp.route("/<int:machine_id>/carte.pdf")
@bureau_required
def carte_pdf(machine_id: int):
    """Generate the printable QR card to slip inside the pinball machine.

    The card bears both QR codes (breakdown + guestbook) and the configurable
    intro message. The QR codes encode only the machine token, so the printed
    card stays valid when the machine moves between centers.
    """
    from app.models.config import AssociationConfig

    machine = db.session.get(Machine, machine_id)
    if machine is None:
        abort(404)
    token = _ensure_public_token(machine)
    cfg = AssociationConfig.get()

    breakdown_url = url_for("machines.public_machine_breakdown", token=token, _external=True)
    feedback_url = url_for("machines.public_machine_feedback", token=token, _external=True)
    html = render_template(
        "machines/carte.html",
        machine=machine,
        cfg=cfg,
        message=(cfg.flipper_card_message or "").strip() or _DEFAULT_CARD_MESSAGE,
        breakdown_qr=base64.b64encode(_make_qr_svg(breakdown_url)).decode("ascii"),
        feedback_qr=base64.b64encode(_make_qr_svg(feedback_url)).decode("ascii"),
        logo_data=base64.b64encode(cfg.logo).decode("ascii") if cfg.logo else None,
    )
    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html).write_pdf()
    except Exception:
        return Response(html, mimetype="text/html")
    safe = _make_safe_filename(machine.display_name)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'inline; filename="carte_{safe}.pdf"'},
    )


# ---------------------------------------------------------------------------
# Public machine pages — no auth, token-protected
# ---------------------------------------------------------------------------


@bp.route("/m/<token>/panne", methods=["GET", "POST"])
@limiter.limit(
    "5 per hour",
    methods=["POST"],
    key_func=lambda: f"{request.remote_addr}:{request.view_args.get('token', '')}",
)
def public_machine_breakdown(token: str):
    """Public breakdown form for one machine, identified by its permanent token.

    The current center (if any) is resolved at request time from the machine's
    active installation — the QR code itself never encodes the center.
    """
    machine = _machine_by_public_token(token)
    inst = machine.current_installation
    center = inst.center if inst else None

    form = PublicMachineBreakdownForm()
    if form.validate_on_submit():
        record = MaintenanceRecord(
            machine_id=machine.id,
            center_id=center.id if center else None,
            date=date.today(),
            description=form.description.data.strip(),
            maintainer_name=form.reporter_name.data.strip(),
            status=MaintenanceStatus.OPEN,
        )
        if machine.status != MachineStatus.RETIRED:
            machine.status = MachineStatus.MAINTENANCE
        db.session.add(record)
        db.session.commit()
        return render_template(
            "machines/public_machine_breakdown.html",
            machine=machine,
            center=center,
            submitted=True,
        )

    return render_template(
        "machines/public_machine_breakdown.html",
        machine=machine,
        center=center,
        form=form,
    )


@bp.route("/m/<token>/livre-or", methods=["GET", "POST"])
@limiter.limit(
    "5 per hour",
    methods=["POST"],
    key_func=lambda: f"{request.remote_addr}:{request.view_args.get('token', '')}",
)
def public_machine_feedback(token: str):
    """Public guestbook form for one machine, identified by its permanent token.

    The testimonial is attached to the center currently hosting the machine. If
    the machine is not installed anywhere (e.g. during an event), the entry is
    stored without a center and labelled "Durant un événement".
    """
    machine = _machine_by_public_token(token)
    inst = machine.current_installation
    center = inst.center if inst else None

    form = FeedbackForm()
    if request.method == "POST":
        # Honeypot — silently accept but discard if filled
        if form.website.data:
            logger.warning("Honeypot triggered on machine feedback machine=%d", machine.id)
            flash("Merci pour votre témoignage !", "success")
            return redirect(url_for("centers.feedback_thanks"))
        if form.validate_on_submit():
            rating_raw = form.rating.data
            rating = int(rating_raw) if rating_raw else None
            feedback = CenterFeedback(
                center_id=center.id if center else None,
                machine_id=machine.id,
                submitted_by=form.submitted_by.data.strip(),
                content=form.content.data.strip(),
                rating=rating,
            )
            db.session.add(feedback)
            db.session.commit()
            flash("Merci pour votre témoignage ! Il sera publié après modération.", "success")
            return redirect(url_for("centers.feedback_thanks"))

    return render_template(
        "machines/public_machine_feedback.html",
        machine=machine,
        center=center,
        during_event=center is None,
        form=form,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_qr_svg(url: str) -> bytes:
    """Return a QR code as SVG bytes for the given URL."""
    import qrcode
    import qrcode.image.svg as qrsvg

    factory = qrsvg.SvgPathImage
    img = qrcode.make(url, image_factory=factory, box_size=8, border=2)
    buf = __import__("io").BytesIO()
    img.save(buf)
    return buf.getvalue()


def _make_safe_filename(name: str, max_len: int = 40) -> str:
    """Sanitize a machine name for use in HTTP Content-Disposition headers."""
    return (
        name.encode("ascii", "ignore")
        .decode("ascii")
        .replace(" ", "_")
        .replace("/", "-")
        .replace("_", "-")[:max_len]
    )


def _active_center_choices() -> list[tuple[int, str]]:
    """Return (id, name) pairs for centers that can receive machines."""
    centers = db.session.scalars(
        db.select(Center)
        .where(Center.status.in_([CenterStatus.ACTIVE.value, CenterStatus.PROSPECT.value]))
        .order_by(Center.name)
    ).all()
    return [(c.id, f"{c.name} ({c.city})") for c in centers]


def _member_choices() -> list[tuple[int, str]]:
    """Return (id, full_name) pairs for active members."""
    users = db.session.scalars(
        db.select(User).where(User.is_active.is_(True)).order_by(User.last_name, User.first_name)
    ).all()
    return [(u.id, u.full_name) for u in users]
