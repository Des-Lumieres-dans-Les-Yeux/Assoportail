"""Unit tests for document upload, MIME validation, gallery, and download."""

import io
import os
import shutil

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.document import Document, DocumentType
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Magic byte stubs for each allowed format
# ---------------------------------------------------------------------------

_JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # valid JPEG magic
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # valid PNG magic
_PDF_MAGIC = b"%PDF-1.4\n" + b"\x00" * 100  # valid PDF magic
_DOCX_MAGIC = b"PK\x03\x04" + b"\x00" * 100  # ZIP / DOCX magic
_WEBP_MAGIC = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100
_MP4_MAGIC = b"\x00\x00\x00\x18" + b"ftyp" + b"isom" + b"\x00" * 100


def _upload(
    client: FlaskClient,
    data: bytes,
    filename: str,
    doc_type: str = "other",
):
    """Helper: POST a file upload to /documents/upload."""
    return client.post(
        "/documents/upload",
        data={
            "file": (io.BytesIO(data), filename),
            "type": doc_type,
            "description": "",
            "category": "",
            "entity_type": "",
            "entity_id": "",
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Cleanup — remove test uploads after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_uploads(app: Flask):
    yield
    upload_dir = app.config["UPLOAD_FOLDER"]
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestDocumentAccess:
    def test_member_can_view_gallery(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/documents/")
        assert response.status_code == 200

    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        response = client.get("/documents/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_upload(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/documents/upload")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Upload — valid files
# ---------------------------------------------------------------------------


class TestDocumentUpload:
    def test_bureau_can_upload_jpeg(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        response = _upload(bureau_client, _JPEG_MAGIC, "photo.jpg", "photo")
        assert response.status_code in {301, 302}
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="photo.jpg")
            ).scalar_one_or_none()
            assert doc is not None
            assert doc.mime_type == "image/jpeg"
            assert doc.type == DocumentType.PHOTO.value
            assert doc.uploaded_by_id == bureau_user.id
            assert doc.size_bytes > 0

    def test_bureau_can_upload_pdf(self, app: Flask, bureau_client: FlaskClient) -> None:
        response = _upload(bureau_client, _PDF_MAGIC, "rapport.pdf", "report")
        assert response.status_code in {301, 302}
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="rapport.pdf")
            ).scalar_one_or_none()
            assert doc is not None
            assert doc.mime_type == "application/pdf"

    def test_stored_filename_follows_convention(
        self, app: Flask, bureau_client: FlaskClient
    ) -> None:
        _upload(bureau_client, _PNG_MAGIC, "Mon Flipper.png", "photo")
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="Mon Flipper.png")
            ).scalar_one_or_none()
            assert doc is not None
            # stored_filename should be lowercase, no spaces, date-prefixed
            assert " " not in doc.stored_filename
            assert doc.stored_filename.endswith(".png")
            assert "2026" in doc.stored_filename or "202" in doc.stored_filename

    def test_file_is_written_to_disk(self, app: Flask, bureau_client: FlaskClient) -> None:
        _upload(bureau_client, _JPEG_MAGIC, "disk_test.jpg", "photo")
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="disk_test.jpg")
            ).scalar_one()
            upload_folder = app.config["UPLOAD_FOLDER"]
            file_path = os.path.join(upload_folder, doc.subdir, doc.stored_filename)
            assert os.path.isfile(file_path)


# ---------------------------------------------------------------------------
# Upload — rejection cases
# ---------------------------------------------------------------------------


class TestDocumentUploadRejection:
    def test_disallowed_extension_rejected(self, app: Flask, bureau_client: FlaskClient) -> None:
        response = _upload(bureau_client, b"#!/usr/bin/env python", "exploit.py")
        assert response.status_code == 200  # re-renders form
        assert "autorisée" in response.data.decode()
        with app.app_context():
            count = _db.session.execute(_db.select(_db.func.count(Document.id))).scalar()
            assert count == 0

    def test_mime_mismatch_rejected(self, app: Flask, bureau_client: FlaskClient) -> None:
        # JPEG magic bytes but .png extension
        response = _upload(bureau_client, _JPEG_MAGIC, "fake.png")
        assert response.status_code == 200
        assert "MIME" in response.data.decode()
        with app.app_context():
            count = _db.session.execute(_db.select(_db.func.count(Document.id))).scalar()
            assert count == 0

    def test_oversized_photo_rejected(self, app: Flask, bureau_client: FlaskClient) -> None:
        # PNG magic + 11 MB of zeros (exceeds 10 MB photo limit)
        big_data = _PNG_MAGIC + b"\x00" * (11 * 1024 * 1024)
        response = _upload(bureau_client, big_data, "huge.png", "photo")
        assert response.status_code == 200
        assert "Mo" in response.data.decode()
        with app.app_context():
            count = _db.session.execute(_db.select(_db.func.count(Document.id))).scalar()
            assert count == 0

    def test_no_file_is_rejected(self, bureau_client: FlaskClient) -> None:
        response = bureau_client.post(
            "/documents/upload",
            data={"type": "other", "description": ""},
            content_type="multipart/form-data",
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Gallery and download
# ---------------------------------------------------------------------------


class TestDocumentGallery:
    def test_uploaded_document_appears_in_gallery(
        self, app: Flask, bureau_client: FlaskClient, auth_client: FlaskClient
    ) -> None:
        _upload(bureau_client, _PDF_MAGIC, "visible.pdf", "other")
        response = auth_client.get("/documents/")
        assert "visible.pdf" in response.data.decode()

    def test_download_returns_file(
        self, app: Flask, bureau_client: FlaskClient, auth_client: FlaskClient
    ) -> None:
        _upload(bureau_client, _JPEG_MAGIC, "download_me.jpg", "photo")
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="download_me.jpg")
            ).scalar_one()
            doc_id = doc.id
        response = auth_client.get(f"/documents/{doc_id}")
        assert response.status_code == 200
        assert response.data[:3] == b"\xff\xd8\xff"  # JPEG magic in response

    def test_bureau_can_delete_document(self, app: Flask, bureau_client: FlaskClient) -> None:
        _upload(bureau_client, _PDF_MAGIC, "delete_me.pdf", "other")
        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="delete_me.pdf")
            ).scalar_one()
            doc_id = doc.id
        bureau_client.post(f"/documents/{doc_id}/delete")
        with app.app_context():
            assert _db.session.get(Document, doc_id) is None


# ---------------------------------------------------------------------------
# Junction table linking
# ---------------------------------------------------------------------------


class TestDocumentJunctionLinking:
    def test_document_linked_to_event(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        from datetime import UTC, datetime

        from app.models.document import event_documents
        from app.models.event import Event

        # Create an event
        with app.app_context():
            event = Event(
                title="Journée doc",
                status="planned",
                event_date=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
                created_by_id=bureau_user.id,
            )
            _db.session.add(event)
            _db.session.commit()
            event_id = event.id

        # Upload linked to the event
        bureau_client.post(
            "/documents/upload",
            data={
                "file": (io.BytesIO(_JPEG_MAGIC), "event_photo.jpg"),
                "type": "photo",
                "description": "",
                "category": "",
                "entity_type": "event",
                "entity_id": str(event_id),
            },
            content_type="multipart/form-data",
        )

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="event_photo.jpg")
            ).scalar_one()
            row = _db.session.execute(
                _db.select(event_documents).where(
                    event_documents.c.document_id == doc.id,
                    event_documents.c.event_id == event_id,
                )
            ).one_or_none()
            assert row is not None
