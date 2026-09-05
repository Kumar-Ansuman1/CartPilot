from datetime import datetime, timedelta, timezone
from sqlite3 import OperationalError
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.audit_events import (
    list_audit_events,
    record_audit_event,
)
from backend.app.database import database_connection
from backend.app.main import app
from backend.app.models import Quote, ShoppingRequest
from backend.app.quote_store import (
    mark_order_created,
    save_quote,
    save_verified_payment,
)
from backend.app.shopping_session_store import (
    create_shopping_session,
    get_shopping_session,
    mark_shopping_session_expired,
    mark_shopping_session_quoted,
    record_base_product_selection,
)


client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH", str(tmp_path / "audit-api.db")
    )

    def reject_network(*args, **kwargs):
        raise AssertionError("Reading audit history must not call a provider.")

    monkeypatch.setattr("requests.sessions.Session.request", reject_network)


def make_session():
    return create_shopping_session(
        request=ShoppingRequest(
            query="USB-C charger",
            budget_paise=300_000,
            allowed_categories=["chargers"],
            compatibility_tags=["usb-c"],
        ),
        catalog_version="test-v1",
        base_product_skus=["CHG-001"],
    )


def record_search(session_id, *, subject="search", created_at=None):
    return record_audit_event(
        session_id=session_id,
        event_type="catalog_searched",
        subject=subject,
        actor="deterministic_core",
        outcome="recorded",
        reason_code="CATALOG_SEARCH_COMPLETED",
        explanation="Products were filtered using the buyer's constraints.",
        amount_paise=300_000,
        currency="INR",
        created_at=created_at,
    )


def test_returns_only_requested_session_events_in_insertion_order():
    session = make_session()
    other_session = make_session()
    now = datetime.now(timezone.utc)
    first = record_search(session.session_id, created_at=now)
    other = record_search(other_session.session_id)
    # A repaired event can have an earlier timestamp but a later insertion.
    second = record_search(
        session.session_id,
        subject="retry",
        created_at=now - timedelta(minutes=1),
    )

    response = client.get(f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "session_id": session.session_id,
        "quote_id": None,
        "events": [
            first.model_dump(mode="json"),
            second.model_dump(mode="json"),
        ],
    }
    assert other.event_id not in response.text
    assert other_session.session_id not in response.text
    assert "request" not in response.json()


def test_existing_session_without_events_returns_empty_timeline():
    session = make_session()

    response = client.get(f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": session.session_id,
        "quote_id": None,
        "events": [],
    }


def test_unknown_session_returns_404_even_if_orphan_events_exist():
    session_id = f"session_{uuid4().hex}"
    record_search(session_id)

    response = client.get(f"/api/shop/{session_id}/audit")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "The shopping session was not found."
    }


@pytest.mark.parametrize("session_id", [
    "not-a-session",
    "session_abc",
    "session_" + "a" * 33,
    "session_" + "g" * 32,
    "session_" + "A" * 32,
])
def test_malformed_session_id_is_rejected_before_database_lookup(session_id):
    with patch("backend.app.main.get_shopping_session") as lookup:
        response = client.get(f"/api/shop/{session_id}/audit")

    assert response.status_code == 422
    lookup.assert_not_called()


@pytest.mark.parametrize("mark_expired", [False, True])
def test_expired_history_remains_readable_without_changing_session(mark_expired):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    with patch("backend.app.shopping_session_store.datetime") as clock:
        clock.now.return_value = past
        session = make_session()
    if mark_expired:
        mark_shopping_session_expired(session.session_id)
    event = record_search(session.session_id)
    before = get_shopping_session(session.session_id)

    response = client.get(f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 200
    assert response.json()["events"] == [event.model_dump(mode="json")]
    assert get_shopping_session(session.session_id) == before
    assert list_audit_events(session.session_id) == [event]


def test_quote_and_payment_history_is_readable_without_database_changes():
    session = make_session()
    now = datetime.now(timezone.utc)
    quote = Quote(
        quote_id=f"quote_{uuid4().hex}",
        catalog_version="test-v1",
        currency="INR",
        base_product_sku="CHG-001",
        base_price_paise=199_900,
        upsell_product_sku=None,
        upsell_price_paise=0,
        total_paise=199_900,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    record_base_product_selection(
        session_id=session.session_id,
        base_product_sku="CHG-001",
        cross_sell_option_skus=[],
    )
    save_quote(quote)
    mark_shopping_session_quoted(
        session_id=session.session_id, quote_id=quote.quote_id
    )
    mark_order_created(quote.quote_id, "order_audit123")
    payment = save_verified_payment(
        quote_id=quote.quote_id,
        razorpay_order_id="order_audit123",
        razorpay_payment_id="pay_audit123",
    )
    record_search(session.session_id)
    event = record_audit_event(
        session_id=session.session_id,
        quote_id=quote.quote_id,
        event_type="payment_verified",
        subject="payment",
        actor="deterministic_core",
        outcome="recorded",
        reason_code="PAYMENT_SIGNATURE_VERIFIED",
        explanation="The callback signature was verified for the stored order.",
        amount_paise=quote.total_paise,
        currency=quote.currency,
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment.razorpay_payment_id,
        created_at=payment.verified_at,
    )
    with database_connection() as connection:
        before = list(connection.iterdump())

    first = client.get(f"/api/shop/{session.session_id}/audit")
    second = client.get(f"/api/shop/{session.session_id}/audit")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["quote_id"] == quote.quote_id
    assert len(first.json()["events"]) == 2
    assert first.json()["events"][-1] == event.model_dump(mode="json")
    for secret_field in ("razorpay_signature", "razorpay_key_secret"):
        assert secret_field not in first.text
    with database_connection() as connection:
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_timeline_does_not_accept_write_methods(method):
    session = make_session()
    event = record_search(session.session_id)

    response = client.request(method, f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 405
    assert list_audit_events(session.session_id) == [event]


@pytest.mark.parametrize("failing_function", [
    "get_shopping_session", "list_audit_events"
])
def test_database_failure_returns_safe_503(failing_function):
    session = make_session()
    with patch(
        f"backend.app.main.{failing_function}",
        side_effect=OperationalError("Private database path or SQL details"),
    ):
        response = client.get(f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The audit timeline is temporarily unavailable."
    }
    assert "Private database" not in response.text


def test_corrupt_event_returns_safe_503():
    session = make_session()
    event = record_search(session.session_id)
    # Simulate storage damage outside the append-only application interface.
    with database_connection() as connection:
        connection.execute(
            "UPDATE audit_events SET event_json = ? WHERE event_id = ?",
            ('{"private_corruption_details": true}', event.event_id),
        )

    response = client.get(f"/api/shop/{session.session_id}/audit")

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The audit timeline is temporarily unavailable."
    }
    assert "private_corruption_details" not in response.text
