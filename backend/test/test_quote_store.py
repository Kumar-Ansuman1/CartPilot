from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import Quote
from backend.app.quote_store import (
    QuoteConflictError,
    get_stored_quote,
    save_quote_idempotently,
)


@pytest.fixture(autouse=True)
def isolated_database(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "cartpilot-test.db"

    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(database_path),
    )


def _quote(
    *,
    base_product_sku: str = "CHARGER-001",
    created_at: datetime | None = None,
) -> Quote:
    quote_created_at = created_at or datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc,
    )

    return Quote(
        quote_id=(
            "quote_00000000000000000000000000000001"
        ),
        catalog_version="test-v1",
        currency="INR",
        base_product_sku=base_product_sku,
        base_price_paise=100_000,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=100_000,
        created_at=quote_created_at,
        expires_at=quote_created_at
        + timedelta(minutes=5),
    )


def test_first_quote_is_saved() -> None:
    quote = _quote()

    stored_quote = save_quote_idempotently(quote)

    assert stored_quote.quote == quote
    assert stored_quote.status == "pending"
    assert get_stored_quote(quote.quote_id) is not None


def test_identical_retry_returns_original_quote() -> None:
    first_quote = _quote()

    retry_quote = _quote(
        created_at=first_quote.created_at
        + timedelta(seconds=10)
    )

    first_result = save_quote_idempotently(
        first_quote
    )
    retry_result = save_quote_idempotently(
        retry_quote
    )

    assert retry_result == first_result
    assert (
        retry_result.quote.created_at
        == first_quote.created_at
    )


def test_different_quote_for_same_id_is_rejected() -> None:
    save_quote_idempotently(_quote())

    conflicting_quote = _quote(
        base_product_sku="CHARGER-002"
    )

    with pytest.raises(
        QuoteConflictError,
        match="different quote already exists",
    ):
        save_quote_idempotently(
            conflicting_quote
        )
        