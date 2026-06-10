"""Unit tests for member management — CRUD, membership lifecycle, access control."""

from datetime import date, timedelta
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.member import Membership, MembershipSource, MembershipStatus
from app.models.user import User, UserRole
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_membership(
    app: Flask,
    user_id: int,
    *,
    source: MembershipSource = MembershipSource.CASH,
    days_offset: int = 0,
    expired: bool = False,
    pending: bool = False,
    amount: Decimal = Decimal("15.00"),
) -> int:
    """Insert a Membership row and return its id."""
    today = date.today()
    if expired:
        started = today - timedelta(days=400)
        expires = today - timedelta(days=35)
    else:
        started = today - timedelta(days=days_offset)
        expires = started + timedelta(days=365)

    with app.app_context():
        m = Membership(
            user_id=user_id,
            source=source,
            amount=amount,
            started_at=started,
            expires_at=expires,
            is_pending=pending,
        )
        _db.session.add(m)
        _db.session.commit()
        return m.id


# ---------------------------------------------------------------------------
# Membership status hybrid_property
# ---------------------------------------------------------------------------


class TestMembershipStatus:
    """Verify the status hybrid_property for all three states."""

    def test_active_membership_has_status_active(self, app: Flask, member_user: UserInfo) -> None:
        """A membership expiring in the future is ACTIVE."""
        mid = _add_membership(app, member_user.id)
        with app.app_context():
            m = _db.session.get(Membership, mid)
            assert m.status == MembershipStatus.ACTIVE

    def test_expired_membership_has_status_expired(self, app: Flask, member_user: UserInfo) -> None:
        """A membership whose expires_at is in the past is EXPIRED."""
        mid = _add_membership(app, member_user.id, expired=True)
        with app.app_context():
            m = _db.session.get(Membership, mid)
            assert m.status == MembershipStatus.EXPIRED

    def test_pending_membership_has_status_pending(self, app: Flask, member_user: UserInfo) -> None:
        """A membership with is_pending=True is PENDING regardless of dates."""
        mid = _add_membership(app, member_user.id, pending=True)
        with app.app_context():
            m = _db.session.get(Membership, mid)
            assert m.status == MembershipStatus.PENDING

    def test_status_expression_filters_active(self, app: Flask, member_user: UserInfo) -> None:
        """The SQL expression for status allows filtering on server side."""
        _add_membership(app, member_user.id)
        _add_membership(app, member_user.id, expired=True)
        with app.app_context():
            actives = _db.session.scalars(
                _db.select(Membership).where(Membership.status == MembershipStatus.ACTIVE.value)
            ).all()
            assert len(actives) == 1
            assert actives[0].status == MembershipStatus.ACTIVE


# ---------------------------------------------------------------------------
# Member list — bureau access
# ---------------------------------------------------------------------------


class TestMemberList:
    """Access control and display for the member list."""

    def test_bureau_can_access_member_list(self, bureau_client: FlaskClient) -> None:
        """Bureau users can view the member list."""
        response = bureau_client.get("/members/")
        assert response.status_code == 200

    def test_member_cannot_access_member_list(self, auth_client: FlaskClient) -> None:
        """Regular members are forbidden from the member list."""
        response = auth_client.get("/members/", follow_redirects=False)
        assert response.status_code == 403

    def test_unauthenticated_user_redirected_from_member_list(self, client: FlaskClient) -> None:
        """Unauthenticated requests to the member list redirect to login."""
        response = client.get("/members/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_list_shows_member_name(
        self, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """The member list renders the enrolled member's name."""
        response = bureau_client.get("/members/")
        assert response.status_code == 200
        assert "Alice Dupont" in response.data.decode()

    def test_member_list_search_filters_by_name(
        self, bureau_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        """The ?q= filter returns only matching members."""
        response = bureau_client.get("/members/?q=Alice")
        assert response.status_code == 200
        body = response.data.decode()
        assert "membre@test.com" in body
        assert "bureau@test.com" not in body


# ---------------------------------------------------------------------------
# Member detail
# ---------------------------------------------------------------------------


class TestMemberDetail:
    """Member detail page content and access."""

    def test_bureau_can_view_member_detail(
        self, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Bureau users can view another member's detail page."""
        response = bureau_client.get(f"/members/{member_user.id}")
        assert response.status_code == 200
        assert "Alice Dupont" in response.data.decode()

    def test_detail_shows_active_membership(
        self, app: Flask, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """An active membership is shown with the 'Actif' badge."""
        _add_membership(app, member_user.id)
        response = bureau_client.get(f"/members/{member_user.id}")
        assert "Actif" in response.data.decode()

    def test_detail_404_for_nonexistent_member(self, bureau_client: FlaskClient) -> None:
        """Requesting a non-existent member id returns 404."""
        response = bureau_client.get("/members/99999")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Create member
# ---------------------------------------------------------------------------


class TestMemberCreate:
    """Creating a new member via the bureau form."""

    def test_bureau_can_create_new_member(self, app: Flask, bureau_client: FlaskClient) -> None:
        """Bureau users can create a new member account."""
        response = bureau_client.post(
            "/members/new",
            data={
                "first_name": "Claire",
                "last_name": "Fontaine",
                "email": "claire@test.com",
                "password": "motdepasse123",
                "role": "member",
                "gender": "not_specified",
                "phone": "",
                "address": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            user = _db.session.execute(
                _db.select(User).filter_by(email="claire@test.com")
            ).scalar_one_or_none()
            assert user is not None
            assert user.role == UserRole.MEMBER
            assert user.is_active is True

    def test_duplicate_email_is_rejected_on_create(
        self, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Creating a member with an already-used email shows an error."""
        response = bureau_client.post(
            "/members/new",
            data={
                "first_name": "Clone",
                "last_name": "Dupont",
                "email": "membre@test.com",
                "password": "motdepasse123",
                "role": "member",
                "gender": "not_specified",
                "phone": "",
                "address": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "déjà utilisée" in response.data.decode()

    def test_member_cannot_create_member(self, auth_client: FlaskClient) -> None:
        """Regular members are forbidden from creating new members."""
        response = auth_client.get("/members/new", follow_redirects=False)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Edit member
# ---------------------------------------------------------------------------


class TestMemberEdit:
    """Editing an existing member's profile."""

    def test_bureau_can_edit_member(
        self, app: Flask, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Bureau users can update a member's profile fields."""
        response = bureau_client.post(
            f"/members/{member_user.id}/edit",
            data={
                "first_name": "Alicia",
                "last_name": "Dupont",
                "email": "membre@test.com",
                "role": "member",
                "gender": "female",
                "phone": "0601020304",
                "address": "",
                "is_active": "y",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            user = _db.session.get(User, member_user.id)
            assert user.first_name == "Alicia"
            assert user.phone == "0601020304"

    def test_bureau_can_deactivate_member(
        self, app: Flask, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Bureau users can deactivate a member by unchecking is_active."""
        bureau_client.post(
            f"/members/{member_user.id}/edit",
            data={
                "first_name": "Alice",
                "last_name": "Dupont",
                "email": "membre@test.com",
                "role": "member",
                "gender": "not_specified",
                "phone": "",
                "address": "",
                # is_active not sent → False
            },
            follow_redirects=True,
        )
        with app.app_context():
            user = _db.session.get(User, member_user.id)
            assert user.is_active is False


# ---------------------------------------------------------------------------
# Cash membership
# ---------------------------------------------------------------------------


class TestCashMembership:
    """Recording a cash membership payment."""

    def test_bureau_can_add_cash_membership(
        self, app: Flask, bureau_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Bureau users can record a cash membership for a member."""
        today = date.today()
        response = bureau_client.post(
            f"/members/{member_user.id}/membership/new",
            data={
                "amount": "15.00",
                "started_at": today.strftime("%Y-%m-%d"),
                "expires_at": (today + timedelta(days=365)).strftime("%Y-%m-%d"),
                "notes": "Paiement en espèces lors de l'AG",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        with app.app_context():
            memberships = _db.session.scalars(
                _db.select(Membership).where(Membership.user_id == member_user.id)
            ).all()
            assert len(memberships) == 1
            m = memberships[0]
            assert m.source == MembershipSource.CASH
            assert m.amount == Decimal("15.00")
            assert m.status == MembershipStatus.ACTIVE
            assert "AG" in (m.notes or "")

    def test_member_cannot_add_membership(
        self, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Regular members are forbidden from adding memberships."""
        response = auth_client.post(
            f"/members/{member_user.id}/membership/new",
            data={
                "amount": "15.00",
                "started_at": date.today().strftime("%Y-%m-%d"),
                "expires_at": (date.today() + timedelta(days=365)).strftime("%Y-%m-%d"),
                "notes": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class TestProfile:
    """Own profile view — accessible to all authenticated members."""

    def test_member_can_view_own_profile(
        self, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Authenticated members can access their own profile page."""
        response = auth_client.get("/members/profile")
        assert response.status_code == 200
        assert "Alice Dupont" in response.data.decode()

    def test_profile_shows_membership_history(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """The profile page lists the member's own memberships."""
        _add_membership(app, member_user.id)
        response = auth_client.get("/members/profile")
        assert response.status_code == 200
        assert "Actif" in response.data.decode()

    def test_unauthenticated_user_redirected_from_profile(self, client: FlaskClient) -> None:
        """Unauthenticated requests to /members/profile redirect to login."""
        response = client.get("/members/profile", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]
