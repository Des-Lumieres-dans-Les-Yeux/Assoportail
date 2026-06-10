"""Celery tasks — CERFA generation, Drive archival, and email delivery."""

from __future__ import annotations

import io
import logging
import os
import tempfile
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
    from mailmerge import MailMerge

    from app.extensions import db
    from app.models.config import AssociationConfig
    from app.models.treasury import Transaction

    transaction = db.session.get(Transaction, transaction_id)
    if transaction is None:
        return {"status": "error", "error": "Transaction introuvable."}

    cfg = AssociationConfig.get()
    if not cfg.name:
        return {"status": "error", "error": "Configuration association manquante."}

    # Resolve template column
    col_map = {
        "particulier": "cerfa_tpl_particulier",
        "mecena": "cerfa_tpl_entreprise",
        "nature": "cerfa_tpl_nature",
    }
    donation_type = transaction.donation_type or "particulier"
    col = col_map.get(donation_type, "cerfa_tpl_particulier")
    tpl_data = getattr(cfg, col)
    if tpl_data is None:
        return {"status": "error", "error": f"Modèle CERFA « {donation_type} » non téléversé."}

    # Write template to temp file for MailMerge
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(tpl_data)
        template_path = tmp.name

    receipt_id = f"DON-{transaction.date.year}-{transaction.id:05d}"
    merge_data = {
        "Prénom": transaction.donor_first_name or "",
        "Nom": transaction.donor_name or "",
        "Adresse_de_livraison": transaction.donor_address or "",
        "Ville_de_livraison": transaction.donor_city or "",
        "Code_postal_de_livraison": transaction.donor_zip or "",
        "IDRECU": receipt_id,
        "Montant_perçu": f"{transaction.amount:.2f} €",
        "Date": transaction.date.strftime("%d/%m/%Y"),
    }
    if donation_type == "nature":
        merge_data["Courriel"] = transaction.donor_email or ""
        merge_data["Don"] = transaction.donor_description or ""

    try:
        with MailMerge(template_path) as document:
            document.merge(**merge_data)
            buf = io.BytesIO()
            document.write(buf)
    finally:
        os.unlink(template_path)

    # Convert DOCX → PDF via LibreOffice headless
    docx_bytes = buf.getvalue()
    pdf_bytes = _convert_docx_to_pdf(docx_bytes)
    filename = f"{receipt_id}.pdf"

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


def _convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert DOCX bytes to PDF using LibreOffice headless."""
    import subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "input.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        subprocess.run(  # noqa: S603
            [  # noqa: S607
                "soffice",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf",
                "--outdir",
                tmpdir,
                docx_path,
            ],
            check=True,
            timeout=60,
            capture_output=True,
        )

        pdf_path = os.path.join(tmpdir, "input.pdf")
        with open(pdf_path, "rb") as f:
            return f.read()
