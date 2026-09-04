from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app.checkout_service import (
    QuoteExpiredError,
    RazorpayOrderError,
    create_checkout_order,
)
from backend.app.config import get_settings
from backend.app.models import Quote
from backend.app.quote_store import (
    get_stored_quote,
    save_quote,
)


@pytest.fixture(autouse=True)
def configure_test_environment(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "test_cartpilot.db"),
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_example")
    monkeypatch.setenv(
        "RAZORPAY_KEY_SECRET",
        "test-razorpay-secret",
    )

    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


def make_quote(
    *,
    expired: bool = False,
) -> Quote:
    now = datetime.now(timezone.utc)

    if expired:
        created_at = now - timedelta(minutes=10)
        expires_at = now - timedelta(minutes=5)
    else:
        created_at = now
        expires_at = now + timedelta(minutes=5)

    return Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-TEST-001",
        base_price_paise=199900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199900,
        created_at=created_at,
        expires_at=expires_at,
    )


def test_creates_order_using_stored_quote_amount():
    quote = make_quote()
    save_quote(quote)

    mock_client = Mock()
    mock_client.order.create.return_value = {
        "id": "order_test123",
        "amount": quote.total_paise,
        "currency": quote.currency,
        "receipt": quote.quote_id,
        "status": "created",
    }

    with patch(
        "backend.app.checkout_service._create_razorpay_client",
        return_value=mock_client,
    ):
        result = create_checkout_order(quote.quote_id)

    payload = mock_client.order.create.call_args.args[0]

    assert payload["amount"] == quote.total_paise
    assert payload["receipt"] == quote.quote_id
    assert result.razorpay_order_id == "order_test123"

    stored_quote = get_stored_quote(quote.quote_id)

    assert stored_quote is not None
    assert stored_quote.status == "order_created"
    assert stored_quote.razorpay_order_id == "order_test123"


def test_repeated_confirmation_returns_same_order():
    quote = make_quote()
    save_quote(quote)

    mock_client = Mock()
    mock_client.order.create.return_value = {
        "id": "order_test123",
        "amount": quote.total_paise,
        "currency": quote.currency,
        "receipt": quote.quote_id,
        "status": "created",
    }

    with patch(
        "backend.app.checkout_service._create_razorpay_client",
        return_value=mock_client,
    ):
        first_result = create_checkout_order(quote.quote_id)
        second_result = create_checkout_order(quote.quote_id)

    assert first_result.razorpay_order_id == "order_test123"
    assert second_result.razorpay_order_id == "order_test123"
    mock_client.order.create.assert_called_once()


def test_expired_quote_never_calls_razorpay():
    quote = make_quote(expired=True)
    save_quote(quote)

    mock_client = Mock()

    with patch(
        "backend.app.checkout_service._create_razorpay_client",
        return_value=mock_client,
    ):
        with pytest.raises(QuoteExpiredError):
            create_checkout_order(quote.quote_id)

    mock_client.order.create.assert_not_called()

    stored_quote = get_stored_quote(quote.quote_id)

    assert stored_quote is not None
    assert stored_quote.status == "expired"


def test_rejects_unexpected_razorpay_amount():
    quote = make_quote()
    save_quote(quote)

    mock_client = Mock()
    mock_client.order.create.return_value = {
        "id": "order_test123",
        "amount": 100,
        "currency": quote.currency,
        "receipt": quote.quote_id,
        "status": "created",
    }

    with patch(
        "backend.app.checkout_service._create_razorpay_client",
        return_value=mock_client,
    ):
        with pytest.raises(
            RazorpayOrderError,
            match="unexpected amount",
        ):
            create_checkout_order(quote.quote_id)

    stored_quote = get_stored_quote(quote.quote_id)

    assert stored_quote is not None
    assert stored_quote.status == "pending"