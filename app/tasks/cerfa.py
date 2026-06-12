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
def generate_and_send_cerfa(self, transaction_id: int) -> dict:
    """Generate a CERFA document, archive it to Drive, and email it to the donor.

    Returns a dict with ``status``, ``filename``, and optional ``error``.
    """
    from app.extensions import db
    from app.models.config import AssociationConfig
    from app.models.treasury import Transaction
    from app.services.cerfa import generate_cerfa_pdf

    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        return {"status": "error", "error": "Transaction introuvable."}

    cfg = AssociationConfig.get()
    if not cfg.name:
        return {"status": "error", "error": "Configuration association manquante."}

    try:
        pdf_bytes, filename = generate_cerfa_pdf(transaction, cfg)
    except Exception as exc:
        logger.exception("CERFA generation failed for transaction %d", transaction_id)
        return {"status": "error", "error": str(exc)}

    # Archive to Drive
    if not transaction.cerfa_drive_file_id:
        try:
            from flask import current_app

            from app.services.drive import DriveService

            if current_app.config.get("GOOGLE_SHARED_DRIVE_ID"):
                drive_svc = DriveService.from_db()
                fid, wlink = drive_svc.upload_file(
                    pdf_bytes,
                    filename,
                    "application/pdf",
                    "cerfa",
                    year=transaction.date.year,
                )
                transaction.cerfa_drive_file_id = fid
                transaction.cerfa_drive_web_link = wlink
        except Exception as exc:
            logger.warning("Drive archival of CERFA failed: %s", exc)

    # Send email if donor has an email address
    if transaction.donor_email:
        try:
            from app.services.mailer import send_cerfa_receipt_email

            donor_display = (
                " ".join(filter(None, [transaction.donor_first_name, transaction.donor_name]))
                or "Donateur"
            )
            send_cerfa_receipt_email(
                to_email=transaction.donor_email,
                donor_name=donor_display,
                amount=f"{transaction.amount:.2f} €",
                receipt_filename=filename,
                docx_bytes=pdf_bytes,
            )
            transaction.cerfa_sent_at = datetime.now(UTC)
        except Exception as exc:
            logger.exception("Failed to send CERFA email for transaction %d", transaction_id)
            db.session.commit()
            return {"status": "partial", "filename": filename, "error": str(exc)}

    db.session.commit()
    return {"status": "ok", "filename": filename}
