from datetime import datetime, timezone
from threading import Lock
from typing import Literal

import razorpay
from pydantic import BaseModel, ConfigDict

from backend.app.audit_events import (
    new_audit_event,
    record_audit_event,
    save_audit_event_idempotently,
)
from backend.app.config import get_settings
from backend.app.models import Quote
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


def _record_checkout_confirmation(
    *,
    session_id: str,
    quote: Quote,
) -> None:
    record_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="checkout_confirmed",
        subject=f"checkout:{quote.quote_id}",
        actor="buyer",
        outcome="allowed",
        reason_code="BUYER_CONFIRMED_CHECKOUT",
        explanation=(
            "The buyer explicitly confirmed the stored "
            "quote before an order could be requested."
        ),
        amount_paise=quote.total_paise,
        currency=quote.currency,
    )


def _append_order_creation_attempt(
    *,
    session_id: str,
    quote: Quote,
) -> None:
    event = new_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="order_creation_requested",
        actor="deterministic_core",
        outcome="recorded",
        reason_code="RAZORPAY_ORDER_REQUESTED",
        explanation=(
            "The deterministic core requested one "
            "Razorpay order using the immutable quote "
            "amount and quote ID as its receipt."
        ),
        amount_paise=quote.total_paise,
        currency=quote.currency,
    )

    save_audit_event_idempotently(event)


def _record_order_failure(
    *,
    session_id: str,
    quote: Quote,
    reason_code: str,
    explanation: str,
) -> None:
    event = new_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="order_created",
        actor="razorpay",
        outcome="failed",
        reason_code=reason_code,
        explanation=explanation,
        amount_paise=quote.total_paise,
        currency=quote.currency,
    )

    save_audit_event_idempotently(event)


def _record_validated_order(
    *,
    session_id: str,
    quote: Quote,
    razorpay_order_id: str,
) -> None:
    record_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="order_created",
        subject=f"order:{razorpay_order_id}",
        actor="razorpay",
        outcome="recorded",
        reason_code="RAZORPAY_ORDER_RESPONSE_VALIDATED",
        explanation=(
            "The Razorpay order ID, amount, currency, "
            "receipt and status matched the stored quote."
        ),
        amount_paise=quote.total_paise,
        currency=quote.currency,
        razorpay_order_id=razorpay_order_id,
    )


def _record_expired_quote_rejection(
    *,
    session_id: str,
    quote: Quote,
) -> None:
    record_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="quote_expired",
        subject=f"quote-expired:{quote.quote_id}",
        actor="deterministic_core",
        outcome="recorded",
        reason_code="QUOTE_TIME_LIMIT_REACHED",
        explanation=(
            "The quote reached its expiry time and "
            "could no longer authorize checkout."
        ),
        amount_paise=quote.total_paise,
        currency=quote.currency,
    )

    record_audit_event(
        session_id=session_id,
        quote_id=quote.quote_id,
        event_type="checkout_confirmed",
        subject=f"rejected-expired:{quote.quote_id}",
        actor="buyer",
        outcome="rejected",
        reason_code="CHECKOUT_QUOTE_EXPIRED",
        explanation=(
            "Checkout confirmation was rejected because "
            "the stored quote had expired."
        ),
        amount_paise=quote.total_paise,
        currency=quote.currency,
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
            if shopping_session is not None:
                record_audit_event(
                    session_id=(
                        shopping_session.session_id
                    ),
                    quote_id=stored_quote.quote.quote_id,
                    event_type="checkout_confirmed",
                    subject=(
                        "rejected-unlinked:"
                        f"{stored_quote.quote.quote_id}"
                    ),
                    actor="buyer",
                    outcome="rejected",
                    reason_code="QUOTE_NOT_LINKED_TO_SESSION",
                    explanation=(
                        "Checkout confirmation was rejected "
                        "because the quote was not linked to "
                        "a completed shopping session."
                    ),
                    amount_paise=(
                        stored_quote.quote.total_paise
                    ),
                    currency=stored_quote.quote.currency,
                )

            raise QuoteNotLinkedError(
                "Quote is not linked to a completed "
                "shopping session."
            )

        quote = stored_quote.quote

        # Idempotent retry: return the existing order
        # instead of creating another Razorpay order.
        if stored_quote.status == "order_created":
            if stored_quote.razorpay_order_id is None:
                record_audit_event(
                    session_id=shopping_session.session_id,
                    quote_id=quote.quote_id,
                    event_type="order_created",
                    subject=(
                        "invalid-stored-order-state:"
                        f"{quote.quote_id}"
                    ),
                    actor="deterministic_core",
                    outcome="failed",
                    reason_code="STORED_ORDER_ID_MISSING",
                    explanation=(
                        "The local quote state indicated an "
                        "order, but no Razorpay order ID was "
                        "stored."
                    ),
                    amount_paise=quote.total_paise,
                    currency=quote.currency,
                )
                raise RazorpayOrderError(
                    "Stored order state is incomplete."
                )

            _record_checkout_confirmation(
                session_id=shopping_session.session_id,
                quote=quote,
            )
            _record_validated_order(
                session_id=shopping_session.session_id,
                quote=quote,
                razorpay_order_id=(
                    stored_quote.razorpay_order_id
                ),
            )

            return _build_checkout_result(
                quote_id=quote.quote_id,
                razorpay_order_id=(
                    stored_quote.razorpay_order_id
                ),
                amount_paise=quote.total_paise,
            )

        if stored_quote.status == "expired":
            _record_expired_quote_rejection(
                session_id=shopping_session.session_id,
                quote=quote,
            )
            raise QuoteExpiredError(
                "Quote has expired."
            )

        now = datetime.now(timezone.utc)

        if now >= quote.expires_at:
            mark_quote_expired(
                quote.quote_id
            )
            _record_expired_quote_rejection(
                session_id=shopping_session.session_id,
                quote=quote,
            )
            raise QuoteExpiredError(
                "Quote has expired."
            )

        _record_checkout_confirmation(
            session_id=shopping_session.session_id,
            quote=quote,
        )
        _append_order_creation_attempt(
            session_id=shopping_session.session_id,
            quote=quote,
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
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="RAZORPAY_ORDER_REQUEST_FAILED",
                explanation=(
                    "The Razorpay order request failed "
                    "before a valid order response was "
                    "received."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay order creation failed."
            ) from exc

        if not isinstance(response, dict):
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="INVALID_RAZORPAY_RESPONSE",
                explanation=(
                    "Razorpay returned an order response "
                    "with an invalid structure."
                ),
            )
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
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="INVALID_RAZORPAY_ORDER_ID",
                explanation=(
                    "Razorpay returned an invalid order ID, "
                    "so the response was not stored."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay returned an invalid order ID."
            )

        if (
            response.get("amount")
            != quote.total_paise
        ):
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="RAZORPAY_ORDER_AMOUNT_MISMATCH",
                explanation=(
                    "The Razorpay order amount did not "
                    "match the immutable quote amount."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay returned an unexpected amount."
            )

        if (
            response.get("currency")
            != quote.currency
        ):
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="RAZORPAY_ORDER_CURRENCY_MISMATCH",
                explanation=(
                    "The Razorpay order currency did not "
                    "match the immutable quote currency."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay returned an unexpected currency."
            )

        if (
            response.get("receipt")
            != quote.quote_id
        ):
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="RAZORPAY_ORDER_RECEIPT_MISMATCH",
                explanation=(
                    "The Razorpay order receipt did not "
                    "match the immutable quote ID."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay returned an unexpected receipt."
            )

        if response.get("status") != "created":
            _record_order_failure(
                session_id=shopping_session.session_id,
                quote=quote,
                reason_code="RAZORPAY_ORDER_STATUS_INVALID",
                explanation=(
                    "Razorpay did not return the required "
                    "created order status."
                ),
            )
            raise RazorpayOrderError(
                "Razorpay order was not "
                "created successfully."
            )

        mark_order_created(
            quote_id=quote.quote_id,
            razorpay_order_id=razorpay_order_id,
        )

        _record_validated_order(
            session_id=shopping_session.session_id,
            quote=quote,
            razorpay_order_id=razorpay_order_id,
        )

        return _build_checkout_result(
            quote_id=quote.quote_id,
            razorpay_order_id=razorpay_order_id,
            amount_paise=quote.total_paise,
        )
