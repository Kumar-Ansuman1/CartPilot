import pytest
from pydantic import ValidationError

from backend.app.catalog import Catalog, load_catalog
from backend.app.catalog_search import search_catalog
from backend.app.models import Quote, ShoppingRequest
from backend.app.quote_service import create_quote
from backend.app.recommender import (
    recommend_base_product,
    recommend_cross_sell,
)


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture
def shopping_request() -> ShoppingRequest:
    return ShoppingRequest(
        query="30W GaN fast charger",
        budget_paise=200000,
        allowed_categories=["chargers", "cables"],
        compatibility_tags=["usb-c"],
    )


def test_catalog_loads_and_filters_unavailable(
    catalog: Catalog,
) -> None:
    assert len(catalog.products) == 13
    assert len(catalog.available_products()) == 12

    available_skus = {
        product.sku
        for product in catalog.available_products()
    }

    assert "CHG-45W-001" not in available_skus


def test_catalog_lookup_is_case_insensitive(
    catalog: Catalog,
) -> None:
    product = catalog.get_product("chg-30w-001")

    assert product is not None
    assert product.sku == "CHG-30W-001"


def test_search_applies_budget_and_availability(
    catalog: Catalog,
) -> None:
    results = search_catalog(
        catalog=catalog,
        query="compact fast charger",
        max_price_paise=150000,
        category="chargers",
        compatibility_tags=["usb-c"],
    )

    assert [product.sku for product in results] == [
        "CHG-20W-001",
        "CHG-30W-001",
    ]


def test_confirmation_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError):
        ShoppingRequest(
            query="Buy a charger",
            budget_paise=200000,
            confirmation_required=False,
        )


def test_base_product_recommendation(
    catalog: Catalog,
    shopping_request: ShoppingRequest,
) -> None:
    product = recommend_base_product(
        catalog,
        shopping_request,
    )

    assert product is not None
    assert product.sku == "CHG-30W-001"
    assert product.price_paise <= shopping_request.budget_paise


def test_impossible_request_returns_no_product(
    catalog: Catalog,
) -> None:
    request = ShoppingRequest(
        query="iPhone 15 protective case",
        budget_paise=50000,
        allowed_categories=["cases"],
        compatibility_tags=["iphone-15"],
    )

    assert recommend_base_product(catalog, request) is None


def test_cross_sell_stays_within_mandate(
    catalog: Catalog,
    shopping_request: ShoppingRequest,
) -> None:
    base_product = recommend_base_product(
        catalog,
        shopping_request,
    )

    assert base_product is not None

    cross_sell = recommend_cross_sell(
        catalog,
        shopping_request,
        base_product,
    )

    assert cross_sell is not None
    assert cross_sell.sku == "CBL-C2C-001"

    final_total = (
        base_product.price_paise
        + cross_sell.price_paise
    )

    assert final_total <= shopping_request.budget_paise


def test_cross_sell_is_rejected_when_limit_is_too_low(
    catalog: Catalog,
) -> None:
    request = ShoppingRequest(
        query="30W GaN fast charger",
        budget_paise=160000,
        allowed_categories=["chargers", "cables"],
        compatibility_tags=["usb-c"],
    )

    base_product = recommend_base_product(catalog, request)

    assert base_product is not None
    assert recommend_cross_sell(
        catalog,
        request,
        base_product,
    ) is None


def test_quote_uses_trusted_catalogue_prices(
    catalog: Catalog,
    shopping_request: ShoppingRequest,
) -> None:
    quote = create_quote(
        catalog=catalog,
        request=shopping_request,
        base_product_sku="CHG-30W-001",
        upsell_product_sku="CBL-C2C-001",
    )

    assert quote.base_price_paise == 129900
    assert quote.upsell_price_paise == 39900
    assert quote.total_paise == 169800
    assert quote.catalog_version == "1.0.0"
    assert quote.expires_at > quote.created_at


def test_quote_rejects_unapproved_cross_sell(
    catalog: Catalog,
    shopping_request: ShoppingRequest,
) -> None:
    with pytest.raises(
        ValueError,
        match="not an approved cross-sell",
    ):
        create_quote(
            catalog=catalog,
            request=shopping_request,
            base_product_sku="CHG-30W-001",
            upsell_product_sku="MNT-CAR-001",
        )


def test_quote_cannot_be_modified(
    catalog: Catalog,
    shopping_request: ShoppingRequest,
) -> None:
    quote: Quote = create_quote(
        catalog=catalog,
        request=shopping_request,
        base_product_sku="CHG-30W-001",
        upsell_product_sku="CBL-C2C-001",
    )

    with pytest.raises(ValidationError):
        quote.total_paise = 100