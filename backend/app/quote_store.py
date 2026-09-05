from backend.app.database import database_connection
from typing import Literal
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from backend.app.models import Quote

from sqlite3 import IntegrityError


PaymentVerificationSource = Literal[
    "browser_callback",
    "webhook",
]


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
    verification_source: PaymentVerificationSource = (
        "browser_callback"
    )
    verified_at: datetime

class QuoteConflictError(Exception):
    pass


def _quote_terms(
    quote: Quote,
) -> tuple[
    str,
    str,
    str,
    int,
    str | None,
    int,
    int,
]:
    return (
        quote.catalog_version,
        quote.currency,
        quote.base_product_sku,
        quote.base_price_paise,
        quote.upsell_product_sku,
        quote.upsell_price_paise,
        quote.total_paise,
    )


def initialize_quote_store() -> None:
    with database_connection() as connection:
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
                verification_source TEXT NOT NULL
                    DEFAULT 'browser_callback'
                    CHECK (
                        verification_source IN (
                            'browser_callback',
                            'webhook'
                        )
                    ),
                verified_at TEXT NOT NULL,
                FOREIGN KEY (quote_id)
                    REFERENCES quotes (quote_id)
            )
            """
        )

        payment_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(payments)"
            ).fetchall()
        }

        if "verification_source" not in payment_columns:
            connection.execute(
                """
                ALTER TABLE payments
                ADD COLUMN verification_source TEXT NOT NULL
                    DEFAULT 'browser_callback'
                    CHECK (
                        verification_source IN (
                            'browser_callback',
                            'webhook'
                        )
                    )
                """
            )


def save_quote(quote: Quote) -> StoredQuote:
    initialize_quote_store()

    with database_connection() as connection:
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

    with database_connection() as connection:
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


def get_stored_quote_by_order_id(
    razorpay_order_id: str,
) -> StoredQuote | None:
    initialize_quote_store()

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT quote_json, status, razorpay_order_id
            FROM quotes
            WHERE razorpay_order_id = ?
            """,
            (razorpay_order_id,),
        ).fetchone()

    if row is None:
        return None

    quote_json, status, stored_order_id = row

    return StoredQuote(
        quote=Quote.model_validate_json(quote_json),
        status=status,
        razorpay_order_id=stored_order_id,
    )

def save_quote_idempotently(
    quote: Quote,
) -> StoredQuote:
    try:
        return save_quote(quote)
    except IntegrityError as exc:
        stored_quote = get_stored_quote(
            quote.quote_id
        )

        if stored_quote is None:
            raise

        if (
            _quote_terms(stored_quote.quote)
            != _quote_terms(quote)
        ):
            raise QuoteConflictError(
                "A different quote already exists "
                "for this shopping session."
            ) from exc

        return stored_quote

def mark_quote_expired(
    quote_id: str,
) -> StoredQuote:
    initialize_quote_store()

    with database_connection() as connection:
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

    with database_connection() as connection:
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

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT
                quote_id,
                razorpay_order_id,
                razorpay_payment_id,
                status,
                verification_source,
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
        verification_source=row[4],
        verified_at=datetime.fromisoformat(row[5]),
    )


def save_verified_payment(
    *,
    quote_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    verification_source: PaymentVerificationSource = (
        "browser_callback"
    ),
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

    with database_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO payments (
                razorpay_payment_id,
                razorpay_order_id,
                quote_id,
                status,
                verification_source,
                verified_at
            )
            VALUES (?, ?, ?, 'verified', ?, ?)
            """,
            (
                razorpay_payment_id,
                razorpay_order_id,
                quote_id,
                verification_source,
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
