"""Unit tests for center installation requests and global guestbook/emailing tags."""

import hashlib
import hmac
import time
from datetime import UTC, datetime

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.center import (
    Center,
    CenterFeedback,
    CenterStatus,
    InstallationRequest,
)
from app.models.mailing import MailingCampaign, MailingRecipient, RecipientStatus
from app.models.user import User, UserRole


def _captcha_data(app: Flask, a: int = 4, b: int = 3) -> dict:
    """Return valid captcha hidden-field data for the installation request form."""
    key = app.secret_key
    if isinstance(key, str):
        key = key.encode()
    ts = int(time.time())
    token = hmac.new(key, f"{a},{b},{ts}".encode(), hashlib.sha256).hexdigest()
    return {
        "captcha_a": str(a),
        "captcha_b": str(b),
        "captcha_ts": str(ts),
        "captcha_token": token,
        "captcha_answer": str(a + b),
    }


def _make_pending_request(
    app: Flask, *, center_name: str = "Clinique Est", email: str = "referent@est.fr"
) -> int:
    with app.app_context():
        req = InstallationRequest(
            center_name=center_name,
            address="45 avenue Foch",
            city="Metz",
            zip_code="57000",
            contact_name="Jean Dupont",
            contact_role="Animateur",
            contact_email=email,
            contact_phone="0387000000",
            motivation="Nous serions ravis d'accueillir un flipper pour nos patients.",
            status="pending",
            created_at=datetime.now(UTC),
        )
        _db.session.add(req)
        _db.session.commit()
        return req.id


class TestInstallationRequestPublic:
    def test_public_user_can_submit_request(self, app: Flask, client: FlaskClient) -> None:
        response = client.post(
            "/centers/request-installation",
            data={
                "center_name": "EHPAD Saint-Jean",
                "address": "1 avenue de Verdun",
                "city": "Nancy",
                "zip_code": "54000",
                "contact_name": "Alice Martin",
                "contact_role": "Directrice",
                "contact_email": "directrice@sj-nancy.fr",
                "contact_phone": "0383000000",
                "motivation": "Projet d'animation avec des jeux anciens pour les aînés.",
                "website": "",
                **_captcha_data(app),
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        assert "merci" in response.headers["Location"]

        with app.app_context():
            req = _db.session.execute(
                _db.select(InstallationRequest).filter_by(center_name="EHPAD Saint-Jean")
            ).scalar_one_or_none()
            assert req is not None
            assert req.status == "pending"
            assert req.contact_name == "Alice Martin"

    def test_honeypot_discards_submission(self, app: Flask, client: FlaskClient) -> None:
        client.post(
            "/centers/request-installation",
            data={
                "center_name": "Spam Center",
                "address": "",
                "city": "SpamCity",
                "zip_code": "00000",
                "contact_name": "Spammer",
                "contact_role": "",
                "contact_email": "spam@spam.com",
                "contact_phone": "",
                "motivation": "Buy something!",
                "website": "http://spambot.com",  # Honeypot filled
            },
        )
        with app.app_context():
            req = _db.session.execute(
                _db.select(InstallationRequest).filter_by(center_name="Spam Center")
            ).scalar_one_or_none()
            assert req is None


class TestInstallationRequestAdmin:
    def test_member_cannot_view_requests(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/centers/requests")
        assert response.status_code == 403

    def test_bureau_can_view_requests(self, app: Flask, bureau_client: FlaskClient) -> None:
        _make_pending_request(app, center_name="Clinique Saint-Pierre")
        response = bureau_client.get("/centers/requests")
        assert response.status_code == 200
        assert "Clinique Saint-Pierre" in response.data.decode()

    def test_bureau_can_approve_request(self, app: Flask, bureau_client: FlaskClient) -> None:
        req_id = _make_pending_request(app, center_name="Clinique Ouest")

        # Access approval page
        response = bureau_client.get(f"/centers/requests/{req_id}/approve")
        assert response.status_code == 200
        assert "Clinique Ouest" in response.data.decode()

        # Submit approval
        response = bureau_client.post(
            f"/centers/requests/{req_id}/approve",
            data={
                "name": "Clinique Ouest Modifiée",
                "address": "45 avenue Foch",
                "city": "Metz",
                "zip_code": "57000",
                "status": "prospect",
                "notes": "Validé par le bureau.",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            req = _db.session.get(InstallationRequest, req_id)
            assert req.status == "approved"
            assert req.created_center_id is not None

            center = _db.session.get(Center, req.created_center_id)
            assert center is not None
            assert center.name == "Clinique Ouest Modifiée"
            assert center.status == CenterStatus.PROSPECT

            assert len(center.contacts) == 1
            assert center.contacts[0].name == "Jean Dupont"
            assert center.contacts[0].email == "referent@est.fr"

    def test_bureau_can_reject_request(self, app: Flask, bureau_client: FlaskClient) -> None:
        req_id = _make_pending_request(app, center_name="Clinique Sud")
        response = bureau_client.post(f"/centers/requests/{req_id}/reject", follow_redirects=False)
        assert response.status_code in {301, 302}

        with app.app_context():
            req = _db.session.get(InstallationRequest, req_id)
            assert req.status == "rejected"
            assert req.created_center_id is None


class TestGlobalGuestbook:
    def test_member_can_view_global_guestbook(self, app: Flask, auth_client: FlaskClient) -> None:
        with app.app_context():
            c = Center(name="Livre d'or Center", city="Nancy", zip_code="54000")
            _db.session.add(c)
            _db.session.flush()
            fb = CenterFeedback(
                center_id=c.id,
                submitted_by="Patient Heureux",
                content="Génial le flipper !",
                is_published=True,
            )
            _db.session.add(fb)
            _db.session.commit()

        response = auth_client.get("/centers/guestbook")
        assert response.status_code == 200
        assert "Patient Heureux" in response.data.decode()
        assert "Génial le flipper !" in response.data.decode()


class TestMailingPlaceholderTags:
    def test_email_tag_replacement(self, app: Flask) -> None:
        from unittest.mock import MagicMock

        from app.tasks.mailing import _send_recipients

        with app.app_context():
            # Create a user (required by FK on mailing_campaigns.created_by_id)
            user = User(
                email="test-mailing@example.com",
                password_hash="x",
                first_name="Test",
                last_name="User",
                role=UserRole.BUREAU,
            )
            _db.session.add(user)
            _db.session.flush()

            # Create a center and contact
            c = Center(
                name="Mailing Center", city="Metz", zip_code="57000", status=CenterStatus.ACTIVE
            )
            _db.session.add(c)
            _db.session.flush()

            # Create a campaign
            campaign = MailingCampaign(
                name="Test campaign",
                subject="Test sub [[lien_panne]]",
                body_html=(
                    "Bonjour, voici le livre d'or [[lien_livre_or]] et de panne [[lien_panne]]"
                ),
                created_by_id=user.id,
            )
            _db.session.add(campaign)
            _db.session.flush()

            # Create a mailing recipient
            recipient = MailingRecipient(
                campaign_id=campaign.id,
                email="contact@mailingcenter.fr",
                center_id=c.id,
                status=RecipientStatus.PENDING.value,
            )
            _db.session.add(recipient)
            _db.session.commit()

            campaign_id = campaign.id

        # Mock the GmailClient
        mock_client = MagicMock()

        import base64
        import email as _email

        # test_request_context lets url_for(_external=True) build URLs without
        # needing SERVER_NAME configured in settings.
        with app.test_request_context():
            # Call _send_recipients (requires Flask app + request context for url_for)
            result = _send_recipients(campaign_id, mock_client)
            assert result["sent"] == 1

            # Fetch the updated center to get expected tokens
            updated_c = _db.session.query(Center).filter_by(name="Mailing Center").one()
            assert updated_c.feedback_token is not None
            assert updated_c.breakdown_token is not None

            # Decode the raw RFC-822 message passed to send_message
            mock_client.send_message.assert_called_once()
            raw_msg_b64 = mock_client.send_message.call_args[0][0]
            raw_bytes = base64.urlsafe_b64decode(raw_msg_b64.encode())

            # Parse the MIME structure and collect all decoded text
            msg = _email.message_from_bytes(raw_bytes)
            full_text = ""
            for part in msg.walk():
                payload = part.get_payload(decode=True)
                if payload:
                    full_text += payload.decode("utf-8", errors="replace")

            # Both tokens must appear somewhere in the decoded message body
            assert updated_c.feedback_token in full_text, (
                f"feedback_token not found in email body: {full_text[:300]}"
            )
            assert updated_c.breakdown_token in full_text, (
                f"breakdown_token not found in email body: {full_text[:300]}"
            )
            assert "feedback/" in full_text
            assert "breakdown/" in full_text or "panne" in full_text
