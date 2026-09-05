import hashlib
import hmac
import json
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.audit_events import (
    AuditEventConflictError,
    deterministic_audit_event_id,
    get_audit_event,
    record_audit_event,
)
from backend.app.config import get_settings
from backend.app.quote_store import (
    StoredPayment,
    get_stored_quote_by_order_id,
    save_verified_payment,
)
from backend.app.shopping_session_store import (
    get_shopping_session_by_quote_id,
)
from backend.app.webhook_store import (
    WebhookEventConflictError,
    get_processed_webhook_event,
    save_processed_webhook_event,
)


class WebhookConfigurationError(Exception):
    pass


class InvalidWebhookSignatureError(Exception):
    pass


class MalformedWebhookError(Exception):
    pass


class WebhookStateError(Exception):
    pass


MAX_WEBHOOK_BODY_BYTES = 1_000_000


class RazorpayPaymentEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^pay_[A-Za-z0-9]+$")
    order_id: str = Field(pattern=r"^order_[A-Za-z0-9]+$")
    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    status: str = Field(min_length=1, max_length=50)
    captured: bool


class RazorpayPaymentContainer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entity: RazorpayPaymentEntity


class RazorpayOrderPaidPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment: RazorpayPaymentContainer


class RazorpayWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str = Field(min_length=1, max_length=100)
    payload: dict[str, object] = Field(default_factory=dict)


class WebhookProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["processed", "duplicate", "ignored"]
    event_id: str
    event_type: str
    quote_id: str | None = None
    razorpay_payment_id: str | None = None


def _clean_event_id(event_id: str) -> str:
    cleaned_event_id = event_id.strip()

    if (
        not cleaned_event_id
        or len(cleaned_event_id) > 255
        or not cleaned_event_id.isascii()
        or any(character.isspace() for character in cleaned_event_id)
        or any(
            ord(character) < 33 or ord(character) > 126
            for character in cleaned_event_id
        )
    ):
        raise MalformedWebhookError(
            "The Razorpay webhook event ID is invalid."
        )

    return cleaned_event_id


def _verify_webhook_signature(
    *,
    raw_body: bytes,
    signature: str,
) -> None:
    settings = get_settings()

    if settings.razorpay_webhook_secret is None:
        raise WebhookConfigurationError(
            "The Razorpay webhook secret is not configured."
        )

    webhook_secret = (
        settings.razorpay_webhook_secret.get_secret_value()
    )

    if not webhook_secret:
        raise WebhookConfigurationError(
            "The Razorpay webhook secret is not configured."
        )

    cleaned_signature = signature.strip()

    if (
        len(cleaned_signature) != 64
        or any(
            character not in "0123456789abcdefABCDEF"
            for character in cleaned_signature
        )
    ):
        raise InvalidWebhookSignatureError(
            "The Razorpay webhook signature is invalid."
        )

    expected_signature = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        expected_signature,
        cleaned_signature.lower(),
    ):
        raise InvalidWebhookSignatureError(
            "The Razorpay webhook signature is invalid."
        )


def _parse_envelope(
    raw_body: bytes,
) -> RazorpayWebhookEnvelope:
    try:
        decoded_body = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedWebhookError(
            "The Razorpay webhook body is not valid JSON."
        ) from exc

    try:
        return RazorpayWebhookEnvelope.model_validate(decoded_body)
    except ValidationError as exc:
        raise MalformedWebhookError(
            "The Razorpay webhook envelope is invalid."
        ) from exc


def _duplicate_result(
    *,
    event_id: str,
    event_type: str,
) -> WebhookProcessingResult:
    return WebhookProcessingResult(
        status="duplicate",
        event_id=event_id,
        event_type=event_type,
    )


def process_razorpay_webhook(
    *,
    raw_body: bytes,
    signature: str,
    event_id: str,
) -> WebhookProcessingResult:
    if (
        not raw_body
        or len(raw_body) > MAX_WEBHOOK_BODY_BYTES
    ):
        raise MalformedWebhookError(
            "The Razorpay webhook body size is invalid."
        )

    cleaned_event_id = _clean_event_id(event_id)
    _verify_webhook_signature(
        raw_body=raw_body,
        signature=signature,
    )

    payload_sha256 = hashlib.sha256(raw_body).hexdigest()
    existing_event = get_processed_webhook_event(
        cleaned_event_id
    )

    if existing_event is not None:
        if existing_event.payload_sha256 != payload_sha256:
            raise WebhookEventConflictError(
                "The webhook event ID was reused with different content."
            )

        return _duplicate_result(
            event_id=cleaned_event_id,
            event_type=existing_event.event_type,
        )

    envelope = _parse_envelope(raw_body)

    if envelope.event != "order.paid":
        _, inserted = save_processed_webhook_event(
            event_id=cleaned_event_id,
            payload_sha256=payload_sha256,
            event_type=envelope.event,
        )

        if not inserted:
            return _duplicate_result(
                event_id=cleaned_event_id,
                event_type=envelope.event,
            )

        return WebhookProcessingResult(
            status="ignored",
            event_id=cleaned_event_id,
            event_type=envelope.event,
        )

    try:
        order_paid = RazorpayOrderPaidPayload.model_validate(
            envelope.payload
        )
    except ValidationError as exc:
        raise MalformedWebhookError(
            "The order.paid webhook payload is invalid."
        ) from exc

    payment_entity = order_paid.payment.entity
    stored_quote = get_stored_quote_by_order_id(
        payment_entity.order_id
    )

    if stored_quote is None:
        raise WebhookStateError(
            "The webhook order is not known to CartPilot."
        )

    session = get_shopping_session_by_quote_id(
        stored_quote.quote.quote_id
    )

    def reject(
        *,
        reason_code: str,
        explanation: str,
    ) -> NoReturn:
        if session is not None:
            record_audit_event(
                session_id=session.session_id,
                quote_id=stored_quote.quote.quote_id,
                event_type="payment_rejected",
                subject=f"webhook:{cleaned_event_id}:{reason_code}",
                actor="razorpay",
                outcome="rejected",
                reason_code=reason_code,
                explanation=explanation,
                amount_paise=(
                    payment_entity.amount
                    if payment_entity.currency.upper() == "INR"
                    else None
                ),
                currency=(
                    "INR"
                    if payment_entity.currency.upper() == "INR"
                    else None
                ),
                razorpay_order_id=payment_entity.order_id,
                razorpay_payment_id=payment_entity.id,
            )

        raise WebhookStateError(explanation)

    if stored_quote.status != "order_created":
        reject(
            reason_code="WEBHOOK_ORDER_STATE_INVALID",
            explanation=(
                "The signed webhook was rejected because its quote "
                "does not have a created Razorpay order."
            ),
        )

    if (
        payment_entity.status != "captured"
        or payment_entity.captured is not True
    ):
        reject(
            reason_code="WEBHOOK_PAYMENT_NOT_CAPTURED",
            explanation=(
                "The signed webhook did not confirm a captured payment."
            ),
        )

    if payment_entity.amount != stored_quote.quote.total_paise:
        reject(
            reason_code="WEBHOOK_AMOUNT_MISMATCH",
            explanation=(
                "The signed webhook amount did not match the immutable "
                "CartPilot quote."
            ),
        )

    if payment_entity.currency.upper() != stored_quote.quote.currency:
        reject(
            reason_code="WEBHOOK_CURRENCY_MISMATCH",
            explanation=(
                "The signed webhook currency did not match the immutable "
                "CartPilot quote."
            ),
        )

    try:
        payment: StoredPayment = save_verified_payment(
            quote_id=stored_quote.quote.quote_id,
            razorpay_order_id=payment_entity.order_id,
            razorpay_payment_id=payment_entity.id,
            verification_source="webhook",
        )
    except ValueError:
        reject(
            reason_code="WEBHOOK_PAYMENT_CONFLICT",
            explanation=(
                "The signed webhook conflicted with the payment already "
                "stored for this quote."
            ),
        )

    if session is not None:
        recovered = (
            payment.verification_source == "webhook"
        )
        audit_subject = f"webhook:{cleaned_event_id}"
        audit_event_id = deterministic_audit_event_id(
            session_id=session.session_id,
            event_type="payment_reconciled",
            subject=audit_subject,
        )

        # If only the delivery-marker write failed, preserve the audit event
        # already committed by the first attempt instead of reclassifying it.
        if get_audit_event(audit_event_id) is None:
            try:
                record_audit_event(
                    session_id=session.session_id,
                    quote_id=stored_quote.quote.quote_id,
                    event_type="payment_reconciled",
                    subject=audit_subject,
                    actor="razorpay",
                    outcome="recovered" if recovered else "recorded",
                    reason_code=(
                        "PAYMENT_RECOVERED_BY_WEBHOOK"
                        if recovered
                        else "PAYMENT_WEBHOOK_CONFIRMED"
                    ),
                    explanation=(
                        "A signed Razorpay order.paid webhook recovered the "
                        "payment after the browser callback was unavailable."
                        if recovered
                        else (
                            "A signed Razorpay order.paid webhook independently "
                            "confirmed the payment already stored from checkout."
                        )
                    ),
                    amount_paise=stored_quote.quote.total_paise,
                    currency=stored_quote.quote.currency,
                    razorpay_order_id=payment.razorpay_order_id,
                    razorpay_payment_id=payment.razorpay_payment_id,
                    created_at=payment.verified_at,
                )
            except AuditEventConflictError:
                concurrent_event = get_audit_event(audit_event_id)

                if (
                    concurrent_event is None
                    or concurrent_event.quote_id
                    != stored_quote.quote.quote_id
                    or concurrent_event.razorpay_order_id
                    != payment.razorpay_order_id
                    or concurrent_event.razorpay_payment_id
                    != payment.razorpay_payment_id
                ):
                    raise

    _, inserted = save_processed_webhook_event(
        event_id=cleaned_event_id,
        payload_sha256=payload_sha256,
        event_type=envelope.event,
    )

    if not inserted:
        return _duplicate_result(
            event_id=cleaned_event_id,
            event_type=envelope.event,
        )

    return WebhookProcessingResult(
        status="processed",
        event_id=cleaned_event_id,
        event_type=envelope.event,
        quote_id=stored_quote.quote.quote_id,
        razorpay_payment_id=payment.razorpay_payment_id,
    )
