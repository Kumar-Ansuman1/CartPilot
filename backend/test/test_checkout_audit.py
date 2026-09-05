from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app.audit_events import list_audit_events
from backend.app.checkout_service import (
    QuoteExpiredError,
    RazorpayOrderError,
    create_checkout_order,
)
from backend.app.config import get_settings
from backend.app.models import Quote, ShoppingRequest
from backend.app.quote_store import save_quote
from backend.app.shopping_session_store import (
    create_shopping_session,
    mark_shopping_session_quoted,
    record_base_product_selection,
)


@pytest.fixture(autouse=True)
def configure_test_environment(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "checkout-audit.db"),
    )
    monkeypatch.setenv(
        "GROQ_API_KEY",
        "test-groq-key",
    )
    monkeypatch.setenv(
        "RAZORPAY_KEY_ID",
        "rzp_test_example",
    )
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
        base_price_paise=199_900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199_900,
        created_at=created_at,
        expires_at=expires_at,
    )


def save_and_link_quote(quote: Quote) -> str:
    request = ShoppingRequest(
        query="USB-C charger",
        budget_paise=300_000,
        allowed_categories=["chargers"],
        compatibility_tags=["usb-c"],
    )
    session = create_shopping_session(
        request=request,
        catalog_version=quote.catalog_version,
        base_product_skus=[quote.base_product_sku],
    )

    record_base_product_selection(
        session_id=session.session_id,
        base_product_sku=quote.base_product_sku,
        cross_sell_option_skus=[],
    )
    save_quote(quote)
    mark_shopping_session_quoted(
        session_id=session.session_id,
        quote_id=quote.quote_id,
    )

    return session.session_id


def successful_order_response(
    quote: Quote,
) -> dict[str, object]:
    return {
        "id": "order_test123",
        "amount": quote.total_paise,
        "currency": quote.currency,
        "receipt": quote.quote_id,
        "status": "created",
    }


def test_successful_order_creation_is_audited() -> None:
    quote = make_quote()
    session_id = save_and_link_quote(quote)
    mock_client = Mock()
    mock_client.order.create.return_value = (
        successful_order_response(quote)
    )

    with patch(
        "backend.app.checkout_service."
        "_create_razorpay_client",
        return_value=mock_client,
    ):
        create_checkout_order(quote.quote_id)

    events = list_audit_events(session_id)

    assert [event.event_type for event in events] == [
        "checkout_confirmed",
        "order_creation_requested",
        "order_created",
    ]
    assert events[0].actor == "buyer"
    assert events[0].amount_paise == 199_900
    assert events[1].actor == "deterministic_core"
    assert events[2].actor == "razorpay"
    assert events[2].razorpay_order_id == (
        "order_test123"
    )


def test_order_retry_does_not_duplicate_events() -> None:
    quote = make_quote()
    session_id = save_and_link_quote(quote)
    mock_client = Mock()
    mock_client.order.create.return_value = (
        successful_order_response(quote)
    )

    with patch(
        "backend.app.checkout_service."
        "_create_razorpay_client",
        return_value=mock_client,
    ):
        create_checkout_order(quote.quote_id)
        first_events = list_audit_events(session_id)
        create_checkout_order(quote.quote_id)

    assert list_audit_events(session_id) == first_events
    mock_client.order.create.assert_called_once()


def test_expired_checkout_rejection_is_audited() -> None:
    quote = make_quote(expired=True)
    session_id = save_and_link_quote(quote)
    mock_client = Mock()

    with patch(
        "backend.app.checkout_service."
        "_create_razorpay_client",
        return_value=mock_client,
    ):
        with pytest.raises(QuoteExpiredError):
            create_checkout_order(quote.quote_id)

    events = list_audit_events(session_id)

    assert [event.event_type for event in events] == [
        "quote_expired",
        "checkout_confirmed",
    ]
    assert events[-1].outcome == "rejected"
    assert events[-1].reason_code == (
        "CHECKOUT_QUOTE_EXPIRED"
    )
    mock_client.order.create.assert_not_called()


def test_amount_mismatch_failure_is_audited() -> None:
    quote = make_quote()
    session_id = save_and_link_quote(quote)
    response = successful_order_response(quote)
    response["amount"] = 100
    mock_client = Mock()
    mock_client.order.create.return_value = response

    with patch(
        "backend.app.checkout_service."
        "_create_razorpay_client",
        return_value=mock_client,
    ):
        with pytest.raises(
            RazorpayOrderError,
            match="unexpected amount",
        ):
            create_checkout_order(quote.quote_id)

    failure_event = list_audit_events(session_id)[-1]

    assert failure_event.event_type == "order_created"
    assert failure_event.outcome == "failed"
    assert failure_event.reason_code == (
        "RAZORPAY_ORDER_AMOUNT_MISMATCH"
    )


def test_provider_request_failure_is_audited() -> None:
    quote = make_quote()
    session_id = save_and_link_quote(quote)
    mock_client = Mock()
    mock_client.order.create.side_effect = RuntimeError(
        "provider unavailable"
    )

    with patch(
        "backend.app.checkout_service."
        "_create_razorpay_client",
        return_value=mock_client,
    ):
        with pytest.raises(
            RazorpayOrderError,
            match="creation failed",
        ):
            create_checkout_order(quote.quote_id)

    failure_event = list_audit_events(session_id)[-1]

    assert failure_event.event_type == "order_created"
    assert failure_event.outcome == "failed"
    assert failure_event.reason_code == (
        "RAZORPAY_ORDER_REQUEST_FAILED"
    )
