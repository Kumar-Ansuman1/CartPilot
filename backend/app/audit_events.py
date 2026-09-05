from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.app.database import database_connection


AuditActor = Literal[
    "buyer",
    "ai",
    "deterministic_core",
    "razorpay",
]

AuditEventType = Literal[
    "intent_extracted",
    "catalog_searched",
    "base_options_offered",
    "base_product_selected",
    "cross_sell_options_offered",
    "cross_sell_decided",
    "quote_created",
    "quote_expired",
    "session_expired",
    "checkout_confirmed",
    "order_creation_requested",
    "order_created",
    "payment_verification_requested",
    "payment_verified",
    "payment_rejected",
    "payment_reconciled",
]

AuditOutcome = Literal[
    "allowed",
    "rejected",
    "recorded",
    "failed",
    "recovered",
]


class AuditEvent(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    event_id: str = Field(
        pattern=r"^audit_[0-9a-f]{32}$"
    )
    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    quote_id: str | None = Field(
        default=None,
        pattern=r"^quote_[0-9a-f]{32}$",
    )

    event_type: AuditEventType
    actor: AuditActor
    outcome: AuditOutcome

    reason_code: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    explanation: str = Field(
        min_length=3,
        max_length=300,
    )

    sku: str | None = Field(
        default=None,
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z0-9][A-Z0-9_-]*$",
    )
    amount_paise: int | None = Field(
        default=None,
        ge=0,
    )
    currency: Literal["INR"] | None = None

    razorpay_order_id: str | None = Field(
        default=None,
        pattern=r"^order_[A-Za-z0-9]+$",
    )
    razorpay_payment_id: str | None = Field(
        default=None,
        pattern=r"^pay_[A-Za-z0-9]+$",
    )

    created_at: datetime

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return value.strip().upper()

    @model_validator(mode="after")
    def validate_event(self) -> "AuditEvent":
        has_amount = self.amount_paise is not None
        has_currency = self.currency is not None

        if has_amount != has_currency:
            raise ValueError(
                "Audit amount and currency must be "
                "provided together."
            )

        if self.created_at.tzinfo is None:
            raise ValueError(
                "Audit timestamp must include timezone "
                "information."
            )

        return self


class AuditEventConflictError(Exception):
    pass


def new_audit_event(
    *,
    session_id: str,
    event_type: AuditEventType,
    actor: AuditActor,
    outcome: AuditOutcome,
    reason_code: str,
    explanation: str,
    quote_id: str | None = None,
    sku: str | None = None,
    amount_paise: int | None = None,
    currency: Literal["INR"] | None = None,
    razorpay_order_id: str | None = None,
    razorpay_payment_id: str | None = None,
    event_id: str | None = None,
    created_at: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id or f"audit_{uuid4().hex}",
        session_id=session_id,
        quote_id=quote_id,
        event_type=event_type,
        actor=actor,
        outcome=outcome,
        reason_code=reason_code,
        explanation=explanation,
        sku=sku,
        amount_paise=amount_paise,
        currency=currency,
        razorpay_order_id=razorpay_order_id,
        razorpay_payment_id=razorpay_payment_id,
        created_at=(
            created_at
            or datetime.now(timezone.utc)
        ),
    )


def initialize_audit_event_store() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                quote_id TEXT,
                event_type TEXT NOT NULL,
                event_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_audit_events_session_sequence
            ON audit_events (
                session_id,
                sequence_number
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_audit_events_quote_sequence
            ON audit_events (
                quote_id,
                sequence_number
            )
            """
        )


def get_audit_event(
    event_id: str,
) -> AuditEvent | None:
    cleaned_event_id = event_id.strip()

    if not cleaned_event_id:
        raise ValueError("Audit event ID is required.")

    initialize_audit_event_store()

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT event_json
            FROM audit_events
            WHERE event_id = ?
            """,
            (cleaned_event_id,),
        ).fetchone()

    if row is None:
        return None

    return AuditEvent.model_validate_json(row[0])


def save_audit_event_idempotently(
    event: AuditEvent,
) -> AuditEvent:
    initialize_audit_event_store()

    with database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO audit_events (
                event_id,
                session_id,
                quote_id,
                event_type,
                event_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.session_id,
                event.quote_id,
                event.event_type,
                event.model_dump_json(),
                event.created_at.isoformat(),
            ),
        )

        if cursor.rowcount == 1:
            return event

    existing_event = get_audit_event(event.event_id)

    if existing_event == event:
        return existing_event

    raise AuditEventConflictError(
        "A different audit event already exists "
        "with this event ID."
    )


def list_audit_events(
    session_id: str,
) -> list[AuditEvent]:
    cleaned_session_id = session_id.strip()

    if not cleaned_session_id:
        raise ValueError(
            "Shopping session ID is required."
        )

    initialize_audit_event_store()

    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT event_json
            FROM audit_events
            WHERE session_id = ?
            ORDER BY sequence_number
            """,
            (cleaned_session_id,),
        ).fetchall()

    return [
        AuditEvent.model_validate_json(row[0])
        for row in rows
    ]


def list_quote_audit_events(
    quote_id: str,
) -> list[AuditEvent]:
    cleaned_quote_id = quote_id.strip()

    if not cleaned_quote_id:
        raise ValueError("Quote ID is required.")

    initialize_audit_event_store()

    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT event_json
            FROM audit_events
            WHERE quote_id = ?
            ORDER BY sequence_number
            """,
            (cleaned_quote_id,),
        ).fetchall()

    return [
        AuditEvent.model_validate_json(row[0])
        for row in rows
    ]
