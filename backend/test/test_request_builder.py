import pytest

from backend.app.models import ExtractedShoppingIntent
from backend.app.request_builder import build_shopping_request


def test_builds_request_and_converts_rupees_to_paise():
    intent = ExtractedShoppingIntent(
        search_query="USB-C charger",
        budget_rupees=2000,
        requested_categories=["chargers"],
        compatibility_tags=["android", "usb-c"],
        needs_clarification=False,
        clarification_question=None,
    )

    request = build_shopping_request(intent)

    assert request.query == "USB-C charger"
    assert request.budget_paise == 200000
    assert request.allowed_categories == ["chargers"]
    assert request.compatibility_tags == ["android", "usb-c"]
    assert request.confirmation_required is True


def test_rejects_intent_that_needs_clarification():
    intent = ExtractedShoppingIntent(
        search_query="protective phone case",
        budget_rupees=None,
        requested_categories=["cases"],
        compatibility_tags=[],
        needs_clarification=True,
        clarification_question="What is your phone model and budget?",
    )

    with pytest.raises(
        ValueError,
        match="clarification is required",
    ):
        build_shopping_request(intent)