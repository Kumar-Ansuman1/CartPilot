from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.delegated_buyer import (
    DelegatedBuyerNoMatchError,
    DelegatedBuyerPlanError,
    DelegatedPurchaseResult,
    run_delegated_purchase,
)
from backend.app.mandate_execution_ledger import (
    MandateAlreadyConsumedError,
    MandateAlreadyReservedError,
    MandateExecutionNotFoundError,
    MandateExecutionState,
    MandateExecutionStateError,
    get_mandate_execution_state,
)
from backend.app.purchase_mandate_service import PurchaseMandateNotFoundError


router = APIRouter(tags=["delegated-commerce"])


class DelegatedPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate_id: str = Field(pattern=r"^mandate_[0-9a-f]{32}$")
    task: str = Field(default="", max_length=500)


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


@router.post(
    "/api/delegated-shop",
    response_model=DelegatedPurchaseResult,
    summary="Create a mandate-bound AI purchase plan and quote",
)
def delegated_shop(request: DelegatedPurchaseRequest) -> DelegatedPurchaseResult:
    return _execute(request)


@router.post(
    "/api/agent/purchase-plan",
    response_model=DelegatedPurchaseResult,
    summary="External-agent safe delegated-commerce capability",
)
def external_agent_purchase_plan(
    request: DelegatedPurchaseRequest,
) -> DelegatedPurchaseResult:
    """External agents may request a plan, but cannot create orders or pay."""
    return _execute(request)


@router.get(
    "/api/agent/executions/{execution_id}",
    response_model=MandateExecutionState,
    summary="Read delegated mandate execution state",
)
def get_execution_state(
    execution_id: Annotated[
        str,
        Path(pattern=r"^execution_[0-9a-f]{32}$"),
    ],
    response: Response,
) -> MandateExecutionState:
    state = get_mandate_execution_state(execution_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Mandate execution was not found.")
    response.headers["Cache-Control"] = "no-store"
    return state


@router.get(
    "/api/agent/capabilities",
    summary="Describe the deliberately limited external-agent surface",
)
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
