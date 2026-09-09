"""VolunteerCredit model — time credited to a member outside of event slots."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

if TYPE_CHECKING:
    from app.models.machine import MachineInstallation
    from app.models.task import Task
    from app.models.user import User


class VolunteerCreditSource(enum.StrEnum):
    """What generated a volunteer time credit."""

    TASK = "task"
    MACHINE_INSTALLATION = "machine_installation"


class VolunteerCredit(db.Model):
    """A block of volunteer time credited to a member.

    Unlike event-slot presence (computed on the fly from ``SlotAvailability``),
    this is time explicitly logged by a member or the bureau — e.g. resolving
    a task, or installing/moving a machine. Counted alongside event hours in
    ``_member_volunteer_hours``.

    Attributes:
        id: Primary key.
        user_id: FK to the credited User.
        hours: Time credited, in hours (e.g. 1.5).
        source: What generated this credit.
        task_id: FK to the Task, when source is TASK (nullable).
        machine_installation_id: FK to the MachineInstallation, when source
            is MACHINE_INSTALLATION (nullable — also null for a retrieval to
            stock, which has no resulting installation row).
        note: Optional free-text note (e.g. a copy of the task comment).
        created_by_id: FK to the User who logged this credit (nullable).
        created_at: Creation timestamp (UTC).
    """

    __tablename__ = "volunteer_credits"
    __auditable__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    source: Mapped[VolunteerCreditSource] = mapped_column(
        Enum(
            VolunteerCreditSource,
            name="volunteer_credit_source",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    machine_installation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machine_installations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    created_by: Mapped[User | None] = relationship("User", foreign_keys=[created_by_id])
    task: Mapped[Task | None] = relationship("Task", back_populates="volunteer_credits")
    machine_installation: Mapped[MachineInstallation | None] = relationship("MachineInstallation")

    def __repr__(self) -> str:
        return (
            f"<VolunteerCredit user={self.user_id} hours={self.hours} source={self.source.value}>"
        )
