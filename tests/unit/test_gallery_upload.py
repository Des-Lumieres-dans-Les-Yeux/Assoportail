"""Tests pour l'upload média depuis la galerie (route upload_gallery_media).

Couvre :
- upload sans événement (photo) → Document créé, type PHOTO, non lié
- upload avec événement valide → Document lié dans event_documents
- upload d'une vidéo valide
- rejets : extension non autorisée, MIME mismatch, fichier PDF (non photo/vidéo),
  dépassement de taille
- entity_id invalide/inexistant → traité comme « aucun événement »
- accès sans authentification → redirigé
- non-régression : route upload_media (depuis un événement) fonctionne toujours
"""

import io
import os
import shutil
from datetime import UTC, datetime

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.document import Document, DocumentType, event_documents
from app.models.event import Event
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Magic byte stubs (réutilisés depuis test_documents.py)
# ---------------------------------------------------------------------------

_JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_WEBP_MAGIC = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100
_MP4_MAGIC = b"\x00\x00\x00\x18" + b"ftyp" + b"isom" + b"\x00" * 100
_PDF_MAGIC = b"%PDF-1.4\n" + b"\x00" * 100
_WEBM_MAGIC = b"\x1a\x45\xdf\xa3" + b"\x00" * 100

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_gallery(
    client: FlaskClient,
    files: list[tuple[bytes, str]],
    entity_id: str = "",
    description: str = "",
):
    """POST vers /documents/upload-gallery-media avec un ou plusieurs fichiers."""
    data: dict = {
        "entity_id": entity_id,
        "description": description,
    }
    if len(files) == 1:
        data["file"] = (io.BytesIO(files[0][0]), files[0][1])
    else:
        data["file"] = [(io.BytesIO(d), n) for d, n in files]
    return client.post(
        "/documents/upload-gallery-media",
        data=data,
        content_type="multipart/form-data",
        follow_redirects=False,
    )


def _make_event(app: Flask, user_id: int) -> int:
    """Crée un événement en base et retourne son id."""
    with app.app_context():
        event = Event(
            title="Festival test",
            status="planned",
            event_date=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            created_by_id=user_id,
        )
        _db.session.add(event)
        _db.session.commit()
        return event.id


# ---------------------------------------------------------------------------
# Nettoyage uploads après chaque test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_uploads(app: Flask):
    yield
    upload_dir = app.config["UPLOAD_FOLDER"]
    if os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Contrôle d'accès
# ---------------------------------------------------------------------------


class TestGalleryUploadAccess:
    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        """Un utilisateur non connecté est redirigé (pas de 200 ni 500)."""
        resp = _post_gallery(client, [(_JPEG_MAGIC, "photo.jpg")])
        assert resp.status_code in {301, 302}
        assert "/auth/login" in resp.headers["Location"]

    def test_member_can_upload(self, auth_client: FlaskClient) -> None:
        """Un membre standard (non bureau) peut uploader depuis la galerie."""
        resp = _post_gallery(auth_client, [(_JPEG_MAGIC, "ok.jpg")])
        # Redirige vers la galerie
        assert resp.status_code in {301, 302}
        assert "/documents/" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# Upload réussis
# ---------------------------------------------------------------------------


class TestGalleryUploadSuccess:
    def test_photo_without_event(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Photo valide sans événement → 1 Document PHOTO non lié."""
        resp = _post_gallery(auth_client, [(_PNG_MAGIC, "sans_evt.png")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="sans_evt.png")
            ).scalar_one_or_none()
            assert doc is not None, "Document non créé"
            assert doc.type == DocumentType.PHOTO.value
            assert doc.mime_type == "image/png"
            assert doc.uploaded_by_id == member_user.id

            # Pas de lien dans event_documents
            row = _db.session.execute(
                _db.select(event_documents).where(event_documents.c.document_id == doc.id)
            ).one_or_none()
            assert row is None, "Document ne devrait pas être lié à un événement"

    def test_photo_linked_to_event(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Photo valide avec événement existant → Document lié dans event_documents."""
        event_id = _make_event(app, member_user.id)
        resp = _post_gallery(
            auth_client,
            [(_JPEG_MAGIC, "avec_evt.jpg")],
            entity_id=str(event_id),
        )
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="avec_evt.jpg")
            ).scalar_one_or_none()
            assert doc is not None

            row = _db.session.execute(
                _db.select(event_documents).where(
                    event_documents.c.document_id == doc.id,
                    event_documents.c.event_id == event_id,
                )
            ).one_or_none()
            assert row is not None, "Document devrait être lié à l'événement"

    def test_video_upload(self, app: Flask, auth_client: FlaskClient) -> None:
        """Vidéo MP4 valide → Document créé avec type VIDEO."""
        resp = _post_gallery(auth_client, [(_MP4_MAGIC, "clip.mp4")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="clip.mp4")
            ).scalar_one_or_none()
            assert doc is not None
            assert doc.type == DocumentType.VIDEO.value
            assert doc.mime_type == "video/mp4"

    def test_webm_video_upload(self, app: Flask, auth_client: FlaskClient) -> None:
        """Vidéo WebM valide → Document créé avec type VIDEO."""
        resp = _post_gallery(auth_client, [(_WEBM_MAGIC, "clip.webm")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="clip.webm")
            ).scalar_one_or_none()
            assert doc is not None
            assert doc.type == DocumentType.VIDEO.value

    def test_multiple_files_uploaded(self, app: Flask, auth_client: FlaskClient) -> None:
        """Plusieurs fichiers → tous créés."""
        resp = _post_gallery(
            auth_client,
            [(_JPEG_MAGIC, "a.jpg"), (_PNG_MAGIC, "b.png")],
        )
        assert resp.status_code in {301, 302}

        with app.app_context():
            count = _db.session.scalar(_db.select(_db.func.count(Document.id)))
            assert count == 2


# ---------------------------------------------------------------------------
# Rejets
# ---------------------------------------------------------------------------


class TestGalleryUploadRejection:
    def test_no_file_flash_warning(self, auth_client: FlaskClient) -> None:
        """Aucun fichier soumis → redirigé avec warning, aucun Document créé."""
        resp = auth_client.post(
            "/documents/upload-gallery-media",
            data={"entity_id": "", "description": ""},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in {301, 302}

    def test_disallowed_extension_rejected(self, app: Flask, auth_client: FlaskClient) -> None:
        """Extension non autorisée (.py) → Document non créé."""
        resp = _post_gallery(auth_client, [(_JPEG_MAGIC, "exploit.py")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            count = _db.session.scalar(_db.select(_db.func.count(Document.id)))
            assert count == 0

    def test_mime_mismatch_rejected(self, app: Flask, auth_client: FlaskClient) -> None:
        """Magic bytes JPEG mais extension .png → MIME mismatch → rejeté."""
        resp = _post_gallery(auth_client, [(_JPEG_MAGIC, "fake.png")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            count = _db.session.scalar(_db.select(_db.func.count(Document.id)))
            assert count == 0

    def test_pdf_rejected(self, app: Flask, auth_client: FlaskClient) -> None:
        """Un PDF (non photo/vidéo) doit être rejeté même si l'extension est .pdf."""
        resp = _post_gallery(auth_client, [(_PDF_MAGIC, "doc.pdf")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            count = _db.session.scalar(_db.select(_db.func.count(Document.id)))
            assert count == 0

    def test_oversized_photo_rejected(self, app: Flask, auth_client: FlaskClient) -> None:
        """Photo dépassant MAX_UPLOAD_PHOTO → rejetée, aucun Document."""
        with app.app_context():
            limit = app.config["MAX_UPLOAD_PHOTO"]
        big = _PNG_MAGIC + b"\x00" * (limit + 1024)
        resp = _post_gallery(auth_client, [(big, "gros.png")])
        assert resp.status_code in {301, 302}

        with app.app_context():
            count = _db.session.scalar(_db.select(_db.func.count(Document.id)))
            assert count == 0

    def test_invalid_entity_id_treated_as_no_event(
        self, app: Flask, auth_client: FlaskClient
    ) -> None:
        """entity_id non numérique → ignoré, upload réussit sans lien événement."""
        resp = _post_gallery(auth_client, [(_JPEG_MAGIC, "ghost.jpg")], entity_id="not-a-number")
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="ghost.jpg")
            ).scalar_one_or_none()
            assert doc is not None, "Document devrait être créé"
            row = _db.session.execute(
                _db.select(event_documents).where(event_documents.c.document_id == doc.id)
            ).one_or_none()
            assert row is None

    def test_nonexistent_entity_id_treated_as_no_event(
        self, app: Flask, auth_client: FlaskClient
    ) -> None:
        """entity_id numérique mais événement inexistant → upload sans lien, pas d'erreur 400."""
        resp = _post_gallery(auth_client, [(_JPEG_MAGIC, "nowhere.jpg")], entity_id="99999")
        assert resp.status_code in {301, 302}

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="nowhere.jpg")
            ).scalar_one_or_none()
            assert doc is not None, "Document devrait être créé malgré entity_id inexistant"
            row = _db.session.execute(
                _db.select(event_documents).where(event_documents.c.document_id == doc.id)
            ).one_or_none()
            assert row is None


# ---------------------------------------------------------------------------
# Non-régression : route upload_media (depuis un événement) doit toujours
# fonctionner exactement comme avant
# ---------------------------------------------------------------------------


class TestUploadMediaNonRegression:
    def test_upload_media_with_valid_event(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo
    ) -> None:
        """Route /upload-media (POST) : photo liée à un événement existant → succès."""
        event_id = _make_event(app, member_user.id)
        resp = auth_client.post(
            "/documents/upload-media",
            data={
                "entity_id": str(event_id),
                "description": "",
                "file": (io.BytesIO(_JPEG_MAGIC), "regr.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in {301, 302}
        assert f"/events/{event_id}" in resp.headers.get("Location", "")

        with app.app_context():
            doc = _db.session.execute(
                _db.select(Document).filter_by(original_filename="regr.jpg")
            ).scalar_one_or_none()
            assert doc is not None
            row = _db.session.execute(
                _db.select(event_documents).where(
                    event_documents.c.document_id == doc.id,
                    event_documents.c.event_id == event_id,
                )
            ).one_or_none()
            assert row is not None

    def test_upload_media_missing_entity_id_returns_400(self, auth_client: FlaskClient) -> None:
        """Route /upload-media sans entity_id → 400 (comportement inchangé)."""
        resp = auth_client.post(
            "/documents/upload-media",
            data={"file": (io.BytesIO(_JPEG_MAGIC), "noid.jpg")},
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_upload_media_nonexistent_event_returns_404(self, auth_client: FlaskClient) -> None:
        """Route /upload-media avec event inexistant → 404 (comportement inchangé)."""
        resp = auth_client.post(
            "/documents/upload-media",
            data={
                "entity_id": "99999",
                "file": (io.BytesIO(_JPEG_MAGIC), "noevt.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    def test_unauthenticated_upload_media_redirected(self, client: FlaskClient) -> None:
        """Route /upload-media sans session → redirigé vers login."""
        resp = client.post(
            "/documents/upload-media",
            data={
                "entity_id": "1",
                "file": (io.BytesIO(_JPEG_MAGIC), "anon.jpg"),
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        assert resp.status_code in {301, 302}
        assert "/auth/login" in resp.headers["Location"]
