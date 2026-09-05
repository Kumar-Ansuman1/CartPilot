import re
from datetime import datetime

import razorpay

from backend.app.audit_events import (
    AuditActor,
    AuditEventType,
    AuditOutcome,
    record_audit_event,
)
from backend.app.config import get_settings
from backend.app.quote_store import (
    StoredPayment,
    get_stored_quote,
    save_verified_payment,
)
from backend.app.shopping_session_store import (
    get_shopping_session_by_quote_id,
)


class PaymentVerificationError(Exception):
    pass


class PaymentQuoteNotFoundError(PaymentVerificationError):
    pass


class PaymentStateError(PaymentVerificationError):
    pass


class InvalidPaymentSignatureError(PaymentVerificationError):
    pass


def _create_razorpay_client() -> razorpay.Client:
    settings = get_settings()

    return razorpay.Client(
        auth=(
            settings.razorpay_key_id,
            settings.razorpay_key_secret.get_secret_value(),
        )
    )


def verify_and_record_payment(
    *,
    quote_id: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> StoredPayment:
    cleaned_order_id = razorpay_order_id.strip()
    cleaned_payment_id = razorpay_payment_id.strip()
    cleaned_signature = razorpay_signature.strip()

    if not re.fullmatch(r"order_[A-Za-z0-9]+", cleaned_order_id):
        raise ValueError("Invalid Razorpay order ID.")

    if not re.fullmatch(r"pay_[A-Za-z0-9]+", cleaned_payment_id):
        raise ValueError("Invalid Razorpay payment ID.")

    if (
        len(cleaned_signature) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in cleaned_signature
        )
    ):
        raise ValueError("Invalid Razorpay signature format.")

    stored_quote = get_stored_quote(quote_id)

    if stored_quote is None:
        raise PaymentQuoteNotFoundError(
            "The payment quote was not found."
        )

    shopping_session = get_shopping_session_by_quote_id(
        stored_quote.quote.quote_id
    )

    def record_payment_event(
        *,
        event_type: AuditEventType,
        actor: AuditActor = "deterministic_core",
        outcome: AuditOutcome,
        reason_code: str,
        explanation: str,
        created_at: datetime | None = None,
    ) -> None:
        # Older directly stored quotes may have no shopping session.
        # Preserve their verification behavior without inventing an owner.
        if shopping_session is None:
            return

        record_audit_event(
            session_id=shopping_session.session_id,
            quote_id=stored_quote.quote.quote_id,
            event_type=event_type,
            subject=(
                f"{reason_code}:{cleaned_order_id}:{cleaned_payment_id}"
            ),
            actor=actor,
            outcome=outcome,
            reason_code=reason_code,
            explanation=explanation,
            # These are quote terms, not a fetched payment amount.
            amount_paise=stored_quote.quote.total_paise,
            currency=stored_quote.quote.currency,
            razorpay_order_id=cleaned_order_id,
            razorpay_payment_id=cleaned_payment_id,
            created_at=created_at,
        )

    record_payment_event(
        event_type="payment_verification_requested",
        actor="buyer",
        outcome="recorded",
        reason_code="PAYMENT_VERIFICATION_REQUESTED",
        explanation=(
            "The buyer submitted a payment callback for server-side "
            "order and signature verification."
        ),
    )

    if stored_quote.status != "order_created":
        record_payment_event(
            event_type="payment_rejected",
            outcome="rejected",
            reason_code="PAYMENT_ORDER_NOT_CREATED",
            explanation=(
                "Payment verification was rejected because the quote "
                "does not have a created Razorpay order."
            ),
        )
        raise PaymentStateError(
            "The quote does not have a Razorpay order."
        )

    # Compare against our server-stored order ID, not an ID
    # supplied only by the frontend.
    if stored_quote.razorpay_order_id != cleaned_order_id:
        record_payment_event(
            event_type="payment_rejected",
            outcome="rejected",
            reason_code="PAYMENT_ORDER_ID_MISMATCH",
            explanation=(
                "The callback order ID did not match the server-stored "
                "order for this quote."
            ),
        )
        raise PaymentStateError(
            "The Razorpay order ID does not match the stored order."
        )

    try:
        client = _create_razorpay_client()
        verified = client.utility.verify_payment_signature(
            {
                "razorpay_order_id": (
                    stored_quote.razorpay_order_id
                ),
                "razorpay_payment_id": cleaned_payment_id,
                "razorpay_signature": cleaned_signature,
            }
        )
    except razorpay.errors.SignatureVerificationError as exc:
        record_payment_event(
            event_type="payment_rejected",
            outcome="rejected",
            reason_code="PAYMENT_SIGNATURE_INVALID",
            explanation=(
                "The callback signature failed verification against "
                "the server-stored order ID."
            ),
        )
        raise InvalidPaymentSignatureError(
            "Payment signature verification failed."
        ) from exc
    except Exception:
        record_payment_event(
            event_type="payment_rejected",
            outcome="failed",
            reason_code="PAYMENT_VERIFICATION_ERROR",
            explanation=(
                "The signature verifier could not complete. "
                "This attempt did not record a verified payment."
            ),
        )
        raise

    if verified is not True:
        record_payment_event(
            event_type="payment_rejected",
            outcome="rejected",
            reason_code="PAYMENT_SIGNATURE_INVALID",
            explanation=(
                "The callback signature failed verification against "
                "the server-stored order ID."
            ),
        )
        raise InvalidPaymentSignatureError(
            "Payment signature verification failed."
        )

    try:
        payment = save_verified_payment(
            quote_id=stored_quote.quote.quote_id,
            razorpay_order_id=stored_quote.razorpay_order_id,
            razorpay_payment_id=cleaned_payment_id,
        )
    except ValueError:
        record_payment_event(
            event_type="payment_rejected",
            outcome="rejected",
            reason_code="PAYMENT_RECORD_CONFLICT",
            explanation=(
                "The verified callback could not be recorded because "
                "it conflicted with the stored quote or payment."
            ),
        )
        raise

    # Run signature verification on every retry, then reuse the original
    # payment and event. A retry also repairs a missing success audit event
    # if its write failed after the payment was committed.
    record_payment_event(
        event_type="payment_verified",
        outcome="recorded",
        reason_code="PAYMENT_SIGNATURE_VERIFIED",
        explanation=(
            "The callback signature was verified against the server-stored "
            "order ID and the payment record was saved. "
            "Capture status has not been checked."
        ),
        created_at=payment.verified_at,
    )

    return payment
