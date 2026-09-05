import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from backend.app import payment_service
from backend.app.audit_events import (
    list_audit_events,
    list_quote_audit_events,
)
from backend.app.config import get_settings
from backend.app.models import Quote, ShoppingRequest
from backend.app.payment_service import (
    InvalidPaymentSignatureError,
    PaymentQuoteNotFoundError,
    PaymentStateError,
    verify_and_record_payment,
)
from backend.app.quote_store import (
    get_verified_payment,
    mark_order_created,
    mark_quote_expired,
    save_quote,
)
from backend.app.shopping_session_store import (
    create_shopping_session,
    mark_shopping_session_quoted,
    record_base_product_selection,
)


TEST_SECRET = "test-razorpay-secret"
ORDER_ID = "order_audit123"
PAYMENT_ID = "pay_audit123"


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH", str(tmp_path / "payment-audit.db")
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_example")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", TEST_SECRET)
    get_settings.cache_clear()

    # Signature verification uses the real SDK locally; no live payments.
    def reject_network(*args, **kwargs):
        raise AssertionError("Payment audit tests must not make HTTP requests.")

    monkeypatch.setattr("requests.sessions.Session.request", reject_network)
    yield
    get_settings.cache_clear()


def make_linked_quote(*, status="order_created", expired=False):
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(minutes=10) if expired else now
    quote = Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-TEST-001",
        base_price_paise=199_900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199_900,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=5),
    )
    session = create_shopping_session(
        request=ShoppingRequest(
            query="USB-C charger",
            budget_paise=300_000,
            allowed_categories=["chargers"],
            compatibility_tags=["usb-c"],
        ),
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
        session_id=session.session_id, quote_id=quote.quote_id
    )
    if status == "order_created":
        mark_order_created(quote.quote_id, ORDER_ID)
    elif status == "expired":
        mark_quote_expired(quote.quote_id)
    return quote, session.session_id


@pytest.fixture
def ordered_quote():
    return make_linked_quote()


def callback(quote, *, order_id=ORDER_ID, payment_id=PAYMENT_ID):
    signature = hmac.new(
        TEST_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "quote_id": quote.quote_id,
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    }


def test_success_records_signature_verification_with_quote_terms(ordered_quote):
    quote, session_id = ordered_quote
    payload = callback(quote)
    payment = verify_and_record_payment(**payload)
    events = list_audit_events(session_id)

    assert [e.event_type for e in events] == [
        "payment_verification_requested", "payment_verified"
    ]
    assert events[0].actor == "buyer"
    assert events[1].actor == "deterministic_core"
    assert events[1].outcome == "recorded"
    assert events[1].reason_code == "PAYMENT_SIGNATURE_VERIFIED"
    assert events[1].created_at == payment.verified_at
    assert "Capture status has not been checked" in events[1].explanation
    for event in events:
        assert event.quote_id == quote.quote_id
        assert event.amount_paise == quote.total_paise
        assert event.currency == "INR"
        assert event.razorpay_order_id == ORDER_ID
        assert event.razorpay_payment_id == PAYMENT_ID
    assert list_quote_audit_events(quote.quote_id) == events
    persisted_json = "".join(e.model_dump_json() for e in events)
    assert payload["razorpay_signature"] not in persisted_json
    assert TEST_SECRET not in persisted_json
    assert "razorpay_signature" not in persisted_json


def test_retry_rechecks_signature_without_duplicate_events(ordered_quote):
    quote, session_id = ordered_quote
    client = payment_service._create_razorpay_client()
    with patch.object(
        payment_service, "_create_razorpay_client", return_value=client
    ), patch.object(
        client.utility,
        "verify_payment_signature",
        wraps=client.utility.verify_payment_signature,
    ) as verifier:
        first = verify_and_record_payment(**callback(quote))
        first_events = list_audit_events(session_id)
        second = verify_and_record_payment(**callback(quote))

    assert verifier.call_count == 2
    assert first == second
    assert list_audit_events(session_id) == first_events


def test_invalid_signature_is_audited_and_later_valid_retry_succeeds(ordered_quote):
    quote, session_id = ordered_quote
    payload = callback(quote)
    payload["razorpay_signature"] = "0" * 64
    with pytest.raises(InvalidPaymentSignatureError):
        verify_and_record_payment(**payload)
    first_events = list_audit_events(session_id)
    assert first_events[-1].reason_code == "PAYMENT_SIGNATURE_INVALID"
    assert first_events[-1].outcome == "rejected"
    assert get_verified_payment(quote.quote_id) is None

    with pytest.raises(InvalidPaymentSignatureError):
        verify_and_record_payment(**payload)
    assert list_audit_events(session_id) == first_events

    verify_and_record_payment(**callback(quote))
    events = list_audit_events(session_id)
    assert [e.event_type for e in events] == [
        "payment_verification_requested", "payment_rejected", "payment_verified"
    ]
    assert events[:2] == first_events


def test_invalid_retry_cannot_reuse_a_verified_payment(ordered_quote):
    quote, session_id = ordered_quote
    original = verify_and_record_payment(**callback(quote))
    payload = callback(quote)
    payload["razorpay_signature"] = "0" * 64
    with pytest.raises(InvalidPaymentSignatureError):
        verify_and_record_payment(**payload)

    events = list_audit_events(session_id)
    assert events[-1].reason_code == "PAYMENT_SIGNATURE_INVALID"
    assert sum(e.event_type == "payment_verified" for e in events) == 1
    assert get_verified_payment(quote.quote_id) == original


def test_order_mismatch_is_audited_before_signature_verification(ordered_quote):
    quote, session_id = ordered_quote
    with patch.object(payment_service, "_create_razorpay_client") as factory:
        with pytest.raises(PaymentStateError, match="does not match"):
            verify_and_record_payment(**callback(quote, order_id="order_other"))
    factory.assert_not_called()
    event = list_audit_events(session_id)[-1]
    assert event.event_type == "payment_rejected"
    assert event.reason_code == "PAYMENT_ORDER_ID_MISMATCH"
    assert event.razorpay_order_id == "order_other"
    assert get_verified_payment(quote.quote_id) is None


@pytest.mark.parametrize("status", ["pending", "expired"])
def test_quote_without_created_order_is_rejected_and_audited(status):
    quote, session_id = make_linked_quote(status=status)
    with patch.object(payment_service, "_create_razorpay_client") as factory:
        with pytest.raises(PaymentStateError):
            verify_and_record_payment(**callback(quote))
    factory.assert_not_called()
    assert list_audit_events(session_id)[-1].reason_code == "PAYMENT_ORDER_NOT_CREATED"
    assert get_verified_payment(quote.quote_id) is None


def test_payment_after_quote_expiry_still_verifies_an_existing_order():
    quote, session_id = make_linked_quote(expired=True)
    verify_and_record_payment(**callback(quote))
    assert list_audit_events(session_id)[-1].event_type == "payment_verified"


@pytest.mark.parametrize("verifier_result", [False, None, 1])
def test_verifier_must_return_true_to_record_success(ordered_quote, verifier_result):
    quote, session_id = ordered_quote
    client = Mock()
    client.utility.verify_payment_signature.return_value = verifier_result
    with patch.object(payment_service, "_create_razorpay_client", return_value=client):
        with pytest.raises(InvalidPaymentSignatureError):
            verify_and_record_payment(**callback(quote))
    assert list_audit_events(session_id)[-1].reason_code == "PAYMENT_SIGNATURE_INVALID"
    assert get_verified_payment(quote.quote_id) is None


def test_verifier_error_is_audited_without_raw_exception_details(ordered_quote):
    quote, session_id = ordered_quote
    payload = callback(quote)
    client = Mock()
    client.utility.verify_payment_signature.side_effect = RuntimeError(
        f"Sensitive failure: {TEST_SECRET} {payload['razorpay_signature']}"
    )
    with patch.object(payment_service, "_create_razorpay_client", return_value=client):
        with pytest.raises(RuntimeError):
            verify_and_record_payment(**payload)
    events = list_audit_events(session_id)
    assert events[-1].reason_code == "PAYMENT_VERIFICATION_ERROR"
    assert events[-1].outcome == "failed"
    assert get_verified_payment(quote.quote_id) is None
    persisted_json = "".join(e.model_dump_json() for e in events)
    assert "Sensitive failure" not in persisted_json
    assert TEST_SECRET not in persisted_json
    assert payload["razorpay_signature"] not in persisted_json


def test_conflicting_payment_is_audited_without_replacing_original(ordered_quote):
    quote, session_id = ordered_quote
    original = verify_and_record_payment(**callback(quote))
    with pytest.raises(ValueError, match="different payment"):
        verify_and_record_payment(**callback(quote, payment_id="pay_other"))
    events = list_audit_events(session_id)
    assert events[-1].reason_code == "PAYMENT_RECORD_CONFLICT"
    assert events[-1].outcome == "rejected"
    assert events[-1].razorpay_payment_id == "pay_other"
    assert sum(e.event_type == "payment_verified" for e in events) == 1
    assert get_verified_payment(quote.quote_id) == original


def test_retry_repairs_success_event_after_audit_write_failure(ordered_quote):
    quote, session_id = ordered_quote
    record = payment_service.record_audit_event

    def fail_success_event(**kwargs):
        if kwargs["event_type"] == "payment_verified":
            raise RuntimeError("Audit write unavailable")
        return record(**kwargs)

    with patch.object(payment_service, "record_audit_event", side_effect=fail_success_event):
        with pytest.raises(RuntimeError, match="Audit write unavailable"):
            verify_and_record_payment(**callback(quote))

    original = get_verified_payment(quote.quote_id)
    assert original is not None
    assert len(list_audit_events(session_id)) == 1
    assert verify_and_record_payment(**callback(quote)) == original
    events = list_audit_events(session_id)
    assert len(events) == 2
    assert events[-1].event_type == "payment_verified"
    assert events[-1].created_at == original.verified_at


def test_unknown_quote_does_not_fabricate_a_session(ordered_quote):
    quote, session_id = ordered_quote
    payload = callback(quote)
    payload["quote_id"] = f"quote_{uuid4().hex}"
    with pytest.raises(PaymentQuoteNotFoundError):
        verify_and_record_payment(**payload)
    assert list_audit_events(session_id) == []
    assert list_quote_audit_events(payload["quote_id"]) == []


@pytest.mark.parametrize("field,value", [
    ("razorpay_order_id", "order_"),
    ("razorpay_order_id", "order_bad-id"),
    ("razorpay_payment_id", "pay_"),
    ("razorpay_payment_id", "pay_bad-id"),
    ("razorpay_signature", "bad-signature"),
])
def test_malformed_callback_stops_before_audit_and_sdk(ordered_quote, field, value):
    quote, session_id = ordered_quote
    payload = callback(quote)
    payload[field] = value
    with patch.object(payment_service, "_create_razorpay_client") as factory:
        with pytest.raises(ValueError):
            verify_and_record_payment(**payload)
    factory.assert_not_called()
    assert list_audit_events(session_id) == []
    assert get_verified_payment(quote.quote_id) is None
