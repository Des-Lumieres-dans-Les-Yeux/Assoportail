"""Smoke tests for the social blueprint."""

from flask.testing import FlaskClient

from app.extensions import db
from app.models.social import SocialPost, SocialPostStatus
from tests.conftest import UserInfo


class TestSocialRoutes:
    """Smoke tests for the social blueprint."""

    def test_list_posts_redirects_when_unauthenticated(self, client: FlaskClient) -> None:
        """GET /social/ redirects unauthenticated users."""
        response = client.get("/social/")
        assert response.status_code in {301, 302}
        assert "/auth/login" in response.headers["Location"]

    def test_list_posts_renders_for_bureau(self, bureau_client: FlaskClient) -> None:
        """GET /social/ returns 200 for bureau users."""
        response = bureau_client.get("/social/")
        assert response.status_code == 200

    def test_detail_page_renders_for_bureau(
        self, app, bureau_client: FlaskClient, bureau_user: UserInfo
    ) -> None:
        """GET /social/<post_id> returns 200 and doesn't crash on documents.serve."""
        # Create a dummy post
        with app.app_context():
            post = SocialPost(
                title="Test Post",
                body_html="Test Content",
                status=SocialPostStatus.DRAFT,
                created_by_id=bureau_user.id,
            )
            db.session.add(post)
            db.session.commit()
            post_id = post.id

        response = bureau_client.get(f"/social/{post_id}")
        assert response.status_code == 200
