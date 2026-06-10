"""Machine, MachineInstallation, and MaintenanceRecord models."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.center import Center
    from app.models.document import Document
    from app.models.task import Task
    from app.models.user import User


class MachineStatus(enum.StrEnum):
    """Operational state of a pinball machine."""

    STOCK = "stock"
    INSTALLED = "installed"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class MaintenanceStatus(enum.StrEnum):
    """Lifecycle state of a maintenance record."""

    OPEN = "open"
    RESOLVED = "resolved"


class Machine(db.Model):
    """A pinball machine owned by the association.

    Attributes:
        id: Primary key.
        model: Model / game title (e.g. "Medieval Madness").
        manufacturer: Brand name (e.g. "Williams").
        serial_number: Unique serial number (nullable).
        year: Manufacturing year (nullable).
        status: Operational state.
        last_checked_at: Date of last operational check (reset by "machine OK" button).
        notes: Internal notes (nullable).
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "machines"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_number: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    serial_number: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[MachineStatus] = mapped_column(
        Enum(
            MachineStatus, name="machine_status", values_callable=lambda obj: [e.value for e in obj]
        ),
        nullable=False,
        default=MachineStatus.STOCK,
    )
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    last_checked_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    installations: Mapped[list[MachineInstallation]] = relationship(
        "MachineInstallation",
        back_populates="machine",
        order_by="MachineInstallation.installed_at.desc()",
        cascade="all, delete-orphan",
    )
    maintenance_records: Mapped[list[MaintenanceRecord]] = relationship(
        "MaintenanceRecord",
        back_populates="machine",
        order_by="MaintenanceRecord.date.desc()",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list[Task]] = relationship(
        "Task",
        back_populates="machine",
        order_by="Task.created_at.desc()",
    )
    documents: Mapped[list[Document]] = relationship("Document", secondary="machine_documents")
    event_machines: Mapped[list] = relationship(
        "EventMachine",
        back_populates="machine",
        order_by="EventMachine.added_at.desc()",
    )

    @property
    def current_installation(self) -> MachineInstallation | None:
        """Return the active installation, or None if not currently installed."""
        return next((i for i in self.installations if i.removed_at is None), None)

    @property
    def display_name(self) -> str:
        """Human-readable identifier: manufacturer + model."""
        return f"{self.manufacturer} — {self.model}"

    @property
    def days_since_last_activity(self) -> int | None:
        """Days since the most recent event (check, installation, or maintenance).

        Returns None if there is no activity recorded at all.
        """
        candidates: list[date] = []
        if self.last_checked_at:
            candidates.append(self.last_checked_at)
        for inst in self.installations:
            candidates.append(inst.installed_at)
        for mr in self.maintenance_records:
            candidates.append(mr.date)
            if mr.resolved_at:
                candidates.append(mr.resolved_at)
        if not candidates:
            return None
        most_recent = max(candidates)
        return (date.today() - most_recent).days

    def __repr__(self) -> str:
        return f"<Machine {self.display_name!r} status={self.status.value}>"


class MachineInstallation(db.Model):
    """A record of a machine being placed at a center or a member's home.

    A machine can only be installed at one location at a time.
    The partial unique index ``uq_machine_active_installation`` enforces this
    at the database level: only one row per ``machine_id`` may have
    ``removed_at IS NULL``.

    Either ``center_id`` or ``hosted_by_id`` is set — never both.

    Attributes:
        id: Primary key.
        machine_id: FK to the Machine.
        center_id: FK to the Center (nullable — null when hosted by a member).
        hosted_by_id: FK to the User hosting the machine at home (nullable).
        installed_at: Date the machine was delivered.
        removed_at: Date the machine was retrieved (nullable → still installed).
        notes: Optional notes about this installation period.
    """

    __tablename__ = "machine_installations"
    __auditable__ = True
    __table_args__ = (
        Index(
            "uq_machine_active_installation",
            "machine_id",
            unique=True,
            postgresql_where="removed_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    center_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    hosted_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    installed_at: Mapped[date] = mapped_column(Date, nullable=False)
    removed_at: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    machine: Mapped[Machine] = relationship("Machine", back_populates="installations")
    center: Mapped[Center | None] = relationship("Center", back_populates="installations")
    hosted_by: Mapped[User | None] = relationship("User", foreign_keys=[hosted_by_id])

    @property
    def is_active(self) -> bool:
        """True while the machine is currently installed (not yet removed)."""
        return self.removed_at is None

    @property
    def location_label(self) -> str:
        """Human-readable location name."""
        if self.center:
            return self.center.name
        if self.hosted_by:
            return f"Chez {self.hosted_by.full_name}"
        return "—"

    def __repr__(self) -> str:
        return (
            f"<MachineInstallation machine={self.machine_id} "
            f"center={self.center_id} hosted_by={self.hosted_by_id} active={self.is_active}>"
        )


class MaintenanceRecord(db.Model):
    """A record of maintenance work performed on a machine.

    Can be linked to a Task (from a center breakdown report) via
    ``source_task_id``.

    Attributes:
        id: Primary key.
        machine_id: FK to the Machine.
        date: Date the maintenance was performed.
        description: What was done.
        cost: Repair cost in EUR (nullable).
        maintainer_name: Free-text name of the person who did the work.
        maintainer_user_id: FK to a portal User if the maintainer is a member (nullable).
        source_task_id: FK to the Task that triggered this maintenance (nullable).
        created_at: Record creation timestamp (UTC).
    """

    __tablename__ = "maintenance_records"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    center_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("centers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[MaintenanceStatus] = mapped_column(
        Enum(
            MaintenanceStatus,
            name="maintenance_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=MaintenanceStatus.OPEN,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    maintainer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    maintainer_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resolved_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolution_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    machine: Mapped[Machine] = relationship("Machine", back_populates="maintenance_records")
    center: Mapped[Center | None] = relationship("Center", back_populates="maintenance_records")
    maintainer_user: Mapped[User | None] = relationship("User", foreign_keys=[maintainer_user_id])
    resolved_by: Mapped[User | None] = relationship("User", foreign_keys=[resolved_by_id])
    source_task: Mapped[Task | None] = relationship("Task", foreign_keys=[source_task_id])
    documents: Mapped[list[Document]] = relationship("Document", secondary="maintenance_documents")

    @property
    def is_open(self) -> bool:
        """True while the maintenance has not been resolved."""
        return self.status == MaintenanceStatus.OPEN

    def __repr__(self) -> str:
        return f"<MaintenanceRecord machine={self.machine_id} date={self.date}>"
