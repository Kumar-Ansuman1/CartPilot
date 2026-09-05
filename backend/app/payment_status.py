from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.quote_store import (
    PaymentVerificationSource,
    get_stored_quote,
    get_verified_payment,
)


class PaymentStatusNotFoundError(Exception):
    pass


class PaymentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quote_id: str = Field(pattern=r"^quote_[0-9a-f]{32}$")
    status: Literal[
        "checkout_not_started",
        "confirmation_pending",
        "verified",
        "expired",
    ]
    razorpay_order_id: str | None = Field(
        default=None,
        pattern=r"^order_[A-Za-z0-9]+$",
    )
    razorpay_payment_id: str | None = Field(
        default=None,
        pattern=r"^pay_[A-Za-z0-9]+$",
    )
    verification_source: PaymentVerificationSource | None = None
    verified_at: datetime | None = None


def read_payment_status(
    quote_id: str,
) -> PaymentStatusResponse:
    stored_quote = get_stored_quote(quote_id)

    if stored_quote is None:
        raise PaymentStatusNotFoundError(
            "The payment quote was not found."
        )

    payment = get_verified_payment(quote_id)

    if payment is not None:
        return PaymentStatusResponse(
            quote_id=quote_id,
            status="verified",
            razorpay_order_id=payment.razorpay_order_id,
            razorpay_payment_id=payment.razorpay_payment_id,
            verification_source=payment.verification_source,
            verified_at=payment.verified_at,
        )

    status_by_quote_state = {
        "pending": "checkout_not_started",
        "order_created": "confirmation_pending",
        "expired": "expired",
    }

    return PaymentStatusResponse(
        quote_id=quote_id,
        status=status_by_quote_state[stored_quote.status],
        razorpay_order_id=stored_quote.razorpay_order_id,
    )
