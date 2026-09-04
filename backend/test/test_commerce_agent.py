from unittest.mock import patch

from backend.app.commerce_agent import run_commerce_agent
from backend.app.models import ExtractedShoppingIntent


def test_clarification_stops_before_catalog_search():
    intent = ExtractedShoppingIntent(
        search_query="protective phone case",
        budget_rupees=None,
        requested_categories=["cases"],
        compatibility_tags=[],
        needs_clarification=True,
        clarification_question="What is your phone model and budget?",
    )

    with (
        patch(
            "backend.app.commerce_agent.extract_shopping_intent",
            return_value=intent,
        ),
        patch(
            "backend.app.commerce_agent.load_catalog",
        ) as mock_load_catalog,
    ):
        result = run_commerce_agent("I need a phone case")

    assert result.status == "clarification_required"
    assert result.quote is None
    assert result.base_product is None
    mock_load_catalog.assert_not_called()


def test_returns_no_match_when_budget_is_too_low():
    intent = ExtractedShoppingIntent(
        search_query="USB-C charger",
        budget_rupees=1,
        requested_categories=["chargers"],
        compatibility_tags=["usb-c"],
        needs_clarification=False,
        clarification_question=None,
    )

    with patch(
        "backend.app.commerce_agent.extract_shopping_intent",
        return_value=intent,
    ):
        result = run_commerce_agent("USB-C charger under one rupee")

    assert result.status == "no_match"
    assert result.quote is None
    assert result.base_product is None


def test_creates_quote_without_initiating_payment():
    intent = ExtractedShoppingIntent(
        search_query="USB-C fast charger",
        budget_rupees=5000,
        requested_categories=["chargers"],
        compatibility_tags=["usb-c"],
        needs_clarification=False,
        clarification_question=None,
    )

    with patch(
        "backend.app.commerce_agent.extract_shopping_intent",
        return_value=intent,
    ):
        result = run_commerce_agent(
            "I need a USB-C charger under 5000 rupees"
        )

    assert result.status == "quote_ready"
    assert result.base_product is not None
    assert result.quote is not None
    assert result.quote.base_product_sku == result.base_product.sku
    assert result.quote.total_paise <= 500000
    assert any(
        "Payment was not initiated" in step
        for step in result.decision_trace
    )