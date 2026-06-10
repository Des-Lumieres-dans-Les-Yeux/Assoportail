"""Google Drive API client — Shared Drive file management.

Uses the same OAuth2 token as the Gmail client (the token must include the
``drive.file`` scope).  Uploads are organised into a folder hierarchy under
a root folder named ``Assoportail`` inside the configured Shared Drive.

Folder structure
----------------
::

    <Shared Drive>/
    └── Assoportail/
        ├── Photos/
        ├── Vidéos/
        ├── Factures/
        ├── Rapports/
        ├── Contrats/
        └── Autres/

Configuration
-------------
Set ``GOOGLE_SHARED_DRIVE_ID`` in ``.env`` to the Shared Drive's ID (the long
alphanumeric string from the Drive URL).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ROOT_FOLDER_NAME = "Assoportail"

# doc_type → (category_folder | None, leaf_folder, use_year_subfolder)
# category_folder=None means the leaf sits directly under _ROOT_FOLDER_NAME
_SUBFOLDER_DEFS: dict[str, tuple[str | None, str, bool]] = {
    "photo": (None, "Photos", True),
    "video": (None, "Vidéos", True),
    "invoice": ("Comptabilité", "Factures", True),
    "cerfa": ("Comptabilité", "Reçus fiscaux", True),
    "report": ("Comptabilité", "Rapports", True),
    "contract": ("Administratif", "Contrats", False),
    "machine": ("Machines", "Documents", True),
    "receipt": ("Comptabilité", "Justificatifs", True),
    "other": ("Administratif", "Autres", False),
}


class DriveService:
    """Thin wrapper around the Drive API v3.

    Usage::

        svc = DriveService.from_db()
        file_id, web_link = svc.upload_file(data, "photo.jpg", "image/jpeg", "photo")
    """

    def __init__(self, service, drive_id: str | None) -> None:
        self._service = service
        self._drive_id = drive_id
        # Cache: folder name → folder ID
        self._folder_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_db(cls) -> DriveService:
        """Build a DriveService using the token stored in the database.

        Raises:
            RuntimeError: If no token is stored or decryption fails.
        """
        from flask import current_app
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from app.extensions import db
        from app.models.email import GmailToken
        from app.services.gmail import decrypt_token

        token_row = db.session.get(GmailToken, 1)
        if token_row is None:
            raise RuntimeError("No Google token stored. Please authorize via /mailbox/oauth/start.")

        try:
            token_data = decrypt_token(token_row.token_encrypted)
        except Exception as exc:
            raise RuntimeError(f"Failed to decrypt Google token: {exc}") from exc

        creds = Credentials.from_authorized_user_info(token_data)
        service = build("drive", "v3", credentials=creds)
        drive_id = current_app.config.get("GOOGLE_SHARED_DRIVE_ID") or None
        return cls(service, drive_id)

    # ------------------------------------------------------------------
    # Folder management
    # ------------------------------------------------------------------

    def _find_or_create_folder(self, name: str, parent_id: str | None) -> str:
        """Return the folder ID for *name* under *parent_id*, creating it if absent."""
        cache_key = f"{parent_id}:{name}"
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]

        extra_params: dict = {}
        if self._drive_id:
            extra_params = {
                "corpora": "drive",
                "driveId": self._drive_id,
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
            }

        query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        result = self._service.files().list(q=query, fields="files(id)", **extra_params).execute()
        files = result.get("files", [])
        if files:
            folder_id = files[0]["id"]
        else:
            folder_id = self._create_folder(name, parent_id)

        self._folder_cache[cache_key] = folder_id
        return folder_id

    def _create_folder(self, name: str, parent_id: str | None) -> str:
        """Create a Drive folder and return its ID."""
        metadata: dict = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            metadata["parents"] = [parent_id]
        elif self._drive_id:
            metadata["parents"] = [self._drive_id]

        extra: dict = {}
        if self._drive_id:
            extra["supportsAllDrives"] = True

        folder = self._service.files().create(body=metadata, fields="id", **extra).execute()
        return folder["id"]

    def _get_folder_at_path(self, *parts: str) -> str:
        """Navigate/create a chain of folders under the root, return leaf folder ID."""
        folder_id = self._find_or_create_folder(_ROOT_FOLDER_NAME, self._drive_id)
        for part in parts:
            folder_id = self._find_or_create_folder(part, folder_id)
        return folder_id

    def _get_subfolder_id(self, doc_type: str, year: int | None = None) -> str:
        """Return the ID of the target subfolder for *doc_type*.

        Folder hierarchy:
        - Photos/<year>/  — media gets a year subfolder
        - Vidéos/<year>/
        - Comptabilité/Factures/<year>/  — accounting gets category + year
        - Comptabilité/Reçus fiscaux/<year>/
        - Comptabilité/Rapports/<year>/
        - Administratif/Contrats/  — admin docs stay flat
        - Administratif/Autres/
        """
        category, leaf, use_year = _SUBFOLDER_DEFS.get(doc_type, (None, "Autres", False))
        parts: list[str] = []
        if category:
            parts.append(category)
        parts.append(leaf)
        if use_year and year:
            parts.append(str(year))
        return self._get_folder_at_path(*parts)

    # ------------------------------------------------------------------
    # Upload / delete
    # ------------------------------------------------------------------

    def upload_file(
        self,
        data: bytes,
        filename: str,
        mime_type: str,
        doc_type: str,
        year: int | None = None,
    ) -> tuple[str, str]:
        """Upload *data* to the appropriate Drive subfolder.

        Args:
            data: Raw file bytes.
            filename: Display name for the file on Drive.
            mime_type: MIME type of the file.
            doc_type: DocumentType value used to pick the subfolder.
            year: Optional year used to create a year-based subfolder for
                  media and accounting document types.

        Returns:
            ``(file_id, web_view_link)`` tuple.
        """
        import io

        from googleapiclient.http import MediaIoBaseUpload

        folder_id = self._get_subfolder_id(doc_type, year=year)

        metadata: dict = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)

        extra: dict = {}
        if self._drive_id:
            extra["supportsAllDrives"] = True

        uploaded = (
            self._service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id,webViewLink",
                **extra,
            )
            .execute()
        )
        return uploaded["id"], uploaded.get("webViewLink", "")

    def download_file(self, file_id: str) -> bytes:
        """Download file content from Drive by its ID.

        Returns:
            Raw file bytes.
        """
        import io

        from googleapiclient.http import MediaIoBaseDownload

        extra: dict = {}
        if self._drive_id:
            extra["supportsAllDrives"] = True

        request = self._service.files().get_media(fileId=file_id, **extra)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    def delete_file(self, file_id: str) -> None:
        """Delete a file from Drive by its ID.

        Silently ignores 404 errors (file already removed).
        """
        from googleapiclient.errors import HttpError

        extra: dict = {}
        if self._drive_id:
            extra["supportsAllDrives"] = True

        try:
            self._service.files().delete(fileId=file_id, **extra).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                logger.warning("Drive file %s not found on delete (already removed)", file_id)
            else:
                raise


def is_drive_configured() -> bool:
    """Return True if a Google token with drive scope is available."""
    try:
        from app.extensions import db
        from app.models.email import GmailToken

        token_row = db.session.get(GmailToken, 1)
        return token_row is not None
    except Exception:
        return False
