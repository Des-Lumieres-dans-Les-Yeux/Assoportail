"""Meetings blueprint routes — list, detail, create, edit, attendees, tasks."""

from datetime import UTC

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.meetings import bp
from app.blueprints.meetings.forms import (
    AttendanceForm,
    CreateTaskFromMeetingForm,
    LinkTaskForm,
    MeetingForm,
)
from app.decorators import bureau_required
from app.extensions import db
from app.models.meeting import Meeting
from app.models.task import Task, TaskPriority, TaskSource, TaskStatus
from app.models.user import User

# ---------------------------------------------------------------------------
# Meeting list
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def list_meetings():
    """Render the list of all meetings, newest first."""
    meetings = db.session.scalars(
        db.select(Meeting).options(selectinload(Meeting.attendees)).order_by(Meeting.date.desc())
    ).all()
    return render_template("meetings/list.html", meetings=meetings)


# ---------------------------------------------------------------------------
# Meeting detail
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>")
@login_required
def detail(meeting_id: int):
    """Render the detail page for a meeting."""
    meeting = db.session.get(
        Meeting,
        meeting_id,
        options=[
            selectinload(Meeting.created_by),
            selectinload(Meeting.attendees),
            selectinload(Meeting.tasks).selectinload(Task.assigned_to),
        ],
    )
    if meeting is None:
        abort(404)

    attendance_form = AttendanceForm()

    task_form = LinkTaskForm()
    task_form.task_id.choices = _linkable_task_choices(meeting)

    create_task_form = CreateTaskFromMeetingForm()

    all_users = db.session.scalars(
        db.select(User).where(User.is_active.is_(True)).order_by(User.last_name, User.first_name)
    ).all()

    return render_template(
        "meetings/detail.html",
        meeting=meeting,
        attendance_form=attendance_form,
        task_form=task_form,
        create_task_form=create_task_form,
        all_users=all_users,
    )


# ---------------------------------------------------------------------------
# Create meeting — bureau only
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new meeting."""
    form = MeetingForm()

    if form.validate_on_submit():
        meeting = Meeting(
            title=form.title.data.strip(),
            date=form.date.data.replace(tzinfo=UTC),
            location=(form.location.data or "").strip() or None,
            minutes=(form.minutes.data or "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(meeting)
        db.session.commit()
        flash(f"Réunion « {meeting.title} » créée.", "success")
        return redirect(url_for("meetings.detail", meeting_id=meeting.id))

    return render_template("meetings/form.html", form=form, title="Nouvelle réunion")


# ---------------------------------------------------------------------------
# Edit meeting — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(meeting_id: int):
    """Edit an existing meeting."""
    meeting = db.session.get(Meeting, meeting_id)
    if meeting is None:
        abort(404)

    form = MeetingForm(obj=meeting)
    if request.method == "GET" and meeting.date:
        # Strip tzinfo so DateTimeLocalField can populate the input correctly.
        form.date.data = meeting.date.replace(tzinfo=None)

    if form.validate_on_submit():
        meeting.title = form.title.data.strip()
        meeting.date = form.date.data.replace(tzinfo=UTC)
        meeting.location = (form.location.data or "").strip() or None
        meeting.minutes = (form.minutes.data or "").strip() or None
        db.session.commit()
        flash("Réunion mise à jour.", "success")
        return redirect(url_for("meetings.detail", meeting_id=meeting.id))

    return render_template(
        "meetings/form.html", form=form, title="Modifier la réunion", meeting=meeting
    )


# ---------------------------------------------------------------------------
# Attendance sync — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>/attendance", methods=["POST"])
@bureau_required
def attendance(meeting_id: int):
    """Sync the full attendee list from a checkbox form."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.attendees)])
    if meeting is None:
        abort(404)

    form = AttendanceForm()
    if not form.validate_on_submit():
        abort(400)

    selected_ids = {int(v) for v in request.form.getlist("user_ids") if v.isdigit()}
    users = (
        db.session.scalars(db.select(User).where(User.id.in_(selected_ids))).all()
        if selected_ids
        else []
    )

    meeting.attendees = list(users)
    db.session.commit()
    flash("Liste des participants mise à jour.", "success")
    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


@bp.route("/<int:meeting_id>/attendees", methods=["POST"])
@bureau_required
def add_attendee(meeting_id: int):
    """Bureau: add a specific user to the meeting attendees."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.attendees)])
    if meeting is None:
        abort(404)
    user_id = request.form.get("user_id", type=int)
    if not user_id:
        abort(400)
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user not in meeting.attendees:
        meeting.attendees.append(user)
        db.session.commit()
        flash(f"{user.full_name} ajouté(e) à la réunion.", "success")
    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


@bp.route("/<int:meeting_id>/attendees/<int:user_id>/remove", methods=["POST"])
@bureau_required
def remove_attendee(meeting_id: int, user_id: int):
    """Bureau: remove a specific user from the meeting attendees."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.attendees)])
    if meeting is None:
        abort(404)
    user = db.session.get(User, user_id)
    if user and user in meeting.attendees:
        meeting.attendees.remove(user)
        db.session.commit()
        flash(f"{user.full_name} retiré(e) de la réunion.", "success")
    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


# ---------------------------------------------------------------------------
# Task linking — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>/tasks", methods=["POST"])
@bureau_required
def link_task(meeting_id: int):
    """Link a task to the meeting."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.tasks)])
    if meeting is None:
        abort(404)

    form = LinkTaskForm()
    form.task_id.choices = _linkable_task_choices(meeting)

    if form.validate_on_submit():
        task = db.session.get(Task, form.task_id.data)
        if task and task not in meeting.tasks:
            meeting.tasks.append(task)
            db.session.commit()
            flash(f"Tâche « {task.title} » liée à la réunion.", "success")
    else:
        flash("Sélectionnez une tâche valide.", "warning")

    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


@bp.route("/<int:meeting_id>/tasks/<int:task_id>/unlink", methods=["POST"])
@bureau_required
def unlink_task(meeting_id: int, task_id: int):
    """Remove the link between a task and the meeting."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.tasks)])
    if meeting is None:
        abort(404)

    task = db.session.get(Task, task_id)
    if task and task in meeting.tasks:
        meeting.tasks.remove(task)
        db.session.commit()
        flash(f"Tâche « {task.title} » détachée de la réunion.", "success")

    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


# ---------------------------------------------------------------------------
# Create task from meeting — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>/create-task", methods=["POST"])
@bureau_required
def create_task(meeting_id: int):
    """Create a new task linked to this meeting."""
    meeting = db.session.get(Meeting, meeting_id, options=[selectinload(Meeting.tasks)])
    if meeting is None:
        abort(404)

    form = CreateTaskFromMeetingForm()
    if form.validate_on_submit():
        task = Task(
            title=form.title.data.strip(),
            source=TaskSource.MEETING,
            source_meeting_id=meeting.id,
            created_by_id=current_user.id,
            status=TaskStatus.OPEN,
            priority=TaskPriority.NORMAL,
        )
        db.session.add(task)
        db.session.flush()  # assign task.id before linking
        meeting.tasks.append(task)
        db.session.commit()
        flash(f"Tâche « {task.title} » créée et liée à la réunion.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("meetings.detail", meeting_id=meeting_id))


# ---------------------------------------------------------------------------
# Delete meeting — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:meeting_id>/delete", methods=["POST"])
@bureau_required
def delete(meeting_id: int):
    """Delete a meeting and redirect to the meeting list."""
    meeting = db.session.get(Meeting, meeting_id)
    if meeting is None:
        abort(404)

    db.session.delete(meeting)
    db.session.commit()
    flash("Réunion supprimée.", "success")
    return redirect(url_for("meetings.list_meetings"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _linkable_task_choices(meeting: Meeting) -> list[tuple[int, str]]:
    """Return (id, title) for open/in-progress tasks not yet linked to this meeting."""
    current_ids = {t.id for t in meeting.tasks}
    tasks = db.session.scalars(
        db.select(Task)
        .where(Task.status.in_([TaskStatus.OPEN.value, TaskStatus.IN_PROGRESS.value]))
        .where(Task.id.not_in(current_ids) if current_ids else db.true())
        .order_by(Task.created_at.desc())
    ).all()
    return [(t.id, t.title) for t in tasks]
