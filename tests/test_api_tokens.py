"""Tests for the web-based API token management UI (members blueprint).

CSRF is disabled in the test config (WTF_CSRF_ENABLED=False), so POST requests
do not need a csrf_token field.
"""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.api_token import ApiToken
from app.models.user import User, UserRole
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(client: FlaskClient, user: UserInfo) -> None:
    """Log in *client* as *user* by injecting the Flask-Login session cookie."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True


def _create_token_for(app: Flask, user: UserInfo, name: str = "test token") -> tuple[str, int]:
    """Insert an ApiToken for *user* and return (plaintext, token_id)."""
    with app.app_context():
        plaintext, token = ApiToken.generate(name=name, user_id=user.id)
        _db.session.add(token)
        _db.session.commit()
        token_id = token.id
        _db.session.remove()
    return plaintext, token_id


def _extract_plaintext(html: str) -> str:
    """Extract the dldly_ token from the response HTML."""
    start = html.find("dldly_")
    assert start != -1, "dldly_ token not found in response"
    chars = []
    for ch in html[start:]:
        if ch in (" ", "\n", "<", '"', "'"):
            break
        chars.append(ch)
    return "".join(chars)


# ---------------------------------------------------------------------------
# 1. Access control
# ---------------------------------------------------------------------------


class TestAccessControl:
    def test_get_requires_login(self, client: FlaskClient) -> None:
        """Unauthenticated request is redirected to login."""
        resp = client.get("/members/api-tokens")
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "/login" in location or "auth" in location

    def test_get_bureau_returns_200(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Bureau user can access the token management page."""
        _login(client, bureau_user)
        resp = client.get("/members/api-tokens")
        assert resp.status_code == 200
        assert "Accès API".encode() in resp.data  # "Accès API"

    def test_get_member_returns_403(
        self, app: Flask, client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Regular member (non-bureau) is forbidden."""
        _login(client, member_user)
        resp = client.get("/members/api-tokens")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. Create token
# ---------------------------------------------------------------------------


class TestCreateToken:
    def test_post_creates_token_in_db(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """POSTing a valid form creates an ApiToken row for the current user."""
        _login(client, bureau_user)
        resp = client.post(
            "/members/api-tokens",
            data={"name": "Mon outil", "expires_days": ""},
            follow_redirects=False,
        )
        # Should render 200 (not redirect) because new_token is displayed
        assert resp.status_code == 200

        with app.app_context():
            tokens = _db.session.scalars(
                _db.select(ApiToken).where(ApiToken.user_id == bureau_user.id)
            ).all()
            assert len(tokens) == 1
            assert tokens[0].name == "Mon outil"
            _db.session.remove()

    def test_post_response_contains_plaintext(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """The response page shows the plaintext token starting with 'dldly_'."""
        _login(client, bureau_user)
        resp = client.post(
            "/members/api-tokens",
            data={"name": "Outil test", "expires_days": ""},
        )
        assert resp.status_code == 200
        assert b"dldly_" in resp.data

    def test_token_prefix_matches_plaintext(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """token_prefix stored in DB is the first 12 chars of the plaintext."""
        _login(client, bureau_user)
        resp = client.post(
            "/members/api-tokens",
            data={"name": "Prefix check", "expires_days": ""},
        )
        assert resp.status_code == 200

        plaintext = _extract_plaintext(resp.data.decode("utf-8"))

        with app.app_context():
            token = _db.session.scalars(
                _db.select(ApiToken).where(ApiToken.user_id == bureau_user.id)
            ).first()
            assert token is not None
            assert token.token_prefix == plaintext[:12]
            _db.session.remove()

    def test_token_hash_verifies_plaintext(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """SHA-256 hash stored in DB matches the displayed plaintext."""
        _login(client, bureau_user)
        resp = client.post(
            "/members/api-tokens",
            data={"name": "Hash check", "expires_days": ""},
        )
        plaintext = _extract_plaintext(resp.data.decode("utf-8"))

        with app.app_context():
            token = _db.session.scalars(
                _db.select(ApiToken).where(ApiToken.user_id == bureau_user.id)
            ).first()
            assert token is not None
            assert token.token_hash == ApiToken.hash_token(plaintext)
            _db.session.remove()

    def test_post_missing_name_redirects(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """Invalid form (missing name) flashes danger and redirects back."""
        _login(client, bureau_user)
        resp = client.post(
            "/members/api-tokens",
            data={"name": "", "expires_days": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # No token should have been created
        with app.app_context():
            count = _db.session.scalar(
                _db.select(_db.func.count(ApiToken.id)).where(ApiToken.user_id == bureau_user.id)
            )
            assert count == 0
            _db.session.remove()


# ---------------------------------------------------------------------------
# 3. Revoke token
# ---------------------------------------------------------------------------


class TestRevokeToken:
    def test_revoke_sets_revoked_true(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """POSTing to revoke endpoint sets revoked=True in DB."""
        _, token_id = _create_token_for(app, bureau_user)
        _login(client, bureau_user)
        resp = client.post(
            f"/members/api-tokens/{token_id}/revoke",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        with app.app_context():
            token = _db.session.get(ApiToken, token_id)
            assert token is not None
            assert token.revoked is True
            _db.session.remove()

    def test_revoke_other_user_token_is_403(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A bureau user cannot revoke another user's token (403)."""
        # Create a second bureau user
        with app.app_context():
            other = User(
                email="other_bureau@test.com",
                first_name="Other",
                last_name="Bureau",
                role=UserRole.BUREAU,
                is_active=True,
                must_change_password=False,
            )
            other.set_password("motdepasse123")
            _db.session.add(other)
            _db.session.commit()
            other_id = other.id
            _db.session.remove()

        other_info = UserInfo(
            id=other_id,
            email="other_bureau@test.com",
            first_name="Other",
            last_name="Bureau",
            role=UserRole.BUREAU,
            is_active=True,
            must_change_password=False,
            password_hash="",
        )
        _, token_id = _create_token_for(app, other_info)

        _login(client, bureau_user)
        resp = client.post(
            f"/members/api-tokens/{token_id}/revoke",
            data={},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Security — plaintext not shown on subsequent GET
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_get_does_not_contain_plaintext(
        self, app: Flask, client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """A regular GET to the list page does NOT reveal the token plaintext."""
        plaintext, _ = _create_token_for(app, bureau_user)
        _login(client, bureau_user)
        resp = client.get("/members/api-tokens")
        assert resp.status_code == 200
        # The full plaintext must not appear — only the 12-char prefix
        assert plaintext.encode() not in resp.data
