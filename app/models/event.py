"""Event, Expense, CashBox and CashEntry models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from datetime import date as date_type
from datetime import time as time_type
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.email import InboundEmail
    from app.models.machine import Machine
    from app.models.task import Task
    from app.models.user import User


# Many-to-many: Event ↔ User (attendees)
event_attendees = Table(
    "event_attendees",
    db.Model.metadata,
    Column("event_id", Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class EventStatus(enum.StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Event(db.Model):
    """An association event (installation day, fundraiser, etc.).

    Attributes:
        id: Primary key.
        title: Event title.
        description: Optional long description.
        status: Current lifecycle status.
        event_date: When the event takes place (UTC).
        location: Physical or virtual location (nullable).
        created_by_id: FK to the User who created the record.
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "events"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EventStatus.PLANNED.value
    )
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    volunteer_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    created_by: Mapped[User] = relationship("User", foreign_keys=[created_by_id])
    attendees: Mapped[list[User]] = relationship(
        "User", secondary=event_attendees, order_by="User.last_name"
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="event_documents")
    expenses: Mapped[list[Expense]] = relationship(
        "Expense",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="Expense.submitted_at",
    )
    cashbox: Mapped[CashBox | None] = relationship(
        "CashBox", back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task", back_populates="source_event", order_by="Task.created_at"
    )
    emails: Mapped[list[InboundEmail]] = relationship(
        "InboundEmail", back_populates="event", order_by="InboundEmail.received_at"
    )
    event_machines: Mapped[list[EventMachine]] = relationship(
        "EventMachine",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventMachine.added_at",
    )
    slots: Mapped[list[EventSlot]] = relationship(
        "EventSlot",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventSlot.slot_date, EventSlot.start_time",
    )
    dates: Mapped[list[EventDate]] = relationship(
        "EventDate",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventDate.day",
    )
    volunteers: Mapped[list[EventVolunteer]] = relationship(
        "EventVolunteer",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="EventVolunteer.registered_at",
    )

    @property
    def volunteer_hours(self) -> float:
        """Total volunteer-hours across all slots.

        For each slot with a start_time and end_time, counts the number of
        members + confirmed volunteers registered as present or maybe,
        multiplied by the slot duration in hours.
        """
        total = 0.0
        for slot in self.slots:
            if not slot.start_time or not slot.end_time:
                continue
            duration_h = (
                (slot.end_time.hour * 60 + slot.end_time.minute)
                - (slot.start_time.hour * 60 + slot.start_time.minute)
            ) / 60
            if duration_h <= 0:
                continue
            people = sum(
                1 for a in slot.availabilities if a.status.value in ("present", "maybe")
            ) + sum(
                1
                for va in getattr(slot, "volunteer_availabilities", [])
                if va.status.value in ("present", "maybe") and va.volunteer.confirmed
            )
            total += duration_h * people
        return round(total, 1)

    @property
    def unique_participants_count(self) -> int:
        """Total unique individuals (members + confirmed volunteers) who joined/signed up."""
        member_ids = {u.id for u in self.attendees}
        for slot in self.slots:
            for a in slot.availabilities:
                if a.status.value in ("present", "maybe"):
                    member_ids.add(a.user_id)

        confirmed_volunteers = {v.id for v in self.volunteers if v.confirmed}
        return len(member_ids) + len(confirmed_volunteers)

    @property
    def unique_confirmed_count(self) -> int:
        """Unique individuals (members + confirmed volunteers) marked as present."""
        member_ids = set()
        for slot in self.slots:
            for a in slot.availabilities:
                if a.status.value == "present":
                    member_ids.add(a.user_id)

        volunteer_ids = set()
        for slot in self.slots:
            for va in slot.volunteer_availabilities:
                if va.status.value == "present" and va.volunteer.confirmed:
                    volunteer_ids.add(va.volunteer_id)

        return len(member_ids) + len(volunteer_ids)

    def __repr__(self) -> str:
        return f"<Event {self.title!r} date={self.event_date.date()}>"


class ExpenseType(enum.StrEnum):
    TRAVEL = "travel"
    SUPPLY = "supply"
    OTHER = "other"


class Expense(db.Model):
    """A reimbursable expense submitted by a member for an event.

    Attributes:
        id: Primary key.
        event_id: FK to the Event.
        user_id: FK to the member who submitted the expense.
        type: Expense category (travel, supply, other).
        amount: Amount in euros (2 decimal places).
        description: What the expense was for.
        submitted_at: When the expense was submitted.
        validated_at: When bureau validated it (nullable).
        validated_by_id: FK to the bureau user who validated (nullable).
    """

    __tablename__ = "expenses"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validated_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    distance_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    receipt_document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    event: Mapped[Event] = relationship("Event", back_populates="expenses")
    submitter: Mapped[User] = relationship("User", foreign_keys=[user_id])
    validated_by: Mapped[User | None] = relationship("User", foreign_keys=[validated_by_id])
    receipt_document: Mapped[Document | None] = relationship(
        "Document", foreign_keys=[receipt_document_id]
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="expense_documents")

    @property
    def is_validated(self) -> bool:
        """True if the expense has been validated by bureau."""
        return self.validated_at is not None

    def __repr__(self) -> str:
        return f"<Expense {self.amount}€ event={self.event_id}>"


class CashEntryType(enum.StrEnum):
    DONATION = "donation"
    SALE = "sale"
    OTHER = "other"


class CashBox(db.Model):
    """Cash box opened for an event to track on-site cash flow.

    Attributes:
        id: Primary key.
        event_id: FK to the Event (unique — one cashbox per event).
        opened_at: When the cashbox was opened.
        closed_at: When the cashbox was closed (nullable).
        opening_amount: Starting cash amount in euros.
        closing_amount: Final counted cash amount (nullable, set on close).
        reconciled_by_id: FK to the bureau user who closed the cashbox.
        reconciled_at: When the cashbox was reconciled.
        reconciliation_note: Optional note explaining discrepancies.
    """

    __tablename__ = "cashboxes"
    __auditable__ = True
    __table_args__ = (UniqueConstraint("event_id", name="uq_cashbox_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opening_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    closing_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    reconciled_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[Event] = relationship("Event", back_populates="cashbox")
    reconciled_by: Mapped[User | None] = relationship("User", foreign_keys=[reconciled_by_id])
    entries: Mapped[list[CashEntry]] = relationship(
        "CashEntry",
        back_populates="cashbox",
        cascade="all, delete-orphan",
        order_by="CashEntry.recorded_at",
    )

    @property
    def is_closed(self) -> bool:
        """True if the cashbox has been closed."""
        return self.closed_at is not None

    @property
    def expected_amount(self) -> Decimal:
        """Sum of opening amount and all entries."""
        return self.opening_amount + sum((e.amount for e in self.entries), Decimal("0"))

    @property
    def discrepancy(self) -> Decimal | None:
        """Difference between actual closing amount and expected amount."""
        if self.closing_amount is None:
            return None
        return self.closing_amount - self.expected_amount

    def __repr__(self) -> str:
        return f"<CashBox event={self.event_id} open={not self.is_closed}>"


class CashEntry(db.Model):
    """A single cash transaction recorded against an open cashbox.

    Attributes:
        id: Primary key.
        cashbox_id: FK to the CashBox.
        type: Transaction category (donation, sale, other).
        amount: Transaction amount in euros (can be negative for refunds).
        note: Optional free-text description.
        recorded_by_id: FK to the User who recorded the entry.
        recorded_at: When the entry was recorded.
    """

    __tablename__ = "cash_entries"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cashbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cashboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recorded_by_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    cashbox: Mapped[CashBox] = relationship("CashBox", back_populates="entries")
    recorded_by: Mapped[User] = relationship("User", foreign_keys=[recorded_by_id])

    def __repr__(self) -> str:
        return f"<CashEntry {self.amount}€ type={self.type}>"


class EventMachine(db.Model):
    """Association between an Event and a Machine brought to that event.

    Attributes:
        event_id: FK to the Event (part of composite PK).
        machine_id: FK to the Machine (part of composite PK).
        comment: Optional free-text note (e.g. setup instructions).
        added_by_id: FK to the User who linked the machine.
        added_at: When the link was created (UTC).
    """

    __tablename__ = "event_machines"
    __auditable__ = True

    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="CASCADE"), primary_key=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    event: Mapped[Event] = relationship("Event", back_populates="event_machines")
    machine: Mapped[Machine] = relationship("Machine", back_populates="event_machines")
    added_by: Mapped[User] = relationship("User", foreign_keys=[added_by_id])

    def __repr__(self) -> str:
        return f"<EventMachine event={self.event_id} machine={self.machine_id}>"


# ---------------------------------------------------------------------------
# Event scheduling — slots and member availability
# ---------------------------------------------------------------------------


class SlotAvailabilityStatus(enum.StrEnum):
    """A member's stated availability for an event slot."""

    PRESENT = "present"
    ABSENT = "absent"
    MAYBE = "maybe"


class EventSlot(db.Model):
    """A named time window within an event (e.g. Sat 9h–14h, Sun 10h–16h).

    Attributes:
        id: Primary key.
        event_id: FK to the parent Event.
        slot_date: Calendar date of this slot.
        start_time: Optional start time (wall clock, no timezone).
        end_time: Optional end time (wall clock, no timezone).
        label: Optional short label (e.g. "Installation", "Tournoi").
    """

    __tablename__ = "event_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    start_time: Mapped[time_type | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time_type | None] = mapped_column(Time, nullable=True)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    event: Mapped[Event] = relationship("Event", back_populates="slots")
    availabilities: Mapped[list[SlotAvailability]] = relationship(
        "SlotAvailability",
        back_populates="slot",
        cascade="all, delete-orphan",
    )
    volunteer_availabilities: Mapped[list[VolunteerSlotAvailability]] = relationship(
        "VolunteerSlotAvailability",
        back_populates="slot",
        cascade="all, delete-orphan",
    )

    @property
    def display_time(self) -> str:
        """Human-readable time range string."""
        if self.start_time and self.end_time:
            return f"{self.start_time.strftime('%Hh%M')} – {self.end_time.strftime('%Hh%M')}"
        if self.start_time:
            return f"à partir de {self.start_time.strftime('%Hh%M')}"
        return ""

    def availability_for(self, user_id: int) -> SlotAvailability | None:
        """Return the SlotAvailability for *user_id*, or None if not declared."""
        for a in self.availabilities:
            if a.user_id == user_id:
                return a
        return None

    def __repr__(self) -> str:
        return f"<EventSlot event={self.event_id} date={self.slot_date}>"


class SlotAvailability(db.Model):
    """A member's declared availability for a specific EventSlot.

    Composite PK on (slot_id, user_id) — one row per member per slot.

    Attributes:
        slot_id: FK to EventSlot (part of PK).
        user_id: FK to User (part of PK).
        status: present / absent / maybe.
        updated_at: Last update timestamp (UTC).
    """

    __tablename__ = "slot_availabilities"

    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("event_slots.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[SlotAvailabilityStatus] = mapped_column(
        Enum(
            SlotAvailabilityStatus,
            name="slot_availability_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    slot: Mapped[EventSlot] = relationship("EventSlot", back_populates="availabilities")
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return (
            f"<SlotAvailability slot={self.slot_id} user={self.user_id} status={self.status.value}>"
        )


class EventVolunteer(db.Model):
    """A non-member volunteer who accessed an event via a public token link.

    Flow: volunteer enters name + email via the event's volunteer link.
    A confirmation email is sent with a personal token link.
    Once confirmed, they can manage their slot registrations via that link.
    """

    __tablename__ = "event_volunteers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    personal_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    event: Mapped[Event] = relationship("Event", back_populates="volunteers")
    slot_availabilities: Mapped[list[VolunteerSlotAvailability]] = relationship(
        "VolunteerSlotAvailability",
        back_populates="volunteer",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<EventVolunteer {self.name!r} event={self.event_id}>"


class VolunteerSlotAvailability(db.Model):
    """A volunteer's registration for a specific EventSlot."""

    __tablename__ = "volunteer_slot_availabilities"

    slot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("event_slots.id", ondelete="CASCADE"), primary_key=True
    )
    volunteer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("event_volunteers.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[SlotAvailabilityStatus] = mapped_column(
        Enum(
            SlotAvailabilityStatus,
            name="slot_availability_status",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    slot: Mapped[EventSlot] = relationship("EventSlot", back_populates="volunteer_availabilities")
    volunteer: Mapped[EventVolunteer] = relationship(
        "EventVolunteer", back_populates="slot_availabilities"
    )

    def __repr__(self) -> str:
        return f"<VolunteerSlotAvailability slot={self.slot_id} vol={self.volunteer_id}>"


class EventDate(db.Model):
    """A specific day on which a non-consecutive event takes place.

    Only populated when an event spans non-consecutive dates.
    When event.dates is non-empty, the calendar navigation jumps between these days
    instead of displaying a continuous range.
    """

    __tablename__ = "event_dates"
    __table_args__ = (UniqueConstraint("event_id", "day", name="uq_event_date_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date_type] = mapped_column(Date, nullable=False)

    event: Mapped[Event] = relationship("Event", back_populates="dates")

    def __repr__(self) -> str:
        return f"<EventDate event={self.event_id} day={self.day}>"
