import re

import pytest

from backend.app.catalog import Catalog
from backend.app.models import Product, ShoppingRequest
from backend.app.quote_service import create_quote


def _catalog() -> Catalog:
    product = Product(
        sku="CHARGER-001",
        name="USB-C Charger",
        description="A compact USB-C wall charger.",
        category="chargers",
        price_paise=100_000,
        stock=10,
        tags=["charger"],
        compatibility_tags=["usb-c"],
        cross_sell_skus=[],
        active=True,
    )

    return Catalog(
        merchant_id="voltcart",
        merchant_name="VoltCart",
        catalog_version="test-v1",
        currency="INR",
        products={product.sku: product},
    )


def _request() -> ShoppingRequest:
    return ShoppingRequest(
        query="USB-C charger",
        budget_paise=200_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def test_same_session_produces_same_quote_id() -> None:
    session_id = (
        "session_00000000000000000000000000000001"
    )

    first_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
        session_id=session_id,
    )

    second_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
        session_id=session_id,
    )

    assert first_quote.quote_id == second_quote.quote_id
    assert first_quote.quote_id == (
        "quote_00000000000000000000000000000001"
    )


def test_different_sessions_produce_different_quote_ids() -> None:
    first_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
        session_id=(
            "session_00000000000000000000000000000001"
        ),
    )

    second_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
        session_id=(
            "session_00000000000000000000000000000002"
        ),
    )

    assert first_quote.quote_id != second_quote.quote_id


def test_legacy_quote_ids_remain_random() -> None:
    first_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
    )

    second_quote = create_quote(
        catalog=_catalog(),
        request=_request(),
        base_product_sku="CHARGER-001",
    )

    assert re.fullmatch(
        r"quote_[0-9a-f]{32}",
        first_quote.quote_id,
    )
    assert first_quote.quote_id != second_quote.quote_id


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        "session_invalid",
        "quote_00000000000000000000000000000001",
        "session_1234",
    ],
)
def test_invalid_session_id_is_rejected(
    session_id: str,
) -> None:
    with pytest.raises(
        ValueError,
        match="valid shopping-session ID",
    ):
        create_quote(
            catalog=_catalog(),
            request=_request(),
            base_product_sku="CHARGER-001",
            session_id=session_id,
        )

def _catalog_with_cross_sell(
    *,
    cable_compatibility_tags: list[str] | None = None,
) -> Catalog:
    charger = Product(
        sku="CHARGER-001",
        name="USB-C Charger",
        description="A compact USB-C wall charger.",
        category="chargers",
        price_paise=100_000,
        stock=10,
        tags=["charger"],
        compatibility_tags=["usb-c"],
        cross_sell_skus=["CABLE-001"],
        active=True,
    )

    cable = Product(
        sku="CABLE-001",
        name="USB-C Cable",
        description="A compatible USB-C charging cable.",
        category="cables",
        price_paise=30_000,
        stock=10,
        tags=["cable"],
        compatibility_tags=(
            cable_compatibility_tags
            if cable_compatibility_tags is not None
            else ["usb-c"]
        ),
        cross_sell_skus=[],
        active=True,
    )

    return Catalog(
        merchant_id="voltcart",
        merchant_name="VoltCart",
        catalog_version="test-v1",
        currency="INR",
        products={
            charger.sku: charger,
            cable.sku: cable,
        },
    )


def test_approved_cross_category_product_is_allowed() -> None:
    quote = create_quote(
        catalog=_catalog_with_cross_sell(),
        request=_request(),
        base_product_sku="CHARGER-001",
        upsell_product_sku="CABLE-001",
    )

    assert quote.base_product_sku == "CHARGER-001"
    assert quote.upsell_product_sku == "CABLE-001"
    assert quote.total_paise == 130_000


def test_cross_sell_is_not_added_automatically() -> None:
    quote = create_quote(
        catalog=_catalog_with_cross_sell(),
        request=_request(),
        base_product_sku="CHARGER-001",
    )

    assert quote.upsell_product_sku is None
    assert quote.upsell_price_paise == 0
    assert quote.total_paise == 100_000


def test_cross_category_product_must_still_be_compatible() -> None:
    with pytest.raises(
        ValueError,
        match="compatibility requirements",
    ):
        create_quote(
            catalog=_catalog_with_cross_sell(
                cable_compatibility_tags=["lightning"]
            ),
            request=_request(),
            base_product_sku="CHARGER-001",
            upsell_product_sku="CABLE-001",
        )