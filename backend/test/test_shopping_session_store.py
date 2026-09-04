from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.models import (
    Quote,
    ShoppingRequest,
)
from backend.app.quote_store import save_quote
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionStateError,
    create_shopping_session,
    get_shopping_session,
    mark_shopping_session_expired,
    mark_shopping_session_quoted,
    record_base_product_selection,
)


def configure_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / name),
    )


def make_request() -> ShoppingRequest:
    return ShoppingRequest(
        query="usb-c charger",
        budget_paise=300_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )


def create_session():
    return create_shopping_session(
        request=make_request(),
        catalog_version="test-v1",
        base_product_skus=[
            "CHG-001",
            "CHG-002",
            "CHG-003",
        ],
    )


def save_test_quote(
    *,
    quote_id: str,
    base_product_sku: str = "CHG-001",
    catalog_version: str = "test-v1",
    cross_sell_sku: str | None = None,
) -> Quote:
    created_at = datetime.now(timezone.utc)

    cross_sell_price = (
        30_000
        if cross_sell_sku is not None
        else 0
    )

    quote = Quote(
        quote_id=quote_id,
        catalog_version=catalog_version,
        currency="INR",
        base_product_sku=base_product_sku,
        base_price_paise=200_000,
        upsell_product_sku=cross_sell_sku,
        upsell_price_paise=cross_sell_price,
        total_paise=200_000 + cross_sell_price,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
    )

    save_quote(quote)

    return quote


def test_creates_and_retrieves_shopping_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "create-session.db",
    )

    created_session = create_session()

    loaded_session = get_shopping_session(
        created_session.session_id
    )

    assert loaded_session == created_session
    assert (
        loaded_session.status
        == "awaiting_base_selection"
    )


def test_missing_shopping_session_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "missing-session.db",
    )

    result = get_shopping_session(
        "session_00000000000000000000000000000000"
    )

    assert result is None


def test_records_base_selection_idempotently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "base-selection.db",
    )

    session = create_session()

    first_result = record_base_product_selection(
        session_id=session.session_id,
        base_product_sku="chg-002",
        cross_sell_option_skus=[
            "CBL-001",
            "CBL-002",
        ],
    )

    second_result = record_base_product_selection(
        session_id=session.session_id,
        base_product_sku="CHG-002",
        cross_sell_option_skus=[
            "CBL-001",
            "CBL-002",
        ],
    )

    assert first_result == second_result
    assert (
        first_result.status
        == "awaiting_cross_sell_decision"
    )
    assert (
        first_result.selected_base_product_sku
        == "CHG-002"
    )


def test_rejects_base_product_that_was_not_offered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "invalid-base.db",
    )

    session = create_session()

    with pytest.raises(
        ShoppingSessionStateError,
        match="was not offered",
    ):
        record_base_product_selection(
            session_id=session.session_id,
            base_product_sku="CHG-999",
            cross_sell_option_skus=[],
        )


def test_expired_session_cannot_select_base_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "expired-session.db",
    )

    session = create_session()

    expired_session = mark_shopping_session_expired(
        session.session_id
    )

    assert expired_session.status == "expired"

    with pytest.raises(
        ShoppingSessionExpiredError,
        match="has expired",
    ):
        record_base_product_selection(
            session_id=session.session_id,
            base_product_sku="CHG-001",
            cross_sell_option_skus=[],
        )


def test_quote_requires_base_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "quote-without-base.db",
    )

    session = create_session()

    with pytest.raises(
        ShoppingSessionStateError,
        match="base product must be selected",
    ):
        mark_shopping_session_quoted(
            session_id=session.session_id,
            quote_id=(
                "quote_"
                "11111111111111111111111111111111"
            ),
        )


def test_links_matching_quote_to_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "matching-quote.db",
    )

    session = create_session()

    selected_session = record_base_product_selection(
        session_id=session.session_id,
        base_product_sku="CHG-001",
        cross_sell_option_skus=["CBL-001"],
    )

    quote = save_test_quote(
        quote_id=(
            "quote_"
            "22222222222222222222222222222222"
        ),
        cross_sell_sku="CBL-001",
    )

    completed_session = mark_shopping_session_quoted(
        session_id=selected_session.session_id,
        quote_id=quote.quote_id,
    )

    assert completed_session.status == "quote_created"
    assert completed_session.quote_id == quote.quote_id


def test_rejects_quote_for_different_base_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_database(
        monkeypatch,
        tmp_path,
        "wrong-quote-base.db",
    )

    session = create_session()

    selected_session = record_base_product_selection(
        session_id=session.session_id,
        base_product_sku="CHG-001",
        cross_sell_option_skus=[],
    )

    quote = save_test_quote(
        quote_id=(
            "quote_"
            "33333333333333333333333333333333"
        ),
        base_product_sku="CHG-002",
    )

    with pytest.raises(
        ShoppingSessionStateError,
        match="base product does not match",
    ):
        mark_shopping_session_quoted(
            session_id=selected_session.session_id,
            quote_id=quote.quote_id,
        )