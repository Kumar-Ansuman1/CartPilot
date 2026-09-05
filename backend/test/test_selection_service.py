from pathlib import Path

import pytest

import backend.app.selection_service as selection_service
from backend.app.catalog import Catalog
from backend.app.models import (
    Product,
    ShoppingRequest,
)
from backend.app.shopping_session_store import (
    ShoppingSessionStateError,
    create_shopping_session,
    get_shopping_session,
)


def make_product(
    *,
    sku: str,
    name: str,
    category: str,
    price_paise: int,
    cross_sell_skus: list[str] | None = None,
) -> Product:
    return Product(
        sku=sku,
        name=name,
        description="A product used for selection testing.",
        category=category,
        price_paise=price_paise,
        stock=5,
        tags=["usb-c"],
        compatibility_tags=["usb-c"],
        cross_sell_skus=cross_sell_skus or [],
        active=True,
    )


def make_catalog(
    *,
    version: str = "test-v1",
) -> Catalog:
    products = [
        make_product(
            sku="CHG-001",
            name="USB-C Charger One",
            category="chargers",
            price_paise=200_000,
            cross_sell_skus=[
                "CBL-002",
                "CBL-001",
            ],
        ),
        make_product(
            sku="CHG-002",
            name="USB-C Charger Two",
            category="chargers",
            price_paise=220_000,
        ),
        make_product(
            sku="CHG-003",
            name="USB-C Charger Three",
            category="chargers",
            price_paise=240_000,
        ),
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
    ]

    return Catalog(
        merchant_id="voltcart",
        merchant_name="VoltCart",
        catalog_version=version,
        currency="INR",
        products={
            product.sku: product
            for product in products
        },
    )


def make_request() -> ShoppingRequest:
    return ShoppingRequest(
        query="usb-c charger",
        budget_paise=400_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def create_test_session():
    return create_shopping_session(
        request=make_request(),
        catalog_version="test-v1",
        base_product_skus=[
            "CHG-001",
            "CHG-002",
            "CHG-003",
        ],
    )


def test_selects_offered_base_and_returns_two_cross_sells(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "selection.db"),
    )
    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        make_catalog,
    )

    session = create_test_session()

    result = selection_service.select_base_product(
        session_id=session.session_id,
        base_product_sku="chg-001",
    )

    assert (
        result.status
        == "cross_sell_decision_required"
    )
    assert (
        result.selected_base_product.sku
        == "CHG-001"
    )
    assert [
        product.sku
        for product in result.cross_sell_options
    ] == [
        "CBL-001",
        "CBL-002",
    ]

    stored_session = get_shopping_session(
        session.session_id
    )

    assert stored_session is not None
    assert (
        stored_session.status
        == "awaiting_cross_sell_decision"
    )
    assert (
        stored_session.selected_base_product_sku
        == "CHG-001"
    )
    assert stored_session.cross_sell_option_skus == [
        "CBL-001",
        "CBL-002",
    ]


def test_rejects_base_product_not_offered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "unoffered.db"),
    )

    session = create_test_session()

    with pytest.raises(
        ShoppingSessionStateError,
        match="was not offered",
    ):
        selection_service.select_base_product(
            session_id=session.session_id,
            base_product_sku="CHG-999",
        )


def test_rejects_changed_catalog_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "changed-catalog.db"),
    )
    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        lambda: make_catalog(version="test-v2"),
    )

    session = create_test_session()

    with pytest.raises(
        selection_service.CatalogVersionChangedError,
        match="catalog changed",
    ):
        selection_service.select_base_product(
            session_id=session.session_id,
            base_product_sku="CHG-001",
        )


def test_rejects_product_that_is_no_longer_eligible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "unavailable.db"),
    )

    catalog = make_catalog()
    catalog.products["CHG-001"].stock = 0

    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        lambda: catalog,
    )

    session = create_test_session()

    with pytest.raises(
        selection_service.BaseProductUnavailableError,
        match="no longer eligible",
    ):
        selection_service.select_base_product(
            session_id=session.session_id,
            base_product_sku="CHG-001",
        )

def prepare_base_selected_session(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "cross-sell-selection.db"),
    )
    monkeypatch.setattr(
        selection_service,
        "load_catalog",
        make_catalog,
    )

    session = create_test_session()

    selection_service.select_base_product(
        session_id=session.session_id,
        base_product_sku="CHG-001",
    )

    return session


def test_decline_cross_sell_creates_base_only_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selected_session(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="decline",
        )
    )

    assert result.status == "quote_ready"
    assert result.cross_sell_decision == "declined"
    assert result.quote.base_product_sku == "CHG-001"
    assert result.quote.upsell_product_sku is None
    assert result.quote.total_paise == 200_000

    stored_session = get_shopping_session(
        session.session_id
    )

    assert stored_session is not None
    assert stored_session.status == "quote_created"
    assert (
        stored_session.quote_id
        == result.quote.quote_id
    )


def test_accept_offered_cross_sell_creates_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selected_session(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="cbl-001",
        )
    )

    assert result.status == "quote_ready"
    assert result.cross_sell_decision == "accepted"
    assert result.quote.base_product_sku == "CHG-001"
    assert (
        result.quote.upsell_product_sku
        == "CBL-001"
    )
    assert result.quote.total_paise == 230_000


@pytest.mark.parametrize(
    (
        "decision",
        "cross_sell_product_sku",
        "expected_message",
    ),
    [
        (
            "accept",
            None,
            "requires a product SKU",
        ),
        (
            "decline",
            "CBL-001",
            "must not include a product SKU",
        ),
    ],
)
def test_requires_consistent_explicit_decision(
    decision: str,
    cross_sell_product_sku: str | None,
    expected_message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=expected_message,
    ):
        selection_service.finalize_cross_sell_decision(
            session_id=(
                "session_00000000000000000000000000000001"
            ),
            decision=decision,
            cross_sell_product_sku=(
                cross_sell_product_sku
            ),
        )


def test_rejects_cross_sell_that_was_not_offered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selected_session(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    with pytest.raises(
        ShoppingSessionStateError,
        match="was not offered",
    ):
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-999",
        )


def test_repeated_same_decision_returns_existing_quote(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selected_session(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    first_result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-001",
        )
    )

    repeated_result = (
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="accept",
            cross_sell_product_sku="CBL-001",
        )
    )

    assert (
        repeated_result.quote
        == first_result.quote
    )
    assert (
        repeated_result.quote.quote_id
        == first_result.quote.quote_id
    )


def test_cannot_change_decision_after_quote_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = prepare_base_selected_session(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    selection_service.finalize_cross_sell_decision(
        session_id=session.session_id,
        decision="accept",
        cross_sell_product_sku="CBL-001",
    )

    with pytest.raises(
        ShoppingSessionStateError,
        match="different cross-sell decision",
    ):
        selection_service.finalize_cross_sell_decision(
            session_id=session.session_id,
            decision="decline",
        )