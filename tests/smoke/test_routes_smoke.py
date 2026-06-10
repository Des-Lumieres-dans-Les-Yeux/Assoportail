"""Smoke tests — one per blueprint route, verifying each returns the expected HTTP status.

These tests do not assert business logic; they guard against broken routing,
missing templates, and import errors.
"""

from flask.testing import FlaskClient


class TestAuthRoutes:
    """Smoke tests for the auth blueprint."""

    def test_login_page_renders(self, client: FlaskClient) -> None:
        """GET /auth/login returns 200 for unauthenticated users."""
        response = client.get("/auth/login")
        assert response.status_code == 200

    def test_register_page_redirects_unauthenticated(self, client: FlaskClient) -> None:
        """GET /auth/register redirects unauthenticated users (bureau-only route)."""
        response = client.get("/auth/register")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_register_page_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /auth/register returns 200 for bureau users."""
        response = bureau_client.get("/auth/register")
        assert response.status_code == 200

    def test_change_password_redirects_unauthenticated(self, client: FlaskClient) -> None:
        """GET /auth/change-password redirects unauthenticated users."""
        response = client.get("/auth/change-password")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_change_password_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /auth/change-password returns 200 for an authenticated member."""
        response = auth_client.get("/auth/change-password")
        assert response.status_code == 200

    def test_logout_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /auth/logout redirects to login when not authenticated."""
        response = client.get("/auth/logout")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_logout_succeeds_when_authenticated(self, auth_client: FlaskClient) -> None:
        """GET /auth/logout redirects to login after successful logout."""
        response = auth_client.get("/auth/logout")
        assert response.status_code in {301, 302}


class TestDashboardRoutes:
    """Smoke tests for the dashboard blueprint."""

    def test_dashboard_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET / redirects unauthenticated users to the login page."""
        response = client.get("/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_dashboard_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET / returns 200 for an authenticated member."""
        response = auth_client.get("/")
        assert response.status_code == 200

    def test_dashboard_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET / returns 200 for a bureau member."""
        response = bureau_client.get("/")
        assert response.status_code == 200


class TestMailboxRoutes:
    """Smoke tests for the mailbox blueprint."""

    def test_inbox_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /mailbox/ redirects unauthenticated users."""
        response = client.get("/mailbox/")
        assert response.status_code in {301, 302}

    def test_inbox_forbidden_for_member(self, auth_client: FlaskClient) -> None:
        """GET /mailbox/ returns 403 for a non-bureau member."""
        response = auth_client.get("/mailbox/")
        assert response.status_code == 403

    def test_inbox_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /mailbox/ returns 200 for bureau users."""
        response = bureau_client.get("/mailbox/")
        assert response.status_code == 200

    def test_rules_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /mailbox/rules returns 200 for bureau users."""
        response = bureau_client.get("/mailbox/rules")
        assert response.status_code == 200

    def test_create_rule_form_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /mailbox/rules/new returns 200 for bureau users."""
        response = bureau_client.get("/mailbox/rules/new")
        assert response.status_code == 200


class TestMailingRoutes:
    """Smoke tests for the mailing blueprint."""

    def test_list_campaigns_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /mailing/ redirects unauthenticated users."""
        response = client.get("/mailing/")
        assert response.status_code in {301, 302}

    def test_list_campaigns_forbidden_for_member(self, auth_client: FlaskClient) -> None:
        """GET /mailing/ returns 403 for a non-bureau member."""
        response = auth_client.get("/mailing/")
        assert response.status_code == 403

    def test_list_campaigns_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /mailing/ returns 200 for bureau users."""
        response = bureau_client.get("/mailing/")
        assert response.status_code == 200

    def test_create_campaign_form_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /mailing/new returns 200 for bureau users."""
        response = bureau_client.get("/mailing/new")
        assert response.status_code == 200


class TestMembersRoutes:
    """Smoke tests for the members blueprint."""

    def test_list_members_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /members/ redirects unauthenticated users."""
        response = client.get("/members/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_members_forbidden_for_member(self, auth_client: FlaskClient) -> None:
        """GET /members/ returns 403 for a non-bureau member."""
        response = auth_client.get("/members/")
        assert response.status_code == 403

    def test_list_members_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /members/ returns 200 for bureau users."""
        response = bureau_client.get("/members/")
        assert response.status_code == 200

    def test_profile_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /members/profile redirects unauthenticated users."""
        response = client.get("/members/profile")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_profile_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /members/profile returns 200 for an authenticated member."""
        response = auth_client.get("/members/profile")
        assert response.status_code == 200

    def test_profile_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /members/profile returns 200 for a bureau member."""
        response = bureau_client.get("/members/profile")
        assert response.status_code == 200


class TestMachinesRoutes:
    """Smoke tests for the machines blueprint."""

    def test_list_machines_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /machines/ redirects unauthenticated users."""
        response = client.get("/machines/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_machines_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /machines/ returns 200 for an authenticated member."""
        response = auth_client.get("/machines/")
        assert response.status_code == 200

    def test_list_machines_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /machines/ returns 200 for bureau users."""
        response = bureau_client.get("/machines/")
        assert response.status_code == 200


class TestCentersRoutes:
    """Smoke tests for the centers blueprint."""

    def test_list_centers_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /centers/ redirects unauthenticated users."""
        response = client.get("/centers/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_centers_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /centers/ returns 200 for an authenticated member."""
        response = auth_client.get("/centers/")
        assert response.status_code == 200

    def test_list_centers_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /centers/ returns 200 for bureau users."""
        response = bureau_client.get("/centers/")
        assert response.status_code == 200


class TestEventsRoutes:
    """Smoke tests for the events blueprint."""

    def test_list_events_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /events/ redirects unauthenticated users."""
        response = client.get("/events/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_events_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /events/ returns 200 for an authenticated member."""
        response = auth_client.get("/events/")
        assert response.status_code == 200

    def test_list_events_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /events/ returns 200 for bureau users."""
        response = bureau_client.get("/events/")
        assert response.status_code == 200


class TestDocumentsRoutes:
    """Smoke tests for the documents blueprint."""

    def test_gallery_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /documents/ redirects unauthenticated users."""
        response = client.get("/documents/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_gallery_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /documents/ returns 200 for an authenticated member."""
        response = auth_client.get("/documents/")
        assert response.status_code == 200

    def test_gallery_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /documents/ returns 200 for bureau users."""
        response = bureau_client.get("/documents/")
        assert response.status_code == 200


class TestTasksRoutes:
    """Smoke tests for the tasks blueprint."""

    def test_list_tasks_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /tasks/ redirects unauthenticated users."""
        response = client.get("/tasks/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_tasks_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /tasks/ returns 200 for an authenticated member."""
        response = auth_client.get("/tasks/")
        assert response.status_code == 200

    def test_list_tasks_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /tasks/ returns 200 for bureau users."""
        response = bureau_client.get("/tasks/")
        assert response.status_code == 200


class TestTreasuryRoutes:
    """Smoke tests for the treasury blueprint."""

    def test_list_transactions_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /treasury/ redirects unauthenticated users."""
        response = client.get("/treasury/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_transactions_forbidden_for_member(self, auth_client: FlaskClient) -> None:
        """GET /treasury/ returns 403 for a non-bureau member."""
        response = auth_client.get("/treasury/")
        assert response.status_code == 403

    def test_list_transactions_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /treasury/ returns 200 for bureau users."""
        response = bureau_client.get("/treasury/")
        assert response.status_code == 200


class TestMeetingsRoutes:
    """Smoke tests for the meetings blueprint."""

    def test_list_meetings_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /meetings/ redirects unauthenticated users."""
        response = client.get("/meetings/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_meetings_renders_for_member(self, auth_client: FlaskClient) -> None:
        """GET /meetings/ returns 200 for an authenticated member."""
        response = auth_client.get("/meetings/")
        assert response.status_code == 200

    def test_list_meetings_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /meetings/ returns 200 for bureau users."""
        response = bureau_client.get("/meetings/")
        assert response.status_code == 200


class TestErrorPages:
    """Smoke tests for HTTP error handlers."""

    def test_404_renders(self, client: FlaskClient) -> None:
        """A request to an unknown route returns 404 with a rendered page."""
        response = client.get("/cette-page-nexiste-pas")
        assert response.status_code == 404
        assert b"404" in response.data

    def test_403_renders_for_member_on_bureau_route(self) -> None:
        """403 is tested via unit tests — placeholder for future bureau routes."""
        # Full 403 test added in Phase 2 when bureau-only routes are introduced.
        pass
