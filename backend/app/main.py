from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


from backend.app.checkout_service import (
    CheckoutOrder,
    QuoteExpiredError,
    QuoteNotFoundError,
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
from backend.app.quote_store import StoredPayment

from backend.app.selection_service import (
    BaseProductUnavailableError,
    BaseSelectionResult,
    CatalogVersionChangedError,
    select_base_product,
)

from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
)


class BuyerMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=3, max_length=500)

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

class BaseProductSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        pattern=r"^session_[0-9a-f]{32}$"
    )
    base_product_sku: str = Field(
        pattern=r"^\S{3,100}$"
    )

app = FastAPI(
    title="CartPilot API",
    description="Safe agentic commerce API for electronics accessories.",
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
def shop(request: BuyerMessageRequest) -> CommerceAgentResult:
    try:
        return run_commerce_agent(request.message)

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="The AI intent service is temporarily unavailable.",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="The shopping request could not be processed safely.",
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

    except QuoteExpiredError as exc:
        raise HTTPException(
            status_code=410,
            detail="The quote has expired. Please request a new quote.",
        ) from exc

    except RazorpayOrderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The payment provider could not create the order.",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="The quote cannot be used in its current state.",
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
            razorpay_order_id=request.razorpay_order_id,
            razorpay_payment_id=request.razorpay_payment_id,
            razorpay_signature=request.razorpay_signature,
        )

    except PaymentQuoteNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="The payment quote was not found.",
        ) from exc

    except PaymentStateError as exc:
        raise HTTPException(
            status_code=409,
            detail="The payment does not match the stored order.",
        ) from exc

    except InvalidPaymentSignatureError as exc:
        raise HTTPException(
            status_code=400,
            detail="Payment signature verification failed.",
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="The payment response is invalid.",
        ) from exc

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
            base_product_sku=request.base_product_sku,
        )

    except ShoppingSessionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="The shopping session was not found.",
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
                "The selected product is no longer available. "
                "Please start a new search."
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