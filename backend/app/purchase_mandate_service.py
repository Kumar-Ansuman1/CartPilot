from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from backend.app.audit_events import record_audit_event
from backend.app.models import (
    CompatibilityTag,
    Product,
    ProductCategory,
    PurchaseMandate,
)
from backend.app.purchase_mandate_store import (
    get_purchase_mandate,
    save_purchase_mandate,
)


class PurchaseMandateNotFoundError(Exception):
    pass


class PurchaseMandateExpiredError(Exception):
    pass


class PurchaseMandatePolicyError(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MandateProductAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mandate_id: str
    sku: str
    authorized: Literal[True] = True


def create_purchase_mandate(
    *,
    budget_paise: int,
    allowed_categories: list[ProductCategory],
    required_compatibility: list[CompatibilityTag] | None = None,
    max_cross_sell_percentage: int = 20,
    expires_in_minutes: int = 30,
    checkout_confirmation_required: Literal[True] = True,
    buyer_goal: str | None = None,
    created_at: datetime | None = None,
) -> PurchaseMandate:
    if not 1 <= expires_in_minutes <= 1_440:
        raise ValueError(
            "Mandate validity must be between 1 and 1440 minutes."
        )

    creation_time = created_at or datetime.now(timezone.utc)
    mandate = PurchaseMandate(
        mandate_id=f"mandate_{uuid4().hex}",
        budget_paise=budget_paise,
        allowed_categories=allowed_categories,
        required_compatibility=(
            required_compatibility or []
        ),
        max_cross_sell_percentage=max_cross_sell_percentage,
        checkout_confirmation_required=(
            checkout_confirmation_required
        ),
        buyer_goal=buyer_goal,
        created_at=creation_time,
        expires_at=creation_time
        + timedelta(minutes=expires_in_minutes),
    )

    save_purchase_mandate(mandate)
    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_created",
        subject="buyer-approved-terms",
        actor="buyer",
        outcome="recorded",
        reason_code="BUYER_APPROVED_MANDATE",
        explanation=(
            "The buyer approved and stored immutable purchase limits."
        ),
        amount_paise=mandate.budget_paise,
        currency=mandate.currency,
        created_at=mandate.created_at,
    )
    return mandate


def authorize_product_under_mandate(
    *,
    mandate_id: str,
    product: Product,
    current_total_paise: int = 0,
    is_cross_sell: bool = False,
    evaluated_at: datetime | None = None,
) -> MandateProductAuthorization:
    if current_total_paise < 0:
        raise ValueError("Current purchase total cannot be negative.")

    mandate = get_purchase_mandate(mandate_id)
    if mandate is None:
        raise PurchaseMandateNotFoundError(
            "Purchase mandate was not found."
        )

    now = evaluated_at or datetime.now(timezone.utc)
    if now >= mandate.expires_at:
        _record_expiry_rejection(mandate, product, now)
        raise PurchaseMandateExpiredError(
            "Purchase mandate has expired."
        )

    violation = _find_policy_violation(
        mandate=mandate,
        product=product,
        current_total_paise=current_total_paise,
        is_cross_sell=is_cross_sell,
    )
    if violation is not None:
        reason_code, explanation = violation
        _record_policy_rejection(
            mandate=mandate,
            product=product,
            reason_code=reason_code,
            explanation=explanation,
            evaluated_at=now,
        )
        raise PurchaseMandatePolicyError(
            reason_code,
            explanation,
        )

    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_accepted",
        subject=f"product:{product.sku}",
        actor="deterministic_core",
        outcome="allowed",
        reason_code="PRODUCT_WITHIN_MANDATE",
        explanation=(
            "The product satisfies the buyer-approved mandate."
        ),
        sku=product.sku,
        amount_paise=product.price_paise,
        currency=mandate.currency,
        created_at=now,
    )
    return MandateProductAuthorization(
        mandate_id=mandate.mandate_id,
        sku=product.sku,
    )


def _find_policy_violation(
    *,
    mandate: PurchaseMandate,
    product: Product,
    current_total_paise: int,
    is_cross_sell: bool,
) -> tuple[str, str] | None:
    if not product.active or product.stock <= 0:
        return (
            "PRODUCT_UNAVAILABLE",
            "The product is not currently available for purchase.",
        )

    if product.category.strip().lower() not in (
        mandate.allowed_categories
    ):
        return (
            "CATEGORY_NOT_ALLOWED",
            "The product category is outside the buyer-approved mandate.",
        )

    product_tags = {
        tag.strip().lower()
        for tag in product.compatibility_tags
    }
    missing_tags = set(mandate.required_compatibility) - product_tags
    if missing_tags:
        return (
            "COMPATIBILITY_NOT_SATISFIED",
            "The product does not satisfy required compatibility terms.",
        )

    if current_total_paise + product.price_paise > mandate.budget_paise:
        return (
            "BUDGET_EXCEEDED",
            "The product would exceed the buyer-approved budget.",
        )

    if is_cross_sell:
        if current_total_paise <= 0:
            return (
                "CROSS_SELL_BASE_REQUIRED",
                "A cross-sell requires an approved base-product total.",
            )
        if (
            product.price_paise * 100
            > current_total_paise
            * mandate.max_cross_sell_percentage
        ):
            return (
                "CROSS_SELL_LIMIT_EXCEEDED",
                "The cross-sell exceeds the buyer-approved percentage.",
            )

    return None


def _record_policy_rejection(
    *,
    mandate: PurchaseMandate,
    product: Product,
    reason_code: str,
    explanation: str,
    evaluated_at: datetime,
) -> None:
    subject = f"product:{product.sku}:{reason_code}"
    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_policy_violated",
        subject=subject,
        actor="deterministic_core",
        outcome="rejected",
        reason_code=reason_code,
        explanation=explanation,
        sku=product.sku,
        amount_paise=product.price_paise,
        currency=mandate.currency,
        created_at=evaluated_at,
    )
    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_rejected",
        subject=subject,
        actor="deterministic_core",
        outcome="rejected",
        reason_code="PRODUCT_REJECTED_BY_MANDATE",
        explanation=(
            "The product was rejected by the buyer-approved mandate."
        ),
        sku=product.sku,
        amount_paise=product.price_paise,
        currency=mandate.currency,
        created_at=evaluated_at,
    )


def _record_expiry_rejection(
    mandate: PurchaseMandate,
    product: Product,
    evaluated_at: datetime,
) -> None:
    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_expired",
        subject="authorization-attempt",
        actor="deterministic_core",
        outcome="rejected",
        reason_code="MANDATE_EXPIRED",
        explanation=(
            "The mandate expired before the authorization attempt."
        ),
        created_at=evaluated_at,
    )
    record_audit_event(
        mandate_id=mandate.mandate_id,
        event_type="mandate_rejected",
        subject=f"expired-product:{product.sku}",
        actor="deterministic_core",
        outcome="rejected",
        reason_code="EXPIRED_MANDATE_REJECTED",
        explanation=(
            "The product was rejected because the mandate expired."
        ),
        sku=product.sku,
        amount_paise=product.price_paise,
        currency=mandate.currency,
        created_at=evaluated_at,
    )
