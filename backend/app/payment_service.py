import razorpay

from backend.app.config import get_settings
from backend.app.quote_store import (
    StoredPayment,
    get_stored_quote,
    save_verified_payment,
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

    if not cleaned_order_id.startswith("order_"):
        raise ValueError("Invalid Razorpay order ID.")

    if not cleaned_payment_id.startswith("pay_"):
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

    if stored_quote.status != "order_created":
        raise PaymentStateError(
            "The quote does not have a Razorpay order."
        )

    # Compare against our server-stored order ID, not an ID
    # supplied only by the frontend.
    if stored_quote.razorpay_order_id != cleaned_order_id:
        raise PaymentStateError(
            "The Razorpay order ID does not match the stored order."
        )

    client = _create_razorpay_client()

    try:
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
        raise InvalidPaymentSignatureError(
            "Payment signature verification failed."
        ) from exc

    if verified is not True:
        raise InvalidPaymentSignatureError(
            "Payment signature verification failed."
        )

    return save_verified_payment(
        quote_id=stored_quote.quote.quote_id,
        razorpay_order_id=stored_quote.razorpay_order_id,
        razorpay_payment_id=cleaned_payment_id,
    )