"""Data models package.

Import all models here so Flask-Migrate (Alembic) can detect them
when generating migration scripts.
"""

from app.models.center import Center, CenterFeedback, InstallationRequest
from app.models.config import AssociationConfig
from app.models.document import Document
from app.models.email import EmailRule, EmailRuleLog, GmailToken, InboundEmail
from app.models.event import (
    CashBox,
    CashEntry,
    Event,
    EventDate,
    EventMachine,
    EventVolunteer,
    Expense,
    VolunteerSlotAvailability,
)
from app.models.machine import Machine, MachineInstallation, MaintenanceRecord
from app.models.mailing import MailingCampaign, MailingRecipient
from app.models.meeting import Meeting
from app.models.member import Membership
from app.models.social import (
    SocialAccount,
    SocialPost,
    SocialPostImage,
    SocialPostProcessedImage,
    SocialPublishLog,
)
from app.models.task import Task, TaskComment
from app.models.treasury import Tag, Transaction
from app.models.user import User

__all__ = [
    "AssociationConfig",
    "CashBox",
    "CashEntry",
    "Center",
    "CenterFeedback",
    "InstallationRequest",
    "Document",
    "EmailRule",
    "EmailRuleLog",
    "Event",
    "EventDate",
    "EventMachine",
    "EventVolunteer",
    "GmailToken",
    "InboundEmail",
    "MailingCampaign",
    "MailingRecipient",
    "Expense",
    "Machine",
    "MachineInstallation",
    "MaintenanceRecord",
    "Meeting",
    "Membership",
    "Tag",
    "Task",
    "TaskComment",
    "SocialAccount",
    "SocialPost",
    "SocialPostImage",
    "SocialPostProcessedImage",
    "SocialPublishLog",
    "VolunteerSlotAvailability",
    "Transaction",
    "User",
]
