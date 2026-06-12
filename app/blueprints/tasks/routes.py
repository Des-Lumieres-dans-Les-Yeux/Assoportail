"""Tasks blueprint routes — board, detail, assignment, comments."""

from datetime import UTC, date, datetime

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.tasks import bp
from app.blueprints.tasks.forms import (
    ConvertToMaintenanceForm,
    TaskClaimForm,
    TaskCommentForm,
    TaskForm,
    TaskStatusForm,
)
from app.decorators import bureau_required
from app.extensions import db
from app.models.email import InboundEmail
from app.models.machine import Machine, MaintenanceRecord, MaintenanceStatus
from app.models.task import Task, TaskComment, TaskPriority, TaskSource, TaskStatus
from app.models.user import User

# ---------------------------------------------------------------------------
# Task list / board
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def list_tasks():
    """Render the task board — all open/in-progress tasks.

    Members see all tasks. Bureau can also see done/cancelled via ?all=1.
    Supports filtering by status (?status=) and priority (?priority=).
    """
    show_all = request.args.get("all") == "1"
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")

    stmt = (
        db.select(Task)
        .options(
            selectinload(Task.created_by),
            selectinload(Task.assigned_to),
            selectinload(Task.source_center),
        )
        .order_by(
            # urgent first, then by creation date desc
            db.case(
                (Task.priority == TaskPriority.URGENT.value, 0),
                (Task.priority == TaskPriority.HIGH.value, 1),
                (Task.priority == TaskPriority.NORMAL.value, 2),
                else_=3,
            ),
            Task.created_at.desc(),
        )
    )

    if status_filter and status_filter in {e.value for e in TaskStatus}:
        stmt = stmt.where(Task.status == status_filter)
    elif not show_all:
        stmt = stmt.where(Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]))

    if priority_filter and priority_filter in {e.value for e in TaskPriority}:
        stmt = stmt.where(Task.priority == priority_filter)

    tasks = db.session.scalars(stmt).all()

    # Maintenance records shown alongside tasks
    maint_stmt = (
        db.select(MaintenanceRecord)
        .options(
            selectinload(MaintenanceRecord.machine),
            selectinload(MaintenanceRecord.resolved_by),
        )
        .order_by(MaintenanceRecord.date.desc())
    )
    if not show_all:
        maint_stmt = maint_stmt.where(MaintenanceRecord.status == MaintenanceStatus.OPEN.value)
    maintenances = db.session.scalars(maint_stmt).all()

    return render_template(
        "tasks/list.html",
        tasks=tasks,
        maintenances=maintenances,
        show_all=show_all,
        status_filter=status_filter,
        priority_filter=priority_filter,
        TaskStatus=TaskStatus,
        TaskPriority=TaskPriority,
        MaintenanceStatus=MaintenanceStatus,
    )


# ---------------------------------------------------------------------------
# Task detail
# ---------------------------------------------------------------------------


@bp.route("/<int:task_id>")
@login_required
def detail(task_id: int):
    """Render the detail page for a task with its comments."""
    task = db.session.get(
        Task,
        task_id,
        options=[
            selectinload(Task.created_by),
            selectinload(Task.assigned_to),
            selectinload(Task.source_center),
            selectinload(Task.source_event),
            selectinload(Task.source_email).selectinload(InboundEmail.documents),
            selectinload(Task.comments).selectinload(TaskComment.author),
        ],
    )
    if task is None:
        abort(404)

    from app.models.event import Event, EventStatus

    comment_form = TaskCommentForm()
    claim_form = TaskClaimForm()
    status_form = TaskStatusForm(status=task.status.value)
    convert_form = ConvertToMaintenanceForm()
    convert_form.machine_id.choices = _machine_choices()

    # Fetch active events for linking
    active_events = db.session.scalars(
        db.select(Event)
        .where(Event.status.in_([EventStatus.PLANNED.value, EventStatus.IN_PROGRESS.value]))
        .order_by(Event.event_date.desc())
    ).all()

    return render_template(
        "tasks/detail.html",
        task=task,
        comment_form=comment_form,
        claim_form=claim_form,
        status_form=status_form,
        convert_form=convert_form,
        active_events=active_events,
        TaskStatus=TaskStatus,
        TaskPriority=TaskPriority,
        TaskSource=TaskSource,
    )


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new task manually."""
    form = TaskForm()
    form.assigned_to_id.choices = _member_choices(include_blank=True)
    form.source_event_id.choices = _event_choices(include_blank=True)

    if form.validate_on_submit():
        task = Task(
            title=form.title.data.strip(),
            description=(form.description.data or "").strip() or None,
            priority=form.priority.data,
            status=form.status.data,
            assigned_to_id=form.assigned_to_id.data or None,
            due_date=form.due_date.data,
            source_event_id=form.source_event_id.data or None,
            created_by_id=current_user.id,
            source=TaskSource.MANUAL,
        )
        if task.source_event_id:
            task.source = TaskSource.EVENT.value
        if task.status == TaskStatus.DONE:
            task.completed_at = datetime.now(UTC)
        db.session.add(task)
        db.session.commit()
        flash(f"Tâche « {task.title} » créée.", "success")
        if task.assigned_to_id and task.assigned_to_id != current_user.id:
            from app.tasks.notifications import notify_task_assigned
            notify_task_assigned.delay(task.id, current_user.full_name)
        return redirect(url_for("tasks.detail", task_id=task.id))

    return render_template("tasks/form.html", form=form, title="Nouvelle tâche")


@bp.route("/<int:task_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(task_id: int):
    """Edit an existing task."""
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    form = TaskForm(obj=task)
    form.assigned_to_id.choices = _member_choices(include_blank=True)
    form.source_event_id.choices = _event_choices(include_blank=True)

    # Pre-select current values
    if request.method == "GET":
        if task.assigned_to_id:
            form.assigned_to_id.data = task.assigned_to_id
        if task.source_event_id:
            form.source_event_id.data = task.source_event_id

    if form.validate_on_submit():
        was_done = task.status == TaskStatus.DONE
        prev_assignee_id = task.assigned_to_id
        task.title = form.title.data.strip()
        task.description = (form.description.data or "").strip() or None
        task.priority = form.priority.data
        task.status = form.status.data
        task.assigned_to_id = form.assigned_to_id.data or None
        task.due_date = form.due_date.data
        task.source_event_id = form.source_event_id.data or None

        if task.source == TaskSource.MANUAL and task.source_event_id:
            task.source = TaskSource.EVENT.value

        if task.status == TaskStatus.DONE and not was_done:
            task.completed_at = datetime.now(UTC)
        elif task.status != TaskStatus.DONE:
            task.completed_at = None

        assignee_changed = task.assigned_to_id and task.assigned_to_id != prev_assignee_id
        db.session.commit()
        if assignee_changed and task.assigned_to_id != current_user.id:
            from app.tasks.notifications import notify_task_assigned
            notify_task_assigned.delay(task.id, current_user.full_name)
        flash("Tâche mise à jour.", "success")
        return redirect(url_for("tasks.detail", task_id=task.id))

    return render_template("tasks/form.html", form=form, title="Modifier la tâche", task=task)


@bp.route("/<int:task_id>/link-event", methods=["POST"])
@bureau_required
def link_task_to_event(task_id: int):
    """Link a task to an existing event."""
    from app.models.event import Event

    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    event_id = request.form.get("event_id", type=int)
    if event_id:
        event = db.session.get(Event, event_id)
        if event:
            task.source_event_id = event.id
            if task.source == TaskSource.MANUAL:
                task.source = TaskSource.EVENT.value
            db.session.commit()
            flash(f"Tâche liée à l'événement « {event.title} ».", "success")
        else:
            flash("Événement introuvable.", "danger")
    else:
        task.source_event_id = None
        db.session.commit()
        flash("Lien avec l'événement supprimé.", "info")

    return redirect(url_for("tasks.detail", task_id=task_id))


def _event_choices(include_blank: bool = False) -> list[tuple]:
    """Return (id, title) for active events, sorted by date desc."""
    from app.models.event import Event, EventStatus

    events = db.session.scalars(
        db.select(Event)
        .where(Event.status.in_([EventStatus.PLANNED.value, EventStatus.IN_PROGRESS.value]))
        .order_by(Event.event_date.desc())
    ).all()
    choices = [(e.id, f"{e.event_date.strftime('%d/%m')} — {e.title}") for e in events]
    if include_blank:
        choices.insert(0, (None, "— Aucun événement —"))
    return choices


# ---------------------------------------------------------------------------
# Status transition — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:task_id>/status", methods=["POST"])
@bureau_required
def set_status(task_id: int):
    """Quick status change from the detail page."""
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    form = TaskStatusForm()
    if form.validate_on_submit():
        new_status = form.status.data
        if new_status in {e.value for e in TaskStatus}:
            was_done = task.status == TaskStatus.DONE
            task.status = new_status
            if new_status == TaskStatus.DONE and not was_done:
                task.completed_at = datetime.now(UTC)
            elif new_status != TaskStatus.DONE:
                task.completed_at = None
            db.session.commit()
            flash("Statut mis à jour.", "success")

    return redirect(url_for("tasks.detail", task_id=task_id))


# ---------------------------------------------------------------------------
# Self-assign (claim) — any member
# ---------------------------------------------------------------------------


@bp.route("/<int:task_id>/claim", methods=["POST"])
@login_required
def claim(task_id: int):
    """Allow a member to self-assign an unassigned open task."""
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    form = TaskClaimForm()
    if not form.validate_on_submit():
        abort(400)

    if task.assigned_to_id is not None:
        flash("Cette tâche est déjà assignée.", "warning")
    elif task.status not in {TaskStatus.OPEN, TaskStatus.IN_PROGRESS}:
        flash("Impossible d'assigner une tâche terminée ou annulée.", "warning")
    else:
        task.assigned_to_id = current_user.id
        if task.status == TaskStatus.OPEN:
            task.status = TaskStatus.IN_PROGRESS
        db.session.commit()
        flash("Tâche assignée à vous-même.", "success")

    return redirect(url_for("tasks.detail", task_id=task_id))


# ---------------------------------------------------------------------------
# Post comment — any member
# ---------------------------------------------------------------------------


@bp.route("/<int:task_id>/comments", methods=["POST"])
@login_required
def add_comment(task_id: int):
    """Add a comment to a task."""
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    form = TaskCommentForm()
    if form.validate_on_submit():
        comment = TaskComment(
            task_id=task_id,
            author_id=current_user.id,
            body=form.body.data.strip(),
        )
        db.session.add(comment)
        db.session.commit()
        flash("Commentaire ajouté.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("tasks.detail", task_id=task_id))


# ---------------------------------------------------------------------------
# Convert breakdown task to maintenance record — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:task_id>/convert-to-maintenance", methods=["POST"])
@bureau_required
def convert_to_maintenance(task_id: int):
    """Convert a center-breakdown task into a MaintenanceRecord.

    Only tasks with source CENTER_BREAKDOWN can be converted.
    """
    task = db.session.get(Task, task_id)
    if task is None:
        abort(404)

    if task.source != TaskSource.CENTER_BREAKDOWN:
        flash(
            "Seules les tâches de type « Panne centre »"
            " peuvent être converties en fiche de maintenance.",
            "warning",
        )
        return redirect(url_for("tasks.detail", task_id=task_id))

    form = ConvertToMaintenanceForm()
    form.machine_id.choices = _machine_choices()

    if form.validate_on_submit():
        description = (form.description.data or "").strip() or task.title
        record = MaintenanceRecord(
            machine_id=form.machine_id.data,
            date=date.today(),
            description=description,
            cost=form.cost.data or None,
            maintainer_name=current_user.full_name,
            maintainer_user_id=current_user.id,
            source_task_id=task.id,
        )
        task.status = TaskStatus.DONE  # enum member, consistent with existing routes
        task.completed_at = datetime.now(UTC)
        db.session.add(record)
        db.session.commit()
        flash("Tâche convertie en fiche de maintenance.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("tasks.detail", task_id=task_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _member_choices(include_blank: bool = False) -> list[tuple]:
    """Return (id, full_name) for all active users, sorted by name."""
    users = db.session.scalars(
        db.select(User).where(User.is_active.is_(True)).order_by(User.last_name, User.first_name)
    ).all()
    choices = [(u.id, u.full_name) for u in users]
    if include_blank:
        choices.insert(0, (None, "— Non assignée —"))
    return choices


def _machine_choices() -> list[tuple]:
    """Return (id, display_name) for all machines, sorted by manufacturer then model."""
    machines = db.session.scalars(
        db.select(Machine).order_by(Machine.manufacturer, Machine.model)
    ).all()
    return [(m.id, m.display_name) for m in machines]


def _notify_task_assigned(task: "Task") -> None:
    """Send an assignment notification email to the task's assignee (best-effort)."""
    import logging

    from app.models.user import User
    from app.services.mailer import send_task_assigned_email

    _log = logging.getLogger(__name__)
    try:
        assignee = db.session.get(User, task.assigned_to_id)
        if assignee and assignee.email:
            portal_url = url_for("tasks.detail", task_id=task.id, _external=True)
            send_task_assigned_email(
                to_email=assignee.email,
                full_name=assignee.full_name,
                task_title=task.title,
                task_description=task.description,
                assigner_name=current_user.full_name,
                portal_url=portal_url,
            )
    except Exception:
        _log.exception("Failed to send task assignment notification for task #%d", task.id)

    try:
        from app.services.push import send_push_notification

        task_url = url_for("tasks.detail", task_id=task.id)
        send_push_notification(
            user_ids=[task.assigned_to_id],
            title="Tâche assignée",
            body=task.title,
            url=task_url,
        )
    except Exception:
        _log.exception("Failed to send push notification for task #%d", task.id)
