from sqlite3 import IntegrityError

from backend.app.database import database_connection
from backend.app.models import PurchaseMandate


class PurchaseMandateConflictError(Exception):
    pass


def initialize_purchase_mandate_store() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_mandates (
                mandate_id TEXT PRIMARY KEY,
                mandate_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )


def save_purchase_mandate(
    mandate: PurchaseMandate,
) -> PurchaseMandate:
    initialize_purchase_mandate_store()

    try:
        with database_connection() as connection:
            connection.execute(
                """
                INSERT INTO purchase_mandates (
                    mandate_id,
                    mandate_json,
                    created_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    mandate.mandate_id,
                    mandate.model_dump_json(),
                    mandate.created_at.isoformat(),
                    mandate.expires_at.isoformat(),
                ),
            )
    except IntegrityError as exc:
        raise PurchaseMandateConflictError(
            "A purchase mandate already exists with this ID."
        ) from exc

    return mandate


def get_purchase_mandate(
    mandate_id: str,
) -> PurchaseMandate | None:
    cleaned_id = mandate_id.strip()
    if not cleaned_id:
        raise ValueError("Purchase mandate ID is required.")

    initialize_purchase_mandate_store()

    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT mandate_json
            FROM purchase_mandates
            WHERE mandate_id = ?
            """,
            (cleaned_id,),
        ).fetchone()

    if row is None:
        return None

    return PurchaseMandate.model_validate_json(row[0])
