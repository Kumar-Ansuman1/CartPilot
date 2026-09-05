from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.checkout_service import (
    CheckoutOrder,
    QuoteExpiredError,
    QuoteNotFoundError,
    QuoteNotLinkedError,
    RazorpayOrderError,
    create_checkout_order,
)
from backend.app.delegated_buyer import (
    DelegatedBuyerNoMatchError,
    DelegatedBuyerPlanError,
    DelegatedPurchaseResult,
    finalize_delegated_quote,
    run_delegated_purchase,
)
from backend.app.mandate_execution_ledger import (
    MandateAlreadyConsumedError,
    MandateAlreadyReservedError,
    MandateExecutionNotFoundError,
    MandateExecutionState,
    MandateExecutionStateError,
    consume_mandate_execution_for_quote,
    get_mandate_execution_state,
    release_mandate_execution,
    release_mandate_execution_for_quote,
)
from backend.app.models import Quote
from backend.app.purchase_mandate_service import (
    PurchaseMandateExpiredError,
    PurchaseMandateNotFoundError,
    PurchaseMandatePolicyError,
)
from backend.app.selection_service import (
    CatalogVersionChangedError,
    SelectedProductsUnavailableError,
)
from backend.app.shopping_session_store import (
    ShoppingSessionExpiredError,
    ShoppingSessionNotFoundError,
    ShoppingSessionStateError,
)


router = APIRouter(tags=["delegated-commerce"])


class DelegatedPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mandate_id: str = Field(pattern=r"^mandate_[0-9a-f]{32}$")
    task: str = Field(default="", max_length=500)


class DelegatedCheckoutConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str = Field(pattern=r"^execution_[0-9a-f]{32}$")
    include_cross_sell: bool = False
    confirmed: Literal[True]


class DelegatedCheckoutResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: Quote
    checkout_order: CheckoutOrder


def _execute(request: DelegatedPurchaseRequest) -> DelegatedPurchaseResult:
    try:
        return run_delegated_purchase(
            mandate_id=request.mandate_id,
            task=request.task,
        )
    except (PurchaseMandateNotFoundError, MandateExecutionNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="The purchase mandate was not found.") from exc
    except (MandateAlreadyReservedError, MandateAlreadyConsumedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MandateExecutionStateError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except DelegatedBuyerNoMatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DelegatedBuyerPlanError as exc:
        raise HTTPException(
            status_code=503,
            detail="The delegated AI planner could not produce a safe purchase plan.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="The delegated AI service is temporarily unavailable.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/delegated-shop", response_model=DelegatedPurchaseResult)
def delegated_shop(request: DelegatedPurchaseRequest) -> DelegatedPurchaseResult:
    return _execute(request)


@router.post("/api/agent/purchase-plan", response_model=DelegatedPurchaseResult)
def external_agent_purchase_plan(
    request: DelegatedPurchaseRequest,
) -> DelegatedPurchaseResult:
    return _execute(request)


@router.post(
    "/api/delegated-checkout/confirm",
    response_model=DelegatedCheckoutResult,
    summary="Buyer-finalize add-on choice, quote, and checkout",
)
def confirm_delegated_checkout(
    request: DelegatedCheckoutConfirmationRequest,
) -> DelegatedCheckoutResult:
    quote: Quote | None = None
    try:
        quote = finalize_delegated_quote(
            execution_id=request.execution_id,
            include_cross_sell=request.include_cross_sell,
        )
        order = create_checkout_order(quote.quote_id)
        consume_mandate_execution_for_quote(quote.quote_id)
        return DelegatedCheckoutResult(
            quote=quote,
            checkout_order=order,
        )
    except (
        PurchaseMandateNotFoundError,
        MandateExecutionNotFoundError,
        ShoppingSessionNotFoundError,
        QuoteNotFoundError,
    ) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PurchaseMandateExpiredError, ShoppingSessionExpiredError) as exc:
        try:
            release_mandate_execution(
                request.execution_id,
                reason_code="DELEGATED_SELECTION_EXPIRED_RELEASED",
            )
        except Exception:
            pass
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except QuoteExpiredError as exc:
        if quote is not None:
            release_mandate_execution_for_quote(quote.quote_id)
        raise HTTPException(status_code=410, detail="The quote has expired.") from exc
    except (
        PurchaseMandatePolicyError,
        MandateExecutionStateError,
        ShoppingSessionStateError,
        CatalogVersionChangedError,
        SelectedProductsUnavailableError,
        QuoteNotLinkedError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RazorpayOrderError as exc:
        raise HTTPException(
            status_code=502,
            detail="The payment provider could not create the order.",
        ) from exc


@router.get(
    "/api/agent/executions/{execution_id}",
    response_model=MandateExecutionState,
)
def get_execution_state(
    execution_id: Annotated[str, Path(pattern=r"^execution_[0-9a-f]{32}$")],
    response: Response,
) -> MandateExecutionState:
    state = get_mandate_execution_state(execution_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Mandate execution was not found.")
    response.headers["Cache-Control"] = "no-store"
    return state


@router.get("/api/agent/capabilities")
def external_agent_capabilities() -> dict[str, object]:
    return {
        "capabilities": [
            "create_mandate_bound_purchase_plan",
            "read_execution_state",
            "read_existing_mandate_via_core_api",
            "read_audit_timeline_via_core_api",
        ],
        "prohibited": [
            "modify_mandate",
            "set_catalog_price",
            "create_razorpay_order_without_buyer_confirmation",
            "verify_or_forge_payment",
        ],
        "checkout_confirmation_required": True,
    }
