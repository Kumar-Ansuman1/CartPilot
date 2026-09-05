from sqlite3 import Error as SQLiteError
from typing import Annotated, Literal

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Path,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from backend.app.audit_events import AuditEvent, list_audit_events
from backend.app.checkout_service import (
    CheckoutOrder,
    QuoteExpiredError,
    QuoteNotFoundError,
    QuoteNotLinkedError,
    RazorpayOrderError,
    create_checkout_order,
)
from backend.app.commerce_agent import (
    CommerceAgentResult,
    run_commerce_agent,
)
from backend.app.payment_service import (
    InvalidPaymentSignatureError,
    PaymentQuoteNotFoundError,
    PaymentStateError,
    verify_and_record_payment,
)
from backend.app.payment_status import (
    PaymentStatusNotFoundError,
    PaymentStatusResponse,
    read_payment_status,
)
from backend.app.payment_webhook import (
    InvalidWebhookSignatureError,
    MalformedWebhookError,
    WebhookConfigurationError,
    WebhookProcessingResult,
    WebhookStateError,
    process_razorpay_webhook,
)
from backend.app.quote_store import StoredPayment
from backend.app.selection_service import (
    BaseProductUnavailableError,
    BaseSelectionResult,
    CatalogVersionChangedError,
    CrossSellDecisionResult,
    SelectedProductsUnavailableError,
    finalize_cross_sell_decision,
    select_base_product,
)
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
    get_shopping_session,
)
from backend.app.webhook_store import WebhookEventConflictError


class ShoppingSessionAuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    quote_id: str | None = Field(
        default=None,
        pattern=r"^quote_[0-9a-f]{32}$",
    )
    events: list[AuditEvent]


class BuyerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=3,
        max_length=500,
    )


class BaseProductSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    base_product_sku: str = Field(
        pattern=r"^\S{3,100}$"
    )


class CrossSellDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    decision: Literal["accept", "decline"]
    cross_sell_product_sku: str | None = Field(
        default=None,
        pattern=r"^\S{3,100}$",
    )

    @model_validator(mode="after")
    def validate_decision(
        self,
    ) -> "CrossSellDecisionRequest":
        if (
            self.decision == "accept"
            and self.cross_sell_product_sku is None
        ):
            raise ValueError(
                "Accepting a cross-sell requires "
                "a product SKU."
            )

        if (
            self.decision == "decline"
            and self.cross_sell_product_sku is not None
        ):
            raise ValueError(
                "Declining cross-sells must not "
                "include a product SKU."
            )

        return self


class CheckoutConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str = Field(
        pattern=r"^quote_[0-9a-f]{32}$"
    )
    confirmed: Literal[True]


class PaymentVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote_id: str = Field(
        pattern=r"^quote_[0-9a-f]{32}$"
    )
    razorpay_order_id: str = Field(
        pattern=r"^order_[A-Za-z0-9]+$"
    )
    razorpay_payment_id: str = Field(
        pattern=r"^pay_[A-Za-z0-9]+$"
    )
    razorpay_signature: str = Field(
        pattern=r"^[0-9a-fA-F]{64}$"
    )


app = FastAPI(
    title="CartPilot API",
    description=(
        "Safe agentic commerce API for "
        "electronics accessories."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/shop",
    response_model=CommerceAgentResult,
)
def shop(
    request: BuyerMessageRequest,
) -> CommerceAgentResult:
    try:
        return run_commerce_agent(
            request.message
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The AI intent service is "
                "temporarily unavailable."
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "The shopping request could not "
                "be processed safely."
            ),
        ) from exc


@app.get(
    "/api/shop/{session_id}/audit",
    response_model=ShoppingSessionAuditResponse,
    summary="Read a shopping session's audit timeline",
)
def get_shopping_audit(
    session_id: Annotated[
        str, Path(pattern=r"^session_[0-9a-f]{32}$")
    ],
    response: Response,
) -> ShoppingSessionAuditResponse:
    """Return stored events in insertion order, including expired sessions.

    The session ID is the lookup key in the current anonymous demo.
    Account ownership checks must accompany any future authenticated flow.
    Reading history does not expire sessions or trigger commerce actions.
    """
    try:
        session = get_shopping_session(session_id)

        if session is None:
            raise HTTPException(
                status_code=404,
                detail="The shopping session was not found.",
            )

        timeline = ShoppingSessionAuditResponse(
            session_id=session.session_id,
            quote_id=session.quote_id,
            events=list_audit_events(session.session_id),
        )
    except (SQLiteError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="The audit timeline is temporarily unavailable.",
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    return timeline


@app.post(
    "/api/shop/select-base",
    response_model=BaseSelectionResult,
)
def select_base(
    request: BaseProductSelectionRequest,
) -> BaseSelectionResult:
    try:
        return select_base_product(
            session_id=request.session_id,
            base_product_sku=(
                request.base_product_sku
            ),
        )

    except ShoppingSessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "The shopping session was not found."
            ),
        ) from exc

    except ShoppingSessionExpiredError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                "The shopping session has expired. "
                "Please start a new search."
            ),
        ) from exc

    except CatalogVersionChangedError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The catalog changed after the search. "
                "Please start a new search."
            ),
        ) from exc

    except BaseProductUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The selected product is no longer "
                "available. Please start a new search."
            ),
        ) from exc

    except ShoppingSessionStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The selected product cannot be used "
                "for this shopping session."
            ),
        ) from exc


@app.post(
    "/api/shop/select-cross-sell",
    response_model=CrossSellDecisionResult,
)
def select_cross_sell(
    request: CrossSellDecisionRequest,
) -> CrossSellDecisionResult:
    try:
        return finalize_cross_sell_decision(
            session_id=request.session_id,
            decision=request.decision,
            cross_sell_product_sku=(
                request.cross_sell_product_sku
            ),
        )

    except ShoppingSessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "The shopping session was not found."
            ),
        ) from exc

    except ShoppingSessionExpiredError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                "The shopping session has expired. "
                "Please start a new search."
            ),
        ) from exc

    except CatalogVersionChangedError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The catalog changed after selection. "
                "Please start a new search."
            ),
        ) from exc

    except SelectedProductsUnavailableError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "A selected product is no longer "
                "eligible. Please start a new search."
            ),
        ) from exc

    except ShoppingSessionStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The cross-sell decision cannot be "
                "applied to this shopping session."
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "The cross-sell decision is invalid."
            ),
        ) from exc


@app.post(
    "/api/checkout/confirm",
    response_model=CheckoutOrder,
)
def confirm_checkout(
    request: CheckoutConfirmationRequest,
) -> CheckoutOrder:
    try:
        return create_checkout_order(
            quote_id=request.quote_id,
        )

    except QuoteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="The quote was not found.",
        ) from exc

    except QuoteNotLinkedError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The quote is not linked to a completed "
                "shopping session."
            ),
        ) from exc

    except QuoteExpiredError as exc:
        raise HTTPException(
            status_code=410,
            detail=(
                "The quote has expired. "
                "Please request a new quote."
            ),
        ) from exc

    except RazorpayOrderError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "The payment provider could not "
                "create the order."
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The quote cannot be used "
                "in its current state."
            ),
        ) from exc


@app.post(
    "/api/payment/verify",
    response_model=StoredPayment,
)
def verify_payment(
    request: PaymentVerificationRequest,
) -> StoredPayment:
    try:
        return verify_and_record_payment(
            quote_id=request.quote_id,
            razorpay_order_id=(
                request.razorpay_order_id
            ),
            razorpay_payment_id=(
                request.razorpay_payment_id
            ),
            razorpay_signature=(
                request.razorpay_signature
            ),
        )

    except PaymentQuoteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "The payment quote was not found."
            ),
        ) from exc

    except PaymentStateError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The payment does not match "
                "the stored order."
            ),
        ) from exc

    except InvalidPaymentSignatureError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Payment signature verification failed."
            ),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "The payment response is invalid."
            ),
        ) from exc


@app.get(
    "/api/payment/status/{quote_id}",
    response_model=PaymentStatusResponse,
    summary="Read payment confirmation status",
)
def get_payment_status(
    quote_id: Annotated[
        str,
        Path(pattern=r"^quote_[0-9a-f]{32}$"),
    ],
    response: Response,
) -> PaymentStatusResponse:
    try:
        result = read_payment_status(quote_id)
    except PaymentStatusNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="The payment quote was not found.",
        ) from exc
    except (SQLiteError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Payment status is temporarily unavailable.",
        ) from exc

    response.headers["Cache-Control"] = "no-store"
    return result


@app.post(
    "/api/payment/webhook",
    response_model=WebhookProcessingResult,
    summary="Process a signed Razorpay webhook",
)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: Annotated[
        str | None,
        Header(alias="X-Razorpay-Signature"),
    ] = None,
    x_razorpay_event_id: Annotated[
        str | None,
        Header(alias="X-Razorpay-Event-Id"),
    ] = None,
) -> WebhookProcessingResult:
    if x_razorpay_signature is None:
        raise HTTPException(
            status_code=400,
            detail="The Razorpay webhook signature is required.",
        )

    if x_razorpay_event_id is None:
        raise HTTPException(
            status_code=400,
            detail="The Razorpay webhook event ID is required.",
        )

    raw_body = await request.body()

    try:
        return process_razorpay_webhook(
            raw_body=raw_body,
            signature=x_razorpay_signature,
            event_id=x_razorpay_event_id,
        )
    except InvalidWebhookSignatureError as exc:
        raise HTTPException(
            status_code=400,
            detail="The Razorpay webhook signature is invalid.",
        ) from exc
    except MalformedWebhookError as exc:
        raise HTTPException(
            status_code=400,
            detail="The Razorpay webhook payload is invalid.",
        ) from exc
    except (
        WebhookStateError,
        WebhookEventConflictError,
    ) as exc:
        raise HTTPException(
            status_code=409,
            detail="The Razorpay webhook conflicts with stored payment state.",
        ) from exc
    except WebhookConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Razorpay webhook processing is not configured.",
        ) from exc
    except (SQLiteError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Razorpay webhook processing is temporarily unavailable.",
        ) from exc
