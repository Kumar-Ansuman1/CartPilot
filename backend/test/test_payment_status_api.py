from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.database import database_connection
from backend.app.main import app
from backend.app.models import Quote
from backend.app.payment_status import read_payment_status
from backend.app.quote_store import (
    get_verified_payment,
    initialize_quote_store,
    mark_order_created,
    mark_quote_expired,
    save_quote,
    save_verified_payment,
)


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "payment-status.db"),
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_example")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-key-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def create_quote() -> Quote:
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
    save_quote(quote)
    return quote


def test_reports_checkout_not_started_for_pending_quote():
    quote = create_quote()

    result = read_payment_status(quote.quote_id)

    assert result.status == "checkout_not_started"
    assert result.razorpay_order_id is None
    assert result.razorpay_payment_id is None
    assert result.verification_source is None


def test_reports_confirmation_pending_after_order_creation():
    quote = create_quote()
    mark_order_created(quote.quote_id, "order_status123")

    result = read_payment_status(quote.quote_id)

    assert result.status == "confirmation_pending"
    assert result.razorpay_order_id == "order_status123"
    assert result.razorpay_payment_id is None


@pytest.mark.parametrize(
    "source",
    ["browser_callback", "webhook"],
)
def test_reports_verified_payment_source(source):
    quote = create_quote()
    mark_order_created(quote.quote_id, "order_status123")
    payment = save_verified_payment(
        quote_id=quote.quote_id,
        razorpay_order_id="order_status123",
        razorpay_payment_id="pay_status123",
        verification_source=source,
    )

    result = read_payment_status(quote.quote_id)

    assert result.status == "verified"
    assert result.razorpay_payment_id == "pay_status123"
    assert result.verification_source == source
    assert result.verified_at == payment.verified_at


def test_reports_expired_quote_without_payment():
    quote = create_quote()
    mark_quote_expired(quote.quote_id)

    assert read_payment_status(quote.quote_id).status == "expired"


def test_existing_payment_table_is_migrated_with_safe_default():
    verified_at = datetime.now(timezone.utc)

    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE payments (
                razorpay_payment_id TEXT PRIMARY KEY,
                razorpay_order_id TEXT NOT NULL UNIQUE,
                quote_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                verified_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO payments (
                razorpay_payment_id,
                razorpay_order_id,
                quote_id,
                status,
                verified_at
            )
            VALUES (?, ?, ?, 'verified', ?)
            """,
            (
                "pay_legacy123",
                "order_legacy123",
                "quote_legacy",
                verified_at.isoformat(),
            ),
        )

    initialize_quote_store()

    with database_connection() as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(payments)"
            ).fetchall()
        }

    assert "verification_source" in columns

    payment = get_verified_payment("quote_legacy")
    assert payment is not None
    assert payment.verification_source == "browser_callback"


def test_api_returns_no_store_status_without_mutating_payment():
    quote = create_quote()
    mark_order_created(quote.quote_id, "order_status123")
    client = TestClient(app)

    first = client.get(f"/api/payment/status/{quote.quote_id}")
    second = client.get(f"/api/payment/status/{quote.quote_id}")

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["status"] == "confirmation_pending"
    assert second.json() == first.json()
    assert get_verified_payment(quote.quote_id) is None


def test_api_returns_not_found_for_unknown_quote():
    quote_id = f"quote_{uuid4().hex}"

    response = TestClient(app).get(
        f"/api/payment/status/{quote_id}"
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "quote_id",
    ["bad", "quote_123", "quote_" + "g" * 32],
)
def test_api_rejects_malformed_quote_id(quote_id):
    response = TestClient(app).get(
        f"/api/payment/status/{quote_id}"
    )

    assert response.status_code == 422
