from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.app.audit_events import (
    AuditEvent,
    AuditEventConflictError,
    deterministic_audit_event_id,
    get_audit_event,
    list_audit_events,
    list_quote_audit_events,
    new_audit_event,
    save_audit_event_idempotently,
)


SESSION_ID = (
    "session_00000000000000000000000000000001"
)
OTHER_SESSION_ID = (
    "session_00000000000000000000000000000002"
)
QUOTE_ID = (
    "quote_00000000000000000000000000000001"
)
CREATED_AT = datetime(
    2026,
    1,
    1,
    tzinfo=timezone.utc,
)


@pytest.fixture(autouse=True)
def isolated_database(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "CARTPILOT_DB_PATH",
        str(tmp_path / "audit-events.db"),
    )


def make_event(
    *,
    event_id: str = (
        "audit_00000000000000000000000000000001"
    ),
    session_id: str = SESSION_ID,
    event_type: str = "base_product_selected",
    reason_code: str = "BUYER_SELECTED_OFFERED_SKU",
    explanation: str = (
        "The buyer selected an offered product."
    ),
) -> AuditEvent:
    return new_audit_event(
        event_id=event_id,
        session_id=session_id,
        quote_id=QUOTE_ID,
        event_type=event_type,
        actor="buyer",
        outcome="recorded",
        reason_code=reason_code,
        explanation=explanation,
        sku="chg-001",
        amount_paise=100_000,
        currency="INR",
        created_at=CREATED_AT,
    )


def test_new_event_normalizes_sku() -> None:
    event = make_event()

    assert event.sku == "CHG-001"
    assert event.created_at.tzinfo is not None


def test_deterministic_event_id_uses_logical_subject() -> None:
    first_id = deterministic_audit_event_id(
        session_id=SESSION_ID,
        event_type="base_product_selected",
        subject="accepted:CHG-001",
    )
    retry_id = deterministic_audit_event_id(
        session_id=SESSION_ID,
        event_type="base_product_selected",
        subject="accepted:CHG-001",
    )
    different_id = deterministic_audit_event_id(
        session_id=SESSION_ID,
        event_type="base_product_selected",
        subject="accepted:CHG-002",
    )

    assert first_id == retry_id
    assert first_id != different_id


def test_event_rejects_unknown_fields() -> None:
    payload = make_event().model_dump()
    payload["buyer_email"] = "not-allowed@example.com"

    with pytest.raises(ValidationError):
        AuditEvent.model_validate(payload)


def test_event_requires_currency_with_amount() -> None:
    payload = make_event().model_dump()
    payload["currency"] = None

    with pytest.raises(
        ValidationError,
        match="provided together",
    ):
        AuditEvent.model_validate(payload)


def test_event_requires_timezone_aware_timestamp() -> None:
    payload = make_event().model_dump()
    payload["created_at"] = datetime(2026, 1, 1)

    with pytest.raises(
        ValidationError,
        match="include timezone",
    ):
        AuditEvent.model_validate(payload)


def test_saves_and_loads_event() -> None:
    event = make_event()

    stored_event = save_audit_event_idempotently(
        event
    )

    assert stored_event == event
    assert get_audit_event(event.event_id) == event
    assert list_audit_events(SESSION_ID) == [event]
    assert list_quote_audit_events(QUOTE_ID) == [
        event
    ]


def test_events_are_listed_in_append_order() -> None:
    first_event = make_event()
    second_event = make_event(
        event_id=(
            "audit_00000000000000000000000000000002"
        ),
        event_type="quote_created",
        reason_code="TRUSTED_TERMS_STORED",
        explanation=(
            "The validated quote terms were stored."
        ),
    )

    save_audit_event_idempotently(first_event)
    save_audit_event_idempotently(second_event)

    assert list_audit_events(SESSION_ID) == [
        first_event,
        second_event,
    ]


def test_identical_retry_returns_existing_event() -> None:
    event = make_event()

    first_result = save_audit_event_idempotently(
        event
    )
    retry_result = save_audit_event_idempotently(
        event
    )

    assert retry_result == first_result
    assert list_audit_events(SESSION_ID) == [event]


def test_logical_retry_keeps_original_timestamp() -> None:
    event = make_event()
    retry_payload = event.model_dump()
    retry_payload["created_at"] = datetime(
        2026,
        1,
        2,
        tzinfo=timezone.utc,
    )
    retry_event = AuditEvent.model_validate(
        retry_payload
    )

    save_audit_event_idempotently(event)
    retry_result = save_audit_event_idempotently(
        retry_event
    )

    assert retry_result == event
    assert retry_result.created_at == CREATED_AT


def test_conflicting_event_id_is_rejected() -> None:
    save_audit_event_idempotently(make_event())

    conflicting_event = make_event(
        explanation="A conflicting explanation.",
    )

    with pytest.raises(
        AuditEventConflictError,
        match="different audit event",
    ):
        save_audit_event_idempotently(
            conflicting_event
        )


def test_session_queries_are_isolated() -> None:
    first_event = make_event()
    second_event = make_event(
        event_id=(
            "audit_00000000000000000000000000000002"
        ),
        session_id=OTHER_SESSION_ID,
    )

    save_audit_event_idempotently(first_event)
    save_audit_event_idempotently(second_event)

    assert list_audit_events(SESSION_ID) == [
        first_event
    ]
    assert list_audit_events(OTHER_SESSION_ID) == [
        second_event
    ]
