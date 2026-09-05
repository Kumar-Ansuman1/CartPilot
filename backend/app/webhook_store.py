from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from backend.app.database import database_connection


class ProcessedWebhookEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=255)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_type: str = Field(min_length=1, max_length=100)
    processed_at: datetime


class WebhookEventConflictError(Exception):
    pass


def initialize_webhook_event_store() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_webhook_events (
                event_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )


def get_processed_webhook_event(
    event_id: str,
) -> ProcessedWebhookEvent | None:
    initialize_webhook_event_store()

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT
                event_id,
                payload_sha256,
                event_type,
                processed_at
            FROM processed_webhook_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

    if row is None:
        return None

    return ProcessedWebhookEvent(
        event_id=row[0],
        payload_sha256=row[1],
        event_type=row[2],
        processed_at=datetime.fromisoformat(row[3]),
    )


def save_processed_webhook_event(
    *,
    event_id: str,
    payload_sha256: str,
    event_type: str,
) -> tuple[ProcessedWebhookEvent, bool]:
    processed_at = datetime.now(timezone.utc)
    initialize_webhook_event_store()

    with database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO processed_webhook_events (
                event_id,
                payload_sha256,
                event_type,
                processed_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                event_id,
                payload_sha256,
                event_type,
                processed_at.isoformat(),
            ),
        )

    stored_event = get_processed_webhook_event(event_id)

    if stored_event is None:
        raise RuntimeError(
            "The processed webhook event could not be stored."
        )

    if (
        stored_event.payload_sha256 != payload_sha256
        or stored_event.event_type != event_type
    ):
        raise WebhookEventConflictError(
            "The webhook event ID was reused with different content."
        )

    return stored_event, cursor.rowcount == 1
