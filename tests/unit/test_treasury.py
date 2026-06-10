"""Unit tests for treasury management — transactions, tags, balance."""

from datetime import date
from decimal import Decimal

from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy.orm import selectinload

from app.extensions import db as _db
from app.models.treasury import Tag, Transaction, TransactionSource, TransactionType
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transaction(
    app: Flask,
    creator_id: int,
    *,
    tx_type: str = "income",
    amount: str = "100.00",
    description: str = "Recette test",
    tx_date: str = "2026-03-01",
) -> int:
    with app.app_context():
        t = Transaction(
            type=tx_type,
            amount=Decimal(amount),
            date=date.fromisoformat(tx_date),
            description=description,
            source=TransactionSource.MANUAL.value,
            created_by_id=creator_id,
        )
        _db.session.add(t)
        _db.session.commit()
        return t.id


def _make_tag(app: Flask, label: str = "subvention", color: str = "#3498db") -> int:
    with app.app_context():
        tag = Tag(label=label, color=color)
        _db.session.add(tag)
        _db.session.commit()
        return tag.id


# ---------------------------------------------------------------------------
# Access control — bureau only
# ---------------------------------------------------------------------------


class TestTreasuryAccess:
    def test_member_cannot_view_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/treasury/", follow_redirects=False)
        assert response.status_code == 403

    def test_bureau_can_view_list(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.get("/treasury/")
        assert response.status_code == 200

    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        response = client.get("/treasury/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_create_transaction(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/treasury/new")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create transaction
# ---------------------------------------------------------------------------


class TestTransactionCreate:
    def test_bureau_can_create_income(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        response = bureau_client.post(
            "/treasury/new",
            data={
                "type": "income",
                "amount": "500.00",
                "date": "2026-04-15",
                "description": "Subvention mairie",
                "category": "subvention",
                "source": "manual",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            t = _db.session.execute(
                _db.select(Transaction).filter_by(description="Subvention mairie")
            ).scalar_one_or_none()
            assert t is not None
            assert t.amount == Decimal("500.00")
            assert t.type == TransactionType.INCOME.value
            assert t.created_by_id == bureau_user.id

    def test_bureau_can_create_expense(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        bureau_client.post(
            "/treasury/new",
            data={
                "type": "expense",
                "amount": "45.00",
                "date": "2026-04-01",
                "description": "Pièces détachées",
                "category": "",
                "source": "manual",
            },
        )
        with app.app_context():
            t = _db.session.execute(
                _db.select(Transaction).filter_by(description="Pièces détachées")
            ).scalar_one_or_none()
            assert t is not None
            assert t.type == TransactionType.EXPENSE.value

    def test_create_requires_description(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/treasury/new",
            data={
                "type": "income",
                "amount": "100.00",
                "date": "2026-04-01",
                "description": "",
                "source": "manual",
            },
        )
        assert response.status_code == 200
        assert "obligatoire" in response.data.decode()


# ---------------------------------------------------------------------------
# Edit transaction
# ---------------------------------------------------------------------------


class TestTransactionEdit:
    def test_bureau_can_edit_transaction(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tid = _make_transaction(app, bureau_user.id)
        bureau_client.post(
            f"/treasury/{tid}/edit",
            data={
                "type": "expense",
                "amount": "75.50",
                "date": "2026-03-15",
                "description": "Description modifiée",
                "category": "maintenance",
                "source": "manual",
            },
        )
        with app.app_context():
            t = _db.session.get(Transaction, tid)
            assert t.type == TransactionType.EXPENSE.value
            assert t.amount == Decimal("75.50")
            assert t.description == "Description modifiée"


# ---------------------------------------------------------------------------
# Balance calculation
# ---------------------------------------------------------------------------


class TestTreasuryBalance:
    def test_balance_reflects_income_minus_expense(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        _make_transaction(
            app, bureau_user.id, tx_type="income", amount="300.00", description="Don A"
        )
        _make_transaction(
            app, bureau_user.id, tx_type="expense", amount="120.00", description="Achat B"
        )

        response = bureau_client.get("/treasury/")
        body = response.data.decode()
        assert "300.00" in body
        assert "120.00" in body
        assert "180.00" in body  # balance = 300 - 120

    def test_signed_amount_property(self, app: Flask, bureau_user: UserInfo) -> None:
        with app.app_context():
            income = Transaction(
                type=TransactionType.INCOME.value,
                amount=Decimal("200.00"),
                date=date(2026, 3, 1),
                description="Test income",
                source=TransactionSource.MANUAL.value,
                created_by_id=bureau_user.id,
            )
            expense = Transaction(
                type=TransactionType.EXPENSE.value,
                amount=Decimal("50.00"),
                date=date(2026, 3, 1),
                description="Test expense",
                source=TransactionSource.MANUAL.value,
                created_by_id=bureau_user.id,
            )
            _db.session.add_all([income, expense])
            _db.session.commit()
            assert income.signed_amount == Decimal("200.00")
            assert expense.signed_amount == Decimal("-50.00")


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class TestTags:
    def test_bureau_can_create_tag(self, app: Flask, bureau_client: FlaskClient) -> None:
        bureau_client.post(
            "/treasury/tags",
            data={"label": "subvention", "color": "#3498db"},
            follow_redirects=False,
        )
        with app.app_context():
            tag = _db.session.execute(
                _db.select(Tag).filter_by(label="subvention")
            ).scalar_one_or_none()
            assert tag is not None
            assert tag.color == "#3498db"

    def test_duplicate_tag_label_rejected(self, app: Flask, bureau_client: FlaskClient) -> None:
        _make_tag(app, label="don")
        bureau_client.post(
            "/treasury/tags",
            data={"label": "don", "color": "#aaaaaa"},
        )
        with app.app_context():
            count = _db.session.execute(
                _db.select(_db.func.count(Tag.id)).where(Tag.label == "don")
            ).scalar()
            assert count == 1  # not duplicated

    def test_bureau_can_delete_tag(self, app: Flask, bureau_client: FlaskClient) -> None:
        tag_id = _make_tag(app)
        bureau_client.post(f"/treasury/tags/{tag_id}/delete")
        with app.app_context():
            assert _db.session.get(Tag, tag_id) is None

    def test_transaction_can_have_tags(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tag_id = _make_tag(app, label="mécénat")
        bureau_client.post(
            "/treasury/new",
            data={
                "type": "income",
                "amount": "1000.00",
                "date": "2026-04-01",
                "description": "Don mécénat",
                "category": "",
                "source": "donation",
                "tag_ids": str(tag_id),
            },
        )
        with app.app_context():
            t = _db.session.execute(
                _db.select(Transaction)
                .filter_by(description="Don mécénat")
                .options(selectinload(Transaction.tags))
            ).scalar_one()
            assert any(tag.id == tag_id for tag in t.tags)

    def test_invalid_color_rejected(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/treasury/tags",
            data={"label": "test", "color": "notacolor"},
        )
        assert response.status_code == 200
        assert "hexadécimal" in response.data.decode()
