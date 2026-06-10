"""Events blueprint routes — list, detail, create, edit, attendees, expenses, cashbox."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.blueprints.events import bp
from app.blueprints.events.forms import (
    AttendanceForm,
    AvailabilityForm,
    CashBoxCloseForm,
    CashBoxOpenForm,
    CashEntryForm,
    EventForm,
    EventMachineForm,
    EventSlotForm,
    ExpenseForm,
    VolunteerAvailabilityForm,
    VolunteerIdentityForm,
)
from app.decorators import bureau_required
from app.extensions import csrf, db, limiter
from app.models.document import Document, DocumentType
from app.models.event import (
    CashBox,
    CashEntry,
    Event,
    EventDate,
    EventMachine,
    EventSlot,
    EventStatus,
    EventVolunteer,
    Expense,
    SlotAvailability,
    SlotAvailabilityStatus,
    VolunteerSlotAvailability,
)
from app.models.machine import Machine
from app.models.task import Task, TaskSource, TaskStatus
from app.models.treasury import Transaction, TransactionSource, TransactionType
from app.models.user import User

# ---------------------------------------------------------------------------
# Event list
# ---------------------------------------------------------------------------


@bp.route("/")
@login_required
def list_events():
    """Render events with optional status filter and upcoming-only toggle (on by default)."""
    status = request.args.get("status", "").strip()
    # Default 'upcoming' to '1' if not explicitly provided in the query string
    # When checkbox is unchecked, we get ['0']. When checked, we get ['0', '1'].
    upcoming_args = request.args.getlist("upcoming")
    if not upcoming_args:
        upcoming = "1"  # Default
    else:
        upcoming = "1" if "1" in upcoming_args else "0"

    page = request.args.get("page", 1, type=int)

    stmt = db.select(Event).options(
        selectinload(Event.attendees),
        selectinload(Event.slots).selectinload(EventSlot.availabilities),
        selectinload(Event.slots)
        .selectinload(EventSlot.volunteer_availabilities)
        .selectinload(VolunteerSlotAvailability.volunteer),
    )

    if status and status in {s.value for s in EventStatus}:
        stmt = stmt.where(Event.status == status)

    if upcoming == "1":
        # Include events that haven't happened yet OR happened within the last 3 days
        three_days_ago = datetime.now(UTC) - timedelta(days=3)
        stmt = stmt.where(Event.event_date >= three_days_ago)
    stmt = stmt.order_by(Event.event_date.asc())

    pagination = db.paginate(stmt, page=page, per_page=20, error_out=False)
    return render_template(
        "events/list.html",
        events=pagination.items,
        pagination=pagination,
        status=status,
        upcoming=upcoming,
        EventStatus=EventStatus,
    )


# ---------------------------------------------------------------------------
# Event detail
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>")
@login_required
def detail(event_id: int):
    """Render the event detail page with expenses and cashbox."""
    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.created_by),
            selectinload(Event.attendees),
            selectinload(Event.expenses).selectinload(Expense.submitter),
            selectinload(Event.expenses).selectinload(Expense.validated_by),
            selectinload(Event.expenses).selectinload(Expense.receipt_document),
            selectinload(Event.cashbox)
            .selectinload(CashBox.entries)
            .selectinload(CashEntry.recorded_by),
            selectinload(Event.cashbox).selectinload(CashBox.reconciled_by),
            selectinload(Event.tasks).selectinload(Task.created_by),
            selectinload(Event.emails),
            selectinload(Event.event_machines).selectinload(EventMachine.machine),
            selectinload(Event.event_machines).selectinload(EventMachine.added_by),
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.documents).selectinload(Document.uploaded_by),
            selectinload(Event.volunteers),
            selectinload(Event.dates),
        ],
    )
    if event is None:
        abort(404)

    attendance_form = AttendanceForm()
    expense_form = ExpenseForm()
    cashbox_open_form = CashBoxOpenForm()
    cash_entry_form = CashEntryForm()
    cashbox_close_form = CashBoxCloseForm()
    event_machine_form = EventMachineForm()
    event_machine_form.machine_id.choices = _available_machine_choices(event)
    slot_form = EventSlotForm()
    avail_form = AvailabilityForm()

    all_users = db.session.scalars(
        db.select(User).where(User.is_active.is_(True)).order_by(User.last_name, User.first_name)
    ).all()

    # Calendar bounds — non-consecutive dates take priority over event_date/end_date
    if event.dates:
        sorted_days = [ed.day for ed in event.dates]
        calendar_start = sorted_days[0].isoformat()
        # FullCalendar validRange.end is exclusive
        calendar_end = (sorted_days[-1] + timedelta(days=1)).isoformat()
        calendar_days = len(sorted_days)
        event_dates_json = [d.isoformat() for d in sorted_days]
    else:
        event_start_date = event.event_date.date()
        if event.end_date:
            calendar_days = max(1, (event.end_date.date() - event_start_date).days + 1)
        else:
            calendar_days = 1
        calendar_start = event_start_date.isoformat()
        # FullCalendar validRange.end is exclusive
        calendar_end = (event_start_date + timedelta(days=calendar_days)).isoformat()
        event_dates_json = []

    # Time bounds: fit the calendar to the event hours (1h padding each side)
    start_hour = max(0, event.event_date.hour - 1)
    if event.end_date:
        end_hour = min(24, event.end_date.hour + 2)
    else:
        end_hour = min(24, event.event_date.hour + 4)
    calendar_slot_min = f"{start_hour:02d}:00:00"
    calendar_slot_max = f"{end_hour:02d}:00:00"

    # Participants: attendees + those marked as present/maybe in slots
    participant_set = set(event.attendees)
    for slot in event.slots:
        for a in slot.availabilities:
            if a.status.value in ("present", "maybe"):
                participant_set.add(a.user)
    participants = sorted(participant_set, key=lambda u: (u.last_name or "", u.first_name or ""))

    return render_template(
        "events/detail.html",
        event=event,
        attendance_form=attendance_form,
        expense_form=expense_form,
        cashbox_open_form=cashbox_open_form,
        cash_entry_form=cash_entry_form,
        cashbox_close_form=cashbox_close_form,
        event_machine_form=event_machine_form,
        slot_form=slot_form,
        avail_form=avail_form,
        all_users=all_users,
        participants=participants,
        EventStatus=EventStatus,
        SlotAvailabilityStatus=SlotAvailabilityStatus,
        DocumentType=DocumentType,
        current_user=current_user,
        calendar_days=calendar_days,
        calendar_start=calendar_start,
        calendar_end=calendar_end,
        calendar_slot_min=calendar_slot_min,
        calendar_slot_max=calendar_slot_max,
        event_dates_json=event_dates_json,
    )


# ---------------------------------------------------------------------------
# Slots JSON feed — for FullCalendar
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/slots.json")
@login_required
def slots_json(event_id: int):
    """Return event slots as FullCalendar-compatible JSON."""
    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
        ],
    )
    if event is None:
        abort(404)

    result = []
    for slot in event.slots:
        my_avail = slot.availability_for(current_user.id)
        present = [
            {"id": a.user.id, "name": a.user.first_name, "comment": a.comment or ""}
            for a in slot.availabilities
            if a.status.value == "present"
        ]
        maybe = [
            {"id": a.user.id, "name": a.user.first_name, "comment": a.comment or ""}
            for a in slot.availabilities
            if a.status.value == "maybe"
        ]
        absent = [
            {"id": a.user.id, "name": a.user.first_name, "comment": a.comment or ""}
            for a in slot.availabilities
            if a.status.value == "absent"
        ]
        # Include confirmed volunteers
        for va in slot.volunteer_availabilities:
            if not va.volunteer.confirmed:
                continue
            entry = {"name": f"{va.volunteer.name} (bénévole)", "comment": ""}
            if va.status.value == "present":
                present.append(entry)
            elif va.status.value == "maybe":
                maybe.append(entry)
            elif va.status.value == "absent":
                absent.append(entry)

        if my_avail:
            color = {"present": "#198754", "maybe": "#ffc107", "absent": "#dc3545"}[
                my_avail.status.value
            ]
        else:
            color = "#6c757d"

        count = f"✓{len(present)}" + (f" ?{len(maybe)}" if maybe else "")
        title = (
            f"{slot.label} — {count}"
            if slot.label
            else (count if count.strip("✓0") or present else slot.label or "(créneau)")
        )

        date_str = slot.slot_date.isoformat()
        start = (
            f"{date_str}T{slot.start_time.strftime('%H:%M:%S')}" if slot.start_time else date_str
        )
        end = f"{date_str}T{slot.end_time.strftime('%H:%M:%S')}" if slot.end_time else None

        fc_event: dict = {
            "id": str(slot.id),
            "title": title,
            "start": start,
            "color": color,
            "extendedProps": {
                "slot_id": slot.id,
                "label": slot.label or "",
                "date_label": slot.slot_date.strftime("%A %d/%m/%Y"),
                "time_label": slot.display_time,
                "present": present,
                "maybe": maybe,
                "absent": absent,
                "my_status": my_avail.status.value if my_avail else None,
                "my_comment": my_avail.comment or "" if my_avail else "",
                "slot_date_iso": date_str,
                "start_time_iso": slot.start_time.strftime("%H:%M") if slot.start_time else "",
                "end_time_iso": slot.end_time.strftime("%H:%M") if slot.end_time else "",
            },
        }
        if end:
            fc_event["end"] = end
        result.append(fc_event)

    return jsonify(result)


# ---------------------------------------------------------------------------
# Create event — bureau only
# ---------------------------------------------------------------------------


@bp.route("/new", methods=["GET", "POST"])
@bureau_required
def create():
    """Create a new event."""
    form = EventForm()

    if form.validate_on_submit():
        end = form.end_date.data
        event = Event(
            title=form.title.data.strip(),
            description=(form.description.data or "").strip() or None,
            status=form.status.data,
            event_date=form.event_date.data.replace(tzinfo=UTC),
            end_date=end.replace(tzinfo=UTC) if end else None,
            location=(form.location.data or "").strip() or None,
            created_by_id=current_user.id,
        )
        db.session.add(event)
        db.session.flush()
        _sync_event_dates(event, request.form.getlist("extra_dates[]"))
        db.session.commit()
        flash(f"Événement « {event.title} » créé.", "success")
        _notify_event_created(event)
        return redirect(url_for("events.detail", event_id=event.id))

    return render_template(
        "events/form.html",
        form=form,
        title="Nouvel événement",
        existing_dates_json=[],
    )


# ---------------------------------------------------------------------------
# Edit event — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@bureau_required
def edit(event_id: int):
    """Edit an existing event."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.dates)])
    if event is None:
        abort(404)

    form = EventForm(obj=event)
    if request.method == "GET":
        if event.event_date:
            form.event_date.data = event.event_date.replace(tzinfo=None)
        if event.end_date:
            form.end_date.data = event.end_date.replace(tzinfo=None)

    if form.validate_on_submit():
        end = form.end_date.data
        event.title = form.title.data.strip()
        event.description = (form.description.data or "").strip() or None
        event.status = form.status.data
        event.event_date = form.event_date.data.replace(tzinfo=UTC)
        event.end_date = end.replace(tzinfo=UTC) if end else None
        event.location = (form.location.data or "").strip() or None
        _sync_event_dates(event, request.form.getlist("extra_dates[]"))
        db.session.commit()
        flash("Événement mis à jour.", "success")
        return redirect(url_for("events.detail", event_id=event.id))

    existing_dates_json = [ed.day.isoformat() for ed in event.dates]
    return render_template(
        "events/form.html",
        form=form,
        title="Modifier l'événement",
        event=event,
        existing_dates_json=existing_dates_json,
    )


# ---------------------------------------------------------------------------
# Attendance sync — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/attendance", methods=["POST"])
@bureau_required
def attendance(event_id: int):
    """Sync the full attendee list from a checkbox form."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.attendees)])
    if event is None:
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

    event.attendees = list(users)
    db.session.commit()
    flash("Liste des participants mise à jour.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Expense submission — any member
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/expenses", methods=["POST"])
@login_required
def submit_expense(event_id: int):
    """Submit a reimbursable expense for an event, with optional receipt photo."""
    import os
    import re

    from flask import current_app
    from werkzeug.utils import secure_filename

    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    receipt_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf"}
    receipt_max = 10 * 1024 * 1024  # 10 MB

    form = ExpenseForm()
    if form.validate_on_submit():
        amount = form.amount.data
        distance_km = None

        if form.type.data == "travel" and form.distance_km.data:
            from app.models.config import AssociationConfig

            cfg = AssociationConfig.get()
            km_rate = Decimal(str(cfg.km_rate)) if cfg.km_rate else Decimal("0.603")
            distance_km = form.distance_km.data
            amount = (distance_km * km_rate).quantize(Decimal("0.01"))
        elif not amount:
            flash("Le montant est obligatoire.", "danger")
            return redirect(url_for("events.detail", event_id=event_id))

        expense = Expense(
            event_id=event_id,
            user_id=current_user.id,
            type=form.type.data,
            amount=amount,
            distance_km=distance_km,
            description=form.description.data.strip(),
        )
        db.session.add(expense)
        db.session.flush()

        # Handle optional receipt upload
        file = request.files.get("receipt_file")
        if file and file.filename:
            safe_name = secure_filename(file.filename)
            ext = os.path.splitext(safe_name)[1].lower()
            if ext in receipt_exts:
                data = file.read()
                if len(data) <= receipt_max:
                    slug = (
                        re.sub(r"[^a-z0-9]+", "-", os.path.splitext(safe_name)[0].lower()).strip(
                            "-"
                        )[:40]
                        or "justificatif"
                    )
                    stored_name = f"{date.today().isoformat()}_receipt_{expense.id}_{slug}{ext}"
                    doc = Document(
                        original_filename=file.filename,
                        stored_filename=stored_name,
                        type=DocumentType.RECEIPT.value,
                        category="receipt",
                        mime_type=file.content_type or "application/octet-stream",
                        size_bytes=len(data),
                        uploaded_by_id=current_user.id,
                        description=f"Justificatif — {form.description.data.strip()[:100]}",
                    )
                    db.session.add(doc)
                    db.session.flush()

                    # Try Drive upload, fallback to local
                    drive_uploaded = False
                    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
                        try:
                            from app.services.drive import DriveService

                            file_id, web_link = DriveService.from_db().upload_file(
                                data, file.filename, doc.mime_type, doc.type
                            )
                            doc.drive_file_id = file_id
                            doc.drive_web_link = web_link
                            drive_uploaded = True
                        except Exception:
                            pass
                    if not drive_uploaded:
                        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], "receipts")
                        os.makedirs(subdir, exist_ok=True)
                        with open(os.path.join(subdir, stored_name), "wb") as fh:
                            fh.write(data)

                    expense.receipt_document_id = doc.id
                else:
                    flash("Le justificatif dépasse 10 Mo.", "warning")
            else:
                flash("Format de justificatif non supporté (JPEG, PNG, GIF, WebP, PDF).", "warning")

        db.session.commit()
        flash("Note de frais soumise.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Expense validation — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/expenses/<int:expense_id>/validate", methods=["POST"])
@bureau_required
def validate_expense(event_id: int, expense_id: int):
    """Validate (approve) a submitted expense."""
    expense = db.session.get(Expense, expense_id)
    if expense is None or expense.event_id != event_id:
        abort(404)

    if not expense.is_validated:
        expense.validated_at = datetime.now(UTC)
        expense.validated_by_id = current_user.id
        # Auto-create treasury transaction from validated expense
        txn = Transaction(
            type=TransactionType.EXPENSE.value,
            amount=expense.amount,
            date=date.today(),
            description=f"Note de frais : {expense.description[:200]}",
            source=TransactionSource.EXPENSE.value,
            source_id=expense.id,
            created_by_id=current_user.id,
        )
        db.session.add(txn)
        db.session.commit()
        flash("Note de frais validée.", "success")

    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Cash box — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/cashbox", methods=["POST"])
@bureau_required
def open_cashbox(event_id: int):
    """Open a cash box for an event."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.cashbox)])
    if event is None:
        abort(404)

    if event.cashbox is not None:
        flash("Une caisse est déjà ouverte pour cet événement.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    form = CashBoxOpenForm()
    if form.validate_on_submit():
        cashbox = CashBox(
            event_id=event_id,
            opened_at=datetime.now(UTC),
            opening_amount=form.opening_amount.data,
        )
        db.session.add(cashbox)
        try:
            db.session.commit()
            flash("Caisse ouverte.", "success")
        except Exception:
            db.session.rollback()
            flash("Une caisse existe déjà pour cet événement.", "warning")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/cashbox/entries", methods=["POST"])
@bureau_required
def add_cashentry(event_id: int):
    """Record a cash entry against the event's open cashbox."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.cashbox)])
    if event is None:
        abort(404)

    if event.cashbox is None or event.cashbox.is_closed:
        flash("Aucune caisse ouverte pour cet événement.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    form = CashEntryForm()
    if form.validate_on_submit():
        entry = CashEntry(
            cashbox_id=event.cashbox.id,
            type=form.type.data,
            amount=form.amount.data,
            note=(form.note.data or "").strip() or None,
            recorded_by_id=current_user.id,
        )
        db.session.add(entry)
        db.session.commit()
        flash("Entrée de caisse enregistrée.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/cashbox/close", methods=["POST"])
@bureau_required
def close_cashbox(event_id: int):
    """Close and reconcile the event's cashbox."""
    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.cashbox).selectinload(CashBox.entries),
        ],
    )
    if event is None:
        abort(404)

    if event.cashbox is None or event.cashbox.is_closed:
        flash("Aucune caisse ouverte à clôturer.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    form = CashBoxCloseForm()
    if form.validate_on_submit():
        cashbox = event.cashbox
        cashbox.closing_amount = form.closing_amount.data
        cashbox.closed_at = datetime.now(UTC)
        cashbox.reconciled_by_id = current_user.id
        cashbox.reconciled_at = datetime.now(UTC)
        cashbox.reconciliation_note = (form.reconciliation_note.data or "").strip() or None
        # Auto-create treasury transaction from cashbox
        net_amount = cashbox.expected_amount  # total collected
        if net_amount > 0:
            txn = Transaction(
                type=TransactionType.INCOME.value,
                amount=net_amount,
                date=date.today(),
                description=f"Caisse événement : {event.title}",
                source=TransactionSource.EVENT.value,
                source_id=event.id,
                created_by_id=current_user.id,
            )
            db.session.add(txn)
        db.session.commit()
        flash("Caisse clôturée.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/cashbox/reset", methods=["POST"])
@bureau_required
def reset_cashbox(event_id: int):
    """Delete the cashbox and all its entries, resetting to zero."""
    event = db.session.get(
        Event,
        event_id,
        options=[selectinload(Event.cashbox).selectinload(CashBox.entries)],
    )
    if event is None:
        abort(404)

    if event.cashbox is None:
        flash("Aucune caisse à annuler.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    db.session.delete(event.cashbox)
    db.session.commit()
    flash("Caisse annulée et remise à zéro.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Create task linked to event — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/tasks", methods=["POST"])
@bureau_required
def create_task(event_id: int):
    """Create a new task linked to this event."""
    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    title = request.form.get("task_title", "").strip()
    if not title:
        flash("Le titre de la tâche est obligatoire.", "danger")
        return redirect(url_for("events.detail", event_id=event_id))

    task = Task(
        title=title,
        status=TaskStatus.OPEN,
        source=TaskSource.EVENT,
        source_event_id=event_id,
        created_by_id=current_user.id,
    )
    db.session.add(task)
    db.session.commit()
    flash(f"Tâche « {task.title} » créée.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Delete event — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/delete", methods=["POST"])
@bureau_required
def delete(event_id: int):
    """Delete an event. Only events with status 'planned' may be deleted."""
    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    if event.status != EventStatus.PLANNED:
        flash(
            "Seuls les événements planifiés (non commencés) peuvent être supprimés.",
            "warning",
        )
        return redirect(url_for("events.detail", event_id=event_id))

    title = event.title
    db.session.delete(event)
    db.session.commit()
    flash(f"Événement « {title} » supprimé.", "success")
    return redirect(url_for("events.list_events"))


# ---------------------------------------------------------------------------
# Reject expense — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/expenses/<int:expense_id>/reject", methods=["POST"])
@bureau_required
def reject_expense(event_id: int, expense_id: int):
    """Reject (un-validate) a previously validated expense."""
    expense = db.session.get(Expense, expense_id)
    if expense is None or expense.event_id != event_id:
        abort(404)

    expense.validated_at = None
    expense.validated_by_id = None
    db.session.commit()
    flash("Note de frais rejetée.", "warning")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Self-registration — any logged-in member
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/join", methods=["POST"])
@login_required
def join(event_id: int):
    """Register current user as an attendee."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.attendees)])
    if event is None:
        abort(404)

    if current_user not in event.attendees:
        event.attendees.append(current_user)
        db.session.commit()
        flash("Vous êtes inscrit à cet événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/leave", methods=["POST"])
@login_required
def leave(event_id: int):
    """Remove current user from attendees."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.attendees)])
    if event is None:
        abort(404)

    if current_user in event.attendees:
        event.attendees.remove(current_user)
        db.session.commit()
        flash("Vous vous êtes désinscrit de cet événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/attendees", methods=["POST"])
@bureau_required
def add_attendee(event_id: int):
    """Bureau: add a specific user to the event attendees."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.attendees)])
    if event is None:
        abort(404)
    user_id = request.form.get("user_id", type=int)
    if not user_id:
        abort(400)
    from app.models.user import User

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user not in event.attendees:
        event.attendees.append(user)
        db.session.commit()
        flash(f"{user.full_name} ajouté(e) à l'événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/attendees/<int:user_id>/remove", methods=["POST"])
@bureau_required
def remove_attendee(event_id: int, user_id: int):
    """Bureau: remove a specific user from the event attendees."""
    event = db.session.get(Event, event_id, options=[selectinload(Event.attendees)])
    if event is None:
        abort(404)
    from app.models.user import User

    user = db.session.get(User, user_id)
    if user and user in event.attendees:
        event.attendees.remove(user)
        db.session.commit()
        flash(f"{user.full_name} retiré(e) de l'événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Machine linking — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/machines", methods=["POST"])
@bureau_required
def add_event_machine(event_id: int):
    """Link a machine to an event."""
    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    form = EventMachineForm()
    form.machine_id.choices = _available_machine_choices(event)

    if not form.validate_on_submit():
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")
        return redirect(url_for("events.detail", event_id=event_id))

    # Guard: machine must not already be linked to this event
    existing = db.session.get(EventMachine, (event_id, form.machine_id.data))
    if existing:
        flash("Cette machine est déjà liée à cet événement.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    link = EventMachine(
        event_id=event_id,
        machine_id=form.machine_id.data,
        comment=(form.comment.data or "").strip() or None,
        added_by_id=current_user.id,
    )
    db.session.add(link)
    db.session.commit()
    flash("Machine ajoutée à l'événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/machines/<int:machine_id>/remove", methods=["POST"])
@bureau_required
def remove_event_machine(event_id: int, machine_id: int):
    """Unlink a machine from an event."""
    link = db.session.get(EventMachine, (event_id, machine_id))
    if link is None:
        abort(404)
    db.session.delete(link)
    db.session.commit()
    flash("Machine retirée de l'événement.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


# ---------------------------------------------------------------------------
# Event slots — bureau only
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/slots", methods=["POST"])
@bureau_required
def add_slot(event_id: int):
    """Add a time slot to an event."""
    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    form = EventSlotForm()
    if form.validate_on_submit():
        slot = EventSlot(
            event_id=event_id,
            slot_date=form.slot_date.data,
            start_time=form.start_time.data,
            end_time=form.end_time.data,
            label=(form.label.data or "").strip() or None,
        )
        db.session.add(slot)
        db.session.commit()
        flash("Créneau ajouté.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id) + "#planning")


@bp.route("/<int:event_id>/slots/<int:slot_id>/edit", methods=["POST"])
@bureau_required
def edit_slot(event_id: int, slot_id: int):
    """Edit a time slot's date, times and label."""
    slot = db.session.get(EventSlot, slot_id)
    if slot is None or slot.event_id != event_id:
        abort(404)

    form = EventSlotForm()
    if form.validate_on_submit():
        slot.slot_date = form.slot_date.data
        slot.start_time = form.start_time.data
        slot.end_time = form.end_time.data
        slot.label = (form.label.data or "").strip() or None
        db.session.commit()
        flash("Créneau modifié.", "success")
    else:
        for errors in form.errors.values():
            for e in errors:
                flash(e, "danger")

    return redirect(url_for("events.detail", event_id=event_id) + "#planning")


@bp.route("/<int:event_id>/slots/<int:slot_id>/delete", methods=["POST"])
@bureau_required
def remove_slot(event_id: int, slot_id: int):
    """Remove a time slot (and all its availability declarations)."""
    slot = db.session.get(EventSlot, slot_id)
    if slot is None or slot.event_id != event_id:
        abort(404)

    form = AttendanceForm()
    if not form.validate_on_submit():
        abort(400)

    db.session.delete(slot)
    db.session.commit()
    flash("Créneau supprimé.", "success")
    return redirect(url_for("events.detail", event_id=event_id) + "#planning")


# ---------------------------------------------------------------------------
# Slot availability — any logged-in member
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/slots/<int:slot_id>/availability", methods=["POST"])
@login_required
def set_availability(event_id: int, slot_id: int):
    """Declare the current user's availability for a slot.

    Returns an HTML partial (HTMX swap) or redirects on plain form submit.
    """
    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)

    slot = db.session.get(
        EventSlot,
        slot_id,
        options=[selectinload(EventSlot.availabilities).selectinload(SlotAvailability.user)],
    )
    if slot is None or slot.event_id != event_id:
        abort(404)

    form = AvailabilityForm()
    if form.validate_on_submit():
        avail = db.session.execute(
            db.select(SlotAvailability).where(
                SlotAvailability.slot_id == slot_id,
                SlotAvailability.user_id == current_user.id,
            )
        ).scalar_one_or_none()

        status = SlotAvailabilityStatus(form.status.data)
        comment = request.form.get("comment", "").strip() or None
        if avail is None:
            avail = SlotAvailability(
                slot_id=slot_id,
                user_id=current_user.id,
                status=status,
                comment=comment,
            )
            db.session.add(avail)
        else:
            avail.status = status
            avail.comment = comment

        db.session.commit()

        # Re-query with fresh data for the partial response
        slot = db.session.get(
            EventSlot,
            slot_id,
            options=[selectinload(EventSlot.availabilities).selectinload(SlotAvailability.user)],
        )

    my_avail = slot.availability_for(current_user.id)

    if request.headers.get("HX-Request"):
        return render_template(
            "events/partials/availability_cell.html",
            slot=slot,
            event=event,
            my_avail=my_avail,
            avail_form=AvailabilityForm(),
        )

    return redirect(url_for("events.detail", event_id=event_id) + "#planning")


@bp.route("/<int:event_id>/slots/<int:slot_id>/manage_availability", methods=["POST"])
@bureau_required
def manage_member_availability(event_id: int, slot_id: int):
    """Bureau-only: set or remove availability for any member on a slot."""
    user_id = request.form.get("user_id", type=int)
    status_val = request.form.get("status")  # present, maybe, absent, or None to remove

    if not user_id:
        abort(400)

    # Check if we should remove the availability
    if not status_val or status_val == "delete":
        db.session.execute(
            db.delete(SlotAvailability).where(
                SlotAvailability.slot_id == slot_id,
                SlotAvailability.user_id == user_id,
            )
        )
        db.session.commit()
        return "", 204

    # Otherwise, update or create
    status = SlotAvailabilityStatus(status_val)
    avail = db.session.execute(
        db.select(SlotAvailability).where(
            SlotAvailability.slot_id == slot_id,
            SlotAvailability.user_id == user_id,
        )
    ).scalar_one_or_none()

    if avail is None:
        avail = SlotAvailability(
            slot_id=slot_id,
            user_id=user_id,
            status=status,
        )
        db.session.add(avail)
    else:
        avail.status = status

    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# PDF export — attendance sheet
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/emargement.pdf")
@bureau_required
def emargement_pdf(event_id: int):
    """Generate and serve the attendance sheet (liste d'émargement) as PDF."""
    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.attendees),
            selectinload(Event.created_by),
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
            selectinload(Event.volunteers),
        ],
    )
    if event is None:
        abort(404)
    html = render_template("events/emargement.html", event=event, today=date.today())
    try:
        from weasyprint import HTML as WP

        pdf = WP(string=html, base_url=".").write_pdf()
    except Exception:
        import logging

        logging.getLogger(__name__).exception("WeasyPrint PDF generation failed, returning HTML")
        return Response(html, mimetype="text/html")
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="emargement_{event_id}.pdf"'},
    )


# ---------------------------------------------------------------------------
# iCalendar export — any authenticated user
# ---------------------------------------------------------------------------


@bp.route("/<int:event_id>/export.ics")
@login_required
def export_ics(event_id: int):
    """Download a single event as an iCalendar (.ics) file.

    For non-consecutive multi-day events, one VEVENT is emitted per EventDate.
    """
    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
            selectinload(Event.volunteers),
            selectinload(Event.dates),
        ],
    )
    if event is None:
        abort(404)
    cal = _build_ics_calendar([event])
    return Response(
        cal.to_ical(),
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="event_{event_id}.ics"'},
    )


@bp.route("/<int:event_id>/dates/<date_str>/export.ics")
@login_required
def export_ics_date(event_id: int, date_str: str):
    """Download a single day of a non-consecutive event as an .ics file."""
    try:
        target_day = date.fromisoformat(date_str)
    except ValueError:
        abort(400)

    event = db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
            selectinload(Event.dates),
        ],
    )
    if event is None:
        abort(404)

    valid_days = {ed.day for ed in event.dates}
    if valid_days and target_day not in valid_days:
        abort(404)

    from icalendar import Calendar as ICal

    cal = ICal()
    cal.add("prodid", "-//Assoportail//Events//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add_component(_make_ics_vevent(event, target_day))

    filename = f"event_{event_id}_{date_str}.ics"
    return Response(
        cal.to_ical(),
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/feed.ics")
@login_required
def events_feed_ics():
    """iCalendar feed of all non-cancelled events (subscribable URL)."""
    from app.models.event import Event as EventModel

    events = db.session.scalars(
        db.select(EventModel)
        .where(EventModel.status != EventStatus.CANCELLED.value)
        .order_by(EventModel.event_date)
    ).all()
    cal = _build_ics_calendar(events)
    return Response(
        cal.to_ical(),
        mimetype="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="assoportail.ics"'},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sync_event_dates(event: Event, raw_dates: list[str]) -> None:
    """Sync EventDate rows from a list of YYYY-MM-DD strings.

    Replaces all existing EventDate records for the event with the new set.
    An empty list clears all records (reverts to consecutive-day mode).
    """
    for ed in list(event.dates):
        db.session.delete(ed)
    days: set[date] = set()
    for s in raw_dates:
        try:
            days.add(date.fromisoformat(s.strip()))
        except ValueError:
            pass
    for d in sorted(days):
        db.session.add(EventDate(event_id=event.id, day=d))


def _make_ics_vevent(event: Event, day: date | None):  # -> icalendar.Event
    """Build a single VEVENT component for *event*, optionally scoped to *day*.

    When *day* is None the whole event is rendered (uid = event-{id}).
    When *day* is provided only the slots of that day are included
    (uid = event-{id}-{day}).
    """
    from icalendar import Event as ICalEvent

    e = ICalEvent()
    e.add("dtstamp", datetime.now(UTC))

    if day is not None:
        e.add("uid", f"event-{event.id}-{day.isoformat()}@assoportail")
        e.add("summary", f"{event.title} — {day.strftime('%d/%m/%Y')}")
        start_dt = datetime.combine(day, event.event_date.timetz().replace(tzinfo=None)).replace(
            tzinfo=UTC
        )
        if event.end_date:
            end_dt = datetime.combine(day, event.end_date.timetz().replace(tzinfo=None)).replace(
                tzinfo=UTC
            )
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(hours=8)
        else:
            end_dt = start_dt + timedelta(hours=8)
        day_slots = sorted(
            [s for s in getattr(event, "slots", []) if s.slot_date == day],
            key=lambda s: (s.slot_date, s.start_time or ""),
        )
    else:
        e.add("uid", f"event-{event.id}@assoportail")
        e.add("summary", event.title)
        start_dt = event.event_date
        end_dt = event.end_date or event.event_date + timedelta(hours=8)
        day_slots = sorted(
            getattr(event, "slots", []),
            key=lambda s: (s.slot_date, s.start_time or ""),
        )

    if event.location:
        e.add("location", event.location)
    e.add("dtstart", start_dt)
    e.add("dtend", end_dt)

    # Build rich description with slots and attendees
    text_parts: list[str] = []
    html_parts: list[str] = []
    if event.description:
        text_parts.append(event.description)
        html_parts.append(f"<p>{event.description}</p>")

    if day_slots:
        text_parts.append("\n--- CRÉNEAUX ---")
        html_parts.append('<h3 style="margin:12px 0 6px;">Créneaux</h3>')
        html_parts.append(
            '<table border="1" cellpadding="4" cellspacing="0" '
            'style="border-collapse:collapse; font-size:13px; width:100%;">'
            "<tr><th>Créneau</th><th>Inscrits</th></tr>"
        )
        for slot in day_slots:
            slot_label = slot.slot_date.strftime("%a %d/%m")
            if slot.start_time:
                slot_label += f" {slot.start_time.strftime('%Hh%M')}"
            if slot.end_time:
                slot_label += f"–{slot.end_time.strftime('%Hh%M')}"
            if slot.label:
                slot_label += f" ({slot.label})"

            names: list[str] = []
            for a in slot.availabilities:
                if a.status.value in ("present", "maybe"):
                    tag = " ?" if a.status.value == "maybe" else ""
                    names.append(f"{a.user.full_name}{tag}")
            for va in getattr(slot, "volunteer_availabilities", []):
                if va.status.value in ("present", "maybe"):
                    tag = " ?" if va.status.value == "maybe" else ""
                    names.append(f"{va.volunteer.name}{tag} (bénévole)")

            text_parts.append(f"\n{slot_label}")
            for n in names:
                text_parts.append(f"  - {n}")
            if not names:
                text_parts.append("  (aucun inscrit)")

            names_html = ", ".join(names) if names else "<em>aucun</em>"
            html_parts.append(
                f"<tr><td><strong>{slot_label}</strong></td><td>{names_html}</td></tr>"
            )
        html_parts.append("</table>")

    e.add("description", "\n".join(text_parts))
    e.add("X-ALT-DESC;FMTTYPE=text/html", "".join(html_parts))
    return e


def _build_ics_calendar(events: list):  # -> icalendar.Calendar
    """Build an icalendar.Calendar from a list of Event objects.

    For events with non-consecutive EventDate rows, one VEVENT is emitted per day.
    For standard events a single VEVENT covers the full range.
    """
    from icalendar import Calendar as ICal

    cal = ICal()
    cal.add("prodid", "-//Assoportail//Events//FR")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Assoportail — Événements")

    for event in events:
        if getattr(event, "dates", None):
            for ed in event.dates:
                cal.add_component(_make_ics_vevent(event, ed.day))
        else:
            cal.add_component(_make_ics_vevent(event, None))

    return cal


def _available_machine_choices(event: Event) -> list[tuple[int, str]]:
    """Return (id, label) pairs for machines not installed in a center and not yet linked."""
    from app.models.machine import MachineStatus

    linked_ids = {em.machine_id for em in event.event_machines} if event.event_machines else set()
    machines = db.session.scalars(
        db.select(Machine)
        .options(db.orm.load_only(Machine.id, Machine.manufacturer, Machine.model, Machine.status))
        .where(Machine.status != MachineStatus.INSTALLED)
        .order_by(Machine.manufacturer, Machine.model)
    ).all()
    return [(m.id, f"{m.manufacturer} — {m.model}") for m in machines if m.id not in linked_ids]


# ---------------------------------------------------------------------------
# Volunteer public access — token-based, no login required
# ---------------------------------------------------------------------------


def _get_volunteer(personal_token: str) -> EventVolunteer | None:
    """Look up a confirmed volunteer by their personal token."""
    vol = db.session.scalars(
        db.select(EventVolunteer).where(
            EventVolunteer.personal_token == personal_token,
            EventVolunteer.confirmed.is_(True),
        )
    ).first()
    return vol


def _load_event_for_volunteer(event_id: int) -> Event:
    """Eagerly load event with slots, volunteer availabilities, docs."""
    return db.session.get(
        Event,
        event_id,
        options=[
            selectinload(Event.created_by),
            selectinload(Event.slots)
            .selectinload(EventSlot.availabilities)
            .selectinload(SlotAvailability.user),
            selectinload(Event.slots)
            .selectinload(EventSlot.volunteer_availabilities)
            .selectinload(VolunteerSlotAvailability.volunteer),
            selectinload(Event.volunteers),
            selectinload(Event.documents),
        ],
    )


@bp.route("/<int:event_id>/volunteer-link", methods=["POST"])
@bureau_required
def generate_volunteer_link(event_id: int):
    """Generate (or regenerate) a permanent token-based URL for volunteer access."""
    import secrets as _secrets

    event = db.session.get(Event, event_id)
    if event is None:
        abort(404)
    event.volunteer_token = _secrets.token_urlsafe(32)
    db.session.commit()
    flash("Lien bénévole généré.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/volunteer/<token>", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("20 per hour")
def volunteer_register(token: str):
    """Public registration page — volunteer enters name + email, receives confirmation."""
    import secrets as _secrets

    event = db.session.scalars(db.select(Event).where(Event.volunteer_token == token)).first()
    if event is None:
        abort(403)

    form = VolunteerIdentityForm()
    if request.method == "POST":
        if form.website.data:
            flash("Merci !", "success")
            return redirect(url_for("events.volunteer_register", token=token))
        if form.validate_on_submit():
            email = form.email.data.strip().lower()
            # Check if already registered
            existing = db.session.scalars(
                db.select(EventVolunteer).where(
                    EventVolunteer.event_id == event.id,
                    EventVolunteer.email == email,
                )
            ).first()
            if existing:
                # Resend confirmation email with their existing personal link
                _send_volunteer_confirmation(existing, event)
                flash("Un email avec votre lien personnel a été renvoyé.", "info")
                return redirect(url_for("events.volunteer_register", token=token))

            personal_token = _secrets.token_urlsafe(32)
            volunteer = EventVolunteer(
                event_id=event.id,
                name=form.name.data.strip(),
                email=email,
                personal_token=personal_token,
                confirmed=False,
            )
            db.session.add(volunteer)
            db.session.commit()
            _send_volunteer_confirmation(volunteer, event)
            flash(
                "Un email de confirmation a été envoyé. Vérifiez votre boîte de réception.",
                "success",
            )
            return redirect(url_for("events.volunteer_register", token=token))

    return render_template("events/volunteer_identity.html", event=event, form=form, token=token)


@bp.route("/volunteer/confirm/<personal_token>")
def volunteer_confirm(personal_token: str):
    """Confirm a volunteer's email and redirect to their personal portal."""
    vol = db.session.scalars(
        db.select(EventVolunteer).where(EventVolunteer.personal_token == personal_token)
    ).first()
    if vol is None:
        abort(403)
    if not vol.confirmed:
        vol.confirmed = True
        db.session.commit()
        flash(f"Bienvenue {vol.name} ! Votre inscription est confirmée.", "success")
    return redirect(url_for("events.volunteer_portal", personal_token=personal_token))


@bp.route("/volunteer/portal/<personal_token>")
def volunteer_portal(personal_token: str):
    """Personal volunteer portal — view event, manage slots, upload media."""
    volunteer = _get_volunteer(personal_token)
    if volunteer is None:
        abort(403)

    event = _load_event_for_volunteer(volunteer.event_id)

    vol_avails = {
        va.slot_id: va
        for va in db.session.scalars(
            db.select(VolunteerSlotAvailability).where(
                VolunteerSlotAvailability.volunteer_id == volunteer.id
            )
        ).all()
    }

    return render_template(
        "events/volunteer_portal.html",
        event=event,
        volunteer=volunteer,
        vol_avails=vol_avails,
        personal_token=personal_token,
        DocumentType=DocumentType,
    )


@bp.route("/volunteer/portal/<personal_token>/slots/<int:slot_id>", methods=["POST"])
@csrf.exempt
@limiter.limit("60 per hour")
def volunteer_set_availability(personal_token: str, slot_id: int):
    """Public: volunteer registers on a slot."""
    volunteer = _get_volunteer(personal_token)
    if volunteer is None:
        abort(403)

    slot = db.session.get(EventSlot, slot_id)
    if slot is None or slot.event_id != volunteer.event_id:
        abort(404)

    form = VolunteerAvailabilityForm()
    if form.validate_on_submit():
        status = SlotAvailabilityStatus(form.status.data)
        avail = db.session.execute(
            db.select(VolunteerSlotAvailability).where(
                VolunteerSlotAvailability.slot_id == slot_id,
                VolunteerSlotAvailability.volunteer_id == volunteer.id,
            )
        ).scalar_one_or_none()
        if avail is None:
            avail = VolunteerSlotAvailability(
                slot_id=slot_id,
                volunteer_id=volunteer.id,
                status=status,
            )
            db.session.add(avail)
        else:
            avail.status = status
        db.session.commit()
        flash("Inscription mise à jour.", "success")

    return redirect(url_for("events.volunteer_portal", personal_token=personal_token))


@bp.route("/volunteer/portal/<personal_token>/upload", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per hour")
def volunteer_upload_media(personal_token: str):
    """Public: volunteer uploads a photo or video to the event."""
    import logging
    import os

    from flask import current_app
    from werkzeug.utils import secure_filename

    from app.models.document import event_documents

    logger = logging.getLogger(__name__)

    volunteer = _get_volunteer(personal_token)
    if volunteer is None:
        abort(403)

    event = db.session.get(Event, volunteer.event_id)
    back = url_for("events.volunteer_portal", personal_token=personal_token)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Aucun fichier sélectionné.", "warning")
        return redirect(back)

    safe_name = secure_filename(file.filename)
    if not safe_name:
        flash("Nom de fichier invalide.", "danger")
        return redirect(back)

    allowed_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"}
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in allowed_exts:
        flash("Seuls les photos et vidéos sont autorisés.", "danger")
        return redirect(back)

    data = file.read()
    max_size = 50 * 1024 * 1024 if ext in (".mp4", ".webm") else 10 * 1024 * 1024
    if len(data) > max_size:
        flash(f"Le fichier dépasse la limite de {max_size // (1024 * 1024)} Mo.", "danger")
        return redirect(back)

    doc_type = DocumentType.VIDEO.value if ext in (".mp4", ".webm") else DocumentType.PHOTO.value
    import re

    slug = (
        re.sub(r"[^a-z0-9]+", "-", os.path.splitext(safe_name)[0].lower()).strip("-")[:40]
        or "media"
    )
    stored_name = f"{date.today().isoformat()}_{doc_type}_{slug}{ext}"

    doc = Document(
        original_filename=file.filename,
        stored_filename=stored_name,
        type=doc_type,
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        uploaded_by_id=event.created_by_id,
        description=f"Ajouté par {volunteer.name}",
    )
    db.session.add(doc)
    db.session.flush()
    db.session.execute(event_documents.insert().values(event_id=event.id, document_id=doc.id))

    drive_uploaded = False
    if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
        try:
            from app.services.drive import DriveService

            file_id, web_link = DriveService.from_db().upload_file(
                data,
                file.filename,
                doc.mime_type,
                doc_type,
                year=date.today().year,
            )
            doc.drive_file_id = file_id
            doc.drive_web_link = web_link
            drive_uploaded = True
        except Exception as exc:
            logger.warning("Drive upload failed for volunteer media: %s", exc)

    if not drive_uploaded:
        subdir = os.path.join(current_app.config["UPLOAD_FOLDER"], doc.subdir)
        os.makedirs(subdir, exist_ok=True)
        with open(os.path.join(subdir, stored_name), "wb") as fh:
            fh.write(data)

    db.session.commit()
    flash(f"Média « {file.filename} » ajouté.", "success")
    return redirect(back)


@bp.route("/<int:event_id>/volunteers/<int:volunteer_id>/delete", methods=["POST"])
@bureau_required
def delete_volunteer(event_id: int, volunteer_id: int):
    """Delete a volunteer from an event."""
    vol = db.session.get(EventVolunteer, volunteer_id)
    if vol is None or vol.event_id != event_id:
        abort(404)
    name = vol.name
    db.session.delete(vol)
    db.session.commit()
    flash(f"Bénévole « {name} » supprimé.", "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/email-participants", methods=["POST"])
@bureau_required
def email_participants(event_id: int):
    """Send an email to all event members and confirmed volunteers."""
    from app.services.mailer import _deliver

    event = db.session.get(
        Event,
        event_id,
        options=[selectinload(Event.attendees), selectinload(Event.volunteers)],
    )
    if event is None:
        abort(404)

    subject = request.form.get("subject", "").strip() or f"Information — {event.title}"
    body = request.form.get("body", "").strip()
    if not body:
        flash("Le contenu du message est obligatoire.", "danger")
        return redirect(url_for("events.detail", event_id=event_id))

    recipients = set()
    for u in event.attendees:
        if u.email:
            recipients.add(u.email)
    for v in event.volunteers:
        if v.confirmed and v.email:
            recipients.add(v.email)

    if not recipients:
        flash("Aucun destinataire.", "warning")
        return redirect(url_for("events.detail", event_id=event_id))

    sent = 0
    for addr in recipients:
        try:
            _deliver(to_email=addr, subject=subject, body=body)
            sent += 1
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Failed to email %s", addr)

    flash(f"Email envoyé à {sent} participant(s).", "success")
    return redirect(url_for("events.detail", event_id=event_id))


def _send_volunteer_confirmation(volunteer: EventVolunteer, event: Event) -> None:
    """Send confirmation email to volunteer with their personal portal link."""
    from app.services.mailer import _deliver

    link = url_for(
        "events.volunteer_confirm", personal_token=volunteer.personal_token, _external=True
    )
    subject = f"Confirmez votre inscription — {event.title}"
    body = (
        f"Bonjour {volunteer.name},\n\n"
        f"Vous vous êtes inscrit(e) comme bénévole pour l'événement « {event.title} ».\n\n"
        f"Cliquez sur le lien ci-dessous pour confirmer votre inscription "
        f"et accéder à votre portail personnel :\n\n"
        f"{link}\n\n"
        f"Ce lien est permanent — conservez-le pour revenir sur vos choix de créneaux.\n\n"
        f"Merci !\n"
        f"— Assoportail"
    )
    try:
        _deliver(to_email=volunteer.email, subject=subject, body=body)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failed to send volunteer confirmation to %s", volunteer.email
        )


def _notify_event_created(event: "Event") -> None:
    """Send a push notification to all active members when a new event is created."""
    import logging

    from app.models.user import User
    from app.services.push import send_push_notification

    _log = logging.getLogger(__name__)
    try:
        from app.extensions import db

        user_ids = [
            row[0]
            for row in db.session.execute(db.select(User.id).where(User.is_active.is_(True))).all()
        ]
        event_url = url_for("events.detail", event_id=event.id)
        send_push_notification(
            user_ids=user_ids,
            title="Nouvel événement",
            body=event.title,
            url=event_url,
        )
    except Exception:
        _log.exception("Failed to send push notification for event #%d", event.id)
