"""Unit tests for task management — CRUD, assignment, comments, status transitions."""

from flask import Flask
from flask.testing import FlaskClient

from app.extensions import db as _db
from app.models.task import Task, TaskComment, TaskPriority, TaskSource, TaskStatus
from tests.conftest import UserInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    app: Flask,
    creator_id: int,
    *,
    title: str = "Réparer flipper",
    status: str = "open",
    priority: str = "normal",
    assigned_to_id: int | None = None,
) -> int:
    with app.app_context():
        task = Task(
            title=title,
            status=status,
            priority=priority,
            created_by_id=creator_id,
            assigned_to_id=assigned_to_id,
            source=TaskSource.MANUAL,
        )
        _db.session.add(task)
        _db.session.commit()
        return task.id


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class TestTaskAccess:
    def test_member_can_view_task_list(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/tasks/")
        assert response.status_code == 200

    def test_unauthenticated_redirected(self, client: FlaskClient) -> None:
        response = client.get("/tasks/", follow_redirects=False)
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_member_cannot_create_task(self, auth_client: FlaskClient) -> None:
        response = auth_client.get("/tasks/new")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Create task
# ---------------------------------------------------------------------------


class TestTaskCreate:
    def test_bureau_can_create_task(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        response = bureau_client.post(
            "/tasks/new",
            data={
                "title": "Commander des pièces de rechange",
                "description": "Flipper droit HS",
                "priority": "high",
                "status": "open",
                "assigned_to_id": "",
                "due_date": "",
            },
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            t = _db.session.execute(
                _db.select(Task).filter_by(title="Commander des pièces de rechange")
            ).scalar_one_or_none()
            assert t is not None
            assert t.priority == TaskPriority.HIGH
            assert t.status == TaskStatus.OPEN
            assert t.source == TaskSource.MANUAL

    def test_creating_task_as_done_sets_completed_at(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        bureau_client.post(
            "/tasks/new",
            data={
                "title": "Tâche déjà terminée",
                "description": "",
                "priority": "normal",
                "status": "done",
                "assigned_to_id": "",
                "due_date": "",
            },
        )
        with app.app_context():
            t = _db.session.execute(
                _db.select(Task).filter_by(title="Tâche déjà terminée")
            ).scalar_one()
            assert t.completed_at is not None


# ---------------------------------------------------------------------------
# Self-assign (claim)
# ---------------------------------------------------------------------------


class TestTaskClaim:
    def test_member_can_claim_unassigned_task(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        response = auth_client.post(
            f"/tasks/{tid}/claim",
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            t = _db.session.get(Task, tid)
            assert t.assigned_to_id == member_user.id
            assert t.status == TaskStatus.IN_PROGRESS

    def test_cannot_claim_already_assigned_task(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id, assigned_to_id=bureau_user.id)
        auth_client.post(f"/tasks/{tid}/claim")
        with app.app_context():
            t = _db.session.get(Task, tid)
            assert t.assigned_to_id == bureau_user.id  # unchanged

    def test_cannot_claim_done_task(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id, status="done")
        auth_client.post(f"/tasks/{tid}/claim")
        with app.app_context():
            t = _db.session.get(Task, tid)
            assert t.assigned_to_id is None  # unchanged


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


class TestTaskStatusTransition:
    def test_bureau_can_change_status(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        bureau_client.post(
            f"/tasks/{tid}/status",
            data={"status": "done"},
        )
        with app.app_context():
            t = _db.session.get(Task, tid)
            assert t.status == TaskStatus.DONE
            assert t.completed_at is not None

    def test_reopening_task_clears_completed_at(
        self, app: Flask, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id, status="done")
        bureau_client.post(f"/tasks/{tid}/status", data={"status": "open"})
        with app.app_context():
            t = _db.session.get(Task, tid)
            assert t.status == TaskStatus.OPEN
            assert t.completed_at is None

    def test_member_cannot_change_status(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        response = auth_client.post(f"/tasks/{tid}/status", data={"status": "done"})
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestTaskComments:
    def test_member_can_post_comment(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        response = auth_client.post(
            f"/tasks/{tid}/comments",
            data={"body": "Je regarde ça demain matin."},
            follow_redirects=False,
        )
        assert response.status_code in {301, 302}
        with app.app_context():
            comment = _db.session.execute(
                _db.select(TaskComment).where(TaskComment.task_id == tid)
            ).scalar_one_or_none()
            assert comment is not None
            assert comment.author_id == member_user.id
            assert "demain" in comment.body

    def test_comment_appears_on_detail_page(
        self, app: Flask, auth_client: FlaskClient, member_user: UserInfo, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        auth_client.post(
            f"/tasks/{tid}/comments",
            data={"body": "Commentaire visible"},
        )
        response = auth_client.get(f"/tasks/{tid}")
        assert "Commentaire visible" in response.data.decode()

    def test_empty_comment_is_rejected(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        tid = _make_task(app, bureau_user.id)
        auth_client.post(f"/tasks/{tid}/comments", data={"body": ""})
        with app.app_context():
            count = _db.session.execute(
                _db.select(_db.func.count(TaskComment.id)).where(TaskComment.task_id == tid)
            ).scalar()
            assert count == 0


# ---------------------------------------------------------------------------
# Priority ordering on list
# ---------------------------------------------------------------------------


class TestTaskOrdering:
    def test_urgent_tasks_appear_before_normal(
        self, app: Flask, auth_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        _make_task(app, bureau_user.id, title="Tâche normale", priority="normal")
        _make_task(app, bureau_user.id, title="Tâche urgente", priority="urgent")
        response = auth_client.get("/tasks/")
        body = response.data.decode()
        assert body.index("urgente") < body.index("normale")
