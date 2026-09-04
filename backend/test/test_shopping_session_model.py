from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.app.models import (
    ShoppingRequest,
    ShoppingSession,
)


def make_request() -> ShoppingRequest:
    return ShoppingRequest(
        query="usb-c charger",
        budget_paise=300_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def make_session(
    **overrides: object,
) -> ShoppingSession:
    created_at = datetime.now(timezone.utc)

    values = {
        "session_id": (
            "session_1234567890abcdef1234567890abcdef"
        ),
        "catalog_version": "test-v1",
        "request": make_request(),
        "base_product_skus": [
            "CHG-001",
            "CHG-002",
            "CHG-003",
        ],
        "selected_base_product_sku": None,
        "cross_sell_option_skus": [],
        "status": "awaiting_base_selection",
        "quote_id": None,
        "created_at": created_at,
        "expires_at": created_at + timedelta(minutes=10),
    }

    values.update(overrides)

    return ShoppingSession.model_validate(values)


def test_creates_session_awaiting_base_selection() -> None:
    session = make_session()

    assert session.status == "awaiting_base_selection"
    assert session.selected_base_product_sku is None
    assert len(session.base_product_skus) == 3


def test_rejects_more_than_three_base_options() -> None:
    with pytest.raises(ValidationError):
        make_session(
            base_product_skus=[
                "CHG-001",
                "CHG-002",
                "CHG-003",
                "CHG-004",
            ]
        )


def test_selected_base_must_be_an_offered_product() -> None:
    with pytest.raises(
        ValidationError,
        match="Selected base product must be one",
    ):
        make_session(
            selected_base_product_sku="CHG-999",
            status="awaiting_cross_sell_decision",
        )


def test_cross_sell_stage_requires_base_selection() -> None:
    with pytest.raises(
        ValidationError,
        match="requires a base product",
    ):
        make_session(
            status="awaiting_cross_sell_decision",
        )


def test_quote_created_requires_quote_id() -> None:
    with pytest.raises(
        ValidationError,
        match="requires a quote ID",
    ):
        make_session(
            selected_base_product_sku="CHG-001",
            status="quote_created",
        )


def test_accepts_valid_cross_sell_stage() -> None:
    session = make_session(
        selected_base_product_sku="CHG-001",
        cross_sell_option_skus=[
            "CBL-001",
            "CBL-002",
        ],
        status="awaiting_cross_sell_decision",
    )

    assert session.selected_base_product_sku == "CHG-001"
    assert session.cross_sell_option_skus == [
        "CBL-001",
        "CBL-002",
    ]