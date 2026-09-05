from pathlib import Path

import pytest

import backend.app.commerce_agent as commerce_agent
from backend.app.catalog import Catalog
from backend.app.models import (
    ExtractedShoppingIntent,
    Product,
)
from backend.app.shopping_session_store import (
    get_shopping_session,
)


def make_product(
    *,
    sku: str,
    price_paise: int,
) -> Product:
    return Product(
        sku=sku,
        name=f"USB-C Charger {sku}",
        description="A compatible USB-C test charger.",
        category="chargers",
        price_paise=price_paise,
        stock=5,
        tags=["charger", "usb-c"],
        compatibility_tags=["usb-c"],
        cross_sell_skus=[],
        active=True,
    )


def make_catalog() -> Catalog:
    products = [
        make_product(
            sku="CHG-001",
            price_paise=100_000,
        ),
        make_product(
            sku="CHG-002",
            price_paise=150_000,
        ),
        make_product(
            sku="CHG-003",
            price_paise=200_000,
        ),
    ]

    return Catalog(
        merchant_id="voltcart",
        merchant_name="VoltCart",
        catalog_version="test-v1",
        currency="INR",
        products={
            product.sku: product
            for product in products
        },
    )


def complete_intent() -> ExtractedShoppingIntent:
    return ExtractedShoppingIntent(
        search_query="usb-c charger",
        budget_rupees=3000,
        requested_categories=["chargers"],
        compatibility_tags=["usb-c"],
        needs_clarification=False,
        clarification_question=None,
    )


def clarification_intent() -> ExtractedShoppingIntent:
    return ExtractedShoppingIntent(
        search_query="usb-c charger",
        budget_rupees=None,
        requested_categories=["chargers"],
        compatibility_tags=["usb-c"],
        needs_clarification=True,
        clarification_question=(
            "What is your maximum budget?"
        ),
    )


def test_stops_when_clarification_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        commerce_agent,
        "extract_shopping_intent",
        lambda _: clarification_intent(),
    )

    result = commerce_agent.run_commerce_agent(
        "I need a USB-C charger"
    )

    assert result.status == "clarification_required"
    assert result.session_id is None
    assert result.base_product_options == []


def test_returns_no_match_without_creating_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        commerce_agent,
        "extract_shopping_intent",
        lambda _: complete_intent(),
    )
    monkeypatch.setattr(
        commerce_agent,
        "load_catalog",
        make_catalog,
    )
    monkeypatch.setattr(
        commerce_agent,
        "recommend_base_products",
        lambda **_: [],
    )

    result = commerce_agent.run_commerce_agent(
        "USB-C charger under 3000"
    )

    assert result.status == "no_match"
    assert result.session_id is None
    assert result.base_product_options == []


def test_creates_session_with_three_base_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "commerce-agent.db"),
    )
    monkeypatch.setattr(
        commerce_agent,
        "extract_shopping_intent",
        lambda _: complete_intent(),
    )
    monkeypatch.setattr(
        commerce_agent,
        "load_catalog",
        make_catalog,
    )

    result = commerce_agent.run_commerce_agent(
        "USB-C charger under 3000"
    )

    assert result.status == "base_selection_required"
    assert result.session_id is not None
    assert len(result.base_product_options) == 3
    assert (
        result.recommended_base_product_sku
        == "CHG-001"
    )

    session = get_shopping_session(
        result.session_id
    )

    assert session is not None
    assert session.status == "awaiting_base_selection"
    assert session.base_product_skus == [
        "CHG-001",
        "CHG-002",
        "CHG-003",
    ]


def test_product_options_hide_internal_catalog_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "safe-options.db"),
    )
    monkeypatch.setattr(
        commerce_agent,
        "extract_shopping_intent",
        lambda _: complete_intent(),
    )
    monkeypatch.setattr(
        commerce_agent,
        "load_catalog",
        make_catalog,
    )

    result = commerce_agent.run_commerce_agent(
        "USB-C charger under 3000"
    )

    serialized_option = (
        result.base_product_options[0].model_dump()
    )

    assert "stock" not in serialized_option
    assert "active" not in serialized_option
    assert "cross_sell_skus" not in serialized_option