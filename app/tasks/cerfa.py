"""Celery tasks — CERFA Drive archival (deferred from the request path)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="tasks.archive_cerfa_to_drive",
    bind=True,
    max_retries=2,
    time_limit=120,
    soft_time_limit=110,
)
def archive_cerfa_to_drive_task(self, transaction_id: int) -> dict:
    """Upload a CERFA receipt to Drive in the background.

    The receipt number/PDF are already issued synchronously in the request;
    only the slow Drive upload is deferred here. Best-effort and idempotent:
    a no-op if already archived (``archive_cerfa_to_drive`` guards on that).
    """
    from app.extensions import db
    from app.models.config import AssociationConfig
    from app.models.treasury import Transaction
    from app.services.cerfa import archive_cerfa_to_drive, generate_cerfa_pdf

    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        return {"status": "error", "error": "Transaction introuvable."}
    if transaction.cerfa_drive_file_id:
        return {"status": "ok", "skipped": "already archived"}

    pdf_bytes, filename = generate_cerfa_pdf(transaction, AssociationConfig.get())
    archive_cerfa_to_drive(transaction, pdf_bytes, filename)
    db.session.commit()
    return {"status": "ok", "filename": filename}
