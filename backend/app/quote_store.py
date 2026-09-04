import os
import sqlite3
from pathlib import Path
from typing import Literal
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from backend.app.models import Quote


DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "cartpilot.db"
)


class StoredQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: Quote
    status: Literal[
        "pending",
        "order_created",
        "expired",
    ]
    razorpay_order_id: str | None = None

class StoredPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    status: Literal["verified"]
    verified_at: datetime


def _get_database_path() -> Path:
    configured_path = os.getenv("CARTPILOT_DB_PATH")

    if configured_path:
        return Path(configured_path)

    return DEFAULT_DATABASE_PATH


def _connect() -> sqlite3.Connection:
    database_path = _get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")

    return connection

def initialize_quote_store() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                quote_id TEXT PRIMARY KEY,
                quote_json TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (
                        status IN (
                            'pending',
                            'order_created',
                            'expired'
                        )
                    ),
                razorpay_order_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                razorpay_payment_id TEXT PRIMARY KEY,
                razorpay_order_id TEXT NOT NULL UNIQUE,
            quote_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
            CHECK (status = 'verified'),
            verified_at TEXT NOT NULL,
            FOREIGN KEY (quote_id)
            REFERENCES quotes (quote_id)
            )
            """
        )


def save_quote(quote: Quote) -> StoredQuote:
    initialize_quote_store()

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO quotes (
                quote_id,
                quote_json,
                status,
                razorpay_order_id,
                created_at,
                expires_at
            )
            VALUES (?, ?, 'pending', NULL, ?, ?)
            """,
            (
                quote.quote_id,
                quote.model_dump_json(),
                quote.created_at.isoformat(),
                quote.expires_at.isoformat(),
            ),
        )

    return StoredQuote(
        quote=quote,
        status="pending",
        razorpay_order_id=None,
    )


def get_stored_quote(
    quote_id: str,
) -> StoredQuote | None:
    initialize_quote_store()

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT quote_json, status, razorpay_order_id
            FROM quotes
            WHERE quote_id = ?
            """,
            (quote_id,),
        ).fetchone()

    if row is None:
        return None

    quote_json, status, razorpay_order_id = row

    return StoredQuote(
        quote=Quote.model_validate_json(quote_json),
        status=status,
        razorpay_order_id=razorpay_order_id,
    )

def mark_quote_expired(
    quote_id: str,
) -> StoredQuote:
    initialize_quote_store()

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE quotes
            SET status = 'expired'
            WHERE quote_id = ?
              AND status = 'pending'
            """,
            (quote_id,),
        )

    stored_quote = get_stored_quote(quote_id)

    if stored_quote is None:
        raise KeyError(f"Quote not found: {quote_id}")

    if cursor.rowcount == 0 and stored_quote.status != "expired":
        raise ValueError(
            f"Quote cannot be expired from status: {stored_quote.status}"
        )

    return stored_quote


def mark_order_created(
    quote_id: str,
    razorpay_order_id: str,
) -> StoredQuote:
    initialize_quote_store()

    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE quotes
            SET status = 'order_created',
                razorpay_order_id = ?
            WHERE quote_id = ?
              AND status = 'pending'
            """,
            (
                razorpay_order_id,
                quote_id,
            ),
        )

    stored_quote = get_stored_quote(quote_id)

    if stored_quote is None:
        raise KeyError(f"Quote not found: {quote_id}")

    if cursor.rowcount == 0:
        if (
            stored_quote.status == "order_created"
            and stored_quote.razorpay_order_id
            == razorpay_order_id
        ):
            return stored_quote

        raise ValueError(
            f"Order cannot be created from quote status: "
            f"{stored_quote.status}"
        )

    return stored_quote

def get_verified_payment(
    quote_id: str,
) -> StoredPayment | None:
    initialize_quote_store()

    with _connect() as connection:
        row = connection.execute(
            """
            SELECT
                quote_id,
                razorpay_order_id,
                razorpay_payment_id,
                status,
                verified_at
            FROM payments
            WHERE quote_id = ?
            """,
            (quote_id,),
        ).fetchone()

    if row is None:
        return None

    return StoredPayment(
        quote_id=row[0],
        razorpay_order_id=row[1],
        razorpay_payment_id=row[2],
        status=row[3],
        verified_at=datetime.fromisoformat(row[4]),
    )


def save_verified_payment(
    *,
    quote_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
) -> StoredPayment:
    stored_quote = get_stored_quote(quote_id)

    if stored_quote is None:
        raise KeyError(f"Quote not found: {quote_id}")

    if stored_quote.status != "order_created":
        raise ValueError(
            "Payment cannot be recorded before order creation."
        )

    if stored_quote.razorpay_order_id != razorpay_order_id:
        raise ValueError(
            "Payment order ID does not match the stored order."
        )

    verified_at = datetime.now(timezone.utc)

    with _connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO payments (
                razorpay_payment_id,
                razorpay_order_id,
                quote_id,
                status,
                verified_at
            )
            VALUES (?, ?, ?, 'verified', ?)
            """,
            (
                razorpay_payment_id,
                razorpay_order_id,
                quote_id,
                verified_at.isoformat(),
            ),
        )

    stored_payment = get_verified_payment(quote_id)

    if stored_payment is None:
        raise ValueError(
            "Payment conflicts with an existing payment record."
        )

    if (
        stored_payment.razorpay_order_id
        != razorpay_order_id
        or stored_payment.razorpay_payment_id
        != razorpay_payment_id
    ):
        raise ValueError(
            "A different payment is already stored for this quote."
        )

    return stored_payment