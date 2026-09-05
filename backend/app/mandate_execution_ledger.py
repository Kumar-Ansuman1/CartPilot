from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.database import database_connection
from backend.app.purchase_mandate_store import get_purchase_mandate


MandateExecutionEventType = Literal[
    "reserved",
    "session_bound",
    "quote_bound",
    "consumed",
    "released",
]

MandateExecutionStatus = Literal[
    "reserved",
    "quote_ready",
    "consumed",
    "released",
]


class MandateExecutionError(Exception):
    pass


class MandateExecutionNotFoundError(MandateExecutionError):
    pass


class MandateAlreadyReservedError(MandateExecutionError):
    pass


class MandateAlreadyConsumedError(MandateExecutionError):
    pass


class MandateExecutionStateError(MandateExecutionError):
    pass


class MandateExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(pattern=r"^execution_event_[0-9a-f]{32}$")
    execution_id: str = Field(pattern=r"^execution_[0-9a-f]{32}$")
    mandate_id: str = Field(pattern=r"^mandate_[0-9a-f]{32}$")
    event_type: MandateExecutionEventType
    amount_paise: int | None = Field(default=None, ge=0)
    session_id: str | None = Field(
        default=None,
        pattern=r"^session_[0-9a-f]{32}$",
    )
    quote_id: str | None = Field(
        default=None,
        pattern=r"^quote_[0-9a-f]{32}$",
    )
    reason_code: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    created_at: datetime


class MandateExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str = Field(pattern=r"^execution_[0-9a-f]{32}$")
    mandate_id: str = Field(pattern=r"^mandate_[0-9a-f]{32}$")
    status: MandateExecutionStatus
    reserved_paise: int = Field(gt=0)
    committed_paise: int | None = Field(default=None, gt=0)
    session_id: str | None = Field(
        default=None,
        pattern=r"^session_[0-9a-f]{32}$",
    )
    quote_id: str | None = Field(
        default=None,
        pattern=r"^quote_[0-9a-f]{32}$",
    )
    created_at: datetime
    updated_at: datetime


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS mandate_execution_events (
    sequence_number INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    execution_id TEXT NOT NULL,
    mandate_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'reserved',
            'session_bound',
            'quote_bound',
            'consumed',
            'released'
        )
    ),
    amount_paise INTEGER,
    session_id TEXT,
    quote_id TEXT,
    reason_code TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


def initialize_mandate_execution_ledger() -> None:
    with database_connection() as connection:
        connection.execute(_CREATE_SQL)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mandate_execution_events_execution
            ON mandate_execution_events (execution_id, sequence_number)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mandate_execution_events_mandate
            ON mandate_execution_events (mandate_id, sequence_number)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mandate_execution_events_quote
            ON mandate_execution_events (quote_id, sequence_number)
            """
        )


def _event_from_row(row: tuple[object, ...]) -> MandateExecutionEvent:
    return MandateExecutionEvent(
        event_id=str(row[0]),
        execution_id=str(row[1]),
        mandate_id=str(row[2]),
        event_type=str(row[3]),
        amount_paise=row[4],
        session_id=row[5],
        quote_id=row[6],
        reason_code=str(row[7]),
        created_at=datetime.fromisoformat(str(row[8])),
    )


def _events_for_execution(
    connection,
    execution_id: str,
) -> list[MandateExecutionEvent]:
    rows = connection.execute(
        """
        SELECT
            event_id,
            execution_id,
            mandate_id,
            event_type,
            amount_paise,
            session_id,
            quote_id,
            reason_code,
            created_at
        FROM mandate_execution_events
        WHERE execution_id = ?
        ORDER BY sequence_number
        """,
        (execution_id,),
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def _state_from_events(
    events: list[MandateExecutionEvent],
) -> MandateExecutionState:
    if not events or events[0].event_type != "reserved":
        raise MandateExecutionStateError(
            "Mandate execution history does not begin with a reservation."
        )

    first = events[0]
    if first.amount_paise is None or first.amount_paise <= 0:
        raise MandateExecutionStateError(
            "Mandate reservation is missing its approved amount."
        )

    status: MandateExecutionStatus = "reserved"
    session_id: str | None = None
    quote_id: str | None = None
    committed_paise: int | None = None

    for event in events[1:]:
        if event.mandate_id != first.mandate_id:
            raise MandateExecutionStateError(
                "Mandate execution history changed mandate ownership."
            )

        if event.event_type == "session_bound":
            if status != "reserved" or session_id is not None:
                raise MandateExecutionStateError(
                    "Session binding is inconsistent with execution state."
                )
            if event.session_id is None:
                raise MandateExecutionStateError(
                    "Session binding is missing a session ID."
                )
            session_id = event.session_id

        elif event.event_type == "quote_bound":
            if status != "reserved" or session_id is None:
                raise MandateExecutionStateError(
                    "Quote binding requires a reserved, session-bound execution."
                )
            if event.quote_id is None or event.amount_paise is None:
                raise MandateExecutionStateError(
                    "Quote binding is missing quote terms."
                )
            if event.amount_paise <= 0 or event.amount_paise > first.amount_paise:
                raise MandateExecutionStateError(
                    "Quote amount exceeds the reserved mandate authority."
                )
            quote_id = event.quote_id
            committed_paise = event.amount_paise
            status = "quote_ready"

        elif event.event_type == "consumed":
            if status != "quote_ready" or quote_id is None:
                raise MandateExecutionStateError(
                    "Consumption requires an authorized quote."
                )
            if event.quote_id != quote_id:
                raise MandateExecutionStateError(
                    "Consumed quote does not match the authorized quote."
                )
            status = "consumed"

        elif event.event_type == "released":
            if status not in {"reserved", "quote_ready"}:
                raise MandateExecutionStateError(
                    "Only an active mandate execution can be released."
                )
            status = "released"

        else:
            raise MandateExecutionStateError(
                "Unknown mandate execution event."
            )

    return MandateExecutionState(
        execution_id=first.execution_id,
        mandate_id=first.mandate_id,
        status=status,
        reserved_paise=first.amount_paise,
        committed_paise=committed_paise,
        session_id=session_id,
        quote_id=quote_id,
        created_at=first.created_at,
        updated_at=events[-1].created_at,
    )


def _insert_event(
    connection,
    *,
    execution_id: str,
    mandate_id: str,
    event_type: MandateExecutionEventType,
    reason_code: str,
    amount_paise: int | None = None,
    session_id: str | None = None,
    quote_id: str | None = None,
    created_at: datetime | None = None,
) -> MandateExecutionEvent:
    event = MandateExecutionEvent(
        event_id=f"execution_event_{uuid4().hex}",
        execution_id=execution_id,
        mandate_id=mandate_id,
        event_type=event_type,
        amount_paise=amount_paise,
        session_id=session_id,
        quote_id=quote_id,
        reason_code=reason_code,
        created_at=created_at or datetime.now(timezone.utc),
    )
    connection.execute(
        """
        INSERT INTO mandate_execution_events (
            event_id,
            execution_id,
            mandate_id,
            event_type,
            amount_paise,
            session_id,
            quote_id,
            reason_code,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            event.execution_id,
            event.mandate_id,
            event.event_type,
            event.amount_paise,
            event.session_id,
            event.quote_id,
            event.reason_code,
            event.created_at.isoformat(),
        ),
    )
    return event


def list_mandate_execution_events(
    execution_id: str,
) -> list[MandateExecutionEvent]:
    cleaned_id = execution_id.strip()
    if not cleaned_id:
        raise ValueError("Mandate execution ID is required.")

    initialize_mandate_execution_ledger()
    with database_connection() as connection:
        return _events_for_execution(connection, cleaned_id)


def get_mandate_execution_state(
    execution_id: str,
) -> MandateExecutionState | None:
    events = list_mandate_execution_events(execution_id)
    if not events:
        return None
    return _state_from_events(events)


def get_mandate_execution_by_quote_id(
    quote_id: str,
) -> MandateExecutionState | None:
    cleaned_quote_id = quote_id.strip()
    if not cleaned_quote_id:
        raise ValueError("Quote ID is required.")

    initialize_mandate_execution_ledger()
    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT execution_id
            FROM mandate_execution_events
            WHERE quote_id = ?
            ORDER BY sequence_number DESC
            LIMIT 1
            """,
            (cleaned_quote_id,),
        ).fetchone()
        if row is None:
            return None
        events = _events_for_execution(connection, str(row[0]))
    return _state_from_events(events)


def reserve_mandate_execution(
    mandate_id: str,
    *,
    created_at: datetime | None = None,
) -> MandateExecutionState:
    mandate = get_purchase_mandate(mandate_id)
    if mandate is None:
        raise MandateExecutionNotFoundError(
            "Purchase mandate was not found."
        )

    now = created_at or datetime.now(timezone.utc)
    if now >= mandate.expires_at:
        raise MandateExecutionStateError(
            "Purchase mandate has expired."
        )

    initialize_mandate_execution_ledger()
    execution_id = f"execution_{uuid4().hex}"

    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT DISTINCT execution_id
            FROM mandate_execution_events
            WHERE mandate_id = ?
            """,
            (mandate.mandate_id,),
        ).fetchall()

        for row in rows:
            state = _state_from_events(
                _events_for_execution(connection, str(row[0]))
            )
            if state.status == "consumed":
                raise MandateAlreadyConsumedError(
                    "Purchase mandate has already been consumed."
                )
            if state.status in {"reserved", "quote_ready"}:
                raise MandateAlreadyReservedError(
                    "Purchase mandate already has an active execution."
                )

        _insert_event(
            connection,
            execution_id=execution_id,
            mandate_id=mandate.mandate_id,
            event_type="reserved",
            amount_paise=mandate.budget_paise,
            reason_code="MANDATE_BUDGET_RESERVED",
            created_at=now,
        )
        state = _state_from_events(
            _events_for_execution(connection, execution_id)
        )

    return state


def bind_execution_session(
    *,
    execution_id: str,
    session_id: str,
) -> MandateExecutionState:
    initialize_mandate_execution_ledger()
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        events = _events_for_execution(connection, execution_id)
        if not events:
            raise MandateExecutionNotFoundError(
                "Mandate execution was not found."
            )
        state = _state_from_events(events)
        if state.session_id == session_id:
            return state
        if state.status != "reserved" or state.session_id is not None:
            raise MandateExecutionStateError(
                "Mandate execution cannot bind this shopping session."
            )
        _insert_event(
            connection,
            execution_id=state.execution_id,
            mandate_id=state.mandate_id,
            event_type="session_bound",
            session_id=session_id,
            reason_code="DELEGATED_SESSION_BOUND",
        )
        return _state_from_events(
            _events_for_execution(connection, execution_id)
        )


def bind_execution_quote(
    *,
    execution_id: str,
    quote_id: str,
    amount_paise: int,
) -> MandateExecutionState:
    if amount_paise <= 0:
        raise ValueError("Quote amount must be positive.")

    initialize_mandate_execution_ledger()
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        events = _events_for_execution(connection, execution_id)
        if not events:
            raise MandateExecutionNotFoundError(
                "Mandate execution was not found."
            )
        state = _state_from_events(events)
        if state.quote_id == quote_id and state.committed_paise == amount_paise:
            return state
        if state.status != "reserved" or state.session_id is None:
            raise MandateExecutionStateError(
                "Mandate execution cannot bind a quote in its current state."
            )
        if amount_paise > state.reserved_paise:
            raise MandateExecutionStateError(
                "Quote exceeds the reserved mandate budget."
            )
        _insert_event(
            connection,
            execution_id=state.execution_id,
            mandate_id=state.mandate_id,
            event_type="quote_bound",
            amount_paise=amount_paise,
            session_id=state.session_id,
            quote_id=quote_id,
            reason_code="IMMUTABLE_QUOTE_BOUND",
        )
        return _state_from_events(
            _events_for_execution(connection, execution_id)
        )


def consume_mandate_execution_for_quote(
    quote_id: str,
) -> MandateExecutionState | None:
    state = get_mandate_execution_by_quote_id(quote_id)
    if state is None:
        return None
    if state.status == "consumed":
        return state
    if state.status != "quote_ready" or state.quote_id != quote_id:
        raise MandateExecutionStateError(
            "Mandate execution is not ready to be consumed."
        )

    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = _state_from_events(
            _events_for_execution(connection, state.execution_id)
        )
        if current.status == "consumed":
            return current
        if current.status != "quote_ready" or current.quote_id != quote_id:
            raise MandateExecutionStateError(
                "Mandate execution changed before consumption."
            )
        _insert_event(
            connection,
            execution_id=current.execution_id,
            mandate_id=current.mandate_id,
            event_type="consumed",
            amount_paise=current.committed_paise,
            session_id=current.session_id,
            quote_id=current.quote_id,
            reason_code="VERIFIED_PAYMENT_CONSUMED_MANDATE",
        )
        return _state_from_events(
            _events_for_execution(connection, current.execution_id)
        )


def release_mandate_execution(
    execution_id: str,
    *,
    reason_code: str = "DELEGATED_EXECUTION_RELEASED",
) -> MandateExecutionState:
    initialize_mandate_execution_ledger()
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        events = _events_for_execution(connection, execution_id)
        if not events:
            raise MandateExecutionNotFoundError(
                "Mandate execution was not found."
            )
        state = _state_from_events(events)
        if state.status == "released":
            return state
        if state.status == "consumed":
            raise MandateExecutionStateError(
                "A consumed mandate execution cannot be released."
            )
        _insert_event(
            connection,
            execution_id=state.execution_id,
            mandate_id=state.mandate_id,
            event_type="released",
            amount_paise=state.committed_paise,
            session_id=state.session_id,
            quote_id=state.quote_id,
            reason_code=reason_code,
        )
        return _state_from_events(
            _events_for_execution(connection, execution_id)
        )


def release_mandate_execution_for_quote(
    quote_id: str,
    *,
    reason_code: str = "QUOTE_EXPIRED_MANDATE_RELEASED",
) -> MandateExecutionState | None:
    state = get_mandate_execution_by_quote_id(quote_id)
    if state is None:
        return None
    if state.status == "released":
        return state
    if state.status == "consumed":
        return state
    return release_mandate_execution(
        state.execution_id,
        reason_code=reason_code,
    )
