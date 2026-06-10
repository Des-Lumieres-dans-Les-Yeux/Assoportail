"""Pytest fixtures shared across the test suite.

Database strategy
-----------------
All tests use PostgreSQL — no SQLite.

Design choices:
  - ``app`` is session-scoped; tables are created once, dropped at end.
  - **No persistent app context** is kept alive between tests.  Each fixture
    that needs the DB opens its own ``app.app_context()`` and closes it.
    This prevents Flask-Login's ``g._login_user`` cache from leaking across
    tests when using a single long-lived context.
  - User fixtures return ``UserInfo`` dataclasses, not SQLAlchemy instances.
    This avoids DetachedInstanceError entirely — there is no session to detach
    from.  The dataclass mirrors the User model's public interface so tests
    calling ``member_user.check_password()`` or ``member_user.is_bureau``
    continue to work without change.
  - ``clean_db`` truncates all tables after every test for clean isolation.
"""

import dataclasses

import bcrypt
import pytest
from dotenv import load_dotenv

load_dotenv()
from flask import Flask
from flask.testing import FlaskClient

from app import create_app
from app.extensions import db as _db
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Lightweight user DTO — avoids SQLAlchemy session coupling in fixtures
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class UserInfo:
    """Serialisable snapshot of a User row, safe to use outside any session."""

    id: int
    email: str
    first_name: str
    last_name: str
    role: UserRole
    is_active: bool
    must_change_password: bool
    password_hash: str

    def check_password(self, password: str) -> bool:
        """Verify a plaintext password against the stored bcrypt hash."""
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    @property
    def full_name(self) -> str:
        """User's full display name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_bureau(self) -> bool:
        """True if the user holds bureau-level access."""
        return self.role == UserRole.BUREAU


# ---------------------------------------------------------------------------
# Application and table lifecycle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app() -> Flask:
    """Create the test application and the DB schema (once per session)."""
    flask_app = create_app("test")
    with flask_app.app_context():
        _db.create_all()
    yield flask_app
    with flask_app.app_context():
        _db.drop_all()


@pytest.fixture(scope="function", autouse=True)
def clean_db(app: Flask) -> None:
    """Truncate all tables after every test using a short-lived context."""
    yield
    with app.app_context():
        try:
            # sorted_tables raises CircularDependencyError when mutually-referencing
            # FKs exist (e.g. tombolas ↔ tombola_tickets). Fall back to unsorted order.
            try:
                tables = list(reversed(_db.metadata.sorted_tables))
            except Exception:
                tables = list(_db.metadata.tables.values())
            for table in tables:
                _db.session.execute(table.delete())
            _db.session.commit()
        except Exception:
            _db.session.rollback()
        finally:
            _db.session.remove()


# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    """Provide a fresh Flask test client (new cookie jar per test)."""
    return app.test_client()


# ---------------------------------------------------------------------------
# User fixtures — insert in DB, return a plain UserInfo dataclass
# ---------------------------------------------------------------------------


def _create_user(
    app: Flask,
    email: str,
    first_name: str,
    last_name: str,
    role: UserRole,
    is_active: bool = True,
) -> UserInfo:
    """Insert a user in the DB and return a session-free UserInfo snapshot."""
    with app.app_context():
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
            is_active=is_active,
            must_change_password=False,
        )
        user.set_password("motdepasse123")
        _db.session.add(user)
        _db.session.commit()
        info = UserInfo(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=user.role,
            is_active=user.is_active,
            must_change_password=user.must_change_password,
            password_hash=user.password_hash,
        )
        _db.session.remove()
    return info


@pytest.fixture
def member_user(app: Flask) -> UserInfo:
    """A standard active member user."""
    return _create_user(app, "membre@test.com", "Alice", "Dupont", UserRole.MEMBER)


@pytest.fixture
def bureau_user(app: Flask) -> UserInfo:
    """A bureau-level user with elevated permissions."""
    return _create_user(app, "bureau@test.com", "Bob", "Martin", UserRole.BUREAU)


@pytest.fixture
def inactive_user(app: Flask) -> UserInfo:
    """An inactive (suspended) member user."""
    return _create_user(
        app, "inactif@test.com", "Carol", "Leblanc", UserRole.MEMBER, is_active=False
    )


# ---------------------------------------------------------------------------
# Authenticated client helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client(app: Flask, member_user: UserInfo) -> FlaskClient:
    """A test client pre-logged-in as an active member (independent cookie jar)."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(member_user.id)
        sess["_fresh"] = True
    return c


@pytest.fixture
def bureau_client(app: Flask, bureau_user: UserInfo) -> FlaskClient:
    """A test client pre-logged-in as a bureau member (independent cookie jar)."""
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(bureau_user.id)
        sess["_fresh"] = True
    return c
