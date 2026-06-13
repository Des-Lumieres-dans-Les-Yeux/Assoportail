"""Celery tasks — CERFA generation, Drive archival, and email delivery."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.generate_and_send_cerfa",
    bind=True,
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
)
def generate_and_send_cerfa(self, transaction_id: int, to_email: str | None = None) -> dict:
    """Issue a CERFA (number + generate + archive) and email it to ``to_email``.

    ``to_email`` defaults to the donor's stored email. Returns a dict with
    ``status``, ``filename``, and optional ``error``.
    """
    from app.extensions import db
    from app.models.config import AssociationConfig
    from app.models.treasury import Transaction
    from app.services.cerfa import issue_cerfa

    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        return {"status": "error", "error": "Transaction introuvable."}

    cfg = AssociationConfig.get()
    if not cfg.name:
        return {"status": "error", "error": "Configuration association manquante."}

    recipient = (to_email or transaction.donor_email or "").strip()
    if not recipient:
        return {"status": "error", "error": "Aucune adresse email de destination."}

    try:
        pdf_bytes, filename = issue_cerfa(transaction, cfg)
    except Exception as exc:
        logger.exception("CERFA generation failed for transaction %d", transaction_id)
        return {"status": "error", "error": str(exc)}

    try:
        from app.services.mailer import send_cerfa_receipt_email

        donor_display = (
            " ".join(filter(None, [transaction.donor_first_name, transaction.donor_name]))
            or "Donateur"
        )
        send_cerfa_receipt_email(
            to_email=recipient,
            donor_name=donor_display,
            amount=f"{transaction.amount:.2f} €",
            receipt_filename=filename,
            docx_bytes=pdf_bytes,
        )
        transaction.cerfa_sent_at = datetime.now(UTC)
        db.session.commit()
    except Exception as exc:
        logger.exception("Failed to send CERFA email for transaction %d", transaction_id)
        return {"status": "partial", "filename": filename, "error": str(exc)}

    return {"status": "ok", "filename": filename}
