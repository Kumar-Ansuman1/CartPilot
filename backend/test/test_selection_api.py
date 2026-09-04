from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import ProductOption
from backend.app.selection_service import (
    BaseProductUnavailableError,
    BaseSelectionResult,
    CatalogVersionChangedError,
)
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
)


client = TestClient(app)

SESSION_ID = (
    "session_1234567890abcdef1234567890abcdef"
)


def make_result() -> BaseSelectionResult:
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=10)
    )

    return BaseSelectionResult(
        status="cross_sell_decision_required",
        message=(
            "Choose an optional add-on or "
            "continue without one."
        ),
        session_id=SESSION_ID,
        session_expires_at=expires_at,
        selected_base_product=ProductOption(
            sku="CHG-001",
            name="USB-C Charger",
            description="A compatible USB-C charger.",
            category="chargers",
            price_paise=200_000,
            currency="INR",
            tags=["usb-c"],
            compatibility_tags=["usb-c"],
        ),
        cross_sell_options=[
            ProductOption(
                sku="CBL-001",
                name="USB-C Cable",
                description="A compatible USB-C cable.",
                category="cables",
                price_paise=30_000,
                currency="INR",
                tags=["usb-c"],
                compatibility_tags=["usb-c"],
            )
        ],
        decision_trace=[
            "The buyer selected CHG-001."
        ],
    )


def test_select_base_endpoint_returns_cross_sell_options() -> None:
    with patch(
        "backend.app.main.select_base_product",
        return_value=make_result(),
    ):
        response = client.post(
            "/api/shop/select-base",
            json={
                "session_id": SESSION_ID,
                "base_product_sku": "CHG-001",
            },
        )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["status"]
        == "cross_sell_decision_required"
    )
    assert (
        response_data["selected_base_product"]["sku"]
        == "CHG-001"
    )
    assert (
        response_data["cross_sell_options"][0]["sku"]
        == "CBL-001"
    )


@pytest.mark.parametrize(
    ("service_error", "expected_status"),
    [
        (
            ShoppingSessionNotFoundError(
                "Session not found."
            ),
            404,
        ),
        (
            ShoppingSessionExpiredError(
                "Session expired."
            ),
            410,
        ),
        (
            CatalogVersionChangedError(
                "Catalog changed."
            ),
            409,
        ),
        (
            BaseProductUnavailableError(
                "Product unavailable."
            ),
            409,
        ),
        (
            ShoppingSessionStateError(
                "Invalid state."
            ),
            409,
        ),
    ],
)
def test_select_base_endpoint_maps_service_errors(
    service_error: Exception,
    expected_status: int,
) -> None:
    with patch(
        "backend.app.main.select_base_product",
        side_effect=service_error,
    ):
        response = client.post(
            "/api/shop/select-base",
            json={
                "session_id": SESSION_ID,
                "base_product_sku": "CHG-001",
            },
        )

    assert response.status_code == expected_status


def test_select_base_endpoint_rejects_invalid_session_id() -> None:
    with patch(
        "backend.app.main.select_base_product"
    ) as mocked_service:
        response = client.post(
            "/api/shop/select-base",
            json={
                "session_id": "invalid",
                "base_product_sku": "CHG-001",
            },
        )

    assert response.status_code == 422
    mocked_service.assert_not_called()