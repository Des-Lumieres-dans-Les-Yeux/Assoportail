"""Unit tests for CERFA receipt-number assignment (sequential, never reused)."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from flask import Flask
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.treasury import Transaction, TransactionSource, TransactionType
from app.services.cerfa import assign_cerfa_number
from tests.conftest import UserInfo


def _make_transaction(bureau_user: UserInfo) -> Transaction:
    txn = Transaction(
        type=TransactionType.INCOME.value,
        amount=Decimal("100.00"),
        date=date(2026, 3, 1),
        description="don de test",
        created_by_id=bureau_user.id,
        source=TransactionSource.DONATION.value,
        donation_type="particulier",
    )
    db.session.add(txn)
    db.session.flush()
    return txn


def test_assign_cerfa_number_sequential_and_idempotent(app: Flask, bureau_user: UserInfo):
    with app.app_context():
        first = _make_transaction(bureau_user)
        number = assign_cerfa_number(first)
        assert number == "DON-2026-00001"
        assert first.cerfa_number == number

        second = _make_transaction(bureau_user)
        assert assign_cerfa_number(second) == "DON-2026-00002"

        # Idempotent: assigning again returns the existing number.
        assert assign_cerfa_number(first) == number
        db.session.commit()


def test_assign_cerfa_number_retries_on_collision(app: Flask, bureau_user: UserInfo):
    """A concurrent inserter grabs the candidate number between the scan and
    the flush (unique-constraint collision) → SAVEPOINT rollback → retry with
    the next number, never reusing the colliding one."""
    with app.app_context():
        txn = _make_transaction(bureau_user)
        original_execute = Session.execute
        collided = {"done": False}

        def racy_execute(statement, *args, **kwargs):
            if not collided["done"]:
                # A second connection commits the candidate number, simulating
                # a concurrent inserter between our scan and our claim.
                with db.engine.connect() as ghost:
                    ghost.execute(
                        insert(Transaction).values(
                            type=TransactionType.INCOME.value,
                            amount=Decimal("10.00"),
                            date=date(2026, 3, 1),
                            description="don concurrent",
                            created_by_id=bureau_user.id,
                            source=TransactionSource.DONATION.value,
                            cerfa_number="DON-2026-00001",
                        )
                    )
                    ghost.commit()
                collided["done"] = True
            return original_execute(db.session(), statement, *args, **kwargs)

        with patch.object(Session, "execute", side_effect=racy_execute):
            number = assign_cerfa_number(txn)

        assert number == "DON-2026-00002"
        assert txn.cerfa_number == "DON-2026-00002"
        # The collision forced a retry, not the colliding number.
        assert collided["done"]
        db.session.commit()
