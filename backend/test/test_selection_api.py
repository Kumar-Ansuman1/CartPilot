from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import ProductOption, Quote
from backend.app.selection_service import (
    BaseProductUnavailableError,
    BaseSelectionResult,
    CatalogVersionChangedError,
    CrossSellDecisionResult,
    SelectedProductsUnavailableError,
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

QUOTE_ID = (
    "quote_1234567890abcdef1234567890abcdef"
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


def make_cross_sell_result(
    *,
    accepted: bool,
) -> CrossSellDecisionResult:
    created_at = datetime.now(timezone.utc)

    upsell_product_sku = (
        "CBL-001"
        if accepted
        else None
    )
    upsell_price_paise = (
        30_000
        if accepted
        else 0
    )

    quote = Quote(
        quote_id=QUOTE_ID,
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-001",
        base_price_paise=200_000,
        upsell_product_sku=upsell_product_sku,
        upsell_price_paise=upsell_price_paise,
        total_paise=(
            200_000 + upsell_price_paise
        ),
        created_at=created_at,
        expires_at=(
            created_at + timedelta(minutes=5)
        ),
    )

    return CrossSellDecisionResult(
        status="quote_ready",
        message=(
            "Review the quote and explicitly "
            "confirm before checkout."
        ),
        session_id=SESSION_ID,
        cross_sell_decision=(
            "accepted"
            if accepted
            else "declined"
        ),
        quote=quote,
        decision_trace=[
            "The buyer made an explicit decision."
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


def test_cross_sell_endpoint_accepts_offered_product() -> None:
    with patch(
        "backend.app.main.finalize_cross_sell_decision",
        return_value=make_cross_sell_result(
            accepted=True
        ),
    ) as mocked_service:
        response = client.post(
            "/api/shop/select-cross-sell",
            json={
                "session_id": SESSION_ID,
                "decision": "accept",
                "cross_sell_product_sku": "CBL-001",
            },
        )

    assert response.status_code == 200

    response_data = response.json()

    assert response_data["status"] == "quote_ready"
    assert (
        response_data["cross_sell_decision"]
        == "accepted"
    )
    assert (
        response_data["quote"]["upsell_product_sku"]
        == "CBL-001"
    )
    assert (
        response_data["quote"]["total_paise"]
        == 230_000
    )

    mocked_service.assert_called_once_with(
        session_id=SESSION_ID,
        decision="accept",
        cross_sell_product_sku="CBL-001",
    )


def test_cross_sell_endpoint_allows_explicit_decline() -> None:
    with patch(
        "backend.app.main.finalize_cross_sell_decision",
        return_value=make_cross_sell_result(
            accepted=False
        ),
    ) as mocked_service:
        response = client.post(
            "/api/shop/select-cross-sell",
            json={
                "session_id": SESSION_ID,
                "decision": "decline",
                "cross_sell_product_sku": None,
            },
        )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["cross_sell_decision"]
        == "declined"
    )
    assert (
        response_data["quote"]["upsell_product_sku"]
        is None
    )
    assert (
        response_data["quote"]["total_paise"]
        == 200_000
    )

    mocked_service.assert_called_once_with(
        session_id=SESSION_ID,
        decision="decline",
        cross_sell_product_sku=None,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "session_id": SESSION_ID,
            "decision": "accept",
        },
        {
            "session_id": SESSION_ID,
            "decision": "decline",
            "cross_sell_product_sku": "CBL-001",
        },
        {
            "session_id": SESSION_ID,
            "decision": "maybe",
        },
    ],
)
def test_cross_sell_endpoint_rejects_invalid_payload(
    payload: dict[str, object],
) -> None:
    with patch(
        "backend.app.main.finalize_cross_sell_decision"
    ) as mocked_service:
        response = client.post(
            "/api/shop/select-cross-sell",
            json=payload,
        )

    assert response.status_code == 422
    mocked_service.assert_not_called()


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
            SelectedProductsUnavailableError(
                "Product unavailable."
            ),
            409,
        ),
        (
            ShoppingSessionStateError(
                "Invalid session state."
            ),
            409,
        ),
        (
            ValueError(
                "Invalid decision."
            ),
            422,
        ),
    ],
)
def test_cross_sell_endpoint_maps_service_errors(
    service_error: Exception,
    expected_status: int,
) -> None:
    with patch(
        "backend.app.main.finalize_cross_sell_decision",
        side_effect=service_error,
    ):
        response = client.post(
            "/api/shop/select-cross-sell",
            json={
                "session_id": SESSION_ID,
                "decision": "accept",
                "cross_sell_product_sku": "CBL-001",
            },
        )

    assert response.status_code == expected_status