from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.app.database import database_connection
from backend.app.models import (
    ShoppingRequest,
    ShoppingSession,
)
from backend.app.quote_store import (
    get_stored_quote,
    initialize_quote_store,
)


class ShoppingSessionError(Exception):
    pass


class ShoppingSessionNotFoundError(
    ShoppingSessionError
):
    pass


class ShoppingSessionExpiredError(
    ShoppingSessionError
):
    pass


class ShoppingSessionStateError(
    ShoppingSessionError
):
    pass


def initialize_shopping_session_store() -> None:
    initialize_quote_store()

    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shopping_sessions (
                session_id TEXT PRIMARY KEY,
                session_json TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (
                        status IN (
                            'awaiting_base_selection',
                            'awaiting_cross_sell_decision',
                            'quote_created',
                            'expired'
                        )
                    ),
                quote_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY (quote_id)
                    REFERENCES quotes (quote_id)
            )
            """
        )


def _normalize_skus(
    skus: list[str],
) -> list[str]:
    normalized_skus: list[str] = []

    for sku in skus:
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError(
                "Product SKUs must be non-empty strings."
            )

        normalized_skus.append(
            sku.strip().upper()
        )

    return normalized_skus


def _updated_session(
    session: ShoppingSession,
    **changes: object,
) -> ShoppingSession:
    session_data = session.model_dump(
        mode="python"
    )
    session_data.update(changes)

    return ShoppingSession.model_validate(
        session_data
    )


def _save_session_transition(
    *,
    previous_status: str,
    updated_session: ShoppingSession,
) -> ShoppingSession:
    with database_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE shopping_sessions
            SET session_json = ?,
                status = ?,
                quote_id = ?
            WHERE session_id = ?
              AND status = ?
            """,
            (
                updated_session.model_dump_json(),
                updated_session.status,
                updated_session.quote_id,
                updated_session.session_id,
                previous_status,
            ),
        )

        if cursor.rowcount != 1:
            raise ShoppingSessionStateError(
                "Shopping session changed during the operation."
            )

    return updated_session


def create_shopping_session(
    *,
    request: ShoppingRequest,
    catalog_version: str,
    base_product_skus: list[str],
    validity_minutes: int = 10,
) -> ShoppingSession:
    if not 1 <= validity_minutes <= 30:
        raise ValueError(
            "Shopping-session validity must be between "
            "1 and 30 minutes."
        )

    normalized_skus = _normalize_skus(
        base_product_skus
    )
    created_at = datetime.now(timezone.utc)

    session = ShoppingSession(
        session_id=f"session_{uuid4().hex}",
        catalog_version=catalog_version.strip(),
        request=request,
        base_product_skus=normalized_skus,
        selected_base_product_sku=None,
        cross_sell_option_skus=[],
        status="awaiting_base_selection",
        quote_id=None,
        created_at=created_at,
        expires_at=created_at + timedelta(
            minutes=validity_minutes
        ),
    )

    initialize_shopping_session_store()

    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO shopping_sessions (
                session_id,
                session_json,
                status,
                quote_id,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, NULL, ?, ?)
            """,
            (
                session.session_id,
                session.model_dump_json(),
                session.status,
                session.created_at.isoformat(),
                session.expires_at.isoformat(),
            ),
        )

    return session


def get_shopping_session(
    session_id: str,
) -> ShoppingSession | None:
    cleaned_session_id = session_id.strip()

    if not cleaned_session_id:
        raise ValueError(
            "Shopping session ID is required."
        )

    initialize_shopping_session_store()

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT
                session_json,
                status,
                quote_id
            FROM shopping_sessions
            WHERE session_id = ?
            """,
            (cleaned_session_id,),
        ).fetchone()

    if row is None:
        return None

    session_json, stored_status, stored_quote_id = row

    session = ShoppingSession.model_validate_json(
        session_json
    )

    if session.status != stored_status:
        raise ValueError(
            "Stored shopping-session status is inconsistent."
        )

    if session.quote_id != stored_quote_id:
        raise ValueError(
            "Stored shopping-session quote is inconsistent."
        )

    return session


def mark_shopping_session_expired(
    session_id: str,
) -> ShoppingSession:
    session = get_shopping_session(session_id)

    if session is None:
        raise ShoppingSessionNotFoundError(
            "Shopping session was not found."
        )

    if session.status == "expired":
        return session

    if session.status == "quote_created":
        raise ShoppingSessionStateError(
            "A completed shopping session cannot be expired."
        )

    expired_session = _updated_session(
        session,
        status="expired",
    )

    return _save_session_transition(
        previous_status=session.status,
        updated_session=expired_session,
    )


def _ensure_session_not_expired(
    session: ShoppingSession,
) -> None:
    if session.status == "expired":
        raise ShoppingSessionExpiredError(
            "Shopping session has expired."
        )

    if (
        session.status != "quote_created"
        and datetime.now(timezone.utc)
        >= session.expires_at
    ):
        mark_shopping_session_expired(
            session.session_id
        )

        raise ShoppingSessionExpiredError(
            "Shopping session has expired."
        )


def record_base_product_selection(
    *,
    session_id: str,
    base_product_sku: str,
    cross_sell_option_skus: list[str],
) -> ShoppingSession:
    cleaned_base_sku = base_product_sku.strip().upper()

    if not cleaned_base_sku:
        raise ValueError(
            "Base-product SKU is required."
        )

    normalized_cross_sell_skus = _normalize_skus(
        cross_sell_option_skus
    )

    session = get_shopping_session(session_id)

    if session is None:
        raise ShoppingSessionNotFoundError(
            "Shopping session was not found."
        )

    _ensure_session_not_expired(session)

    if session.status == "awaiting_cross_sell_decision":
        if (
            session.selected_base_product_sku
            == cleaned_base_sku
            and session.cross_sell_option_skus
            == normalized_cross_sell_skus
        ):
            return session

        raise ShoppingSessionStateError(
            "A different base product has already been selected."
        )

    if session.status != "awaiting_base_selection":
        raise ShoppingSessionStateError(
            "Shopping session is not awaiting base selection."
        )

    if cleaned_base_sku not in session.base_product_skus:
        raise ShoppingSessionStateError(
            "Selected base product was not offered "
            "for this shopping session."
        )

    updated_session = _updated_session(
        session,
        selected_base_product_sku=cleaned_base_sku,
        cross_sell_option_skus=(
            normalized_cross_sell_skus
        ),
        status="awaiting_cross_sell_decision",
    )

    return _save_session_transition(
        previous_status="awaiting_base_selection",
        updated_session=updated_session,
    )


def mark_shopping_session_quoted(
    *,
    session_id: str,
    quote_id: str,
) -> ShoppingSession:
    cleaned_quote_id = quote_id.strip()

    if not cleaned_quote_id:
        raise ValueError("Quote ID is required.")

    session = get_shopping_session(session_id)

    if session is None:
        raise ShoppingSessionNotFoundError(
            "Shopping session was not found."
        )

    if session.status == "quote_created":
        if session.quote_id == cleaned_quote_id:
            return session

        raise ShoppingSessionStateError(
            "A different quote already belongs "
            "to this shopping session."
        )

    _ensure_session_not_expired(session)

    if session.status != "awaiting_cross_sell_decision":
        raise ShoppingSessionStateError(
            "A base product must be selected "
            "before quote creation."
        )

    stored_quote = get_stored_quote(
        cleaned_quote_id
    )

    if stored_quote is None:
        raise ShoppingSessionStateError(
            "The quote does not exist."
        )

    if stored_quote.status != "pending":
        raise ShoppingSessionStateError(
            "Only a pending quote can be linked "
            "to a shopping session."
        )

    quote = stored_quote.quote

    if (
        quote.catalog_version
        != session.catalog_version
    ):
        raise ShoppingSessionStateError(
            "Quote catalog version does not match "
            "the shopping session."
        )

    if (
        quote.base_product_sku
        != session.selected_base_product_sku
    ):
        raise ShoppingSessionStateError(
            "Quote base product does not match "
            "the buyer's selection."
        )

    if (
        quote.upsell_product_sku is not None
        and quote.upsell_product_sku
        not in session.cross_sell_option_skus
    ):
        raise ShoppingSessionStateError(
            "Quote cross-sell was not offered "
            "for this shopping session."
        )

    updated_session = _updated_session(
        session,
        status="quote_created",
        quote_id=cleaned_quote_id,
    )

    return _save_session_transition(
        previous_status="awaiting_cross_sell_decision",
        updated_session=updated_session,
    )
