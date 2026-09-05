import pytest

from backend.app.catalog import Catalog
from backend.app.models import Product, ShoppingRequest
from backend.app.recommender import (
    recommend_base_product,
    recommend_base_products,
    recommend_cross_sell_options,
)


def make_product(
    *,
    sku: str,
    price_paise: int,
    name: str,
    category: str = "chargers",
    stock: int = 5,
    active: bool = True,
    compatibility_tags: list[str] | None = None,
    cross_sell_skus: list[str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        description="A valid product used for recommender testing.",
        category=category,
        price_paise=price_paise,
        stock=stock,
        tags=[],
        compatibility_tags=(
            compatibility_tags
            if compatibility_tags is not None
            else ["usb-c"]
        ),
        cross_sell_skus=(
            cross_sell_skus
            if cross_sell_skus is not None
            else []
        ),
        active=active,
    )


def make_catalog(
    products: list[Product],
) -> Catalog:
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


def make_request(
    *,
    budget_paise: int = 300_000,
) -> ShoppingRequest:
    return ShoppingRequest(
        query="usb-c charger",
        budget_paise=budget_paise,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def test_returns_top_three_base_products() -> None:
    products = [
        make_product(
            sku="CHG-004",
            name="USB-C Charger Delta",
            price_paise=210_000,
        ),
        make_product(
            sku="CHG-002",
            name="USB-C Charger Beta",
            price_paise=150_000,
        ),
        make_product(
            sku="CHG-001",
            name="USB-C Charger Alpha",
            price_paise=120_000,
        ),
        make_product(
            sku="CHG-003",
            name="USB-C Charger Gamma",
            price_paise=180_000,
        ),
    ]

    options = recommend_base_products(
        catalog=make_catalog(products),
        request=make_request(),
    )

    assert [
        product.sku for product in options
    ] == [
        "CHG-001",
        "CHG-002",
        "CHG-003",
    ]


def test_empty_catalog_returns_no_base_products() -> None:
    catalog = make_catalog([])
    request = make_request()

    assert recommend_base_products(
        catalog,
        request,
    ) == []

    assert recommend_base_product(
        catalog,
        request,
    ) is None


def test_base_options_exclude_unavailable_and_expensive_products() -> None:
    products = [
        make_product(
            sku="CHG-VALID",
            name="USB-C Charger Valid",
            price_paise=150_000,
        ),
        make_product(
            sku="CHG-STOCK",
            name="USB-C Charger Out Of Stock",
            price_paise=100_000,
            stock=0,
        ),
        make_product(
            sku="CHG-OFF",
            name="USB-C Charger Inactive",
            price_paise=100_000,
            active=False,
        ),
        make_product(
            sku="CHG-HIGH",
            name="USB-C Charger Expensive",
            price_paise=250_000,
        ),
    ]

    options = recommend_base_products(
        catalog=make_catalog(products),
        request=make_request(
            budget_paise=200_000,
        ),
    )

    assert [
        product.sku for product in options
    ] == ["CHG-VALID"]


def test_cross_sell_options_allow_cross_category_products() -> None:
    base_product = make_product(
        sku="CHG-001",
        name="USB-C Charger",
        price_paise=200_000,
        cross_sell_skus=[
            "CBL-003",
            "CBL-001",
            "CBL-002",
        ],
    )

    products = [
        base_product,
        make_product(
            sku="CBL-001",
            name="USB-C Cable Basic",
            category="cables",
            price_paise=30_000,
        ),
        make_product(
            sku="CBL-002",
            name="USB-C Cable Plus",
            category="cables",
            price_paise=50_000,
        ),
        make_product(
            sku="CBL-003",
            name="USB-C Cable Premium",
            category="cables",
            price_paise=70_000,
        ),
    ]

    options = recommend_cross_sell_options(
        catalog=make_catalog(products),
        request=make_request(
            budget_paise=400_000,
        ),
        base_product=base_product,
    )

    assert [
        product.sku for product in options
    ] == [
        "CBL-001",
        "CBL-002",
    ]


def test_cross_sell_options_apply_safety_filters() -> None:
    base_product = make_product(
        sku="CHG-001",
        name="USB-C Charger",
        price_paise=250_000,
        cross_sell_skus=[
            "CBL-VALID",
            "CBL-HIGH",
            "CBL-STOCK",
            "CBL-OFF",
            "CBL-WRONG",
        ],
    )

    products = [
        base_product,
        make_product(
            sku="CBL-VALID",
            name="USB-C Cable Valid",
            category="cables",
            price_paise=40_000,
        ),
        make_product(
            sku="CBL-HIGH",
            name="USB-C Cable Expensive",
            category="cables",
            price_paise=60_000,
        ),
        make_product(
            sku="CBL-STOCK",
            name="USB-C Cable Out Of Stock",
            category="cables",
            price_paise=20_000,
            stock=0,
        ),
        make_product(
            sku="CBL-OFF",
            name="USB-C Cable Inactive",
            category="cables",
            price_paise=20_000,
            active=False,
        ),
        make_product(
            sku="CBL-WRONG",
            name="Lightning Cable",
            category="cables",
            price_paise=10_000,
            compatibility_tags=["lightning"],
        ),
    ]

    options = recommend_cross_sell_options(
        catalog=make_catalog(products),
        request=make_request(
            budget_paise=300_000,
        ),
        base_product=base_product,
    )

    assert [
        product.sku for product in options
    ] == ["CBL-VALID"]


def test_option_limits_are_bounded() -> None:
    catalog = make_catalog([])
    request = make_request()

    with pytest.raises(
        ValueError,
        match="Base-product option limit",
    ):
        recommend_base_products(
            catalog,
            request,
            limit=0,
        )

    with pytest.raises(
        ValueError,
        match="Cross-sell option limit",
    ):
        recommend_cross_sell_options(
            catalog,
            request,
            make_product(
                sku="CHG-001",
                name="USB-C Charger",
                price_paise=100_000,
            ),
            limit=3,
        )