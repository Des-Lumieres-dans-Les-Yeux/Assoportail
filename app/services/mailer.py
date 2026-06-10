"""Transactional email delivery.

Strategy
--------
1. If a Gmail OAuth2 token is stored in the database, send via the Gmail API
   (uses the association's own Gmail address as sender — no SMTP credentials
   needed and no risk of landing in spam).
2. If Gmail is unavailable or not configured, fall back to SMTP using the
   ``SMTP_*`` environment variables.

Errors are always logged but never raised to the caller — a failed welcome
email must not prevent the account from being created.
"""

from __future__ import annotations

import base64
import logging
import smtplib
from datetime import date, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

logger = logging.getLogger(__name__)


def _clean_header(value: str) -> str:
    """Strip CR/LF characters to prevent email header injection."""
    return value.replace("\r", "").replace("\n", "")


def mask_email(email: str) -> str:
    """Return a partially masked email address suitable for log output.

    Keeps only the first two characters of the local part and the full domain
    so that logs remain useful for debugging without exposing PII.
    Example: "jean.dupont@example.com" → "je***@example.com"
    """
    try:
        local, domain = email.split("@", 1)
        return local[:2] + "***@" + domain
    except ValueError:
        return "***"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def send_membership_expiry_email(
    to_email: str,
    full_name: str,
    expires_at: date,
    days_remaining: int,
    portal_url: str,
) -> None:
    """Remind a member that their membership is about to expire."""
    subject = f"Votre adhésion expire dans {days_remaining} jour{'s' if days_remaining > 1 else ''}"
    body = (
        f"Bonjour {full_name},\n\n"
        f"Votre adhésion à l'association arrive à expiration "
        f"le {expires_at.strftime('%d/%m/%Y')} "
        f"(dans {days_remaining} jour{'s' if days_remaining > 1 else ''}).\n\n"
        f"Pour renouveler votre adhésion, rendez-vous sur le portail :\n{portal_url}\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


def send_event_reminder_email(
    to_email: str,
    full_name: str,
    event_title: str,
    event_date: datetime,
    event_location: str | None,
    portal_url: str,
) -> None:
    """Remind an attendee of an upcoming event (J-3)."""
    subject = f"Rappel — {event_title} dans 3 jours"
    location_line = f"  Lieu       : {event_location}\n" if event_location else ""
    body = (
        f"Bonjour {full_name},\n\n"
        f"Rappel : l'événement « {event_title} » auquel vous êtes inscrit(e) "
        f"a lieu dans 3 jours.\n\n"
        f"  Date       : {event_date.strftime('%d/%m/%Y à %H:%M')}\n"
        f"{location_line}"
        f"\nConsultez les détails : {portal_url}\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


def send_task_assigned_email(
    to_email: str,
    full_name: str,
    task_title: str,
    task_description: str | None,
    assigner_name: str,
    portal_url: str,
) -> None:
    """Notify a member that a task has been assigned to them."""
    subject = f"Tâche assignée — {task_title}"
    desc_line = f"\nDescription : {task_description}\n" if task_description else ""
    body = (
        f"Bonjour {full_name},\n\n"
        f"{assigner_name} vous a assigné une tâche :\n\n"
        f"  Tâche       : {task_title}\n"
        f"{desc_line}"
        f"\nConsultez la tâche : {portal_url}\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


def send_breakdown_alert_email(
    to_email: str,
    full_name: str,
    center_name: str,
    description: str,
    reporter_name: str,
    portal_url: str,
) -> None:
    """Alert a bureau member of a new breakdown report."""
    subject = f"Panne signalée — {center_name}"
    body = (
        f"Bonjour {full_name},\n\n"
        f"Une panne a été signalée au centre « {center_name} » par {reporter_name}.\n\n"
        f"Description :\n{description}\n\n"
        f"Consultez la tâche créée : {portal_url}\n\n"
        f"Cordialement,\n"
        f"Assoportail (notification automatique)"
    )
    _deliver(to_email, subject, body)


def send_cerfa_receipt_email(
    to_email: str,
    donor_name: str,
    amount: str,
    receipt_filename: str,
    docx_bytes: bytes,
) -> None:
    """Send a filled CERFA DOCX receipt to a donor as an email attachment."""
    subject = f"Votre reçu fiscal — {amount}"
    body = (
        f"Bonjour {donor_name},\n\n"
        f"Veuillez trouver ci-joint votre reçu fiscal pour votre don de {amount}.\n\n"
        f"Conservez ce document pour votre déclaration de revenus.\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver_with_attachment(to_email, subject, body, receipt_filename, docx_bytes)


def send_welcome_email(
    to_email: str,
    password: str,
    full_name: str,
    login_url: str,
) -> None:
    """Send an account-creation welcome email with login credentials.

    Tries the Gmail API first; silently falls back to SMTP on any error.

    Args:
        to_email:  Recipient email address.
        password:  Plaintext temporary password to include in the message.
        full_name: Recipient's full name used in the greeting.
        login_url: Absolute URL to the portal login page.
    """
    subject = "Votre compte Assoportail a été créé"
    body = (
        f"Bonjour {full_name},\n\n"
        f"Un compte a été créé pour vous sur le portail Assoportail.\n\n"
        f"  Adresse email : {to_email}\n"
        f"  Mot de passe  : {password}\n\n"
        f"Accédez au portail : {login_url}\n\n"
        f"Vous serez invité(e) à changer votre mot de passe lors de votre\n"
        f"première connexion.\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


def send_admin_reset_email(
    to_email: str,
    password: str,
    full_name: str,
    login_url: str,
) -> None:
    """Send an email after an admin reset a user's password.

    Args:
        to_email:  Recipient email address.
        password:  Plaintext temporary password.
        full_name: Recipient's full name.
        login_url: Absolute URL to the portal login page.
    """
    subject = "Réinitialisation de votre mot de passe par un administrateur"
    body = (
        f"Bonjour {full_name},\n\n"
        f"Un administrateur a réinitialisé votre mot de passe Assoportail.\n\n"
        f"  Adresse email : {to_email}\n"
        f"  Mot de passe temporaire : {password}\n\n"
        f"Accédez au portail : {login_url}\n\n"
        f"Vous devrez configurer un nouveau mot de passe lors de votre connexion.\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


def send_password_reset_email(
    to_email: str,
    full_name: str,
    reset_url: str,
) -> None:
    """Send a password reset link.

    Args:
        to_email:  Recipient email address.
        full_name: Recipient's full name.
        reset_url: Absolute URL with the signed reset token.
    """
    subject = "Réinitialisation de votre mot de passe — Assoportail"
    body = (
        f"Bonjour {full_name},\n\n"
        f"Vous avez demandé la réinitialisation de votre mot de passe.\n\n"
        f"Cliquez sur le lien suivant (valide 30 minutes) :\n"
        f"{reset_url}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n\n"
        f"Cordialement,\n"
        f"L'équipe Assoportail"
    )
    _deliver(to_email, subject, body)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deliver(to_email: str, subject: str, body: str) -> None:
    """Try Gmail API; fall back to SMTP on any exception."""
    try:
        _send_via_gmail(to_email, subject, body)
        logger.info("Email sent via Gmail API to %s", mask_email(to_email))
    except Exception as gmail_exc:
        logger.info("Gmail unavailable (%s) — falling back to SMTP", gmail_exc)
        _send_via_smtp(to_email, subject, body)


def _deliver_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    attachment_filename: str,
    attachment_bytes: bytes,
) -> None:
    """Try Gmail API with attachment; fall back to SMTP on any exception."""
    try:
        _send_via_gmail_with_attachment(
            to_email, subject, body, attachment_filename, attachment_bytes
        )
        logger.info("Email with attachment sent via Gmail API to %s", mask_email(to_email))
    except Exception as gmail_exc:
        logger.info("Gmail unavailable (%s) — falling back to SMTP", gmail_exc)
        _send_via_smtp_with_attachment(
            to_email, subject, body, attachment_filename, attachment_bytes
        )


def _send_via_gmail(to_email: str, subject: str, body: str) -> None:
    """Send using the Gmail API (raises if no token or API error)."""
    from app.services.gmail import GmailClient

    msg = MIMEText(body, "plain", "utf-8")
    msg["To"] = _clean_header(to_email)
    msg["Subject"] = _clean_header(subject)
    # The From header is ignored by the Gmail API; it uses the authenticated account.

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    GmailClient.from_db().send_message(raw)


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    """Send using SMTP (SMTP_* config keys)."""
    cfg = current_app.config
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = _clean_header(subject)
    msg["From"] = _clean_header(cfg["SMTP_FROM"])
    msg["To"] = _clean_header(to_email)

    try:
        with smtplib.SMTP(
            cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"]
        ) as server:
            if cfg["SMTP_USE_TLS"]:
                server.starttls()
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
        logger.info("Email sent via SMTP to %s", mask_email(to_email))
    except Exception:
        logger.exception("Failed to send email via SMTP to %s", mask_email(to_email))


def _send_via_gmail_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    attachment_filename: str,
    attachment_bytes: bytes,
) -> None:
    """Send a multipart email with attachment via Gmail API."""
    from app.services.gmail import GmailClient

    safe_filename = _clean_header(attachment_filename).replace('"', "'")
    msg = MIMEMultipart()
    msg["To"] = _clean_header(to_email)
    msg["Subject"] = _clean_header(subject)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    part = MIMEApplication(attachment_bytes, Name=safe_filename)
    part["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    GmailClient.from_db().send_message(raw)


def _send_via_smtp_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    attachment_filename: str,
    attachment_bytes: bytes,
) -> None:
    """Send a multipart email with attachment via SMTP."""
    cfg = current_app.config
    safe_filename = _clean_header(attachment_filename).replace('"', "'")
    msg = MIMEMultipart()
    msg["Subject"] = _clean_header(subject)
    msg["From"] = _clean_header(cfg["SMTP_FROM"])
    msg["To"] = _clean_header(to_email)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    part = MIMEApplication(attachment_bytes, Name=safe_filename)
    part["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
    msg.attach(part)

    try:
        with smtplib.SMTP(
            cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"]
        ) as server:
            if cfg["SMTP_USE_TLS"]:
                server.starttls()
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
        logger.info("Email with attachment sent via SMTP to %s", mask_email(to_email))
    except Exception:
        logger.exception(
            "Failed to send email with attachment via SMTP to %s", mask_email(to_email)
        )
