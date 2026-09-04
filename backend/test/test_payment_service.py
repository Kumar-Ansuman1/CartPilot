import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from backend.app.config import get_settings
from backend.app.models import Quote
from backend.app.payment_service import (
    InvalidPaymentSignatureError,
    PaymentStateError,
    verify_and_record_payment,
)
from backend.app.quote_store import (
    get_verified_payment,
    mark_order_created,
    save_quote,
)


TEST_SECRET = "test-razorpay-secret"


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
        TEST_SECRET,
    )

    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


def create_ordered_quote() -> tuple[Quote, str]:
    now = datetime.now(timezone.utc)

    quote = Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-TEST-001",
        base_price_paise=199900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199900,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )

    razorpay_order_id = "order_test123"

    save_quote(quote)
    mark_order_created(
        quote_id=quote.quote_id,
        razorpay_order_id=razorpay_order_id,
    )

    return quote, razorpay_order_id


def create_signature(
    order_id: str,
    payment_id: str,
) -> str:
    message = f"{order_id}|{payment_id}".encode("utf-8")

    return hmac.new(
        TEST_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def test_verifies_and_records_valid_payment():
    quote, order_id = create_ordered_quote()
    payment_id = "pay_test123"
    signature = create_signature(order_id, payment_id)

    result = verify_and_record_payment(
        quote_id=quote.quote_id,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
    )

    assert result.status == "verified"
    assert result.quote_id == quote.quote_id
    assert result.razorpay_order_id == order_id
    assert result.razorpay_payment_id == payment_id

    stored_payment = get_verified_payment(quote.quote_id)

    assert stored_payment is not None
    assert stored_payment.razorpay_payment_id == payment_id


def test_rejects_invalid_signature():
    quote, order_id = create_ordered_quote()

    with pytest.raises(InvalidPaymentSignatureError):
        verify_and_record_payment(
            quote_id=quote.quote_id,
            razorpay_order_id=order_id,
            razorpay_payment_id="pay_test123",
            razorpay_signature="0" * 64,
        )

    assert get_verified_payment(quote.quote_id) is None


def test_rejects_order_id_that_does_not_match_quote():
    quote, _ = create_ordered_quote()
    different_order_id = "order_different"
    payment_id = "pay_test123"

    signature = create_signature(
        different_order_id,
        payment_id,
    )

    with pytest.raises(PaymentStateError):
        verify_and_record_payment(
            quote_id=quote.quote_id,
            razorpay_order_id=different_order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=signature,
        )

    assert get_verified_payment(quote.quote_id) is None


def test_repeated_verification_is_idempotent():
    quote, order_id = create_ordered_quote()
    payment_id = "pay_test123"
    signature = create_signature(order_id, payment_id)

    first_result = verify_and_record_payment(
        quote_id=quote.quote_id,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
    )

    second_result = verify_and_record_payment(
        quote_id=quote.quote_id,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
    )

    assert first_result.razorpay_payment_id == payment_id
    assert second_result.razorpay_payment_id == payment_id