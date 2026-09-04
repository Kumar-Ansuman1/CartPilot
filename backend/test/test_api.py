from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.checkout_service import (
    CheckoutOrder,
    QuoteExpiredError,
    QuoteNotFoundError,
    QuoteNotLinkedError,
    RazorpayOrderError,
)
from backend.app.commerce_agent import (
    CommerceAgentResult,
)
from backend.app.main import app
from backend.app.models import (
    ExtractedShoppingIntent,
)
from backend.app.payment_service import (
    InvalidPaymentSignatureError,
    PaymentQuoteNotFoundError,
    PaymentStateError,
)
from backend.app.quote_store import StoredPayment


client = TestClient(app)

TEST_QUOTE_ID = (
    "quote_123e4567e89b12d3a456426614174000"
)
TEST_ORDER_ID = "order_test123"
TEST_PAYMENT_ID = "pay_test123"
TEST_SIGNATURE = "a" * 64


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok"
    }


def test_shop_endpoint_returns_clarification():
    intent = ExtractedShoppingIntent(
        search_query="protective phone case",
        budget_rupees=None,
        requested_categories=["cases"],
        compatibility_tags=[],
        needs_clarification=True,
        clarification_question=(
            "What is your phone model and budget?"
        ),
    )

    result = CommerceAgentResult(
        status="clarification_required",
        message=(
            "What is your phone model and budget?"
        ),
        intent=intent,
        decision_trace=[
            (
                "The request was stopped before "
                "catalog search."
            ),
            (
                "No quote or payment action "
                "was created."
            ),
        ],
    )

    with patch(
        "backend.app.main.run_commerce_agent",
        return_value=result,
    ):
        response = client.post(
            "/api/shop",
            json={
                "message": "I need a phone case"
            },
        )

    assert response.status_code == 200

    response_data = response.json()

    assert (
        response_data["status"]
        == "clarification_required"
    )
    assert response_data["session_id"] is None
    assert (
        response_data["base_product_options"]
        == []
    )
    assert (
        response_data[
            "recommended_base_product_sku"
        ]
        is None
    )
    assert "quote" not in response_data


def test_shop_endpoint_rejects_short_message():
    response = client.post(
        "/api/shop",
        json={
            "message": "hi"
        },
    )

    assert response.status_code == 422


def test_shop_endpoint_handles_ai_failure_safely():
    with patch(
        "backend.app.main.run_commerce_agent",
        side_effect=RuntimeError(
            "Provider failure details"
        ),
    ):
        response = client.post(
            "/api/shop",
            json={
                "message": (
                    "Find me a USB-C charger"
                )
            },
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": (
            "The AI intent service is "
            "temporarily unavailable."
        )
    }
    assert (
        "Provider failure details"
        not in response.text
    )


def test_checkout_requires_explicit_confirmation():
    with patch(
        "backend.app.main.create_checkout_order",
    ) as mock_create_order:
        response = client.post(
            "/api/checkout/confirm",
            json={
                "quote_id": TEST_QUOTE_ID,
                "confirmed": False,
            },
        )

    assert response.status_code == 422
    mock_create_order.assert_not_called()


def test_checkout_confirmation_returns_order():
    checkout_order = CheckoutOrder(
        quote_id=TEST_QUOTE_ID,
        razorpay_order_id=TEST_ORDER_ID,
        razorpay_key_id="rzp_test_example",
        amount_paise=199_900,
        currency="INR",
        status="created",
    )

    with patch(
        "backend.app.main.create_checkout_order",
        return_value=checkout_order,
    ) as mock_create_order:
        response = client.post(
            "/api/checkout/confirm",
            json={
                "quote_id": TEST_QUOTE_ID,
                "confirmed": True,
            },
        )

    assert response.status_code == 200
    assert (
        response.json()["razorpay_order_id"]
        == TEST_ORDER_ID
    )
    assert (
        response.json()["amount_paise"]
        == 199_900
    )

    mock_create_order.assert_called_once_with(
        quote_id=TEST_QUOTE_ID,
    )


@pytest.mark.parametrize(
    (
        "error",
        "expected_status",
        "expected_detail",
    ),
    [
        (
            QuoteNotFoundError(
                "internal quote lookup failure"
            ),
            404,
            "The quote was not found.",
        ),
        (
            QuoteNotLinkedError(
                "internal session-link failure"
            ),
            409,
            (
                "The quote is not linked to a "
                "completed shopping session."
            ),
        ),
        (
            QuoteExpiredError(
                "internal expiration timestamp failure"
            ),
            410,
            (
                "The quote has expired. "
                "Please request a new quote."
            ),
        ),
        (
            RazorpayOrderError(
                "internal provider authentication "
                "failure"
            ),
            502,
            (
                "The payment provider could not "
                "create the order."
            ),
        ),
    ],
)
def test_checkout_confirmation_maps_failures_safely(
    error,
    expected_status,
    expected_detail,
):
    with patch(
        "backend.app.main.create_checkout_order",
        side_effect=error,
    ):
        response = client.post(
            "/api/checkout/confirm",
            json={
                "quote_id": TEST_QUOTE_ID,
                "confirmed": True,
            },
        )

    assert (
        response.status_code
        == expected_status
    )
    assert response.json() == {
        "detail": expected_detail,
    }
    assert str(error) not in response.text


def test_payment_verification_returns_verified_payment():
    verified_payment = StoredPayment(
        quote_id=TEST_QUOTE_ID,
        razorpay_order_id=TEST_ORDER_ID,
        razorpay_payment_id=TEST_PAYMENT_ID,
        status="verified",
        verified_at=datetime.now(
            timezone.utc
        ),
    )

    with patch(
        "backend.app.main.verify_and_record_payment",
        return_value=verified_payment,
    ) as mock_verify:
        response = client.post(
            "/api/payment/verify",
            json={
                "quote_id": TEST_QUOTE_ID,
                "razorpay_order_id": (
                    TEST_ORDER_ID
                ),
                "razorpay_payment_id": (
                    TEST_PAYMENT_ID
                ),
                "razorpay_signature": (
                    TEST_SIGNATURE
                ),
            },
        )

    assert response.status_code == 200
    assert (
        response.json()["status"]
        == "verified"
    )
    assert (
        response.json()["razorpay_payment_id"]
        == TEST_PAYMENT_ID
    )

    mock_verify.assert_called_once_with(
        quote_id=TEST_QUOTE_ID,
        razorpay_order_id=TEST_ORDER_ID,
        razorpay_payment_id=TEST_PAYMENT_ID,
        razorpay_signature=TEST_SIGNATURE,
    )


def test_payment_verification_rejects_malformed_signature():
    with patch(
        "backend.app.main.verify_and_record_payment",
    ) as mock_verify:
        response = client.post(
            "/api/payment/verify",
            json={
                "quote_id": TEST_QUOTE_ID,
                "razorpay_order_id": (
                    TEST_ORDER_ID
                ),
                "razorpay_payment_id": (
                    TEST_PAYMENT_ID
                ),
                "razorpay_signature": "invalid",
            },
        )

    assert response.status_code == 422
    mock_verify.assert_not_called()


@pytest.mark.parametrize(
    (
        "error",
        "expected_status",
        "expected_detail",
    ),
    [
        (
            PaymentQuoteNotFoundError(
                "internal payment lookup failure"
            ),
            404,
            "The payment quote was not found.",
        ),
        (
            PaymentStateError(
                "internal stored order mismatch"
            ),
            409,
            (
                "The payment does not match "
                "the stored order."
            ),
        ),
        (
            InvalidPaymentSignatureError(
                "internal HMAC comparison failure"
            ),
            400,
            (
                "Payment signature verification "
                "failed."
            ),
        ),
    ],
)
def test_payment_verification_maps_failures_safely(
    error,
    expected_status,
    expected_detail,
):
    with patch(
        "backend.app.main.verify_and_record_payment",
        side_effect=error,
    ):
        response = client.post(
            "/api/payment/verify",
            json={
                "quote_id": TEST_QUOTE_ID,
                "razorpay_order_id": (
                    TEST_ORDER_ID
                ),
                "razorpay_payment_id": (
                    TEST_PAYMENT_ID
                ),
                "razorpay_signature": (
                    TEST_SIGNATURE
                ),
            },
        )

    assert (
        response.status_code
        == expected_status
    )
    assert response.json() == {
        "detail": expected_detail,
    }
    assert str(error) not in response.text