"""Unit tests for mailing Celery tasks."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import Flask

from app.extensions import db
from app.models.mailing import CampaignStatus, MailingCampaign, MailingRecipient, RecipientStatus
from app.models.member import Membership, MembershipSource
from app.tasks.mailing import _send_recipients, send_campaign
from tests.conftest import UserInfo


def test_send_campaign_success(app: Flask, bureau_user: UserInfo, member_user: UserInfo):
    """Test that send_campaign task works and updates status to SENT."""
    with app.app_context():
        # 1. Setup data: User needs an active membership to be resolved by the campaign
        membership = Membership(
            user_id=member_user.id,
            source=MembershipSource.CASH,
            amount=20.0,
            started_at=datetime.now(UTC).date(),
            expires_at=(datetime.now(UTC) + timedelta(days=365)).date(),
            is_pending=False,
        )
        db.session.add(membership)

        campaign = MailingCampaign(
            name="Test Task Campaign",
            subject="Task Subject",
            body_html="<p>Task Body</p>",
            status=CampaignStatus.DRAFT.value,
            created_by_id=bureau_user.id,
            recipients_filter={"membership_status": "active", "role": "all"},
        )
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.id

        # 2. Mock Gmail and run task
        with patch("app.services.gmail.GmailClient.from_db") as mock_from_db:
            mock_client = MagicMock()
            mock_from_db.return_value = mock_client

            app.config["MAILING_RATE_LIMIT"] = 0

            # Call .run() directly to bypass ContextTask wrapper which might use a different app
            result = send_campaign.run(campaign_id)

            assert result.get("sent") == 1
            assert result.get("bounced") == 0

            # 3. Verify DB state
            db.session.expire_all()
            updated_campaign = db.session.get(MailingCampaign, campaign_id)
            assert updated_campaign.status == CampaignStatus.SENT.value
            assert updated_campaign.stats_sent == 1

            recipient = db.session.scalars(
                db.select(MailingRecipient).where(MailingRecipient.campaign_id == campaign_id)
            ).first()
            assert recipient is not None
            assert recipient.status == RecipientStatus.SENT.value
            assert recipient.sent_at is not None


def test_send_campaign_handles_already_begun_session(
    app: Flask, bureau_user: UserInfo, member_user: UserInfo
):
    """
    Test that _send_recipients works even if a transaction is implicitly active.
    This reproduces the reported issue where GmailClient.from_db() starts a transaction.
    """
    with app.app_context():
        # 1. Setup data
        campaign = MailingCampaign(
            name="Test Transaction Campaign",
            subject="Subj",
            body_html="<p>Body</p>",
            status=CampaignStatus.DRAFT.value,
            created_by_id=bureau_user.id,
        )
        db.session.add(campaign)
        db.session.commit()
        campaign_id = campaign.id

        recipient = MailingRecipient(
            campaign_id=campaign_id,
            user_id=member_user.id,
            email=member_user.email,
            status=RecipientStatus.PENDING.value,
        )
        db.session.add(recipient)
        db.session.commit()

        # 2. Mock and simulate active transaction
        mock_client = MagicMock()
        app.config["MAILING_RATE_LIMIT"] = 0

        # In Flask-SQLAlchemy, just doing a query can start a transaction
        db.session.execute(db.select(MailingCampaign)).all()

        # Now call _send_recipients.
        # It should NOT fail with "A transaction is already begun on this Session"
        # because we removed the with db.session.begin() call.
        result = _send_recipients(campaign_id, mock_client, rate_limit=0)

        assert result["sent"] == 1

        db.session.expire_all()
        updated_campaign = db.session.get(MailingCampaign, campaign_id)
        assert updated_campaign.status == CampaignStatus.SENT.value
