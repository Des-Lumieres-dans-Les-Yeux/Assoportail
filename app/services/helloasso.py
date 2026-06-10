"""HelloAsso API client and membership synchronisation service.

HelloAsso is the French payment platform used by associations.
This module provides:
  - Pydantic schemas for HelloAsso API v5 response payloads
  - A thin HTTP client (HelloAssoClient) for the REST API
  - sync_helloasso_memberships(): fetches orders and upserts local Membership rows

Usage (from a Celery task or Flask shell)::

    from flask import current_app
    from app.services.helloasso import sync_helloasso_memberships

    created = sync_helloasso_memberships(
        api_token=current_app.config["HELLOASSO_API_TOKEN"],
        org_slug=current_app.config["HELLOASSO_ORGANIZATION_SLUG"],
    )
"""

import logging
import secrets
import string
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extensions import db
from app.models.member import Membership, MembershipSource
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

MEMBERSHIP_DURATION_DAYS = 365


# ---------------------------------------------------------------------------
# Pydantic schemas — HelloAsso API v5 shapes
# ---------------------------------------------------------------------------


class HelloAssoPayer(BaseModel):
    """Payer info embedded in a HelloAsso order."""

    model_config = ConfigDict(populate_by_name=True)

    email: str
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")


class HelloAssoOrderRef(BaseModel):
    """Order reference embedded in a HelloAsso item."""

    id: int
    date: datetime
    payer: HelloAssoPayer


class HelloAssoItemUser(BaseModel):
    """Beneficiary user attached to a HelloAsso order item."""

    model_config = ConfigDict(populate_by_name=True)

    email: str
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")


class HelloAssoItem(BaseModel):
    """A single purchasable item within a HelloAsso order.

    Attributes:
        id: Item identifier (unique within HelloAsso).
        amount: Price in centimes (e.g. 1500 = 15.00 €).
        state: Processing state — ``"Processed"``, ``"Refunded"``, ``"Cancelled"``, …
        type: Item type — ``"Membership"``, ``"Donation"``, ``"Payment"``, …
        order: Reference to the parent order.
        user: Beneficiary user (may differ from the payer).
    """

    id: int
    amount: int
    state: str
    type: str
    order: HelloAssoOrderRef
    user: HelloAssoItemUser | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def amount_non_negative(cls, v: int) -> int:
        """Reject negative amounts — HelloAsso should never send them."""
        if v < 0:
            raise ValueError("item amount must be non-negative")
        return v


class HelloAssoPage(BaseModel):
    """One page of HelloAsso order items."""

    data: list[HelloAssoItem] = Field(default_factory=list)
    total_pages: int = Field(1, alias="totalPages")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class HelloAssoClient:
    """Thin HTTP client for HelloAsso API v5.

    Attributes:
        BASE_URL: Root URL of the HelloAsso REST API.
        TIMEOUT: Request timeout in seconds.
    """

    BASE_URL = "https://api.helloasso.com/v5"
    TIMEOUT = 15.0

    def __init__(self, api_token: str, org_slug: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
        }
        self._org_slug = org_slug

    def get_membership_items(
        self,
        page_index: int = 1,
        page_size: int = 20,
        from_date: datetime | None = None,
    ) -> HelloAssoPage:
        """Fetch one page of membership order items from HelloAsso.

        Args:
            page_index: 1-based page number.
            page_size: Items per page (max 100).
            from_date: Only return items on or after this UTC datetime.

        Returns:
            Parsed HelloAssoPage containing items and total page count.

        Raises:
            httpx.HTTPStatusError: On non-2xx API responses.
        """
        params: dict[str, object] = {
            "pageIndex": page_index,
            "pageSize": page_size,
            "formType": "Membership",
        }
        if from_date:
            params["from"] = from_date.strftime("%Y-%m-%dT%H:%M:%S")

        url = f"{self.BASE_URL}/organizations/{self._org_slug}/orders"

        with httpx.Client(timeout=self.TIMEOUT) as client:
            response = client.get(url, headers=self._headers, params=params)
            response.raise_for_status()
            raw = response.json()

        # The HelloAsso API nests items inside each order object.
        # Flatten them here so callers get a simple list.
        flat_items: list[dict] = []
        pagination = raw.get("pagination", {})

        for order_data in raw.get("data", []):
            order_ref = {
                "id": order_data["id"],
                "date": order_data["date"],
                "payer": order_data.get("payer", {}),
            }
            for item_data in order_data.get("items", []):
                flat_items.append({**item_data, "order": order_ref})

        return HelloAssoPage(
            data=[HelloAssoItem(**i) for i in flat_items],
            totalPages=pagination.get("totalPages", 1),
        )


# ---------------------------------------------------------------------------
# Sync service
# ---------------------------------------------------------------------------


def sync_helloasso_memberships(
    api_token: str,
    org_slug: str,
    from_date: datetime | None = None,
) -> int:
    """Fetch HelloAsso membership orders and create local Membership records.

    Processes only items of type ``"Membership"`` in state ``"Processed"``.
    Uses ``helloasso_order_id`` for deduplication — already-imported orders
    are silently skipped.

    If the beneficiary email is not in the database, a new User is created
    automatically with a random password (the user must reset it on first login).

    Args:
        api_token: HelloAsso API bearer token (from ``.env``).
        org_slug: Organisation slug (from ``.env``).
        from_date: Lower bound for order date filtering. Defaults to 30 days ago.

    Returns:
        Number of new Membership records created in this run.
    """
    if from_date is None:
        from_date = datetime.now(UTC) - timedelta(days=30)

    client = HelloAssoClient(api_token, org_slug)
    created = 0
    page = 1

    while True:
        try:
            page_data = client.get_membership_items(
                page_index=page, page_size=20, from_date=from_date
            )
        except httpx.HTTPStatusError as exc:
            logger.error("HelloAsso API error (page %d): %s", page, exc)
            break
        except Exception:
            logger.exception("Unexpected error fetching HelloAsso page %d", page)
            break

        for item in page_data.data:
            if item.type != "Membership" or item.state != "Processed":
                continue

            order_id = str(item.order.id)

            existing = db.session.execute(
                db.select(Membership).filter_by(helloasso_order_id=order_id)
            ).scalar_one_or_none()
            if existing:
                continue

            email = (item.user.email if item.user else item.order.payer.email).lower()

            user = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()

            if not user:
                first = item.user.first_name if item.user else item.order.payer.first_name
                last = item.user.last_name if item.user else item.order.payer.last_name
                user = User(
                    email=email,
                    first_name=first,
                    last_name=last,
                    role=UserRole.MEMBER,
                    is_active=True,
                )
                user.set_password(_random_password())
                db.session.add(user)
                db.session.flush()  # populate user.id before the FK insert

            started = item.order.date.date()
            membership = Membership(
                user_id=user.id,
                source=MembershipSource.HELLOASSO,
                amount=Decimal(item.amount) / 100,
                started_at=started,
                expires_at=started + timedelta(days=MEMBERSHIP_DURATION_DAYS),
                helloasso_order_id=order_id,
                is_pending=False,
            )
            db.session.add(membership)
            created += 1
            logger.info("Imported HelloAsso membership for %s (order %s)", email, order_id)

        if page >= page_data.total_pages:
            break
        page += 1

    db.session.commit()
    return created


def _random_password() -> str:
    """Generate a cryptographically random 20-character password.

    Used when auto-creating a User from HelloAsso data.
    The user must reset their password before logging in.
    """
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return "".join(secrets.choice(alphabet) for _ in range(20))
