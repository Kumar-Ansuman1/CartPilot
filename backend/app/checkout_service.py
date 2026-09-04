from datetime import datetime, timezone
from threading import Lock
from typing import Literal

import razorpay
from pydantic import BaseModel, ConfigDict

from backend.app.config import get_settings
from backend.app.quote_store import (
    get_stored_quote,
    mark_order_created,
    mark_quote_expired,
)
from backend.app.shopping_session_store import (
    get_shopping_session_by_quote_id,
)


class CheckoutServiceError(Exception):
    pass


class QuoteNotFoundError(CheckoutServiceError):
    pass


class QuoteNotLinkedError(CheckoutServiceError):
    pass


class QuoteExpiredError(CheckoutServiceError):
    pass


class RazorpayOrderError(CheckoutServiceError):
    pass


class CheckoutOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str
    razorpay_order_id: str
    razorpay_key_id: str
    amount_paise: int
    currency: Literal["INR"]
    status: Literal["created"]


# Prevents simultaneous duplicate confirmations in the
# current single-process demo.
_checkout_lock = Lock()


def _create_razorpay_client() -> razorpay.Client:
    settings = get_settings()

    return razorpay.Client(
        auth=(
            settings.razorpay_key_id,
            settings.razorpay_key_secret.get_secret_value(),
        )
    )


def _build_checkout_result(
    quote_id: str,
    razorpay_order_id: str,
    amount_paise: int,
) -> CheckoutOrder:
    settings = get_settings()

    return CheckoutOrder(
        quote_id=quote_id,
        razorpay_order_id=razorpay_order_id,
        razorpay_key_id=settings.razorpay_key_id,
        amount_paise=amount_paise,
        currency="INR",
        status="created",
    )


def create_checkout_order(
    quote_id: str,
) -> CheckoutOrder:
    cleaned_quote_id = quote_id.strip()

    if not cleaned_quote_id:
        raise ValueError(
            "Quote ID is required."
        )

    with _checkout_lock:
        stored_quote = get_stored_quote(
            cleaned_quote_id
        )

        if stored_quote is None:
            raise QuoteNotFoundError(
                "Quote was not found."
            )

        shopping_session = (
            get_shopping_session_by_quote_id(
                cleaned_quote_id
            )
        )

        if (
            shopping_session is None
            or shopping_session.status
            != "quote_created"
            or shopping_session.quote_id
            != cleaned_quote_id
        ):
            raise QuoteNotLinkedError(
                "Quote is not linked to a completed "
                "shopping session."
            )

        quote = stored_quote.quote

        # Idempotent retry: return the existing order
        # instead of creating another Razorpay order.
        if stored_quote.status == "order_created":
            if stored_quote.razorpay_order_id is None:
                raise RazorpayOrderError(
                    "Stored order state is incomplete."
                )

            return _build_checkout_result(
                quote_id=quote.quote_id,
                razorpay_order_id=(
                    stored_quote.razorpay_order_id
                ),
                amount_paise=quote.total_paise,
            )

        if stored_quote.status == "expired":
            raise QuoteExpiredError(
                "Quote has expired."
            )

        now = datetime.now(timezone.utc)

        if now >= quote.expires_at:
            mark_quote_expired(
                quote.quote_id
            )
            raise QuoteExpiredError(
                "Quote has expired."
            )

        payload = {
            "amount": quote.total_paise,
            "currency": quote.currency,
            "receipt": quote.quote_id,
            "partial_payment": False,
            "notes": {
                "quote_id": quote.quote_id,
                "catalog_version": (
                    quote.catalog_version
                ),
                "base_product_sku": (
                    quote.base_product_sku
                ),
                "upsell_product_sku": (
                    quote.upsell_product_sku
                    or "none"
                ),
            },
        }

        try:
            response = (
                _create_razorpay_client()
                .order.create(payload)
            )
        except Exception as exc:
            raise RazorpayOrderError(
                "Razorpay order creation failed."
            ) from exc

        if not isinstance(response, dict):
            raise RazorpayOrderError(
                "Razorpay returned an invalid response."
            )

        razorpay_order_id = response.get("id")

        if (
            not isinstance(razorpay_order_id, str)
            or not razorpay_order_id.startswith(
                "order_"
            )
        ):
            raise RazorpayOrderError(
                "Razorpay returned an invalid order ID."
            )

        if (
            response.get("amount")
            != quote.total_paise
        ):
            raise RazorpayOrderError(
                "Razorpay returned an unexpected amount."
            )

        if (
            response.get("currency")
            != quote.currency
        ):
            raise RazorpayOrderError(
                "Razorpay returned an unexpected currency."
            )

        if (
            response.get("receipt")
            != quote.quote_id
        ):
            raise RazorpayOrderError(
                "Razorpay returned an unexpected receipt."
            )

        if response.get("status") != "created":
            raise RazorpayOrderError(
                "Razorpay order was not "
                "created successfully."
            )

        mark_order_created(
            quote_id=quote.quote_id,
            razorpay_order_id=razorpay_order_id,
        )

        return _build_checkout_result(
            quote_id=quote.quote_id,
            razorpay_order_id=razorpay_order_id,
            amount_paise=quote.total_paise,
        )