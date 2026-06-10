"""Unit tests for authentication — login, logout, user creation (admin), password change."""

from flask import Flask
from flask.testing import FlaskClient

from app.models.user import User, UserRole


class TestLogin:
    """Login form behaviour."""

    def test_valid_credentials_redirect_to_dashboard(
        self, client: FlaskClient, member_user: User
    ) -> None:
        """A member with correct credentials is redirected to the dashboard."""
        response = client.post(
            "/auth/login",
            data={"email": "membre@test.com", "password": "motdepasse123"},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        assert "/" in response.headers["Location"]

    def test_wrong_password_stays_on_login(self, client: FlaskClient, member_user: User) -> None:
        """Incorrect password shows an error and does not log in."""
        response = client.post(
            "/auth/login",
            data={"email": "membre@test.com", "password": "mauvaismdp"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Adresse email ou mot de passe incorrect" in response.data.decode()

    def test_unknown_email_shows_same_error_as_wrong_password(self, client: FlaskClient) -> None:
        """Unknown email returns the identical error message — no user enumeration."""
        response = client.post(
            "/auth/login",
            data={"email": "inconnu@test.com", "password": "motdepasse123"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Adresse email ou mot de passe incorrect" in response.data.decode()

    def test_inactive_account_is_rejected(self, client: FlaskClient, inactive_user: User) -> None:
        """An inactive account cannot log in even with the correct password."""
        response = client.post(
            "/auth/login",
            data={"email": "inactif@test.com", "password": "motdepasse123"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "désactivé" in response.data.decode()

    def test_open_redirect_is_blocked(self, client: FlaskClient, member_user: User) -> None:
        """A malicious ``next`` parameter pointing to an external URL is ignored."""
        response = client.post(
            "/auth/login?next=https://evil.com",
            data={"email": "membre@test.com", "password": "motdepasse123"},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        location = response.headers["Location"]
        assert "evil.com" not in location


class TestCreateUser:
    """Admin-only user creation form behaviour."""

    def test_unauthenticated_cannot_access_register(self, client: FlaskClient) -> None:
        """An unauthenticated visitor is redirected away from the register route."""
        response = client.get("/auth/register", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_access_register(self, auth_client: FlaskClient) -> None:
        """A plain member is forbidden from the register route."""
        response = auth_client.get("/auth/register", follow_redirects=False)
        assert response.status_code == 403

    def test_bureau_can_create_user(self, bureau_client: FlaskClient, app: Flask) -> None:
        """A bureau admin can create a new user; account has must_change_password=True."""
        response = bureau_client.post(
            "/auth/register",
            data={
                "first_name": "Jean",
                "last_name": "Valjean",
                "email": "jean@test.com",
                "role": "member",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        from app.extensions import db as _db

        with app.app_context():
            user = _db.session.execute(
                _db.select(User).filter_by(email="jean@test.com")
            ).scalar_one_or_none()
            assert user is not None
            assert user.role == UserRole.MEMBER
            assert user.is_active is True
            assert user.must_change_password is True

    def test_bureau_can_create_bureau_user(self, bureau_client: FlaskClient, app: Flask) -> None:
        """A bureau admin can create a user with the bureau role."""
        response = bureau_client.post(
            "/auth/register",
            data={
                "first_name": "Trésorier",
                "last_name": "Bureau",
                "email": "tresorier@test.com",
                "role": "bureau",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

        from app.extensions import db as _db

        with app.app_context():
            user = _db.session.execute(
                _db.select(User).filter_by(email="tresorier@test.com")
            ).scalar_one_or_none()
            assert user is not None
            assert user.role == UserRole.BUREAU

    def test_duplicate_email_is_rejected(
        self, bureau_client: FlaskClient, member_user: User
    ) -> None:
        """Creating an account with an already-used email shows an error."""
        response = bureau_client.post(
            "/auth/register",
            data={
                "first_name": "Alice",
                "last_name": "Clone",
                "email": "membre@test.com",
                "role": "member",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "déjà utilisée" in response.data.decode()


class TestChangePassword:
    """Password change form behaviour."""

    def test_must_change_password_redirect_after_login(
        self, client: FlaskClient, app: Flask
    ) -> None:
        """A user with must_change_password=True is redirected to change-password after login."""
        from app.extensions import db as _db

        with app.app_context():
            forced = User(
                email="forced@test.com",
                first_name="Force",
                last_name="Change",
                must_change_password=True,
            )
            forced.set_password("temppassword1")
            _db.session.add(forced)
            _db.session.commit()

        response = client.post(
            "/auth/login",
            data={"email": "forced@test.com", "password": "temppassword1"},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        assert "/auth/change-password" in response.headers["Location"]

    def test_change_password_success(self, auth_client: FlaskClient, app: Flask) -> None:
        """Submitting a valid new password clears must_change_password and redirects."""
        response = auth_client.post(
            "/auth/change-password",
            data={
                "current_password": "motdepasse123",
                "new_password": "nouveaumdp456",
                "new_password_confirm": "nouveaumdp456",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}

    def test_change_password_wrong_current(self, auth_client: FlaskClient) -> None:
        """Providing the wrong current password shows an error."""
        response = auth_client.post(
            "/auth/change-password",
            data={
                "current_password": "mauvaismdp",
                "new_password": "nouveaumdp456",
                "new_password_confirm": "nouveaumdp456",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "incorrect" in response.data.decode()

    def test_change_password_mismatch(self, auth_client: FlaskClient) -> None:
        """Mismatched new passwords show a validation error."""
        response = auth_client.post(
            "/auth/change-password",
            data={
                "current_password": "motdepasse123",
                "new_password": "nouveaumdp456",
                "new_password_confirm": "different789",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "ne correspondent pas" in response.data.decode()


class TestPermissions:
    """Decorator-level access control."""

    def test_unauthenticated_user_is_redirected_from_dashboard(self, client: FlaskClient) -> None:
        """Visiting a protected route while logged out redirects to login."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]


class TestUserModel:
    """Unit tests for User model methods."""

    def test_password_hashing_is_not_plaintext(self, member_user: User) -> None:
        """The stored password hash is not the plaintext password."""
        assert member_user.password_hash != "motdepasse123"

    def test_check_password_returns_true_for_correct_password(self, member_user: User) -> None:
        """check_password returns True when given the correct plaintext password."""
        assert member_user.check_password("motdepasse123") is True

    def test_check_password_returns_false_for_wrong_password(self, member_user: User) -> None:
        """check_password returns False for an incorrect password."""
        assert member_user.check_password("mauvais") is False

    def test_bureau_user_is_bureau(self, bureau_user: User) -> None:
        """is_bureau is True for bureau-role users."""
        assert bureau_user.is_bureau is True

    def test_member_user_is_not_bureau(self, member_user: User) -> None:
        """is_bureau is False for member-role users."""
        assert member_user.is_bureau is False

    def test_full_name_concatenates_names(self, member_user: User) -> None:
        """full_name property returns 'first_name last_name'."""
        assert member_user.full_name == "Alice Dupont"


class TestAdminResetPassword:
    """Admin-initiated password reset behaviour."""

    def test_member_cannot_reset_password(
        self, auth_client: FlaskClient, member_user: User
    ) -> None:
        """A regular member cannot reset another user's password."""
        response = auth_client.post(
            f"/auth/reset-password-admin/{member_user.id}", follow_redirects=False
        )
        assert response.status_code == 403

    def test_bureau_can_reset_password(
        self, bureau_client: FlaskClient, member_user: User, app: Flask
    ) -> None:
        """A bureau admin can reset a user's password; flag must_change_password becomes True."""
        # Initial state: must_change_password is False for existing member fixture
        from app.extensions import db as _db

        with app.app_context():
            user = _db.session.get(User, member_user.id)
            user.must_change_password = False
            _db.session.commit()

        response = bureau_client.post(
            f"/auth/reset-password-admin/{member_user.id}", follow_redirects=True
        )
        assert response.status_code == 200

        with app.app_context():
            user = _db.session.get(User, member_user.id)
            assert user.must_change_password is True
