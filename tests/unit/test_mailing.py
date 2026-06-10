"""Unit tests for the mailing blueprint — campaign CRUD, status transitions."""

from datetime import UTC, datetime

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.mailing import CampaignStatus, MailingCampaign
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_campaign(
    app: Flask,
    creator_id: int,
    *,
    name: str = "Lettre de rentrée",
    subject: str = "Bienvenue dans l'association",
    body_html: str = "<p>Bonjour !</p>",
    status: CampaignStatus = CampaignStatus.DRAFT,
) -> int:
    """Insert a MailingCampaign row and return its id."""
    with app.app_context():
        campaign = MailingCampaign(
            name=name,
            subject=subject,
            body_html=body_html,
            status=status.value,
            created_by_id=creator_id,
        )
        _db.session.add(campaign)
        _db.session.commit()
        return campaign.id


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestMailingAccess:
    """Mailing routes are bureau-only."""

    def test_member_cannot_access_campaign_list(self, auth_client: FlaskClient) -> None:
        """A member-role user is denied access to the campaign list (403)."""
        response = auth_client.get("/mailing/")
        assert response.status_code == 403

    def test_member_cannot_access_create_form(self, auth_client: FlaskClient) -> None:
        """A member-role user receives 403 on the create campaign page."""
        response = auth_client.get("/mailing/new")
        assert response.status_code == 403

    def test_unauthenticated_redirected_from_list(self, client: FlaskClient) -> None:
        """An unauthenticated visitor is redirected to the login page."""
        response = client.get("/mailing/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_bureau_can_access_campaign_list(self, bureau_client: FlaskClient) -> None:
        """A bureau user can access the campaign list (200)."""
        response = bureau_client.get("/mailing/")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Create campaign
# ---------------------------------------------------------------------------


class TestCampaignCreate:
    """Bureau-only campaign creation."""

    def test_bureau_can_create_draft_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Submitting valid data creates a draft MailingCampaign with all fields saved."""
        response = bureau_client.post(
            "/mailing/new",
            data={
                "name": "Appel aux bénévoles",
                "subject": "Rejoignez-nous pour l'installation de mai !",
                "body_html": "<p>Nous avons besoin de vous.</p>",
                "scheduled_at": "",
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            campaign = _db.session.execute(
                _db.select(MailingCampaign).filter_by(name="Appel aux bénévoles")
            ).scalar_one_or_none()
            assert campaign is not None
            assert campaign.subject == "Rejoignez-nous pour l'installation de mai !"
            assert campaign.body_html == "<p>Nous avons besoin de vous.</p>"
            assert campaign.status == CampaignStatus.DRAFT.value
            assert campaign.created_by_id == bureau_user.id

    def test_campaign_with_scheduled_at_gets_status_scheduled(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A campaign submitted with a scheduled_at date gets status=scheduled."""
        response = bureau_client.post(
            "/mailing/new",
            data={
                "name": "Newsletter planifiée",
                "subject": "Nouvelles du mois",
                "body_html": "<p>Voici les actualités.</p>",
                "scheduled_at": "2026-05-01T09:00",
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            campaign = _db.session.execute(
                _db.select(MailingCampaign).filter_by(name="Newsletter planifiée")
            ).scalar_one_or_none()
            assert campaign is not None
            assert campaign.status == CampaignStatus.SCHEDULED.value
            assert campaign.scheduled_at is not None

    def test_create_requires_name(self, bureau_client: FlaskClient) -> None:
        """Submitting without a campaign name stays on the form (validation error)."""
        response = bureau_client.post(
            "/mailing/new",
            data={
                "name": "",
                "subject": "Sujet",
                "body_html": "<p>Corps.</p>",
                "scheduled_at": "",
                "membership_status": "active",
                "role": "all",
            },
        )
        assert response.status_code == 200
        assert "obligatoire" in response.data.decode()

    def test_create_requires_body(self, bureau_client: FlaskClient) -> None:
        """Submitting without a body stays on the form (validation error)."""
        response = bureau_client.post(
            "/mailing/new",
            data={
                "name": "Campagne sans corps",
                "subject": "Un sujet",
                "body_html": "",
                "scheduled_at": "",
                "membership_status": "active",
                "role": "all",
            },
        )
        assert response.status_code == 200
        assert "obligatoire" in response.data.decode()

    def test_member_cannot_create_campaign(self, auth_client: FlaskClient) -> None:
        """A member-role user receives 403 when posting to create."""
        response = auth_client.post(
            "/mailing/new",
            data={
                "name": "Campagne interdite",
                "subject": "Sujet",
                "body_html": "<p>Corps.</p>",
                "membership_status": "active",
                "role": "all",
            },
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Edit campaign
# ---------------------------------------------------------------------------


class TestCampaignEdit:
    """Bureau-only campaign editing, restricted to draft status."""

    def test_bureau_can_edit_draft_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A bureau user can update the name and subject of a draft campaign."""
        cid = _make_campaign(app, bureau_user.id, name="Ancienne campagne")
        bureau_client.post(
            f"/mailing/{cid}/edit",
            data={
                "name": "Campagne révisée",
                "subject": "Nouveau sujet",
                "body_html": "<p>Nouveau corps.</p>",
                "scheduled_at": "",
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=False,
        )
        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign.name == "Campagne révisée"
            assert campaign.subject == "Nouveau sujet"
            assert campaign.status == CampaignStatus.DRAFT.value

    def test_bureau_cannot_edit_sent_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Editing a sent campaign flashes a warning and redirects without modifying data."""
        cid = _make_campaign(
            app, bureau_user.id, name="Campagne envoyée", status=CampaignStatus.SENT
        )
        response = bureau_client.post(
            f"/mailing/{cid}/edit",
            data={
                "name": "Tentative de modification",
                "subject": "Sujet modifié",
                "body_html": "<p>Nouveau corps.</p>",
                "scheduled_at": "",
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        body = response.data.decode()
        assert "brouillon" in body.lower()

        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign.name == "Campagne envoyée"  # unchanged

    def test_edit_nonexistent_campaign_returns_404(self, bureau_client: FlaskClient) -> None:
        """Editing a campaign that does not exist returns 404."""
        response = bureau_client.get("/mailing/99999/edit")
        assert response.status_code == 404

    def test_member_cannot_edit_campaign(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A member-role user receives 403 when trying to edit a campaign."""
        cid = _make_campaign(app, bureau_user.id)
        response = auth_client.get(f"/mailing/{cid}/edit")
        assert response.status_code == 403

    def test_edit_adding_scheduled_at_changes_status_to_scheduled(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Adding a scheduled_at date to a draft campaign changes its status to scheduled."""
        cid = _make_campaign(app, bureau_user.id, name="Draft à planifier")
        bureau_client.post(
            f"/mailing/{cid}/edit",
            data={
                "name": "Draft à planifier",
                "subject": "Sujet",
                "body_html": "<p>Corps.</p>",
                "scheduled_at": "2026-06-15T10:00",
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=False,
        )
        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign.status == CampaignStatus.SCHEDULED.value
            assert campaign.scheduled_at is not None

    def test_edit_removing_scheduled_at_reverts_to_draft(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Clearing scheduled_at on a scheduled campaign reverts its status to draft."""
        cid = _make_campaign(
            app, bureau_user.id, name="Campagne planifiée", status=CampaignStatus.SCHEDULED
        )
        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            campaign.scheduled_at = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
            _db.session.commit()

        bureau_client.post(
            f"/mailing/{cid}/edit",
            data={
                "name": "Campagne planifiée",
                "subject": "Sujet",
                "body_html": "<p>Corps.</p>",
                "scheduled_at": "",  # cleared
                "membership_status": "active",
                "role": "all",
            },
            follow_redirects=False,
        )
        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign.status == CampaignStatus.DRAFT.value
            assert campaign.scheduled_at is None


# ---------------------------------------------------------------------------
# Delete campaign
# ---------------------------------------------------------------------------


class TestCampaignDelete:
    """Bureau-only campaign deletion, restricted to draft status."""

    def test_bureau_can_delete_draft_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A bureau user can delete a draft campaign; it is removed from the DB."""
        cid = _make_campaign(app, bureau_user.id, name="Campagne à supprimer")
        response = bureau_client.post(
            f"/mailing/{cid}/delete",
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign is None

    def test_bureau_cannot_delete_sent_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Deleting a sent campaign flashes a warning and leaves the record intact."""
        cid = _make_campaign(
            app, bureau_user.id, name="Campagne déjà envoyée", status=CampaignStatus.SENT
        )
        response = bureau_client.post(
            f"/mailing/{cid}/delete",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "brouillon" in response.data.decode().lower()

        with app.app_context():
            campaign = _db.session.get(MailingCampaign, cid)
            assert campaign is not None


# ---------------------------------------------------------------------------
# Preview campaign
# ---------------------------------------------------------------------------


class TestCampaignPreview:
    """Bureau-only campaign preview with tag replacement."""

    def test_bureau_can_preview_campaign(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A bureau user can view the preview of a campaign with replaced tags.

        For a member-audience campaign, centre-specific tags ([[lien_panne]],
        [[lien_livre_or]]) are replaced with an empty string — they are not
        applicable to members, so nothing is shown rather than a dummy URL.
        """
        body_html = "<p>Signalez une panne ici : [[lien_panne]]</p>"
        cid = _make_campaign(app, bureau_user.id, body_html=body_html)

        response = bureau_client.get(f"/mailing/{cid}/preview")
        assert response.status_code == 200
        data = response.data.decode()
        assert "Signalez une panne ici" in data
        # The tag is replaced with "" in the rendered body. The info alert
        # legitimately contains [[lien_panne]] as a label, so we check the
        # body directly instead of asserting the string is absent from the page.
        assert "<p>Signalez une panne ici : </p>" in data

    def test_member_cannot_preview_campaign(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A member-role user receives 403 when trying to preview a campaign."""
        cid = _make_campaign(app, bureau_user.id)
        response = auth_client.get(f"/mailing/{cid}/preview")
        assert response.status_code == 403

    def test_preview_nonexistent_campaign_returns_404(self, bureau_client: FlaskClient) -> None:
        """Previewing a campaign that does not exist returns 404."""
        response = bureau_client.get("/mailing/99999/preview")
        assert response.status_code == 404

    def test_member_cannot_delete_campaign(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A member-role user receives 403 when trying to delete a campaign."""
        cid = _make_campaign(app, bureau_user.id)
        response = auth_client.post(f"/mailing/{cid}/delete")
        assert response.status_code == 403
