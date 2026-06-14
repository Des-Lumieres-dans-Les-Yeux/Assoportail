"""Tests de l'API REST v1 — authentification par token Bearer et endpoints événements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.api_token import ApiToken
from app.models.event import Event, EventStatus
from app.models.user import User, UserPermission, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_user_with_perms(
    app: Flask,
    email: str,
    permissions: list[str],
    role: UserRole = UserRole.MEMBER,
) -> int:
    """Crée un User actif avec les permissions données et retourne son id."""
    with app.app_context():
        user = User(
            email=email,
            first_name="API",
            last_name="Testeur",
            role=role,
            is_active=True,
            must_change_password=False,
            permissions=permissions,
        )
        user.set_password("motdepasse123")
        _db.session.add(user)
        _db.session.commit()
        uid = user.id
        _db.session.remove()
    return uid


def _create_token(app: Flask, user_id: int, **kwargs) -> str:
    """Crée un ApiToken pour user_id et retourne le plaintext."""
    with app.app_context():
        plaintext, token = ApiToken.generate(name="test token", user_id=user_id, **kwargs)
        _db.session.add(token)
        _db.session.commit()
        _db.session.remove()
    return plaintext


def _make_event(app: Flask, creator_id: int, title: str = "Événement test") -> int:
    with app.app_context():
        event = Event(
            title=title,
            status=EventStatus.PLANNED.value,
            event_date=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            created_by_id=creator_id,
        )
        _db.session.add(event)
        _db.session.commit()
        eid = event.id
        _db.session.remove()
    return eid


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def events_user_id(app: Flask) -> int:
    """User membre avec permission EVENTS."""
    return _create_user_with_perms(app, "api_events@test.com", [UserPermission.EVENTS.value])


@pytest.fixture
def no_perm_user_id(app: Flask) -> int:
    """User membre sans aucune permission."""
    return _create_user_with_perms(app, "api_noperm@test.com", [])


@pytest.fixture
def events_token(app: Flask, events_user_id: int) -> str:
    return _create_token(app, events_user_id)


@pytest.fixture
def no_perm_token(app: Flask, no_perm_user_id: int) -> str:
    return _create_token(app, no_perm_user_id)


# ---------------------------------------------------------------------------
# 1. Authentification
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_no_auth_header_returns_401(self, client: FlaskClient) -> None:
        resp = client.get("/api/v1/events")
        assert resp.status_code == 401
        data = resp.get_json()
        assert data["error"] == "unauthorized"

    def test_malformed_header_returns_401(self, client: FlaskClient) -> None:
        resp = client.get("/api/v1/events", headers={"Authorization": "Token abc"})
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client: FlaskClient) -> None:
        resp = client.get("/api/v1/events", headers=_bearer("dldly_invalidtoken"))
        assert resp.status_code == 401

    def test_revoked_token_returns_401(
        self, app: Flask, client: FlaskClient, events_user_id: int
    ) -> None:
        plaintext = _create_token(app, events_user_id)
        # Révoquer le token
        with app.app_context():
            tok = _db.session.scalars(
                _db.select(ApiToken).where(
                    ApiToken.token_hash == ApiToken.hash_token(plaintext)
                )
            ).first()
            assert tok is not None
            tok.revoked = True
            _db.session.commit()
            _db.session.remove()
        resp = client.get("/api/v1/events", headers=_bearer(plaintext))
        assert resp.status_code == 401
        assert "révoqué" in resp.get_json()["message"]

    def test_expired_token_returns_401(
        self, app: Flask, client: FlaskClient, events_user_id: int
    ) -> None:
        expires_at = datetime.now(UTC) - timedelta(days=1)
        plaintext = _create_token(app, events_user_id, expires_at=expires_at)
        resp = client.get("/api/v1/events", headers=_bearer(plaintext))
        assert resp.status_code == 401

    def test_valid_token_succeeds(self, client: FlaskClient, events_token: str) -> None:
        resp = client.get("/api/v1/events", headers=_bearer(events_token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 2. Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_no_perm_user_gets_403(self, client: FlaskClient, no_perm_token: str) -> None:
        resp = client.get("/api/v1/events", headers=_bearer(no_perm_token))
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"] == "forbidden"

    def test_bureau_user_always_has_access(self, app: Flask, client: FlaskClient) -> None:
        bureau_id = _create_user_with_perms(
            app, "api_bureau@test.com", [], role=UserRole.BUREAU
        )
        plaintext = _create_token(app, bureau_id)
        resp = client.get("/api/v1/events", headers=_bearer(plaintext))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. POST /api/v1/events
# ---------------------------------------------------------------------------


class TestCreateEvent:
    def test_create_event_minimal(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        resp = client.post(
            "/api/v1/events",
            json={
                "title": "Journée test",
                "event_date": "2026-10-01T09:00:00",
                "status": "planned",
            },
            headers=_bearer(events_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["title"] == "Journée test"
        assert data["status"] == "planned"
        assert data["id"] is not None
        assert data["volunteer_token"] is not None

    def test_create_event_with_slots_and_dates(
        self, app: Flask, client: FlaskClient, events_token: str
    ) -> None:
        resp = client.post(
            "/api/v1/events",
            json={
                "title": "Événement multi-jours",
                "event_date": "2026-11-01T09:00:00",
                "status": "planned",
                "slots": [
                    {
                        "slot_date": "2026-11-01",
                        "start_time": "09:00",
                        "end_time": "17:00",
                        "label": "Matin",
                    }
                ],
                "dates": ["2026-11-01", "2026-11-08"],
            },
            headers=_bearer(events_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert len(data["slots"]) == 1
        assert data["slots"][0]["label"] == "Matin"

    def test_create_event_invalid_status(
        self, client: FlaskClient, events_token: str
    ) -> None:
        resp = client.post(
            "/api/v1/events",
            json={"title": "Test", "event_date": "2026-10-01T09:00:00", "status": "invalid"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 422

    def test_create_event_missing_required_field(
        self, client: FlaskClient, events_token: str
    ) -> None:
        resp = client.post(
            "/api/v1/events",
            json={"title": "Titre sans date"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 422

    def test_created_by_is_api_user(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        resp = client.post(
            "/api/v1/events",
            json={"title": "Check creator", "event_date": "2026-10-15T10:00:00"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 201
        assert resp.get_json()["created_by_id"] == events_user_id


# ---------------------------------------------------------------------------
# 4. GET /api/v1/events
# ---------------------------------------------------------------------------


class TestListEvents:
    def test_list_events_empty(self, client: FlaskClient, events_token: str) -> None:
        resp = client.get("/api/v1/events", headers=_bearer(events_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_events_returns_created(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        _make_event(app, events_user_id, "Événement listable")
        resp = client.get("/api/v1/events", headers=_bearer(events_token))
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 1
        assert data["items"][0]["title"] == "Événement listable"

    def test_list_events_filter_by_status(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        _make_event(app, events_user_id, "Planifié")
        with app.app_context():
            done = Event(
                title="Terminé",
                status=EventStatus.COMPLETED.value,
                event_date=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
                created_by_id=events_user_id,
            )
            _db.session.add(done)
            _db.session.commit()
            _db.session.remove()

        resp = client.get("/api/v1/events?status=planned", headers=_bearer(events_token))
        data = resp.get_json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "planned"

    def test_list_events_pagination(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        for i in range(5):
            _make_event(app, events_user_id, f"Event {i}")
        resp = client.get("/api/v1/events?limit=2&offset=0", headers=_bearer(events_token))
        data = resp.get_json()
        assert data["total"] == 5
        assert len(data["items"]) == 2


# ---------------------------------------------------------------------------
# 5. GET /api/v1/events/<id>
# ---------------------------------------------------------------------------


class TestGetEvent:
    def test_get_event_detail(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid = _make_event(app, events_user_id)
        resp = client.get(f"/api/v1/events/{eid}", headers=_bearer(events_token))
        assert resp.status_code == 200
        assert resp.get_json()["id"] == eid

    def test_get_event_not_found(self, client: FlaskClient, events_token: str) -> None:
        resp = client.get("/api/v1/events/99999", headers=_bearer(events_token))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. POST /api/v1/events/<id>/slots
# ---------------------------------------------------------------------------


class TestAddSlot:
    def test_add_slot(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid = _make_event(app, events_user_id)
        resp = client.post(
            f"/api/v1/events/{eid}/slots",
            json={"slot_date": "2026-09-01", "start_time": "09:00", "end_time": "13:00"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["slot_date"] == "2026-09-01"
        assert data["start_time"] == "09:00:00"

    def test_add_slot_event_not_found(self, client: FlaskClient, events_token: str) -> None:
        resp = client.post(
            "/api/v1/events/99999/slots",
            json={"slot_date": "2026-09-01"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. POST /api/v1/events/<id>/volunteers (idempotence)
# ---------------------------------------------------------------------------


class TestRegisterVolunteer:
    def test_register_volunteer(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid = _make_event(app, events_user_id)
        resp = client.post(
            f"/api/v1/events/{eid}/volunteers",
            json={"name": "Alice", "email": "alice@test.com"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Alice"
        assert data["email"] == "alice@test.com"
        assert "personal_token" in data
        assert data["confirmed"] is False

    def test_register_volunteer_idempotent(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid = _make_event(app, events_user_id)
        payload = {"name": "Bob", "email": "bob@test.com"}
        r1 = client.post(
            f"/api/v1/events/{eid}/volunteers", json=payload, headers=_bearer(events_token)
        )
        r2 = client.post(
            f"/api/v1/events/{eid}/volunteers", json=payload, headers=_bearer(events_token)
        )
        assert r1.status_code == 201
        assert r2.status_code == 200  # déjà inscrit → 200
        # Même token personnel
        assert r1.get_json()["personal_token"] == r2.get_json()["personal_token"]

    def test_register_volunteer_event_not_found(
        self, client: FlaskClient, events_token: str
    ) -> None:
        resp = client.post(
            "/api/v1/events/99999/volunteers",
            json={"name": "Test", "email": "t@test.com"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 8. PUT /api/v1/events/<id>/slots/<slot_id>/availability
# ---------------------------------------------------------------------------


class TestSetAvailability:
    def _setup(self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int):
        eid = _make_event(app, events_user_id)
        # Créer un créneau
        sr = client.post(
            f"/api/v1/events/{eid}/slots",
            json={"slot_date": "2026-09-01", "start_time": "09:00"},
            headers=_bearer(events_token),
        )
        slot_id = sr.get_json()["id"]
        # Inscrire un bénévole
        vr = client.post(
            f"/api/v1/events/{eid}/volunteers",
            json={"name": "Carol", "email": "carol@test.com"},
            headers=_bearer(events_token),
        )
        vol_id = vr.get_json()["id"]
        return eid, slot_id, vol_id

    def test_set_availability_by_id(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid, slot_id, vol_id = self._setup(app, client, events_token, events_user_id)
        resp = client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "present", "volunteer_id": vol_id},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "present"

    def test_set_availability_by_email(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid, slot_id, vol_id = self._setup(app, client, events_token, events_user_id)
        resp = client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "maybe", "email": "carol@test.com"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "maybe"

    def test_update_availability(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid, slot_id, vol_id = self._setup(app, client, events_token, events_user_id)
        client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "present", "volunteer_id": vol_id},
            headers=_bearer(events_token),
        )
        resp = client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "absent", "volunteer_id": vol_id},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "absent"

    def test_invalid_status(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid, slot_id, vol_id = self._setup(app, client, events_token, events_user_id)
        resp = client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "inconnu", "volunteer_id": vol_id},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 422

    def test_missing_volunteer_id_and_email(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        eid, slot_id, _ = self._setup(app, client, events_token, events_user_id)
        resp = client.put(
            f"/api/v1/events/{eid}/slots/{slot_id}/availability",
            json={"status": "present"},
            headers=_bearer(events_token),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 9. Swagger / OpenAPI JSON
# ---------------------------------------------------------------------------


class TestOpenAPISpec:
    def test_openapi_json_accessible(self, client: FlaskClient) -> None:
        resp = client.get("/api/docs/openapi.json")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "openapi" in data
        assert data["info"]["title"] == "DLDLY Portal API"
        # Schemas must be populated (spectree appends a hash suffix, e.g. "EventCreateIn.abc1234")
        schemas = data.get("components", {}).get("schemas", {})
        schema_names = list(schemas)
        assert any(k.startswith("EventCreateIn") for k in schema_names), (
            f"EventCreateIn missing from schemas: {schema_names}"
        )
        assert any(k.startswith("EventOut") for k in schema_names), (
            f"EventOut missing from schemas: {schema_names}"
        )
        # POST /api/v1/events must have a documented requestBody
        post_events = data.get("paths", {}).get("/api/v1/events", {}).get("post", {})
        assert "requestBody" in post_events, (
            "POST /api/v1/events has no requestBody in OpenAPI spec"
        )

    def test_swagger_ui_accessible(self, client: FlaskClient) -> None:
        resp = client.get("/api/docs/swagger/")
        assert resp.status_code == 200

    def test_openapi_no_non_api_paths(self, client: FlaskClient) -> None:
        """La spec ne doit pas exposer les routes internes (/mailbox, /treasury, /members…)."""
        resp = client.get("/api/docs/openapi.json")
        assert resp.status_code == 200
        paths = resp.get_json().get("paths", {})
        leaked = [
            p for p in paths
            if any(
                p.startswith(prefix)
                for prefix in ("/mailbox", "/treasury", "/members", "/auth", "/dashboard")
            )
        ]
        assert not leaked, f"Routes non-API exposées dans la spec OpenAPI : {leaked}"

    def test_last_used_at_update_not_audited(
        self, app: Flask, client: FlaskClient, events_token: str, events_user_id: int
    ) -> None:
        """Mettre à jour last_used_at lors d'un appel API ne doit PAS créer de ligne UPDATE."""
        from app.audit import AuditAction, AuditLog

        # Faire un appel API (met à jour last_used_at)
        resp = client.get("/api/v1/events", headers=_bearer(events_token))
        assert resp.status_code == 200

        with app.app_context():
            update_logs = _db.session.scalars(
                _db.select(AuditLog).where(
                    AuditLog.entity_type == "api_tokens",
                    AuditLog.action == AuditAction.UPDATE,
                )
            ).all()
            assert update_logs == [], (
                f"Des lignes UPDATE audit_logs ont été créées pour api_tokens : {update_logs}"
            )

    def test_token_creation_is_audited(self, app: Flask, events_user_id: int) -> None:
        """Créer un token via ApiToken.generate() + commit doit créer une ligne CREATE."""
        from app.audit import AuditAction, AuditLog
        from app.models.api_token import ApiToken

        with app.app_context():
            plaintext, token = ApiToken.generate(name="audit test token", user_id=events_user_id)
            _db.session.add(token)
            _db.session.commit()

            create_logs = _db.session.scalars(
                _db.select(AuditLog).where(
                    AuditLog.entity_type == "api_tokens",
                    AuditLog.action == AuditAction.CREATE,
                )
            ).all()
            assert len(create_logs) >= 1, (
                "Aucune ligne CREATE audit_logs trouvée pour la création d'un token"
            )
            _db.session.remove()
