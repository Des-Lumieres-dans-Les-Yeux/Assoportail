"""Gmail API client — OAuth2 token management and inbox polling.

Token storage
-------------
The access/refresh token JSON is encrypted with MultiFernet (supports key
rotation) and stored in the ``gmail_tokens`` table.  The ``ENCRYPTION_KEYS``
config entry holds a comma-separated list of base64url-encoded Fernet keys,
newest first.  Old tokens are transparently re-encrypted with the newest key
on next access.

OAuth2 flow
-----------
Single-account web-application flow (the entire association uses one Gmail
account).  The bureau admin visits ``/mailbox/oauth/start``, is redirected to
Google's consent page, and is sent back to ``/mailbox/oauth/callback``.
"""

from __future__ import annotations

import json
import logging

from cryptography.fernet import InvalidToken, MultiFernet
from flask import current_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token encryption / decryption
# ---------------------------------------------------------------------------


def _get_fernet() -> MultiFernet:
    """Build a MultiFernet from the app's ENCRYPTION_KEYS config list."""
    from cryptography.fernet import Fernet

    keys = current_app.config.get("ENCRYPTION_KEYS", [])
    if not keys:
        raise RuntimeError("ENCRYPTION_KEYS is not configured. Add at least one Fernet key.")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt_token(token_data: dict) -> str:
    """Encrypt a Gmail token dict to a string for DB storage.

    Args:
        token_data: Dict from ``google.oauth2.credentials.Credentials.to_json()``.

    Returns:
        Encrypted string suitable for ``GmailToken.token_encrypted``.
    """
    fernet = _get_fernet()
    plaintext = json.dumps(token_data).encode()
    return fernet.encrypt(plaintext).decode()


def decrypt_token(encrypted: str) -> dict:
    """Decrypt a stored Gmail token.

    Args:
        encrypted: Value of ``GmailToken.token_encrypted``.

    Returns:
        Token dict with ``access_token``, ``refresh_token``, etc.

    Raises:
        InvalidToken: If decryption fails (wrong key or corrupted data).
    """
    fernet = _get_fernet()
    plaintext = fernet.decrypt(encrypted.encode())
    return json.loads(plaintext)


def rotate_token_encryption(encrypted: str) -> str:
    """Re-encrypt a token using the newest Fernet key.

    Call this during a key rotation to re-encrypt existing tokens without
    requiring a full re-authorization.

    Args:
        encrypted: Current encrypted token.

    Returns:
        Token encrypted with the newest key.
    """
    fernet = _get_fernet()
    return fernet.rotate(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# Gmail API client
# ---------------------------------------------------------------------------


class GmailClient:
    """Thin wrapper around the Gmail API v1.

    All methods require a valid, non-expired token (the caller is responsible
    for refreshing via ``google.auth.transport.requests.Request``).

    Usage::

        client = GmailClient.from_db()
        messages = client.list_new_messages(after_timestamp=last_poll)
    """

    def __init__(self, service) -> None:
        self._service = service

    @classmethod
    def from_db(cls) -> GmailClient:
        """Build a GmailClient using the token stored in the database.

        Raises:
            RuntimeError: If no token is stored or decryption fails.
        """
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from app.extensions import db
        from app.models.email import GmailToken

        token_row = db.session.get(GmailToken, 1)
        if token_row is None:
            raise RuntimeError("No Gmail token stored. Please authorize via /mailbox/oauth/start.")

        try:
            token_data = decrypt_token(token_row.token_encrypted)
        except (InvalidToken, Exception) as exc:
            raise RuntimeError(f"Failed to decrypt Gmail token: {exc}") from exc

        creds = Credentials.from_authorized_user_info(token_data)
        service = build("gmail", "v1", credentials=creds)
        return cls(service)

    def list_new_messages(self, after_timestamp: int | None = None) -> list[dict]:
        """List unread message IDs received after a Unix timestamp.

        Only fetches emails with the UNREAD label so that already-read
        messages are not re-imported on every poll cycle.

        Args:
            after_timestamp: Unix epoch seconds; only messages after this are
                returned.  Pass None to paginate through recent messages (up to 500).

        Returns:
            List of Gmail message metadata dicts (``id``, ``threadId``).
        """
        query = "in:inbox is:unread"
        if after_timestamp:
            query += f" after:{after_timestamp}"

        messages: list[dict] = []
        page_token: str | None = None
        # On initial import (no after_timestamp) paginate up to 500 messages.
        max_pages = 10 if after_timestamp is None else 1

        for _ in range(max_pages):
            kwargs: dict = {"userId": "me", "q": query, "maxResults": 50}
            if page_token:
                kwargs["pageToken"] = page_token

            result = self._service.users().messages().list(**kwargs).execute()
            messages.extend(result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return messages

    def list_all_unread_ids(self) -> set[str]:
        """Return the set of Gmail message IDs that are currently UNREAD in the inbox."""
        query = "in:inbox is:unread"
        ids: set[str] = set()
        page_token: str | None = None

        for _ in range(20):
            kwargs: dict = {"userId": "me", "q": query, "maxResults": 100}
            if page_token:
                kwargs["pageToken"] = page_token
            result = self._service.users().messages().list(**kwargs).execute()
            for m in result.get("messages", []):
                ids.add(m["id"])
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return ids

    def get_message(self, message_id: str) -> dict:
        """Fetch full message payload for a given Gmail message ID.

        Args:
            message_id: Gmail message identifier.

        Returns:
            Full Gmail API message resource dict.
        """
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

    def mark_as_read(self, message_id: str) -> None:
        """Remove the UNREAD label from a Gmail message.

        Args:
            message_id: Gmail message identifier.
        """
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

    def trash_message(self, message_id: str) -> None:
        """Move a Gmail message to the trash (reversible from Gmail UI).

        Args:
            message_id: Gmail message identifier.
        """
        self._service.users().messages().trash(userId="me", id=message_id).execute()

    def send_message(self, raw_message: str) -> dict:
        """Send an RFC 2822 message via the Gmail API.

        Args:
            raw_message: Base64url-encoded RFC 2822 message string.

        Returns:
            Gmail API message resource for the sent message.
        """
        return (
            self._service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
        )
