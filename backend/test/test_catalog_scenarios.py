from backend.app.catalog import load_catalog
from backend.app.models import ShoppingRequest
from backend.app.recommender import (
    recommend_base_products,
    recommend_cross_sell_options,
)


def test_charger_search_returns_three_ranked_options() -> None:
    catalog = load_catalog()

    request = ShoppingRequest(
        query="usb-c fast charger",
        budget_paise=150_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )

    products = recommend_base_products(
        catalog=catalog,
        request=request,
        limit=3,
    )

    assert [
        product.sku
        for product in products
    ] == [
        "CHG-20W-001",
        "CHG-25W-001",
        "CHG-30W-001",
    ]

    assert all(
        product.active
        and product.stock > 0
        and product.price_paise
        <= request.budget_paise
        for product in products
    )


def test_iphone_case_search_returns_three_options() -> None:
    catalog = load_catalog()

    request = ShoppingRequest(
        query="iphone 15 protective case",
        budget_paise=150_000,
        allowed_categories=["cases"],
        compatibility_tags=["iphone-15"],
    )

    products = recommend_base_products(
        catalog=catalog,
        request=request,
        limit=3,
    )

    assert [
        product.sku
        for product in products
    ] == [
        "CASE-IP15-002",
        "CASE-IP15-001",
        "CASE-IP15-003",
    ]


def test_charger_can_offer_two_cross_category_cables() -> None:
    catalog = load_catalog()

    request = ShoppingRequest(
        query="usb-c fast charger",
        budget_paise=200_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )

    base_product = catalog.get_product(
        "CHG-20W-001"
    )

    assert base_product is not None

    cross_sell_options = (
        recommend_cross_sell_options(
            catalog=catalog,
            request=request,
            base_product=base_product,
            limit=2,
        )
    )

    assert [
        product.sku
        for product in cross_sell_options
    ] == [
        "CBL-C2C-002",
        "CBL-C2C-001",
    ]

    assert all(
        product.category == "cables"
        for product in cross_sell_options
    )

    cross_sell_limit = (
        request.budget_paise
        * request.max_upsell_percentage
        // 100
    )

    assert all(
        product.price_paise
        <= cross_sell_limit
        and (
            base_product.price_paise
            + product.price_paise
            <= request.budget_paise
        )
        for product in cross_sell_options
    )


def test_low_cross_sell_limit_returns_no_option() -> None:
    catalog = load_catalog()

    request = ShoppingRequest(
        query="30w gan fast charger",
        budget_paise=160_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )

    base_product = catalog.get_product(
        "CHG-30W-001"
    )

    assert base_product is not None

    cross_sell_options = (
        recommend_cross_sell_options(
            catalog=catalog,
            request=request,
            base_product=base_product,
            limit=2,
        )
    )

    assert cross_sell_options == []


def test_unavailable_chargers_are_never_offered() -> None:
    catalog = load_catalog()

    request = ShoppingRequest(
        query="usb-c charger",
        budget_paise=300_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )

    products = recommend_base_products(
        catalog=catalog,
        request=request,
        limit=5,
    )

    offered_skus = {
        product.sku
        for product in products
    }

    assert "CHG-45W-001" not in offered_skus
    assert "CHG-25W-OLD" not in offered_skus

    assert all(
        product.active
        and product.stock > 0
        for product in products
    )
    