import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.audit_events import list_audit_events
from backend.app.config import get_settings
from backend.app.main import app
from backend.app.models import Quote, ShoppingRequest
from backend.app.payment_service import verify_and_record_payment
from backend.app.payment_webhook import (
    InvalidWebhookSignatureError,
    MalformedWebhookError,
    WebhookStateError,
    process_razorpay_webhook,
)
from backend.app import payment_webhook
from backend.app.quote_store import (
    get_verified_payment,
    mark_order_created,
    save_quote,
)
from backend.app.shopping_session_store import (
    create_shopping_session,
    mark_shopping_session_quoted,
    record_base_product_selection,
)
from backend.app.webhook_store import (
    WebhookEventConflictError,
    get_processed_webhook_event,
)


WEBHOOK_SECRET = "test-webhook-secret"
KEY_SECRET = "test-key-secret"
ORDER_ID = "order_webhook123"
PAYMENT_ID = "pay_webhook123"
EVENT_ID = "evt_webhook123"


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "payment-webhook.db"),
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_example")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setenv(
        "RAZORPAY_WEBHOOK_SECRET",
        WEBHOOK_SECRET,
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def create_linked_order() -> tuple[Quote, str]:
    now = datetime.now(timezone.utc)
    quote = Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-TEST-001",
        base_price_paise=199_900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199_900,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
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
        session_id=session.session_id,
        quote_id=quote.quote_id,
    )
    mark_order_created(quote.quote_id, ORDER_ID)
    return quote, session.session_id


def webhook_body(
    *,
    event="order.paid",
    order_id=ORDER_ID,
    payment_id=PAYMENT_ID,
    amount=199_900,
    currency="INR",
    status="captured",
    captured=True,
) -> bytes:
    payload = {
        "entity": "event",
        "event": event,
        "payload": {},
        "created_at": 1_700_000_000,
    }

    if event == "order.paid":
        payload["payload"] = {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "order_id": order_id,
                    "amount": amount,
                    "currency": currency,
                    "status": status,
                    "captured": captured,
                }
            }
        }

    return json.dumps(
        payload,
        separators=(",", ":"),
    ).encode()


def sign(raw_body: bytes, secret=WEBHOOK_SECRET) -> str:
    return hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()


def process(raw_body: bytes, *, event_id=EVENT_ID):
    return process_razorpay_webhook(
        raw_body=raw_body,
        signature=sign(raw_body),
        event_id=event_id,
    )


def test_signed_order_paid_recovers_missing_browser_callback():
    quote, session_id = create_linked_order()
    raw_body = webhook_body()

    result = process(raw_body)

    assert result.status == "processed"
    assert result.quote_id == quote.quote_id
    assert result.razorpay_payment_id == PAYMENT_ID
    assert get_verified_payment(quote.quote_id) is not None

    event = list_audit_events(session_id)[-1]
    assert event.event_type == "payment_reconciled"
    assert event.outcome == "recovered"
    assert event.reason_code == "PAYMENT_RECOVERED_BY_WEBHOOK"
    assert event.amount_paise == quote.total_paise


def test_duplicate_delivery_is_acknowledged_without_duplicate_records():
    quote, session_id = create_linked_order()
    raw_body = webhook_body()

    first = process(raw_body)
    first_events = list_audit_events(session_id)
    first_payment = get_verified_payment(quote.quote_id)
    second = process(raw_body)

    assert first.status == "processed"
    assert second.status == "duplicate"
    assert get_verified_payment(quote.quote_id) == first_payment
    assert list_audit_events(session_id) == first_events


def test_event_id_cannot_be_reused_with_modified_payload():
    create_linked_order()
    process(webhook_body())

    with pytest.raises(WebhookEventConflictError):
        process(webhook_body(amount=199_901))


def test_signature_is_checked_against_exact_raw_body():
    create_linked_order()
    compact_body = webhook_body()
    pretty_body = json.dumps(
        json.loads(compact_body),
        indent=2,
    ).encode()

    with pytest.raises(InvalidWebhookSignatureError):
        process_razorpay_webhook(
            raw_body=pretty_body,
            signature=sign(compact_body),
            event_id=EVENT_ID,
        )

    assert get_processed_webhook_event(EVENT_ID) is None


@pytest.mark.parametrize(
    "changes,reason_code",
    [
        ({"amount": 199_901}, "WEBHOOK_AMOUNT_MISMATCH"),
        ({"currency": "USD"}, "WEBHOOK_CURRENCY_MISMATCH"),
        ({"captured": False}, "WEBHOOK_PAYMENT_NOT_CAPTURED"),
        ({"status": "authorized"}, "WEBHOOK_PAYMENT_NOT_CAPTURED"),
    ],
)
def test_signed_but_inconsistent_payment_is_rejected_and_audited(
    changes,
    reason_code,
):
    quote, session_id = create_linked_order()

    with pytest.raises(WebhookStateError):
        process(webhook_body(**changes))

    assert get_verified_payment(quote.quote_id) is None
    assert get_processed_webhook_event(EVENT_ID) is None
    assert list_audit_events(session_id)[-1].reason_code == reason_code


def test_unknown_order_is_rejected_without_fabricated_audit_event():
    _, session_id = create_linked_order()

    with pytest.raises(WebhookStateError):
        process(webhook_body(order_id="order_unknown"))

    assert list_audit_events(session_id) == []
    assert get_processed_webhook_event(EVENT_ID) is None


def test_existing_callback_payment_is_independently_confirmed(monkeypatch):
    quote, session_id = create_linked_order()
    callback_signature = hmac.new(
        KEY_SECRET.encode(),
        f"{ORDER_ID}|{PAYMENT_ID}".encode(),
        hashlib.sha256,
    ).hexdigest()
    verify_and_record_payment(
        quote_id=quote.quote_id,
        razorpay_order_id=ORDER_ID,
        razorpay_payment_id=PAYMENT_ID,
        razorpay_signature=callback_signature,
    )

    result = process(webhook_body())
    events = list_audit_events(session_id)

    assert result.status == "processed"
    assert events[-1].event_type == "payment_reconciled"
    assert events[-1].outcome == "recorded"
    assert events[-1].reason_code == "PAYMENT_WEBHOOK_CONFIRMED"


def test_conflicting_payment_does_not_replace_existing_payment():
    quote, session_id = create_linked_order()
    process(webhook_body(), event_id="evt_first")
    original = get_verified_payment(quote.quote_id)

    with pytest.raises(WebhookStateError):
        process(
            webhook_body(payment_id="pay_different"),
            event_id="evt_second",
        )

    assert get_verified_payment(quote.quote_id) == original
    assert (
        list_audit_events(session_id)[-1].reason_code
        == "WEBHOOK_PAYMENT_CONFLICT"
    )


def test_unsupported_signed_event_is_recorded_as_ignored():
    raw_body = webhook_body(event="payment.failed")

    first = process(raw_body)
    second = process(raw_body)

    assert first.status == "ignored"
    assert second.status == "duplicate"
    assert get_processed_webhook_event(EVENT_ID) is not None


@pytest.mark.parametrize(
    "raw_body",
    [
        b"not-json",
        b"{}",
        json.dumps(
            {
                "event": "order.paid",
                "payload": {},
            }
        ).encode(),
    ],
)
def test_malformed_signed_payload_is_rejected(raw_body):
    with pytest.raises(MalformedWebhookError):
        process(raw_body)

    assert get_processed_webhook_event(EVENT_ID) is None


def test_api_requires_signature_and_event_id_headers():
    client = TestClient(app)

    response = client.post(
        "/api/payment/webhook",
        content=webhook_body(),
    )

    assert response.status_code == 400


def test_api_recovers_payment_and_acknowledges_duplicate():
    quote, _ = create_linked_order()
    raw_body = webhook_body()
    headers = {
        "X-Razorpay-Signature": sign(raw_body),
        "X-Razorpay-Event-Id": EVENT_ID,
        "Content-Type": "application/json",
    }
    client = TestClient(app)

    first = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers=headers,
    )
    second = client.post(
        "/api/payment/webhook",
        content=raw_body,
        headers=headers,
    )

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert first.json()["quote_id"] == quote.quote_id
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_api_returns_service_unavailable_without_webhook_secret(
    monkeypatch,
):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    get_settings.cache_clear()
    raw_body = webhook_body()

    response = TestClient(app).post(
        "/api/payment/webhook",
        content=raw_body,
        headers={
            "X-Razorpay-Signature": sign(raw_body),
            "X-Razorpay-Event-Id": EVENT_ID,
        },
    )

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_retry_repairs_missing_audit_after_payment_write_failure():
    quote, session_id = create_linked_order()
    raw_body = webhook_body()
    real_record = payment_webhook.record_audit_event

    with patch.object(
        payment_webhook,
        "record_audit_event",
        side_effect=RuntimeError("audit store unavailable"),
    ):
        with pytest.raises(RuntimeError, match="audit store unavailable"):
            process(raw_body)

    original_payment = get_verified_payment(quote.quote_id)
    assert original_payment is not None
    assert list_audit_events(session_id) == []
    assert get_processed_webhook_event(EVENT_ID) is None

    with patch.object(
        payment_webhook,
        "record_audit_event",
        wraps=real_record,
    ):
        result = process(raw_body)

    assert result.status == "processed"
    assert get_verified_payment(quote.quote_id) == original_payment
    assert len(list_audit_events(session_id)) == 1


def test_retry_repairs_missing_delivery_marker_without_duplicate_audit():
    quote, session_id = create_linked_order()
    raw_body = webhook_body()
    real_save = payment_webhook.save_processed_webhook_event

    with patch.object(
        payment_webhook,
        "save_processed_webhook_event",
        side_effect=RuntimeError("webhook store unavailable"),
    ):
        with pytest.raises(RuntimeError, match="webhook store unavailable"):
            process(raw_body)

    original_payment = get_verified_payment(quote.quote_id)
    original_events = list_audit_events(session_id)
    assert original_payment is not None
    assert len(original_events) == 1
    assert get_processed_webhook_event(EVENT_ID) is None

    with patch.object(
        payment_webhook,
        "save_processed_webhook_event",
        wraps=real_save,
    ):
        result = process(raw_body)

    assert result.status == "processed"
    assert get_verified_payment(quote.quote_id) == original_payment
    assert list_audit_events(session_id) == original_events
    assert original_events[0].outcome == "recovered"


@pytest.mark.parametrize("raw_body", [b"", b"x" * 1_000_001])
def test_empty_or_oversized_body_is_rejected_before_storage(raw_body):
    with pytest.raises(MalformedWebhookError):
        process_razorpay_webhook(
            raw_body=raw_body,
            signature="0" * 64,
            event_id=EVENT_ID,
        )

    assert get_processed_webhook_event(EVENT_ID) is None
